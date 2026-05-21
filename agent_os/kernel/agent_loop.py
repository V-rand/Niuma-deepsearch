"""
Main agent loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import hashlib
from datetime import datetime, timezone
from time import perf_counter
from pathlib import Path
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI, APIError, APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError, InternalServerError, BadRequestError

from ..core.event_bus import EventBus, EventType, get_event_bus
from ..core.session import Session, SessionManager
from ..config import Settings
from ..memory import ContextCompiler, WorkspaceMemory
from ..skills.loader import SkillLoader
from ..tools.registry import ToolRegistry, ToolResult, get_tool_registry, set_session_context
from .result_filter import ResultFilterAgent, _PRUNE_CHAR_THRESHOLD
from .helpers import (
    format_exception,
    is_content_filter_exception,
    serialize_tool_calls,
    convert_tools_for_model,
    deterministic_tool_call_id,
    normalize_messages_for_cache,
    sanitize_messages_surrogates,
    reconstruct_messages_from_db,
    estimate_messages_tokens,
    extract_message_content,
    format_compression_summary,
    build_compact_handoff,
    slug,
)

logger = logging.getLogger(__name__)

# Tools whose results may be large (>10K chars). Their output is compressed by
# ResultFilterAgent before being injected into the model context.
_DEFAULT_FILTERABLE_TOOLS = frozenset({
    "workspace_search",
})

# Tools that carry a path/pattern argument — conditional skills are checked
# against these paths for auto-activation.
_PATH_BEARING_TOOLS = frozenset({
    "file_read", "file_write", "file_append", "file_delete", "file_edit",
    "file_grep", "file_list", "file_tree",
})

from dataclasses import dataclass, field


@dataclass
class _LoopState:
    """Carries messages list and transition reason between loop iterations."""
    messages: list[dict[str, Any]] = field(default_factory=list)
    transition: str | None = None  # why the previous iteration continued

class AgentLoop:
    def __init__(
        self,
        *,
        settings: "Settings",
        session_manager: SessionManager,
        tool_registry: ToolRegistry | None = None,
        event_bus: EventBus | None = None,
        context_compiler: ContextCompiler,
        workspace_memory: WorkspaceMemory,
        retriever,
        skill_loader: SkillLoader,
        mineru_client=None,
    ):
        self.api_key = settings.api_key or ""
        self.base_url = settings.base_url
        self.model = settings.model
        self.request_timeout_seconds = settings.model_timeout_seconds
        self.max_iterations = settings.max_iterations
        self._CONTEXT_TOKEN_THRESHOLD = settings.context_token_threshold
        self._COMPRESS_HEAD_TURNS = settings.compress_head_turns
        self._COMPRESS_TAIL_TURNS = settings.compress_tail_turns
        self._preserve_recent_tokens = settings.preserve_recent_tokens
        self._reasoning_effort = settings.reasoning_effort
        self._provider = settings.provider
        self._enable_explicit_cache = settings.enable_explicit_cache
        self._filter_tools: frozenset[str] = frozenset(settings.filter_tools) if settings.filter_tools is not None else _DEFAULT_FILTERABLE_TOOLS
        # Frozen at AgentLoop start to keep KV cache prefix stable across context
        # rebuilds. The timestamp is injected as a user message, and while it changes
        # position relative to history, it sits after the system prompt prefix — so
        # the core prefix stays cacheable. Accuracy to the day is sufficient; precise
        # time can be obtained via bash date command when needed.
        self._session_start_time = datetime.now()

        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url,
                                  timeout=self.request_timeout_seconds, max_retries=0)
        self.sessions = session_manager
        self.tools = tool_registry or get_tool_registry()
        self.event_bus = event_bus or get_event_bus()
        self.context_compiler = context_compiler
        self.workspace_memory = workspace_memory
        self.retriever = retriever
        self.skill_loader = skill_loader
        self.mineru_client = mineru_client
        self._sub_agent_counter = 0
        self.result_filter = ResultFilterAgent(
            api_key=self.api_key, base_url=self.base_url, model=self.model,
            timeout_seconds=self.request_timeout_seconds,
        )
        # KV-cache-aware context cache: compiled system prompt + message list per session.
        # When a session's context is "dirty" (tool calls modify files, memory, or skills),
        # the next request rebuilds the system prompt — invalidating the KV prefix cache.
        self._session_contexts: dict[str, dict[str, Any]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._pending_messages: dict[str, list[str]] = {}
        self._interrupt_events: dict[str, asyncio.Event] = {}
        self._uploads_snapshots: dict[str, dict[str, tuple[float, int]]] = {}
        self._context_dirty_sessions: set[str] = set()
        self._memory_dirty_sessions: set[str] = set()
        # Sessions where the model returned a content filter error.
        # Two-level quarantine:
        #   Level 1 (soft): The triggering user message has been deleted from DB.
        #                    On next compilation, load ALL message kinds — the
        #                    deleted message is gone, but previous context remains.
        #   Level 2 (hard): If Level 1 also hits the filter, escalate to system-only.
        #                    Same as original behavior — all chat/tool history purged.
        self._content_filter_quarantine_sessions: set[str] = set()
        self._hard_quarantine_sessions: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def preview_context(self, session_id: str, message: str) -> dict[str, Any]:
        lock = self._get_session_lock(session_id)
        async with lock:
            session, compiled = await self._compile_context(session_id, message)
            if session is None or compiled is None:
                raise ValueError(f"Session not found: {session_id}")
            return {
                "session_id": session.id, "session_name": session.name,
                "stage": session.stage, "system_prompt": compiled.system_prompt,
                "recent_messages": compiled.recent_messages,
                "memory_snippets": compiled.memory_snippets,
            }

    async def _auto_parse_uploads(self, session: Session) -> list[str]:
        """Scan uploads/ for new files and auto-parse them."""
        uploads_dir = Path(session.work_dir) / "uploads"
        if not uploads_dir.exists():
            return []
        parsed = []
        existing_artifacts = list(self.workspace_memory.store.list_artifacts_by_work_dir(session.work_dir))
        parsed_sources = set()
        for row in existing_artifacts:
            meta = self.workspace_memory.store.row_to_json(row) or {}
            sp = (meta.get("metadata") or {}).get("source_path")
            if sp:
                parsed_sources.add(sp)
        for file_path in uploads_dir.iterdir():
            if not file_path.is_file():
                continue
            source_path = f"uploads/{file_path.name}"
            if source_path in parsed_sources:
                continue
            try:
                content = await self._parse_upload_file(file_path, source_path)
                output_path = f"drafts/derived/{file_path.stem}__{file_path.suffix.lstrip('.').lower()}.md"
                await self.workspace_memory.upsert_artifact(
                    session.id, path=output_path, content=content,
                    artifact_type="derived_upload", title=file_path.name,
                    summary=f"Auto-parsed from {source_path}",
                    metadata={"source_path": source_path, "source_paths": [source_path],
                               "generated_by": "auto_parse",
                               "lineage": {"source_paths": [source_path], "generated_by": "auto_parse"}},
                )
                parsed.append(file_path.name)
            except Exception as e:
                logger.warning("Auto-parse failed for %s: %s", file_path.name, e)
                pass
        return parsed

    async def _parse_upload_file(self, source_path: Path, rel_path: str) -> str:
        from ..tools.base_tools import _derived_md, _do_parse

        result = await _do_parse(self.mineru_client, source_path, "ch", None)
        return _derived_md(
            source=rel_path,
            parser=str(result.get("parser", "")),
            md_url=result.get("markdown_url"),
            task_id=result.get("task_id"),
            body=str(result.get("content", "")),
        )

    @staticmethod
    def _snapshot_uploads(work_dir: str) -> dict[str, tuple[float, int]]:
        """Return {(relative_path): (mtime, size)} for uploads/ files."""
        uploads_dir = Path(work_dir) / "uploads"
        if not uploads_dir.exists():
            return {}
        snapshot: dict[str, tuple[float, int]] = {}
        for item in uploads_dir.iterdir():
            if item.is_file():
                stat = item.stat()
                rel = f"uploads/{item.name}"
                snapshot[rel] = (stat.st_mtime, stat.st_size)
        return snapshot

    def _check_uploads_changed(self, work_dir: str, session_id: str) -> list[Path]:
        """Return new or modified uploads since last snapshot."""
        uploads_dir = Path(work_dir) / "uploads"
        if not uploads_dir.exists():
            return []
        prev = self._uploads_snapshots.get(session_id, {})
        current: dict[str, tuple[float, int]] = {}
        new_files: list[Path] = []
        for item in sorted(uploads_dir.iterdir()):
            if not item.is_file():
                continue
            stat = item.stat()
            rel = f"uploads/{item.name}"
            current[rel] = (stat.st_mtime, stat.st_size)
            if rel not in prev or prev[rel] != current[rel]:
                new_files.append(item)
        self._uploads_snapshots[session_id] = current
        return new_files

    async def process(
        self, session_id: str, message: str, stream: bool = False,
        context_mode: str = "default", request_timeout_seconds: int | None = None,
        max_iterations: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        lock = self._get_session_lock(session_id)
        async with lock:
            try:
                session, messages, system_prompt, compiled = await self._get_or_init_session_context(session_id, message)
            except ValueError as exc:
                yield {"type": "error", "error": str(exc)}
                return

            user_message_id = await self.sessions.add_message(session_id, "user", message, kind="chat")
            yield {
                "type": "activity",
                "phase": "context.compiled",
                "detail": "上下文已加载",
                "payload": {"session_id": session_id},
            }
            yield {"type": "activity", "phase": "run.started", "detail": "开始处理用户输入"}

            # Auto-parse any new files in uploads/
            parsed_files = await self._auto_parse_uploads(session)
            if parsed_files:
                names = ", ".join(parsed_files)
                message = f"<system-reminder>[工作区更新：已解析 {names}]</system-reminder>\n\n{message}"
                yield {"type": "activity", "phase": "uploads.parsed", "detail": f"自动解析了 {len(parsed_files)} 个新文件: {names}"}

            # Save initial uploads snapshot for mid-run change detection
            self._uploads_snapshots[session_id] = self._snapshot_uploads(session.work_dir)

            effective_timeout = request_timeout_seconds or self.request_timeout_seconds
            effective_max_iterations = max_iterations or self.max_iterations

            messages.append({"role": "user", "content": message})
            chat_tools = convert_tools_for_model(self.tools.get_available_schemas())

            try:
                iteration = 0
                final_content: str | None = None
                forced_final_turn = False
                consecutive_tool_rounds = 0
                state = _LoopState(messages=messages)
                while iteration < effective_max_iterations:
                    iteration += 1
                    state.transition = None

                    # Check interrupt signal
                    interrupt_event = self._interrupt_events.get(session_id)
                    if interrupt_event is not None and interrupt_event.is_set():
                        interrupt_event.clear()
                        yield {"type": "activity", "phase": "run.interrupted", "detail": "收到中断请求，正在停止"}
                        break

                    # -- Phase 1: attachments (uploads, interventions, pending, nudge) + compression --
                    async for evt in self._inject_turn_attachments(
                        session_id, session, messages, iteration, consecutive_tool_rounds, message, system_prompt,
                    ):
                        yield evt
                        if evt.get("type") == "session.compressed":
                            session_id = evt["new_session_id"]
                            session, messages, system_prompt, _compiled = await self._get_or_init_session_context(session_id, message)
                            if not self._message_tail_matches(messages, role="user", content=message):
                                messages.append({"role": "user", "content": message})
                            state.transition = "session_compressed"
                    if state.transition == "session_compressed":
                        continue

                    # -- Phase 2: Model request → stream parse → usage event --
                    yield {"type": "activity", "phase": "model.requested", "detail": f"第 {iteration} 轮决策中", "payload": {"iteration": iteration}}
                    request_kwargs = self._build_model_request_kwargs(messages, chat_tools)  # side-effects: sanitize + normalize
                    request_started = perf_counter()

                    ok, response = False, None
                    try:
                        ok, result = await self._run_interruptible(
                            session_id,
                            self._request_model_with_retry(request_kwargs, effective_timeout, session_id, iteration),
                        )
                        if ok:
                            response, retry_events = result
                    except BadRequestError as exc:
                        if not self._is_context_length_error(exc):
                            raise
                        yield {
                            "type": "activity",
                            "phase": "context.compressing",
                            "detail": "上下文超过窗口上限，自动压缩中...",
                            "payload": {"reason": "context_length_exceeded", "iteration": iteration},
                        }
                        compacted = False
                        async for evt in self._compress_session_context_flow(session_id, messages, system_prompt, message):
                            yield evt
                            if evt.get("type") == "session.compressed":
                                session_id = evt["new_session_id"]
                                session, messages, system_prompt, _compiled = await self._get_or_init_session_context(session_id, message)
                                if not self._message_tail_matches(messages, role="user", content=message):
                                    messages.append({"role": "user", "content": message})
                                compacted = True
                                break
                        if compacted:
                            state.transition = "session_compressed"
                            continue  # re-enter while loop with compacted session
                        detail = format_exception(exc)
                        yield {"type": "activity", "phase": "run.failed", "detail": detail}
                        yield {"type": "error", "error": detail}
                        return
                    if not ok:
                        yield {"type": "activity", "phase": "run.interrupted", "detail": "收到中断请求，正在停止"}
                        break
                    for evt in retry_events:
                        yield evt

                    parsed: dict[str, Any] = {}
                    async for evt in self._parse_streaming_response(response, request_started, iteration, result=parsed):
                        yield evt
                    tool_calls = parsed["tool_calls"]
                    output_text = parsed["output_text"]
                    reasoning_text = parsed["reasoning_text"]
                    usage_dict = parsed["usage_dict"]
                    latency_ms = parsed["latency_ms"]

                    yield self._format_model_completed_event(usage_dict, latency_ms, iteration, reasoning_text)

                    # Decide: final answer or tool calls
                    if not tool_calls:
                        pending_before_final = self._pending_messages.pop(session_id, [])
                        if pending_before_final:
                            for msg in pending_before_final:
                                messages.append({"role": "user", "content": msg})
                                yield {"type": "activity", "phase": "message.injected", "detail": f"用户注入消息: {msg[:100]}"}
                            state.transition = "pending_messages"
                            continue

                        if not output_text and not forced_final_turn:
                            forced_final_turn = True
                            messages.append({
                                "role": "user",
                                "content": (
                                    "The previous model response was empty and contained no tool calls. "
                                    "Continue the task. If enough information is available, provide the answer; "
                                    "otherwise call the tools needed to proceed."
                                ),
                            })
                            state.transition = "empty_response"
                            continue

                        final_content = output_text
                        break

                    consecutive_tool_rounds += 1

                    async for evt in self._persist_assistant_tool_call(session_id, tool_calls, output_text, reasoning_text, iteration, messages):
                        yield evt

                    # -- Phase 3: Tool execution (parallel or sequential) --
                    parallel_safe = len(tool_calls) > 1 and all(
                        (entry := self.tools.get_entry(c["name"])) and entry.concurrency_safe
                        for c in tool_calls
                    )
                    if parallel_safe:
                        for evt in self._emit_tool_starts(tool_calls):
                            yield evt
                        ok, raw = await self._run_interruptible(
                            session_id,
                            asyncio.gather(
                                *[self._execute_tool_timed(tc, session_id, iteration=iteration) for tc in tool_calls],
                                return_exceptions=True,
                            ),
                        )
                        if not ok:
                            yield {"type": "activity", "phase": "run.interrupted", "detail": "收到中断请求，正在停止"}
                            break
                        async for evt in self._emit_tool_results_parallel(tool_calls, raw, session_id, iteration, message, messages):
                            yield evt
                    else:
                        for tc in tool_calls:
                            call_args = tc["arguments"]
                            if isinstance(call_args, str):
                                try: call_args = json.loads(call_args)
                                except json.JSONDecodeError: pass
                            yield {"type": "tool_call", "name": tc["name"], "arguments": call_args, "summary": self._summarize_tool_call(tc["name"], call_args if isinstance(call_args, dict) else {})}
                            yield {"type": "activity", "phase": "tool.executing", "detail": f"正在调用工具: {tc['name']}"}
                            ok, raw = await self._run_interruptible(
                                session_id,
                                self._execute_tool_timed(tc, session_id, iteration=iteration),
                            )
                            if not ok:
                                yield {"type": "activity", "phase": "run.interrupted", "detail": "收到中断请求，正在停止"}
                                break
                            results = [raw]
                            async for evt in self._emit_tool_results_parallel([tc], results, session_id, iteration, message, messages):
                                yield evt

                    if any(tc["name"] == "todowrite" for tc in tool_calls):
                        consecutive_tool_rounds = 0
                        refreshed = await self.sessions.get(session_id)
                        if refreshed is not None:
                            session = refreshed
                    else:
                        consecutive_tool_rounds += 1

                    state.transition = "next_turn"  # implicit continue → next while iteration

                    if iteration >= effective_max_iterations and not forced_final_turn:
                        forced_final_turn = True
                        effective_max_iterations += 1
                        messages.append({
                            "role": "user",
                            "content": (
                                "Iteration budget reached. Do not treat this as a user request to stop early. "
                                "If the research is incomplete, produce a progress report with completed findings, "
                                "workspace paths, and explicit remaining gaps; otherwise provide the final answer."
                            ),
                        })
                        state.transition = "iteration_budget"
                        continue

                    if forced_final_turn and tool_calls and effective_max_iterations > self.max_iterations + 3:
                        final_content = "已达到本轮最大迭代预算，研究尚未完成。请继续发送“继续”以基于现有工作区归档和工具结果接着推进。"
                        break

                    if forced_final_turn and tool_calls:
                        state.transition = "forced_final_turn"
                        final_content = "已达到最大迭代预算，任务尚未完成。请继续发送'继续'以基于现有工作成果接着推进。"
                        break

                # Final output
                if final_content:
                    await self.sessions.add_message(session_id, "assistant", final_content, kind="chat")
                    messages.append({"role": "assistant", "content": final_content})
                    yield {"type": "activity", "phase": "run.completed", "detail": "已生成最终答复"}
                    yield {"type": "content", "content": final_content}
                else:
                    yield {"type": "activity", "phase": "run.failed", "detail": "未能生成最终答复"}
                    yield {"type": "error", "error": "No final response produced"}
                if session_id in self._context_dirty_sessions:
                    self._session_contexts.pop(session_id, None)
                    self._context_dirty_sessions.discard(session_id)
                else:
                    self._session_contexts[session_id] = {"messages": list(messages), "system_prompt": system_prompt}
                    self._content_filter_quarantine_sessions.discard(session_id)
                    self._hard_quarantine_sessions.discard(session_id)
            except Exception as exc:
                detail = format_exception(exc)
                is_content_filter = self._is_content_filter_error_chain(exc)
                snapshot_path = self._write_failure_snapshot(
                    session=session,
                    session_id=session_id,
                    iteration=locals().get("iteration"),
                    user_message=message,
                    error=detail,
                    error_type=type(exc).__name__,
                    messages=messages,
                    recovery="content_filter_quarantine" if is_content_filter else "none",
                )
                if is_content_filter:
                    if session_id in self._content_filter_quarantine_sessions:
                        # Level 1 soft quarantine still triggered filter →
                        # escalate to Level 2: system-only recovery.
                        self._hard_quarantine_sessions.add(session_id)
                    else:
                        self._session_contexts.pop(session_id, None)
                        self._content_filter_quarantine_sessions.add(session_id)
                    try:
                        await self.sessions.delete_message(user_message_id)
                    except Exception:
                        logger.warning("Failed to rollback filtered user message for session %s", session_id, exc_info=True)
                    yield {
                        "type": "activity",
                        "phase": "context.recovered",
                        "detail": "已回滚本轮用户消息，并将在下一轮临时跳过历史对话上下文以恢复可用性",
                        "payload": {
                            "session_id": session_id,
                            "recovery": "content_filter_quarantine",
                            "snapshot_path": str(snapshot_path) if snapshot_path else None,
                        },
                    }
                payload = {"error_type": type(exc).__name__, "snapshot_path": str(snapshot_path) if snapshot_path else None}
                yield {"type": "activity", "phase": "run.failed", "detail": detail, "payload": payload}
                yield {"type": "error", "error": detail, "payload": payload}

    # ------------------------------------------------------------------
    # Model request with retry
    # ------------------------------------------------------------------

    async def _run_interruptible(
        self, session_id: str, coro,
    ) -> tuple[bool, Any]:
        """Race *coro* against the interrupt event. Returns (ok, result)."""
        interrupt_event = self._interrupt_events.get(session_id)
        if interrupt_event is None:
            return True, await coro
        if interrupt_event.is_set():
            interrupt_event.clear()
            return False, None
        main_task = asyncio.ensure_future(coro)
        async def _watch():
            await interrupt_event.wait()
        watch_task = asyncio.ensure_future(_watch())
        done, _ = await asyncio.wait([main_task, watch_task], return_when=asyncio.FIRST_COMPLETED)
        watch_task.cancel()
        if main_task in done:
            return True, main_task.result()
        main_task.cancel()
        interrupt_event.clear()
        return False, None

    async def _request_model_with_retry(
        self, request_kwargs: dict[str, Any], effective_timeout: int,
        session_id: str, iteration: int,
    ) -> tuple[Any, list[dict[str, Any]]]:
        max_retries = 2
        cf_retried = False
        active_request_kwargs = request_kwargs
        events: list[dict[str, Any]] = []
        for attempt in range(max_retries + 2):
            try:
                response = await asyncio.wait_for(
                    self.client.with_options(timeout=effective_timeout).chat.completions.create(
                        **active_request_kwargs, stream=True, stream_options={"include_usage": True},
                    ),
                    timeout=effective_timeout,
                )
                return response, events
            except (asyncio.TimeoutError, APITimeoutError, APIConnectionError) as exc:
                if attempt < max_retries:
                    wait_s = 2 ** attempt
                    events.append({"type": "activity", "phase": "model.retry", "detail": f"模型请求失败，{wait_s}s 后重试 ({attempt + 1}/{max_retries})", "payload": {"attempt": attempt + 1, "iteration": iteration, "error": str(exc)}})
                    await asyncio.sleep(wait_s)
                else:
                    raise RuntimeError(f"Model request failed after {max_retries + 1} attempts: {format_exception(exc)}") from exc
            except AuthenticationError:
                raise
            except BadRequestError as exc:
                retry_kwargs, stripped = self._content_filter_retry_kwargs(request_kwargs)
                if not cf_retried and is_content_filter_exception(exc) and stripped:
                    cf_retried = True
                    active_request_kwargs = retry_kwargs
                    events.append({
                        "type": "activity", "phase": "model.retry",
                        "detail": f"内容审查命中，已移除 {stripped} 条消息后重试 (1/1)",
                        "payload": {"attempt": attempt + 1, "iteration": iteration, "error": str(exc)},
                    })
                    continue
                raise
            except (APIError, RateLimitError, InternalServerError) as exc:
                if attempt < max_retries:
                    wait_s = 2 ** attempt
                    events.append({"type": "activity", "phase": "model.retry", "detail": f"模型服务异常，{wait_s}s 后重试 ({attempt + 1}/{max_retries})", "payload": {"attempt": attempt + 1, "error": str(exc)}})
                    await asyncio.sleep(wait_s)
                else:
                    raise RuntimeError(format_exception(exc)) from exc
            except Exception as exc:
                retry_kwargs, stripped = self._content_filter_retry_kwargs(request_kwargs)
                if not cf_retried and is_content_filter_exception(exc) and stripped:
                    cf_retried = True
                    active_request_kwargs = retry_kwargs
                    events.append({
                        "type": "activity", "phase": "model.retry",
                        "detail": f"内容审查命中，已移除 {stripped} 条消息后重试 (1/1)",
                        "payload": {"attempt": attempt + 1, "iteration": iteration, "error": str(exc)},
                    })
                    continue
                raise
        return None, events

    @staticmethod
    def _content_filter_retry_kwargs(request_kwargs: dict[str, Any]) -> tuple[dict[str, Any], int]:
        messages = request_kwargs.get("messages")
        if not isinstance(messages, list):
            return request_kwargs, 0
        retry_messages = list(messages)
        stripped = 0
        while retry_messages and stripped < 20:
            last = retry_messages[-1]
            if isinstance(last, dict) and last.get("role") in ("assistant", "tool"):
                retry_messages.pop()
                stripped += 1
                continue
            break
        if not stripped:
            return request_kwargs, 0
        return {**request_kwargs, "messages": retry_messages}, stripped

    @staticmethod
    def _is_context_length_error(exc: BaseException) -> bool:
        """Check if an exception is a context_length_exceeded error."""
        msg = str(exc).lower()
        return "context_length_exceeded" in msg or (
            "413" in msg and "prompt" in msg
        ) or (
            "too long" in msg and "context" in msg
        )

    def _build_model_request_kwargs(
        self, messages: list[dict[str, Any]], chat_tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Prepare request kwargs with thinking mode and tool schemas.

        Side-effects: sanitizes surrogate characters and normalizes messages
        for KV cache prefix stability.
        """
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if self._reasoning_effort:
            if self._provider == "deepseek":
                kwargs["reasoning_effort"] = self._reasoning_effort
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            else:
                kwargs["extra_body"] = {"enable_thinking": True}
        if chat_tools:
            kwargs["tools"] = chat_tools
            kwargs["tool_choice"] = "auto"
            kwargs["parallel_tool_calls"] = True
        sanitize_messages_surrogates(messages)
        normalize_messages_for_cache(messages)
        return kwargs

    def _format_model_completed_event(
        self, usage_dict: dict[str, Any], latency_ms: float, iteration: int,
        reasoning_text: str = "",
    ) -> dict[str, Any]:
        """Build the model.completed activity event with KV cache stats and full reasoning text."""
        usage_text = ""
        if usage_dict:
            pt = usage_dict.get("prompt_tokens", 0) or 0
            ct = usage_dict.get("completion_tokens", 0) or 0
            tt = usage_dict.get("total_tokens", 0) or 0
            cached = usage_dict.get("cached_tokens", 0) or 0
            cache_rate = round(cached / pt * 100, 1) if pt > 0 else 0
            pct = round(tt / self._CONTEXT_TOKEN_THRESHOLD * 100, 1) if self._CONTEXT_TOKEN_THRESHOLD else 0
            usage_text = f" | tokens: prompt {pt:,} + output {ct:,} = total {tt:,} (缓存 {cache_rate}% / 压缩阈值 {pct}%)"
        return {
            "type": "activity",
            "phase": "model.completed",
            "detail": f"模型响应完成 | 耗时: {latency_ms / 1000:.1f}s{usage_text}",
            "payload": {"latency_ms": latency_ms, "usage": usage_dict, "iteration": iteration, "reasoning_text": reasoning_text},
        }

    async def _persist_assistant_tool_call(
        self, session_id: str, tool_calls: list[dict[str, Any]],
        output_text: str, reasoning_text: str, iteration: int,
        messages: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Serialize, persist and yield the assistant message for a tool-call turn.

        Side-effects: appends the assembled assistant message to *messages*
        in-place, and writes it to the DB.  When on DeepSeek native API,
        reasoning_content is saved to metadata so it round-trips on context
        rebuilds.
        """
        serialized_tc = serialize_tool_calls(tool_calls)
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": output_text or "", "tool_calls": serialized_tc, "_iteration": iteration}
        assistant_meta: dict[str, Any] = {"tool_calls": serialized_tc, "iteration": iteration}
        if reasoning_text:
            assistant_msg["reasoning_content"] = reasoning_text
            # DeepSeek thinking mode: tool-call turns MUST round-trip
            # reasoning_content on all subsequent requests or the API
            # returns 400. Non-tool-call turns don't need it (ignored).
            assistant_meta["reasoning_content"] = reasoning_text
        messages.append(assistant_msg)
        await self.sessions.add_message(session_id, "assistant", output_text or "", kind="chat",
                                        metadata=assistant_meta)
        yield {"type": "activity", "phase": "tools.planned",
               "detail": f"模型计划调用: {', '.join(c['name'] for c in tool_calls)}",
               "payload": {"tools": [c["name"] for c in tool_calls], "iteration": iteration}}

    async def _parse_streaming_response(
        self,
        response,
        request_started: float,
        iteration: int,
        *,
        result: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Consume the OpenAI streaming response and yield progress events.

        Handles reasoning_content (thinking), content (final text), and
        tool_call arguments.  Writes the assembled data back into *result*:

          result["tool_calls"]       — list of {id, name, arguments}
          result["output_text"]      — concatenated content chunks
          result["reasoning_text"]   — concatenated thinking chunks
          result["usage_dict"]       — token usage (prompt, completion, cached)
          result["latency_ms"]       — wall-clock time since request_started

        The caller forwards yielded events and reads *result* after the loop.
        """
        reasoning_parts, content_parts, tool_calls_raw, usage_dict = [], [], {}, {}
        tool_arg_progress: dict[int, int] = {}
        thinking_buffer = ""
        async for chunk in response:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                usage_dict = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                    "cached_tokens": (
                        getattr(usage, "prompt_cache_hit_tokens", None)
                        or getattr(getattr(usage, "prompt_tokens_details", None) or {}, "cached_tokens", 0)
                    ),
                }
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_parts.append(rc)
                thinking_buffer += rc
                lb = thinking_buffer[-1] if thinking_buffer else ""
                if lb in "\n。！？":
                    yield {"type": "thinking_stream", "content": thinking_buffer}
                    thinking_buffer = ""
            ct = delta.content
            if ct:
                if thinking_buffer:
                    yield {"type": "thinking_stream", "content": thinking_buffer}
                    thinking_buffer = ""
                content_parts.append(ct)
                yield {"type": "content_stream", "content": ct}
            for tc_delta in delta.tool_calls or []:
                if thinking_buffer:
                    yield {"type": "thinking_stream", "content": thinking_buffer}
                    thinking_buffer = ""
                idx = tc_delta.index
                if idx not in tool_calls_raw:
                    tool_calls_raw[idx] = {"id": "", "name": "", "arguments": ""}
                if tc_delta.id:
                    tool_calls_raw[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_calls_raw[idx]["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tool_calls_raw[idx]["arguments"] += tc_delta.function.arguments
                        name = tool_calls_raw[idx].get("name", "")
                        arg_len = len(tool_calls_raw[idx]["arguments"])
                        last_reported = tool_arg_progress.get(idx, 0)
                        if name in {"file_write", "file_append"} and arg_len - last_reported >= 2000:
                            tool_arg_progress[idx] = arg_len
                            yield {
                                "type": "activity",
                                "phase": "tool.arguments_stream",
                                "detail": f"正在生成 {name} 写入内容: ~{arg_len:,} 字符",
                                "payload": {"tool": name, "argument_chars": arg_len, "iteration": iteration},
                            }

        if thinking_buffer:
            yield {"type": "thinking_stream", "content": thinking_buffer}
        latency_ms = round((perf_counter() - request_started) * 1000, 2)
        output_text = "".join(content_parts)
        reasoning_text = "".join(reasoning_parts)
        tool_calls = []
        for idx in sorted(tool_calls_raw.keys()):
            tc = tool_calls_raw[idx]
            if tc["name"]:
                tc["id"] = deterministic_tool_call_id(tc["name"], tc["arguments"], idx)
                tool_calls.append(tc)
        result["tool_calls"] = tool_calls
        result["output_text"] = output_text
        result["reasoning_text"] = reasoning_text
        result["usage_dict"] = usage_dict
        result["latency_ms"] = latency_ms

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(self, tool_call: dict[str, Any], session_id: str, *,
                            iteration: int | None = None, tool_call_id: str | None = None) -> ToolResult:
        tool_name = tool_call["name"]
        arguments = tool_call["arguments"]
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return ToolResult.fail(f"Invalid JSON arguments: {arguments}")

        session = await self.sessions.get(session_id)
        if session is None:
            return ToolResult.fail(f"Session not found: {session_id}")

        tool_entry = self.tools.get_entry(tool_name)
        if tool_entry is None:
            return ToolResult.fail(f"Tool not enabled: {tool_name}")

        await self.event_bus.publish_typed(EventType.TOOL_CALLED, payload={"tool": tool_name, "arguments": arguments}, session_id=session_id)

        set_session_context(work_dir=session.work_dir, session_id=session_id)
        result = await tool_entry.handler(**arguments)

        await self.event_bus.publish_typed(EventType.TOOL_COMPLETED, payload={"tool": tool_name, "success": result.success}, session_id=session_id)
        if result.success:
            archived_path = await self._archive_external_tool_result(session_id, tool_name, arguments, result)
            if archived_path and isinstance(result.data, dict):
                result.data["archived_path"] = archived_path
                result.data["reuse_reminder"] = (
                    f"本次检索结果已入库: {archived_path}。后续需要同类信息时，"
                    "优先使用 file_read/workspace_search 复用该归档，避免重复检索。"
                )
        if result.success and tool_name in {"file_write", "file_append"}:
            await self.workspace_memory.sync_file_artifact(session_id, arguments.get("path", ""))
        if result.success and tool_name == "file_delete":
            await self.workspace_memory.remove_artifact(session_id, arguments.get("path", ""))
        if result.success and self._tool_updates_memory_context(tool_name, arguments):
            self.mark_memory_dirty(session_id)
        if result.success and self._tool_updates_system_context(tool_name, arguments):
            self.mark_context_dirty(session_id)
        if result.success and tool_name in _PATH_BEARING_TOOLS:
            path = str(arguments.get("path", "") or arguments.get("pattern", "")).strip()
            if path:
                self._try_activate_conditional_skills(session_id, [path])
        return result

    def mark_context_dirty(self, session_id: str) -> None:
        """Mark session for system prompt rebuild on next request."""
        self._context_dirty_sessions.add(session_id)

    def mark_memory_dirty(self, session_id: str) -> None:
        """Mark MEMORY.md for dynamic user-context refresh without rebuilding system prompt."""
        self._memory_dirty_sessions.add(session_id)

    def _try_activate_conditional_skills(self, session_id: str, paths: list[str]) -> None:
        """Activate conditional skills whose paths frontmatter matches *paths*."""
        activated = self.skill_loader.activate_for_paths(paths)
        if activated:
            names = ", ".join(activated)
            logger.info("Activated conditional skills for session %s: %s", session_id, names)
            self.mark_context_dirty(session_id)

    @staticmethod
    def _tool_updates_system_context(tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name == "spawn":
            return True
        if tool_name not in {"file_write", "file_append", "file_delete"}:
            return False
        path = str(arguments.get("path", "")).strip().replace("\\", "/").lstrip("/").lower()
        return path in {"agent.md", "soul.md"}

    @staticmethod
    def _tool_updates_memory_context(tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name not in {"file_write", "file_append", "file_delete"}:
            return False
        path = str(arguments.get("path", "")).strip().replace("\\", "/").lstrip("/").lower()
        return path == "memory.md"

    async def _execute_tool_timed(
        self,
        tool_call: dict[str, Any],
        session_id: str,
        *,
        iteration: int | None = None,
    ) -> tuple[ToolResult, int]:
        started = perf_counter()
        result = await self._execute_tool(
            tool_call,
            session_id,
            iteration=iteration,
            tool_call_id=tool_call.get("id"),
        )
        return result, round((perf_counter() - started) * 1000)

    def _emit_tool_starts(self, tool_calls: list[dict]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for tc in tool_calls:
            call_arguments = tc["arguments"]
            if isinstance(call_arguments, str):
                try:
                    call_arguments = json.loads(call_arguments)
                except json.JSONDecodeError:
                    pass
            summary_args = call_arguments if isinstance(call_arguments, dict) else {}
            events.append({
                "type": "tool_call",
                "name": tc["name"],
                "arguments": call_arguments,
                "summary": self._summarize_tool_call(tc["name"], summary_args),
            })
            events.append({
                "type": "activity",
                "phase": "tool.executing",
                "detail": f"正在并发调用工具: {tc['name']}",
                "payload": {"tool": tc["name"], "parallel": True},
            })
        return events

    async def _emit_tool_results_parallel(
        self, tool_calls: list[dict], results: list, session_id: str,
        iteration: int, message: str, messages: list[dict],
    ) -> AsyncGenerator[dict[str, Any], None]:
        for tc, raw_result in zip(tool_calls, results):
            latency_ms = None
            if isinstance(raw_result, Exception):
                result = ToolResult.fail(str(raw_result))
            elif isinstance(raw_result, tuple):
                result, latency_ms = raw_result
            else:
                result = raw_result
            call_arguments = tc["arguments"]
            if isinstance(call_arguments, str):
                try:
                    call_arguments = json.loads(call_arguments)
                except json.JSONDecodeError:
                    pass
            tool_content = await self._resolve_tool_content_for_messages(tool_name=tc["name"], result=result, user_message=message, session_id=session_id)
            tool_content = self._append_archive_reminder(tool_content, result)
            tool_content = self._append_context_file_update_reminder(tool_content, tc["name"], call_arguments, result)
            await self._save_filtered_tool_result(tool_content, tc["name"], session_id)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_content})
            await self.sessions.add_message(session_id, "tool", tool_content, kind="tool", metadata={"tool_call_id": tc["id"], "tool_name": tc["name"]})
            yield {
                "type": "activity",
                "phase": "tool.completed",
                "detail": f"工具 {tc['name']} 执行完成 | 并发耗时: {latency_ms or 0:.1f}s",
                "payload": {"tool": tc["name"], "latency_ms": latency_ms, "success": result.success, "parallel": True},
            }
            result_dict = result.to_dict()
            if latency_ms is not None:
                result_dict["latency_ms"] = latency_ms
            result_dict["tool"] = tc["name"]
            result_dict["summary"] = self._summarize_tool_result(tc["name"], result_dict)
            archived_path = ((result.data or {}) if result.success else {}).get("archived_path")
            if archived_path:
                yield {
                    "type": "activity",
                    "phase": "artifact.saved",
                    "detail": f"检索结果已入库: {archived_path}",
                    "payload": {"tool": tc["name"], "path": archived_path},
                }
            yield {"type": "tool_result", "result": result_dict}

    # ------------------------------------------------------------------
    # Context compilation
    # ------------------------------------------------------------------

    async def _compile_context(self, session_id: str, message: str, context_mode: str = "default"):
        session = await self.sessions.get(session_id)
        if session is None:
            return None, None
        recent_messages = await self.sessions.get_messages(session_id, limit=10, kinds=["chat"])
        if recent_messages and recent_messages[-1]["role"] == "user" and recent_messages[-1]["content"] == message:
            recent_messages = recent_messages[:-1]
        compiled = self.context_compiler.compile(
            session, user_message=message, recent_messages=recent_messages,
            skills_index=self.skill_loader.build_skills_index_prompt(),
        )
        return session, compiled

    @staticmethod
    def _format_todo_nudge(active_todos: list[dict[str, Any]]) -> str:
        lines = ["<system-reminder>", "Active todos (not updated in several rounds):"]
        for todo in active_todos:
            icon = {"pending": "[ ]", "in_progress": "[>]", "blocked": "[!]"}.get(todo.get("status", "pending"), "[ ]")
            lines.append(f"- {icon} {todo.get('content', '')}")
        lines.append("\nCall todowrite to update status if tasks are done.")
        lines.append("</system-reminder>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Session context cache
    # ------------------------------------------------------------------

    async def _get_or_init_session_context(self, session_id: str, message: str) -> tuple[Session, list[dict[str, Any]], str, Any]:
        if session_id in self._context_dirty_sessions:
            self._session_contexts.pop(session_id, None)
            self._context_dirty_sessions.discard(session_id)
        cached = self._session_contexts.get(session_id)
        if cached is not None:
            session = await self.sessions.get(session_id)
            if session is not None and session.status == "active":
                if session_id in self._memory_dirty_sessions:
                    refreshed_messages = self._refresh_memory_context_message(session, list(cached["messages"]))
                    cached = {"messages": refreshed_messages, "system_prompt": cached["system_prompt"]}
                    self._session_contexts[session_id] = cached
                    self._memory_dirty_sessions.discard(session_id)
                return session, list(cached["messages"]), cached["system_prompt"], None
            self._session_contexts.pop(session_id, None)

        session = await self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        _, compiled = await self._compile_context(session_id, message)
        if compiled is None:
            raise ValueError(f"Failed to compile context for session: {session_id}")

        messages: list[dict[str, Any]] = [{"role": "system", "content": compiled.system_prompt}]
        # Inject memory content as user context (not system prompt - preserves KV cache prefix)
        if compiled.memory_content:
            messages.append({"role": "user", "content": compiled.memory_content})
        tree = self._format_workspace_tree(session.work_dir)
        if tree:
            messages.append({"role": "user", "content": tree})
        messages.append({"role": "user", "content": f"当前日期时间: {self._session_start_time.strftime('%Y-%m-%d %H:%M:%S')}"})
        if session_id in self._hard_quarantine_sessions:
            # Level 2: all prior chat/tool history may still trigger the filter —
            # only load system messages and workspace snapshot as recovery.
            recent = await self.sessions.get_messages(session_id, limit=20, kinds=["system"])
        elif session_id in self._content_filter_quarantine_sessions:
            # Level 1: provider filters can be triggered by prior chat/tool
            # text, not only the current user message. Keep stable system
            # context and workspace/memory, but skip chat/tool history.
            recent = await self.sessions.get_messages(session_id, limit=20, kinds=["system"])
            messages.append({
                "role": "user",
                "content": (
                    "<system-reminder>\n"
                    "上一轮请求因触及内容安全审查被拦截，已自动移除该条触发消息。\n"
                    "本轮已临时跳过历史 chat/tool 上下文，请基于稳定系统上下文、记忆和工作区继续工作。\n"
                    "</system-reminder>"
                ),
            })
        else:
            recent = await self.sessions.get_messages(session_id, kinds=["chat", "tool", "system"])
        messages.extend(reconstruct_messages_from_db(recent, need_reasoning_roundtrip=self._provider == "deepseek"))

        self._session_contexts[session_id] = {"messages": messages, "system_prompt": compiled.system_prompt}
        return session, messages, compiled.system_prompt, compiled

    def _refresh_memory_context_message(self, session: Session, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        refreshed = [message for message in messages if not self._is_memory_context_message(message)]
        memory_content = self.context_compiler.build_memory_content(session)
        if not memory_content:
            return refreshed
        insert_at = 1 if refreshed and refreshed[0].get("role") == "system" else 0
        refreshed.insert(insert_at, {"role": "user", "content": memory_content})
        return refreshed

    @staticmethod
    def _is_memory_context_message(message: dict[str, Any]) -> bool:
        return message.get("role") == "user" and str(message.get("content", "")).startswith("<memory>\n")

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    @staticmethod
    def _message_tail_matches(messages: list[dict[str, Any]], *, role: str, content: str) -> bool:
        if not messages:
            return False
        tail = messages[-1]
        return tail.get("role") == role and extract_message_content(tail.get("content", "")) == content

    def cleanup_session(self, session_id: str) -> None:
        """Drop all cached state for a session — called on session deletion or disposal.
        After this call, the next request for this session will perform a full cold start.
        """
        self._session_contexts.pop(session_id, None)
        self._session_locks.pop(session_id, None)
        self._pending_messages.pop(session_id, None)
        self._interrupt_events.pop(session_id, None)
        self._uploads_snapshots.pop(session_id, None)
        self._context_dirty_sessions.discard(session_id)
        self._content_filter_quarantine_sessions.discard(session_id)
        self._hard_quarantine_sessions.discard(session_id)

    @staticmethod
    def _is_content_filter_error_chain(exc: BaseException) -> bool:
        if is_content_filter_exception(exc):
            return True
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, BaseException) and is_content_filter_exception(cause):
            return True
        return False

    @staticmethod
    def _message_digest(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:12]

    def _write_failure_snapshot(
        self,
        *,
        session: Session,
        session_id: str,
        iteration: int | None,
        user_message: str,
        error: str,
        error_type: str,
        messages: list[dict[str, Any]],
        recovery: str,
    ) -> Path | None:
        logs_dir = Path(session.work_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = logs_dir / f"provider_failure_{ts}.json"
        preview_messages = []
        for msg in messages[-20:]:
            content = extract_message_content(msg.get("content", ""))
            preview_messages.append({
                "role": msg.get("role"),
                "length": len(content),
                "digest": self._message_digest(content),
                "preview_head": content[:160],
                "preview_tail": content[-160:] if len(content) > 160 else "",
            })
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id,
            "iteration": iteration,
            "model": self.model,
            "provider": self._provider,
            "error_type": error_type,
            "error": error,
            "recovery": recovery,
            "user_message_length": len(user_message),
            "user_message_digest": self._message_digest(user_message),
            "messages": preview_messages,
        }
        try:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return target
        except OSError:
            logger.warning("Failed to write provider failure snapshot for session %s", session_id)
            return None

    def inject_message(self, session_id: str, message: str) -> None:
        self._pending_messages.setdefault(session_id, []).append(message)

    def request_interrupt(self, session_id: str) -> None:
        event = self._interrupt_events.get(session_id)
        if event is None:
            self._interrupt_events[session_id] = asyncio.Event()
        self._interrupt_events[session_id].set()

    async def warmup_session_cache(self) -> int:
        active_sessions = await self.sessions.list_active_sessions()
        warmed = 0
        for session in active_sessions:
            _, compiled = await self._compile_context(session.id, "")
            if compiled is None:
                continue
            messages: list[dict[str, Any]] = [{"role": "system", "content": compiled.system_prompt}]
            if compiled.memory_content:
                messages.append({"role": "user", "content": compiled.memory_content})
            tree = self._format_workspace_tree(session.work_dir)
            if tree:
                messages.append({"role": "user", "content": tree})
            recent = await self.sessions.get_messages(session.id, kinds=["chat", "tool", "system"])
            messages.extend(reconstruct_messages_from_db(recent, need_reasoning_roundtrip=self._provider == "deepseek"))
            self._session_contexts[session.id] = {"messages": messages, "system_prompt": compiled.system_prompt}
            warmed += 1
        return warmed

    @staticmethod
    def _format_workspace_tree(work_dir: str) -> str:
        """One-shot directory tree snapshot for the model. Frozen at session start."""
        from pathlib import Path as _Path
        base = _Path(work_dir)
        if not base.exists():
            return ""
        lines = ["<workspace>", "当前工作区目录结构（初始快照，之后不会自动更新）：", ""]
        # Collect entries
        entries: list[tuple[str, bool, int]] = []
        for item in sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if item.is_dir():
                size_kb = sum(f.stat().st_size for f in item.rglob("*") if f.is_file()) / 1024
                entries.append((f"📁 {item.name}/", True, int(size_kb)))
            else:
                size_kb = item.stat().st_size / 1024
                entries.append((f"📄 {item.name}", False, int(size_kb)))
        for name, is_dir, size_kb in entries:
            size_str = f"({size_kb}KB)" if size_kb > 0 else ""
            lines.append(f"  {name} {size_str}")
        lines.append("")
        lines.append("需要最新目录信息请调用 file_list 或 file_tree。")
        lines.append("</workspace>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Context compression
    # ------------------------------------------------------------------

    async def compress_session(self, session_id: str) -> str | None:
        """Trigger context compression. Returns the new session_id, or None."""
        cached = self._session_contexts.get(session_id)
        if cached is None:
            _, _messages, _system_prompt, _ = await self._get_or_init_session_context(session_id, "")
            cached = self._session_contexts.get(session_id)
        if cached is None:
            return None
        new_sid, _summary = await self._compress_session_context(session_id, list(cached["messages"]), cached["system_prompt"], "")
        if not new_sid:
            return None
        _, _messages, _system_prompt, _ = await self._get_or_init_session_context(new_sid, "")
        return new_sid

    async def _compress_session_context_flow(
        self, session_id: str, messages: list[dict], system_prompt: str, message: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        new_session_id, summary_xml = await self._compress_session_context(session_id, messages, system_prompt, message)
        if not new_session_id:
            return
        estimated_tokens = estimate_messages_tokens(messages)
        yield {"type": "activity", "phase": "context.compressed", "detail": f"上下文已压缩，切换到新 session: {new_session_id}", "payload": {"old_session_id": session_id, "new_session_id": new_session_id, "estimated_tokens_before": estimated_tokens}}
        yield {"type": "session.compressed", "old_session_id": session_id, "new_session_id": new_session_id, "estimated_tokens_before": estimated_tokens, "summary": summary_xml}

    async def _compress_session_context(
        self, session_id: str, messages: list[dict[str, Any]], system_prompt: str, user_message: str,
    ) -> tuple[str | None, str | None]:
        # Read previous summary from compression_state.md if it exists
        previous_summary = ""
        session = await self.sessions.get(session_id)
        if session and session.work_dir:
            state_path = Path(session.work_dir) / "compression_state.md"
            if state_path.exists():
                try:
                    text = state_path.read_text("utf-8")
                    _, _, body = text.partition("---\n")
                    _, _, body = body.partition("\n---\n")
                    previous_summary = body.strip()
                except (OSError, IOError):
                    pass

        # Keep tail: walk backwards from end, accumulate token budget
        # so recent context (tool results, next steps) survives intact.
        tail_budget = self._preserve_recent_tokens
        tail_start = 1      # default: no tail kept
        running = 0
        for idx in range(len(messages) - 1, 1, -1):
            group = messages[idx:idx + 1]
            tok = estimate_messages_tokens(group)
            if running + tok <= tail_budget:
                running += tok
                tail_start = idx
            else:
                break

        head_messages = messages[1:tail_start]  # skip system prompt
        tail_messages = messages[tail_start:] if tail_start < len(messages) else []

        if not head_messages:
            # All messages fit in tail budget OR tail budget exceeded by first checked message.
            # If we're still over threshold, compress everything except system prompt.
            if len(messages) <= 2:
                return None, None  # too few messages to compress
            head_messages = messages[1:]
            tail_messages = []

        serialized = self._serialize_for_summary(head_messages)
        summary_xml = await self._generate_compression_summary(serialized, previous_summary=previous_summary)
        if not summary_xml:
            return None, None

        new_session = await self.sessions.fork_session(parent_session_id=session_id, summary_content="", summary_type="compression")
        if new_session is None:
            return None, None

        parent_work_dir = (await self.sessions.get(session_id)).work_dir if (await self.sessions.get(session_id)) else ""

        formatted_summary = format_compression_summary(summary_xml)
        transcript_path = f"{parent_work_dir}/compression_state.md" if parent_work_dir else ""
        handoff = build_compact_handoff(formatted_summary, transcript_path=transcript_path, version=new_session.compression_version)
        new_messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        new_messages.append({"role": "user", "content": handoff})
        # Inject recent context (files, skills) before preserved tail messages
        for ctx in await self._build_post_compact_injections(session_id, tail_messages):
            new_messages.append(ctx)
        # Append preserved tail messages for seamless continuation
        for msg in tail_messages:
            new_messages.append(msg)

        await self.sessions.add_message(new_session.id, "user", handoff, kind="chat")
        for msg in tail_messages:
            role = msg.get("role", "user")
            content = extract_message_content(msg.get("content", ""))
            kind = "tool" if role == "tool" else "chat"
            meta: dict[str, Any] = {}
            if role == "tool":
                meta["tool_call_id"] = msg.get("tool_call_id", "")
            if role == "assistant":
                tcs = msg.get("tool_calls")
                if tcs:
                    meta["tool_calls"] = tcs
                rc = msg.get("reasoning_content")
                if rc:
                    meta["reasoning_content"] = rc
            await self.sessions.add_message(new_session.id, role, content, kind=kind, metadata=meta)

        if parent_work_dir:
            self._write_compression_state(parent_work_dir, new_session.compression_version, summary_xml, parent_session_id=session_id)

        self._session_contexts[new_session.id] = {"messages": new_messages, "system_prompt": system_prompt}
        self._session_contexts.pop(session_id, None)
        self._session_locks.pop(session_id, None)
        return new_session.id, summary_xml

    def _write_compression_state(
        self,
        work_dir: str,
        compression_version: int,
        summary_xml: str,
        *,
        parent_session_id: str,
    ) -> None:
        """Persist the latest compression handoff for future compact summaries."""
        state_path = Path(work_dir) / "compression_state.md"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state_path.write_text(
            "\n".join(
                [
                    "---",
                    f"compression_version: {compression_version}",
                    f"parent_session_id: {parent_session_id}",
                    f"updated_at: {updated_at}",
                    "---",
                    summary_xml.strip(),
                    "",
                ]
            ),
            encoding="utf-8",
        )

    async def _build_post_compact_injections(
        self, parent_session_id: str, tail_messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Scan tail messages for recently written files, inject as context blocks.

        After compaction the model only has the summary + tail — it should not
        need to re-read files it just wrote.  Inject their content as brief
        snapshots so the model can continue without tool calls.
        """
        from pathlib import Path as _Path

        injections: list[dict[str, Any]] = []
        session = await self.sessions.get(parent_session_id)
        if session is None:
            return injections

        # Collect active skills from tail assistant tool_calls
        active_skills: set[str] = set()
        for msg in reversed(tail_messages):
            if msg.get("role") == "assistant":
                tcs = msg.get("tool_calls") or []
                for tc in tcs:
                    if tc.get("name") == "skill_use":
                        args = tc.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                import json
                                args = json.loads(args)
                            except Exception:
                                pass
                        name = args.get("name") if isinstance(args, dict) else ""
                        if name and name not in active_skills:
                            active_skills.add(name)

        # Scan recent file_writes from tail (not just tool messages)
        recent_files: list[str] = []
        for msg in reversed(tail_messages):
            if msg.get("role") == "assistant":
                tcs = msg.get("tool_calls") or []
                for tc in tcs:
                    name = tc.get("name", "")
                    if name in ("file_write", "file_append"):
                        args = tc.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                import json
                                args = json.loads(args)
                            except Exception:
                                continue
                        path = str(args.get("path", "") or "").strip()
                        if path and path not in recent_files:
                            recent_files.append(path)
            if len(recent_files) >= 3:
                break

        if not recent_files and not active_skills:
            return injections

        work_dir = _Path(session.work_dir) if session.work_dir else None
        parts: list[str] = ["<recent_context>"]
        has_content = False

        for file_path in recent_files[:3]:
            if work_dir:
                full = work_dir / file_path
                try:
                    text = full.read_text("utf-8")
                    if len(text) > 3000:
                        text = text[:2000] + "\n...[truncated]...\n" + text[-1000:]
                    parts.append(f"\n### {file_path}")
                    parts.append(f"<content>\n{text}\n</content>")
                    has_content = True
                except Exception:
                    pass

        if active_skills:
            parts.append(f"\n活跃技能: {', '.join(sorted(active_skills))}")
            has_content = True

        if has_content:
            parts.append("\n注意：以上是压缩前的快照，可能已过时。使用前用 file_read 确认。")
            parts.append("</recent_context>")
            injections.append({"role": "user", "content": "\n".join(parts)})

        return injections

    async def _inject_turn_attachments(
        self, session_id: str, session: "Session", messages: list[dict[str, Any]],
        iteration: int, consecutive_tool_rounds: int, user_message: str,
        system_prompt: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Consolidated Phase 1: upload detection, interventions, pending messages,
        todo nudge, and proactive compression.  Modifies *messages* in-place.

        The caller checks for ``session.compressed`` events and re-initialises
        session context when compaction switches to a new session.
        """
        # -- 1. Mid-run upload detection ----------------------------------------
        new_upload_files = self._check_uploads_changed(session.work_dir, session_id)
        if new_upload_files:
            parsed = []
            for file_path in new_upload_files:
                source_path = f"uploads/{file_path.name}"
                try:
                    content = await self._parse_upload_file(file_path, source_path)
                    output_path = f"drafts/derived/{file_path.stem}__{file_path.suffix.lstrip('.').lower()}.md"
                    await self.workspace_memory.upsert_artifact(
                        session.id, path=output_path, content=content,
                        artifact_type="derived_upload", title=file_path.name,
                        summary=f"Auto-parsed from {source_path}",
                        metadata={"source_path": source_path, "source_paths": [source_path],
                                    "generated_by": "auto_parse",
                                    "lineage": {"source_paths": [source_path], "generated_by": "auto_parse"}},
                    )
                    parsed.append(file_path.name)
                except Exception as e:
                    logger.warning("Mid-run auto-parse failed for %s: %s", file_path.name, e)
            if parsed:
                names = ", ".join(parsed)
                reminder = f"<system-reminder>[工作区更新：检测到新文件已解析 {names}]</system-reminder>"
                messages.append({"role": "user", "content": reminder})
                await self.sessions.add_message(session_id, "user", reminder, kind="system")
                yield {"type": "activity", "phase": "uploads.parsed", "detail": f"检测到 {len(parsed)} 个新文件并已解析: {names}"}

        # -- 2. Pending interventions -------------------------------------------
        interventions = await self.sessions.consume_pending_interventions(session_id)
        for intervention in interventions:
            content = str(intervention.get("content") or "").strip()
            if not content:
                continue
            intervention_message = f"<manual_intervention>\n{content}\n</manual_intervention>"
            messages.append({"role": "user", "content": intervention_message})
            await self.sessions.add_message(
                session_id, "user", intervention_message, kind="system",
                metadata={"intervention_id": intervention.get("id")},
            )
            yield {
                "type": "intervention", "content": content,
                "payload": {"id": intervention.get("id"), "status": intervention.get("status")},
            }
            if (intervention.get("metadata") or {}).get("type") == "question":
                yield {
                    "type": "question", "content": content,
                    "payload": {"id": intervention.get("id")},
                }
            yield {
                "type": "activity", "phase": "intervention.applied",
                "detail": f"已应用人工修正: {content[:120]}",
                "payload": {"id": intervention.get("id"), "iteration": iteration},
            }

        # -- 3. Pending user messages -------------------------------------------
        pending = self._pending_messages.pop(session_id, [])
        for msg in pending:
            messages.append({"role": "user", "content": msg})
            yield {"type": "activity", "phase": "message.injected", "detail": f"用户注入消息: {msg[:100]}"}

        # -- 4. Todo nudge (after 4+ rounds without todowrite) ------------------
        if consecutive_tool_rounds > 4:
            refreshed = await self.sessions.get(session_id)
            if refreshed and refreshed.todo_list:
                active = [t for t in refreshed.todo_list if t.get("status") in ("pending", "in_progress", "blocked")]
                if active:
                    nudge = self._format_todo_nudge(active)
                    messages.append({"role": "user", "content": nudge})
                    yield {"type": "activity", "phase": "todo.nudged", "detail": f"注入 {len(active)} 个活跃待办提醒"}

        # -- 5. Proactive compression -------------------------------------------
        if estimate_messages_tokens(messages) > self._CONTEXT_TOKEN_THRESHOLD:
            async for evt in self._compress_session_context_flow(session_id, messages, system_prompt, user_message):
                yield evt

    @staticmethod
    def _serialize_for_summary(turns: list[dict[str, Any]]) -> str:
        parts = []
        for msg in turns:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 15000:
                content = content[:10000] + "\n...[truncated]...\n" + content[-5000:]
            parts.append(f"[{role.upper()}]: {content}")
        return "\n\n".join(parts)

    async def _generate_compression_summary(self, content: str, *, previous_summary: str = "") -> str | None:
        from ..prompts import compression_prompt
        prompt = compression_prompt(content, previous_summary)
        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model, messages=[{"role": "user", "content": prompt}],
                    temperature=0.3, max_tokens=4000,
                ),
                timeout=self.request_timeout_seconds,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            import logging
            logging.warning("Compression summary generation failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Tool result resolution
    # ------------------------------------------------------------------

    async def _resolve_tool_content_for_messages(self, *, tool_name: str, result: ToolResult, user_message: str, session_id: str = "") -> str:
        from .helpers import resolve_tool_content_for_messages
        agent_context = ""
        if session_id:
            session = await self.sessions.get(session_id)
            if session:
                agent_context = session.name or ""
        return await resolve_tool_content_for_messages(
            tool_name=tool_name, result=result, user_message=user_message,
            result_filter=self.result_filter, filterable=tool_name in self._filter_tools,
            agent_context=agent_context,
        )

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
    def _append_context_file_update_reminder(
        tool_content: str,
        tool_name: str,
        arguments: Any,
        result: ToolResult,
    ) -> str:
        if not result.success:
            return tool_content
        if tool_name not in {"file_write", "file_append"}:
            return tool_content
        args = arguments if isinstance(arguments, dict) else {}
        path = str(args.get("path", "")).strip().replace("\\", "/").lstrip("/")
        if path.lower() not in {"memory.md", "agent.md", "soul.md"}:
            return tool_content
        content = str(args.get("content", ""))
        return (
            tool_content
            + "\n\n<context_update_reminder>"
            + f"\nContext control file updated: {path}"
            + "\nThe current system prompt is intentionally not regenerated in this run, to preserve KV-cache prefix stability."
            + "\nUse this appended update as the authoritative in-turn context delta:"
            + f"\n{content}"
            + "\n</context_update_reminder>"
        )

    # ------------------------------------------------------------------
    # Tool result archiving
    # ------------------------------------------------------------------

    async def _archive_external_tool_result(
        self, session_id: str, tool_name: str, arguments: dict[str, Any], result: ToolResult,
    ) -> str | None:
        data = result.data or {}
        now = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if tool_name == "web_search":
            query = str(arguments.get("query", "")).strip()
            results = data.get("results", []) or []
            if not results:
                return None
            content = self._render_web_search_archive(query, results)
            path = f"raw_search/web_search/{now}_{slug(query or tool_name)}.md"
            metadata = {"query": query, "results": results, "generated_by": "tool_archive",
                        "lineage": {"generated_by": "tool_archive", "source_urls": [item.get("url") for item in results if item.get("url")]}}
        elif tool_name == "web_read":
            url = str(arguments.get("url", "")).strip()
            content = self._render_web_read_archive(url, data)
            path = f"raw_search/web_read/{now}_{slug(url or tool_name)}.md"
            metadata = {"url": url, "parser": data.get("parser"), "generated_by": "tool_archive",
                        "lineage": {"generated_by": "tool_archive", "source_urls": [url] if url else []}}
        elif tool_name == "law_retrieve":
            query = str(arguments.get("query", "")).strip()
            results = data.get("results", []) or []
            if not results:
                return None
            content = self._render_law_archive(query, results)
            path = f"raw_search/law/{now}_{slug(query or tool_name)}.md"
            metadata = {"query": query, "results": results, "generated_by": "tool_archive",
                        "lineage": {"generated_by": "tool_archive",
                                    "source_paths": [f"{item.get('laws_name', '')} {item.get('article_tag', '')}".strip() for item in results if item.get("laws_name") or item.get("article_tag")]}}
        elif tool_name == "case_retrieve":
            query = str(arguments.get("query", "")).strip()
            results = data.get("results", []) or []
            if not results:
                return None
            content = self._render_case_archive(query, results)
            path = f"raw_search/case/{now}_{slug(query or tool_name)}.md"
            metadata = {"query": query, "results": results, "generated_by": "tool_archive",
                        "lineage": {"generated_by": "tool_archive",
                                    "source_paths": [item.get("case_no") or item.get("title") for item in results if item.get("case_no") or item.get("title")]}}
        else:
            return None

        await self.workspace_memory.upsert_artifact(
            session_id, path=path, content=content, artifact_type="external_retrieval",
            title=Path(path).name, summary=f"Archived {tool_name} result", metadata=metadata,
        )
        return path

    async def _save_filtered_tool_result(self, tool_content: str, tool_name: str, session_id: str) -> None:
        """Save filtered/compressed tool result to raw_search/ for debug audit.

        Only fires when the result was filtered by ResultFilterAgent (marked
        with ``_filtered: true`` in the JSON payload).  Writes a JSON snapshot
        alongside the raw archive so the filter quality can be inspected.
        """
        if '"_filtered"' not in tool_content and "'_filtered'" not in tool_content:
            return
        session = await self.sessions.get(session_id)
        if session is None or not session.work_dir:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(session.work_dir) / "raw_search" / tool_name
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            (out_dir / f"{ts}_filtered.json").write_text(tool_content, encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _render_web_search_archive(query: str, results: list[dict[str, Any]]) -> str:
        lines = [f"# Web Search Archive", "", f"- Query: {query}", ""]
        for index, item in enumerate(results, start=1):
            lines.extend([f"## Result {index}", f"- Title: {item.get('title', '')}", f"- URL: {item.get('url', '')}", "", str(item.get("content", "")).strip(), ""])
        return "\n".join(lines).strip()

    @staticmethod
    def _render_web_read_archive(url: str, data: dict[str, Any]) -> str:
        lines = ["# Web Read Archive", "", f"- URL: {url}", f"- Parser: {data.get('parser', 'jina-reader')}"]
        if data.get("markdown_url"):
            lines.append(f"- Markdown URL: {data.get('markdown_url')}")
        return "\n".join(lines) + "\n\n---\n\n" + str(data.get("content", "")).strip()

    @staticmethod
    def _render_law_archive(query: str, results: list[dict[str, Any]]) -> str:
        lines = [f"# Law Retrieval Archive", "", f"- Query: {query}", ""]
        for index, item in enumerate(results, start=1):
            lines.extend([f"## Result {index}", f"- Title: {item.get('title', '')}", f"- Law: {item.get('laws_name', '')}", f"- Article: {item.get('article_tag', '')}", "", str(item.get("content", "")).strip(), ""])
        return "\n".join(lines).strip()

    @staticmethod
    def _render_case_archive(query: str, results: list[dict[str, Any]]) -> str:
        lines = [f"# Case Retrieval Archive", "", f"- Query: {query}", ""]
        for index, item in enumerate(results, start=1):
            lines.extend([f"## Result {index}", f"- Title: {item.get('title', '')}", f"- Case No: {item.get('case_no', '')}", f"- Court: {item.get('court', '')}", f"- Source: {item.get('source', '')}", "", str(item.get("content", "")).strip(), ""])
        return "\n".join(lines).strip()

    @staticmethod
    def _summarize_tool_call(tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name in {"web_search", "workspace_search"}:
            return f"query={arguments.get('query', '')}"
        if tool_name in {"file_write", "file_append"}:
            return f"path={arguments.get('path', '')} | 内容 ~{len(str(arguments.get('content', ''))):,} 字符"
        if tool_name in {"web_read", "file_read", "file_delete"}:
            return f"path={arguments.get('path', '')}" if tool_name != "web_read" else f"url={arguments.get('url', '')}"
        if tool_name == "skill_use":
            return f"name={arguments.get('name', '')}"
        if tool_name == "skill_propose":
            return f"name={arguments.get('name', '')}"
        return json.dumps(arguments, ensure_ascii=False)[:240]

    @staticmethod
    def _summarize_tool_result(tool_name: str, result: dict[str, Any]) -> str:
        if not result.get("success"):
            return f"失败: {result.get('error', 'Unknown error')}"
        data = result.get("data") or {}
        if tool_name == "web_search":
            results = data.get("results", []) or []
            summary = "\n".join([f"{i}. [{item.get('title', '')}]({item.get('url', '')})" for i, item in enumerate(results[:5], 1)] + ([f"... 共 {len(results)} 条结果"] if len(results) > 5 else [])) if results else "无结果"
            return AgentLoop._append_archive_path_to_summary(summary, data)
        if tool_name == "web_read":
            return AgentLoop._append_archive_path_to_summary(
                f"URL: [{data.get('url', '')}]({data.get('url', '')}) | Parser: {data.get('parser', 'jina-reader')} | 内容长度: {len(str(data.get('content', '')))} 字符",
                data,
            )
        if tool_name in {"law_retrieve", "case_retrieve"}:
            results = data.get("results", []) or []
            if not results:
                return AgentLoop._append_archive_path_to_summary("无结果", data)
            lines = []
            for i, item in enumerate(results[:5], 1):
                short = (item.get("title", "")[:50])
                if tool_name == "law_retrieve" and item.get("timeliness_name"):
                    short += f" [{item.get('timeliness_name')}]"
                if tool_name == "case_retrieve" and item.get("case_no"):
                    short += f" ({item.get('case_no')})"
                lines.append(f"{i}. {short}")
            if len(results) > 5:
                lines.append(f"... 共 {len(results)} 条结果")
            return AgentLoop._append_archive_path_to_summary("\n".join(lines), data)
        if tool_name == "file_read":
            return f"Path: `{data.get('path', '')}` | 内容长度: {len(str(data.get('content', '')))} 字符"
        if tool_name in {"file_write", "file_append"}:
            return f"Path: `{data.get('path', '')}` | 写入长度: {data.get('size', 0)} 字符"
        if tool_name == "file_delete":
            return f"已删除: `{data.get('path', '')}`"
        if tool_name == "bash":
            return f"Command: `{data.get('command', '')}` | stdout: {len(str(data.get('stdout', '')))} 字符"
        if tool_name == "workspace_search":
            return f"检索到 {len(data.get('results', []) or data.get('hits', []))} 条结果"
        if tool_name == "skill_use":
            return f"Skill: {data.get('name')} | 内容长度: {len(str(data.get('content', '')))} 字符"
        if tool_name == "skill_propose":
            return f"提案: {data.get('name')} → {data.get('path', '')}"
        if tool_name == "spawn":
            return f"子任务: {data.get('sub_task_id')}, 状态: {data.get('status')} | {(data.get('result') or '')[:160]}"
        if isinstance(data, dict):
            return f"Keys: {', '.join(data.keys())}"
        return f"结果类型: {type(data).__name__}, 长度: {len(str(data))}"

    @staticmethod
    def _append_archive_path_to_summary(summary: str, data: dict[str, Any]) -> str:
        archived_path = data.get("archived_path")
        if not archived_path:
            return summary
        return f"{summary}\n已入库: `{archived_path}`"

    # ------------------------------------------------------------------
    # Sub-agent spawn
    # ------------------------------------------------------------------

    async def spawn_sub_agent(self, task_description: str, tools: list[str] | None = None, parent_session_id: str | None = None) -> tuple[str, str]:
        from .sub_agent import SubAgent
        self._sub_agent_counter += 1
        sub_id = f"sub_{self._sub_agent_counter}"
        interrupt_event = self._interrupt_events.get(parent_session_id) if parent_session_id else None
        session = await self.sessions.get(parent_session_id) if parent_session_id else None
        work_dir = session.work_dir if session else ""
        child_interrupt = asyncio.Event()
        self._interrupt_events[sub_id] = child_interrupt
        sub_agent = SubAgent(
            api_key=self.api_key, base_url=self.base_url, model=self.model,
            request_timeout_seconds=self.request_timeout_seconds, parent_session_id=parent_session_id,
            allowed_tools=tools, tool_registry=self.tools, session_manager=self.sessions,
            workspace_memory=self.workspace_memory, retriever=self.retriever, mineru_client=self.mineru_client,
            sub_agent_id=sub_id, interrupt_check=interrupt_event,
            child_interrupt=child_interrupt,
            sub_agent_work_dir=work_dir, reasoning_effort=self._reasoning_effort,
            pending_messages=self._pending_messages,
            filter_tools=self._filter_tools,
        )
        timeout = min(sub_agent.max_iterations * self.request_timeout_seconds * 2, 600)
        result = await asyncio.wait_for(sub_agent.run(task_description), timeout=timeout)
        # Register session_id alias so send_message/task_stop can find by session ID
        if sub_agent._session_id:
            self._interrupt_events[sub_agent._session_id] = child_interrupt
        return sub_agent._session_id, result
