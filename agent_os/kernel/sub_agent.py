"""
Background sub-agent execution.
"""

from __future__ import annotations

import json
import asyncio
import re
import shutil
from datetime import datetime
from time import perf_counter
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from ..core.session import SessionManager
from ..memory.workspace import WorkspaceMemory
from ..tools.registry import ToolRegistry, ToolResult, set_session_context
from .helpers import extract_message_content, format_exception, serialize_tool_calls, slug
from .result_filter import ResultFilterAgent

# Tools whose output is compressed through ResultFilterAgent before entering sub-agent context.
# These are search/retrieval tools that can return 50K+ character results.
_FILTERABLE_TOOLS = frozenset({
    "workspace_search",
})

# Fallback for tests/legacy construction without a ToolRegistry. Runtime uses
# the active registry's ``toolset == "retrieval"`` so config.yaml enable/disable
# and plugin tools are honored.
_DEFAULT_EXTERNAL_RETRIEVAL_TOOLS = frozenset({
    "web_search",
    "web_read",
    "arxiv_search",
    "crossref_search",
    "openalex_works",
    "openalex_entity",
    "wikipedia_lookup",
    "pubmed_search",
    "opencitations_search",
    "law_retrieve",
    "case_retrieve",
})


class SubAgent:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        request_timeout_seconds: int,
        parent_session_id: str | None,
        allowed_tools: list[str] | None,
        tool_registry: ToolRegistry,
        session_manager: SessionManager,
        workspace_memory: WorkspaceMemory,
        retriever,
        mineru_client=None,
        sub_agent_id: str = "",
        max_iterations: int = 32,
        interrupt_check=None,
        reasoning_effort=None,
        sub_agent_work_dir: str = "",
        pending_messages: dict[str, list[str]] | None = None,
        child_interrupt=None,
        filter_tools: frozenset[str] | None = None,
    ):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=request_timeout_seconds, max_retries=0)
        self.model = model
        self.request_timeout_seconds = request_timeout_seconds
        self.parent_session_id = parent_session_id
        self.allowed_tools = allowed_tools
        self.tools = tool_registry
        self.sessions = session_manager
        self.workspace_memory = workspace_memory
        self.retriever = retriever
        self.mineru_client = mineru_client
        self.sub_agent_id = sub_agent_id or "sub_agent"
        self.max_iterations = max_iterations
        self.interrupt_check = interrupt_check
        self.reasoning_effort = reasoning_effort
        self._pending_messages = pending_messages or {}
        self._child_interrupt = child_interrupt
        self._provider = "deepseek" if "deepseek" in str(base_url) else "dashscope"
        self.result_filter = ResultFilterAgent(api_key=api_key, base_url=base_url, model=model, timeout_seconds=request_timeout_seconds)
        self._filter_tools: frozenset[str] = filter_tools if filter_tools is not None else _FILTERABLE_TOOLS
        self._session_id: str = ""
        self._status_path = Path(sub_agent_work_dir) / "raw_search" / "subagents" / sub_agent_id / "_status.jsonl" if sub_agent_work_dir else None
        self._started_at = datetime.now().isoformat(timespec="seconds")
        self._started_at_ts = perf_counter()
        self._tool_count = 0
        self._total_tokens = 0

    def _write_status(
        self,
        iteration: int,
        tool_names: list[str],
        thinking: str = "",
        *,
        status: str = "running",
        phase: str = "model.completed",
        latency_ms: int | None = None,
        error: str = "",
    ) -> None:
        if not self._status_path:
            return
        try:
            self._status_path.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now().isoformat(timespec="seconds")
            payload = {
                "ts": now,
                "updated_at": now,
                "started_at": self._started_at,
                "sub_agent_id": self.sub_agent_id,
                "status": status,
                "phase": phase,
                "iteration": iteration,
                "tool_names": tool_names,
                "thinking": thinking[:120],
                "error": error,
            }
            if latency_ms is not None:
                payload["latency_ms"] = latency_ms
            if status in {"completed", "failed", "interrupted"}:
                payload["completed_at"] = now
            with self._status_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass

    async def run(self, task_description: str) -> str:
        if not self.parent_session_id:
            return "missing parent session"
        session = await self.sessions.get(self.parent_session_id)
        if session is None:
            return "parent session not found"

        # Create an isolated child session that shares the parent's work_dir.
        # Sub-agent messages go here, not into the parent's conversation history.
        parent_name = re.sub(r'[^\w\-_\u4e00-\u9fff]', '_', (session.name or "unnamed")[:16]).rstrip('_.') or "unnamed"
        child = await self.sessions.create(
            name=f"{parent_name}__subagent_{self.sub_agent_id}",
            parent_session_id=self.parent_session_id,
            stage=session.stage,
            metadata={"is_subagent": True, "subagent_id": self.sub_agent_id},
        )
        # Clean up the auto-created dir — sub-agent shares parent's work_dir
        created_dir = Path(child.work_dir)
        child.work_dir = session.work_dir
        await self.sessions.update(child)
        try:
            if created_dir.exists() and created_dir != Path(session.work_dir):
                shutil.rmtree(str(created_dir))
        except OSError:
            pass
        self._session_id = child.id

        from ..prompts import sub_agent

        runtime_rules = (
            f"\n\n<sub_agent_runtime>\n"
            f"- sub_agent_id: {self.sub_agent_id}\n"
            f"- 工作目录共享；不要覆盖主 agent 或其他 sub-agent 的文件。\n"
            f"- 如需写入阶段性研究产物，优先写入 `research/subagents/{self.sub_agent_id}/`。\n"
            f"- 检索结果会自动归档到 raw_search/ 并入库；后续相似检索先复用归档。\n"
            f"- 收到中断信号时立即停止。\n"
            f"</sub_agent_runtime>"
        )

        messages = [{"role": "system", "content": sub_agent + runtime_rules}, {"role": "user", "content": task_description}]
        available_tools = self._convert_tools_for_model()
        final_content = ""
        forced_final_turn = False
        try:
            for iteration in range(self.max_iterations):
                if ((self.interrupt_check is not None and self.interrupt_check.is_set())
                    or (self._child_interrupt is not None and self._child_interrupt.is_set())):
                    self._write_status(iteration, ["*interrupted*"], "收到中断信号", status="interrupted", phase="run.interrupted")
                    await self._notify_parent("killed", "[任务被中断]")
                    return "[任务被中断]"

                # Drain pending messages (from send_message)
                if self._pending_messages:
                    pending = self._pending_messages.pop(self._session_id, [])
                    for msg in pending:
                        messages.append({"role": "user", "content": f"<send_message>\n{msg}\n</send_message>"})

                request_kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
                if self.reasoning_effort:
                    if self._provider == "deepseek":
                        request_kwargs["reasoning_effort"] = self.reasoning_effort
                        request_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
                    else:
                        request_kwargs["extra_body"] = {"enable_thinking": True}
                if available_tools:
                    request_kwargs["tools"] = available_tools
                    request_kwargs["tool_choice"] = "auto"
                    request_kwargs["parallel_tool_calls"] = True
                try:
                    model_task = asyncio.create_task(
                        self.client.chat.completions.create(**request_kwargs)
                    )
                    wait_tasks = [model_task]
                    if self.interrupt_check:
                        wait_tasks.append(asyncio.create_task(self.interrupt_check.wait()))
                    if self._child_interrupt:
                        wait_tasks.append(asyncio.create_task(self._child_interrupt.wait()))
                    done, pending = await asyncio.wait(
                        wait_tasks, timeout=self.request_timeout_seconds, return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if model_task not in done:
                        model_task.cancel()
                        if any(t in done for t in wait_tasks[1:]):
                            raise InterruptedError
                        raise RuntimeError(f"Model request timed out after {self.request_timeout_seconds} seconds")
                    response = model_task.result()
                except asyncio.TimeoutError as exc:
                    raise RuntimeError(f"Model request timed out after {self.request_timeout_seconds} seconds") from exc
                except asyncio.CancelledError:
                    raise
                if ((self.interrupt_check and self.interrupt_check.is_set())
                    or (self._child_interrupt and self._child_interrupt.is_set())):
                    self._write_status(iteration, ["*interrupted*"], "收到中断信号", status="interrupted", phase="run.interrupted")
                    await self._notify_parent("killed", "[任务被中断]")
                    return "[任务被中断]"
                assistant_message = response.choices[0].message
                output_text = extract_message_content(assistant_message.content)
                tool_calls = []
                for tool_call in assistant_message.tool_calls or []:
                    tool_calls.append({"id": tool_call.id, "name": tool_call.function.name, "arguments": tool_call.function.arguments})

                thinking = getattr(assistant_message, "reasoning_content", None)
                thinking_text = extract_message_content(thinking) if thinking else ""
                tool_names = [tc["name"] for tc in tool_calls]
                self._write_status(iteration, tool_names, thinking_text, status="running", phase="model.completed")
                if not tool_calls:
                    if not output_text and not forced_final_turn:
                        forced_final_turn = True
                        messages.append({
                            "role": "user",
                            "content": (
                                "The previous model response was empty and contained no tool calls. "
                                "Continue the assigned subtask. If enough information is available, report findings; "
                                "otherwise call the tools needed to proceed."
                            ),
                        })
                        continue
                    final_content = output_text
                    self._write_status(iteration, [], output_text[:120], status="completed", phase="run.completed")
                    await self._notify_parent("completed", final_content)
                    break

                # -- Tool execution --
                assistant_msg: dict[str, Any] = {"role": "assistant", "content": output_text or "", "tool_calls": serialize_tool_calls(tool_calls)}
                if thinking:
                    assistant_msg["reasoning_content"] = thinking
                messages.append(assistant_msg)

                parallel = len(tool_calls) > 1 and all(
                    (entry := self.tools.get_entry(c["name"])) and entry.concurrency_safe
                    for c in tool_calls
                )
                if parallel:
                    results = await asyncio.gather(
                        *(self._execute_tool(call) for call in tool_calls),
                        return_exceptions=True,
                    )
                    for call, raw_result in zip(tool_calls, results):
                        result = ToolResult.fail(str(raw_result)) if isinstance(raw_result, Exception) else raw_result
                        phase = "tool.failed" if not result.success else "tool.completed"
                        self._write_status(iteration, [call["name"]], result.error or "", status="running", phase=phase, error=result.error or "")
                        tool_content = await self._resolve_tool_content_for_messages(tool_name=call["name"], result=result, user_message=task_description)
                        tool_content = self._append_archive_reminder(tool_content, result)
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_content})
                        self._tool_count += 1
                else:
                    for call in tool_calls:
                        result = await self._execute_tool(call)
                        phase = "tool.failed" if not result.success else "tool.completed"
                        self._write_status(iteration, [call["name"]], result.error or "", status="running", phase=phase, error=result.error or "")
                        tool_content = await self._resolve_tool_content_for_messages(tool_name=call["name"], result=result, user_message=task_description)
                        tool_content = self._append_archive_reminder(tool_content, result)
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": tool_content})
                        self._tool_count += 1
            return final_content
        except Exception as exc:
            detail = format_exception(exc)
            self._write_status(0, ["*error*"], detail, status="failed", phase="run.failed", error=detail)
            await self._notify_parent("failed", detail)
            return detail

    def _convert_tools_for_model(self) -> list[dict[str, Any]]:
        schemas = []
        for tool in self.tools.get_available_schemas():
            function = tool.get("function", {})
            name = function.get("name", "")
            if name == "spawn":
                continue
            if self.allowed_tools is not None and name not in self.allowed_tools:
                continue
            schemas.append({"type": "function", "function": {"name": name, "description": function.get("description", ""), "parameters": function.get("parameters", {})}})
        return schemas

    async def _execute_tool(self, tool_call: dict[str, Any]) -> ToolResult:
        tool_name = tool_call["name"]
        if tool_name == "spawn":
            return ToolResult.fail("SubAgent cannot create subagents")
        arguments = tool_call["arguments"]
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return ToolResult.fail(f"Invalid JSON arguments: {arguments}")
        session = await self.sessions.get(self.parent_session_id or "")
        if session is None:
            return ToolResult.fail("Missing session")
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return ToolResult.fail(f"Tool not allowed: {tool_name}")

        tool_entry = self.tools.get_entry(tool_name)
        if tool_entry is None:
            return ToolResult.fail(f"Tool not enabled: {tool_name}")

        # Use the parent's work_dir (shared filesystem) but the child's
        # session_id (isolated message storage).
        set_session_context(work_dir=session.work_dir, session_id=self._session_id)
        result = await tool_entry.handler(**arguments)
        if result.success:
            archived_path = await self._archive_external_tool_result(self._session_id, tool_name, arguments, result)
            if archived_path and isinstance(result.data, dict):
                result.data["archived_path"] = archived_path
                result.data["reuse_reminder"] = (
                    f"本次检索结果已入库: {archived_path}。后续需要同类信息时，"
                    "优先使用 file_read/workspace_search 复用该归档，避免重复检索。"
                )
        if result.success and tool_name in {"file_write", "file_append"}:
            await self.workspace_memory.sync_file_artifact(self._session_id, arguments.get("path", ""))
        if result.success and tool_name == "file_delete":
            await self.workspace_memory.remove_artifact(self._session_id, arguments.get("path", ""))
        return result

    async def _resolve_tool_content_for_messages(self, *, tool_name: str, result: ToolResult, user_message: str) -> str:
        from .helpers import resolve_tool_content_for_messages
        return await resolve_tool_content_for_messages(
            tool_name=tool_name, result=result, user_message=user_message,
            result_filter=self.result_filter, filterable=tool_name in self._filter_tools,
            agent_context=user_message,
        )

    async def _archive_external_tool_result(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> str | None:
        data = result.data or {}
        now = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        sub_prefix = f"raw_search/subagents/{self.sub_agent_id}"
        if tool_name == "web_search":
            query = str(arguments.get("query", "")).strip()
            results = data.get("results", []) or []
            if not results:
                return None
            content = self._render_web_search_archive(query, results)
            path = f"{sub_prefix}/web_search/{now}_{slug(query or tool_name)}.md"
            metadata = {"query": query, "results": results, "generated_by": self.sub_agent_id}
        elif tool_name == "web_read":
            url = str(arguments.get("url", "")).strip()
            content = self._render_web_read_archive(url, data)
            path = f"{sub_prefix}/web_read/{now}_{slug(url or tool_name)}.md"
            metadata = {"url": url, "parser": data.get("parser"), "generated_by": self.sub_agent_id}
        elif tool_name == "law_retrieve":
            query = str(arguments.get("query", "")).strip()
            results = data.get("results", []) or []
            if not results:
                return None
            content = self._render_law_archive(query, results)
            path = f"{sub_prefix}/law/{now}_{slug(query or tool_name)}.md"
            metadata = {"query": query, "results": results, "generated_by": self.sub_agent_id}
        elif tool_name == "case_retrieve":
            query = str(arguments.get("query", "")).strip()
            results = data.get("results", []) or []
            if not results:
                return None
            content = self._render_case_archive(query, results)
            path = f"{sub_prefix}/case/{now}_{slug(query or tool_name)}.md"
            metadata = {"query": query, "results": results, "generated_by": self.sub_agent_id}
        elif self._is_external_retrieval_tool(tool_name):
            label = self._retrieval_archive_label(arguments, data, tool_name)
            content = self._render_generic_retrieval_archive(tool_name, arguments, data)
            path = f"{sub_prefix}/{tool_name}/{now}_{slug(label)}.md"
            results = data.get("results", []) if isinstance(data.get("results"), list) else []
            metadata = {
                "query": label,
                "tool": tool_name,
                "arguments": arguments,
                "generated_by": self.sub_agent_id,
                "source_urls": [item.get("url") for item in results if isinstance(item, dict) and item.get("url")],
            }
        else:
            return None

        metadata["lineage"] = {
            "generated_by": self.sub_agent_id,
            "sub_agent_id": self.sub_agent_id,
            "source_urls": metadata.get("source_urls", []),
        }
        await self.workspace_memory.upsert_artifact(
            session_id,
            path=path,
            content=content,
            artifact_type="external_retrieval",
            title=Path(path).name,
            summary=f"Archived {tool_name} result from {self.sub_agent_id}",
            metadata=metadata,
        )
        return path

    def _is_external_retrieval_tool(self, tool_name: str) -> bool:
        if tool_name == "workspace_search":
            return False
        tools = getattr(self, "tools", None)
        if tools is not None:
            entry = tools.get_entry(tool_name)
            return bool(entry and entry.toolset == "retrieval")
        return tool_name in _DEFAULT_EXTERNAL_RETRIEVAL_TOOLS

    @staticmethod
    def _append_archive_reminder(tool_content: str, result: ToolResult) -> str:
        if not result.success or not isinstance(result.data, dict):
            return tool_content
        archived_path = result.data.get("archived_path")
        if not archived_path:
            return tool_content
        return (
            tool_content
            + "\n\n<workspace_artifact_reminder>"
            + f"\nThis retrieval result has been saved and indexed at `{archived_path}`."
            + "\nBefore repeating a similar retrieval, use file_read or workspace_search against this archive."
            + "\n</workspace_artifact_reminder>"
        )

    @staticmethod
    def _render_web_search_archive(query: str, results: list[dict[str, Any]]) -> str:
        lines = ["# Web Search Archive", "", f"- Query: {query}", ""]
        for index, item in enumerate(results, start=1):
            lines.extend([f"## Result {index}", f"- Title: {item.get('title', '')}", f"- URL: {item.get('url', '')}", "", str(item.get("content", "")).strip(), ""])
        return "\n".join(lines).strip()

    @staticmethod
    def _render_web_read_archive(url: str, data: dict[str, Any]) -> str:
        lines = ["# Web Read Archive", "", f"- URL: {url}", f"- Parser: {data.get('parser', 'jina-reader')}"]
        return "\n".join(lines) + "\n\n---\n\n" + str(data.get("content", "")).strip()

    @staticmethod
    def _render_law_archive(query: str, results: list[dict[str, Any]]) -> str:
        lines = ["# Law Retrieval Archive", "", f"- Query: {query}", ""]
        for index, item in enumerate(results, start=1):
            lines.extend([f"## Result {index}", f"- Title: {item.get('title', '')}", f"- Law: {item.get('laws_name', '')}", f"- Article: {item.get('article_tag', '')}", "", str(item.get("content", "")).strip(), ""])
        return "\n".join(lines).strip()

    @staticmethod
    def _render_case_archive(query: str, results: list[dict[str, Any]]) -> str:
        lines = ["# Case Retrieval Archive", "", f"- Query: {query}", ""]
        for index, item in enumerate(results, start=1):
            lines.extend([f"## Result {index}", f"- Title: {item.get('title', '')}", f"- Case No: {item.get('case_no', '')}", f"- Court: {item.get('court', '')}", "", str(item.get("content", "")).strip(), ""])
        return "\n".join(lines).strip()

    @staticmethod
    def _retrieval_archive_label(arguments: dict[str, Any], data: dict[str, Any], tool_name: str) -> str:
        for key in ("query", "title", "doi", "openalex_id", "pmid", "identifier", "author", "venue", "topic"):
            value = str(arguments.get(key) or data.get(key) or "").strip()
            if value:
                return value
        return tool_name

    @staticmethod
    def _render_generic_retrieval_archive(tool_name: str, arguments: dict[str, Any], data: dict[str, Any]) -> str:
        lines = [
            f"# {tool_name} Archive",
            "",
            "## Arguments",
            "",
            "```json",
            json.dumps(arguments, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
        ]
        results = data.get("results")
        if isinstance(results, list):
            lines.extend(["## Results", ""])
            for index, item in enumerate(results, start=1):
                if isinstance(item, dict):
                    title = item.get("title") or item.get("display_name") or item.get("pmid") or item.get("doi") or f"Result {index}"
                    lines.extend([f"### {index}. {title}"])
                    url = item.get("url") or item.get("landing_page_url")
                    if url:
                        lines.append(f"- URL: {url}")
                    for key, value in item.items():
                        if key in {"title", "display_name", "url", "landing_page_url", "content"}:
                            continue
                        if value in ("", None, [], {}):
                            continue
                        lines.append(f"- {key}: {json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (list, dict)) else value}")
                    content = str(item.get("content") or item.get("abstract") or "").strip()
                    if content:
                        lines.extend(["", content])
                    lines.append("")
                else:
                    lines.extend([f"### {index}. Result", "", str(item), ""])
            extra = {k: v for k, v in data.items() if k != "results"}
            if extra:
                lines.extend(["## Metadata", "", "```json", json.dumps(extra, ensure_ascii=False, indent=2, default=str), "```"])
            return "\n".join(lines).strip()

        lines.extend(["## Data", "", "```json", json.dumps(data, ensure_ascii=False, indent=2, default=str), "```"])
        return "\n".join(lines).strip()

    async def _notify_parent(self, status: str, result_text: str) -> None:
        """Inject XML task-notification into parent session."""
        if not self.parent_session_id:
            return
        parent = await self.sessions.get(self.parent_session_id)
        if parent is None:
            return
        child = await self.sessions.get(self._session_id)
        if child is None:
            return
        meta = child.metadata or {}
        if meta.get("notified"):
            return  # already notified — prevent duplicates
        duration_ms = int((perf_counter() - self._started_at_ts) * 1000)
        notification = (
            f"<task-notification>\n"
            f"<task-id>{child.id}</task-id>\n"
            f"<sub-agent-id>{self.sub_agent_id}</sub-agent-id>\n"
            f"<status>{status}</status>\n"
            f"<summary>Agent \"{self.sub_agent_id}\" {status}</summary>\n"
            f"<result>{result_text}</result>\n"
            f"<usage>\n"
            f"  <tool_uses>{self._tool_count}</tool_uses>\n"
            f"  <duration_ms>{duration_ms}</duration_ms>\n"
            f"</usage>\n"
            f"</task-notification>"
        )
        await self.sessions.add_message(
            self.parent_session_id, "user", notification,
            kind="system", metadata={"type": "task_notification", "child_id": child.id},
        )
        child.metadata["notified"] = True
        await self.sessions.update(child)

    @staticmethod
    def _format_task_notification(status: str, sub_agent_id: str, session_id: str,
                                   result_text: str, tool_count: int, duration_ms: int) -> str:
        """Format a task-completion notification as XML for injection into coordinator context."""
        return (
            f"<task-notification>\n"
            f"<task-id>{session_id}</task-id>\n"
            f"<sub-agent-id>{sub_agent_id}</sub-agent-id>\n"
            f"<status>{status}</status>\n"
            f"<summary>Agent \"{sub_agent_id}\" {status}</summary>\n"
            f"<result>{result_text}</result>\n"
            f"<usage>\n"
            f"  <tool_uses>{tool_count}</tool_uses>\n"
            f"  <duration_ms>{duration_ms}</duration_ms>\n"
            f"</usage>\n"
            f"</task-notification>"
        )
