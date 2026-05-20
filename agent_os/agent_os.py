"""
AgentOS application assembly.
"""

from __future__ import annotations

import os
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from .config import Settings
from .core.event_bus import get_event_bus
from .core.session import Session, SessionManager, normalize_utc_timestamp
from .ingest import MineruClient
from .kernel.agent_loop import AgentLoop
from .memory import ContextCompiler, SessionRetriever, WorkspaceMemory
from .memory.embedding import EmbeddingClient
from .scheduler import InterruptScheduler, InterruptType
from .skills import SkillLoader
from .storage import SQLiteStore
from .tools import register_all as _register_tools
from .tools.registry import ToolRegistry, set_tool_deps


def check_api_keys() -> tuple[bool, str]:
    settings = Settings.from_env()
    provider = settings.provider
    if provider == "deepseek":
        if os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"):
            return True, ""
        return False, "缺少 DEEPSEEK_API_KEY（DeepSeek 原生模式），也未提供兼容的 OPENAI_API_KEY"
    if provider == "dashscope":
        if os.getenv("OPENAI_API_KEY"):
            return True, ""
        return False, "缺少 OPENAI_API_KEY（百炼/DashScope 兼容模式使用）"
    if settings.api_key:
        return True, ""
    return False, f"缺少 {provider} provider 的 API key"


class AgentOS:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        data_dir: str = "./data",
        feishu_webhook: str | None = None,
        feishu_secret: str | None = None,
        enabled_tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
    ):
        self.settings = (
            Settings.from_env(data_dir=data_dir, model=model)
            if model is not None
            else Settings.from_env(data_dir=data_dir)
        )
        if api_key:
            self.settings.api_key = api_key
        if base_url:
            self.settings.base_url = base_url
        if feishu_webhook is not None:
            self.settings.feishu_webhook = feishu_webhook
        if feishu_secret is not None:
            self.settings.feishu_secret = feishu_secret
        if disabled_tools is not None:
            self.settings.disabled_tools = disabled_tools

        logging.basicConfig(
            level=getattr(logging, self.settings.log_level.upper(), logging.WARNING),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
            force=True,
        )

        self.data_dir = Path(self.settings.data_dir)
        self.store = SQLiteStore(self.settings.database_path)
        self.sessions = SessionManager(data_dir=str(self.data_dir), store=self.store)
        self.event_bus = get_event_bus()

        self.retriever = SessionRetriever(self.store, embedding_client=EmbeddingClient())
        self.workspace_memory = WorkspaceMemory(
            session_manager=self.sessions,
            store=self.store,
            embedding_client=EmbeddingClient(),
        )

        self.skill_loader = SkillLoader(
            extra_skill_dirs=(),
        )
        self.skills = self.skill_loader.discover_all()

        self.context_compiler = ContextCompiler(
            retriever=self.retriever,
            skill_loader=self.skill_loader,
            max_messages=self.settings.max_context_messages,
            max_items=self.settings.max_context_items,
        )
        self.mineru_client = MineruClient(
            base_url=self.settings.mineru_base_url,
            premium_base_url=self.settings.mineru_v4_base_url,
            api_token=self.settings.mineru_api_token,
            premium_model_version=self.settings.mineru_premium_model_version,
            timeout_seconds=self.settings.mineru_timeout_seconds,
            poll_interval_seconds=self.settings.mineru_poll_interval_seconds,
            poll_timeout_seconds=self.settings.mineru_poll_timeout_seconds,
        )

        self.tool_registry = ToolRegistry()
        _register_tools(self.tool_registry)
        set_tool_deps(
            session_manager=self.sessions,
            workspace_memory=self.workspace_memory,
            retriever=self.retriever,
            mineru_client=self.mineru_client,
            skill_loader=self.skill_loader,
        )

        self.scheduler = InterruptScheduler(
            session_manager=self.sessions,
            feishu_webhook=self.settings.feishu_webhook,
            feishu_secret=self.settings.feishu_secret,
            event_bus=self.event_bus,
            check_interval=self.settings.scheduler_interval_seconds,
        )

        self.agent_loop = AgentLoop(
            settings=self.settings,
            session_manager=self.sessions,
            tool_registry=self.tool_registry,
            event_bus=self.event_bus,
            context_compiler=self.context_compiler,
            workspace_memory=self.workspace_memory,
            retriever=self.retriever,
            skill_loader=self.skill_loader,
            mineru_client=self.mineru_client,
        )
        set_tool_deps(agent_loop=self.agent_loop)

        # Apply explicit allow/deny lists before schemas are exposed to models.
        known = set(self.tool_registry.get_all_tool_names())
        keep = set(enabled_tools) if enabled_tools is not None else set(known)
        unknown_enabled = sorted(keep - known)
        if unknown_enabled:
            raise ValueError(
                f"Unknown tool(s) in enabled_tools: {unknown_enabled}. "
                f"Available: {sorted(known)}"
            )

        disabled = set(self.settings.disabled_tools or [])
        unknown_disabled = sorted(disabled - known)
        if unknown_disabled:
            raise ValueError(
                f"Unknown tool(s) in disabled_tools: {unknown_disabled}. "
                f"Available: {sorted(known)}"
            )
        keep -= disabled
        if keep != known:
            self.tool_registry.retain_only(keep)

    async def start(self) -> None:
        await self.scheduler.start()
        await self.agent_loop.warmup_session_cache()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.workspace_memory.drain_background_tasks()
        self.store.close()

    async def upload_file(self, session_id: str, filename: str, content: bytes) -> dict[str, Any]:
        session = await self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        uploads_dir = Path(session.work_dir) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        # Sanitize filename: reject absolute paths, .., and backslashes
        safe_name = filename.replace("\\", "/").split("/")[-1].lstrip("/")
        if not safe_name or safe_name.startswith(".."):
            raise ValueError(f"Invalid filename: {filename}")
        file_path = uploads_dir / safe_name
        file_path.write_bytes(content)
        stat = file_path.stat()
        return {
            "filename": safe_name,
            "path": f"uploads/{safe_name}",
            "size": stat.st_size,
            "session_id": session_id,
        }

    async def create_session(
        self,
        name: str = "",
        description: str = "",
        stage: str = "intake",
        initial_files: dict[str, str] | None = None,
    ) -> Session:
        session = await self.sessions.create(
            name=name,
            description=description,
            stage=stage,
            initial_files=initial_files,
        )
        await self.workspace_memory.upsert_artifact(
            session.id,
            path="workspace.md",
            content=(
                f"# {session.name or session.id}\n\n"
                f"## 当前状态\n\n{session.stage}\n\n"
                "## 目标\n\n"
            ),
            artifact_type="summary",
            title="Workspace Overview",
            summary="工作区总览",
        )
        return session

    async def get_session(self, session_id: str) -> Session | None:
        return await self.sessions.get(session_id)

    async def list_sessions(self) -> list[dict[str, str]]:
        return await self.sessions.list()

    def list_tool_schemas(self) -> list[dict[str, Any]]:
        return self.tool_registry.get_available_schemas()

    async def add_todo(
        self, session_id: str, *, content: str, priority: int = 3,
    ) -> dict[str, Any]:
        todo = await self.sessions.add_todo(session_id, content=content, priority=priority)
        if todo is None:
            raise ValueError(f"Session not found: {session_id}")
        return todo

    async def close_session(self, session_id: str) -> None:
        await self.sessions.close(session_id)
        self.agent_loop.cleanup_session(session_id)

    async def compress_session(self, session_id: str) -> str | None:
        """Compress session context, returns the new session_id."""
        return await self.agent_loop.compress_session(session_id)

    async def delete_session(self, session_id: str) -> bool:
        """删除 session 及其所有数据（包括数据库记录和文件）"""
        result = await self.sessions.delete(session_id)
        if result:
            self.agent_loop.cleanup_session(session_id)
        return result

    async def chat(
        self,
        session_id: str,
        message: str,
        *,
        context_mode: str = "default",
        request_timeout_seconds: int | None = None,
        max_iterations: int | None = None,
    ):
        if not self.agent_loop:
            yield {"type": "error", "error": "Agent loop not initialized"}
            return
        async for event in self.agent_loop.process(
            session_id,
            message,
            context_mode=context_mode,
            request_timeout_seconds=request_timeout_seconds,
            max_iterations=max_iterations,
        ):
            yield event

    def inject_message(self, session_id: str, message: str) -> None:
        """在运行中的 session 下一轮迭代前注入用户消息。不影响 KV cache。"""
        self.agent_loop.inject_message(session_id, message)

    def request_interrupt(self, session_id: str) -> None:
        """请求优雅中断当前 session 的 process()。"""
        self.agent_loop.request_interrupt(session_id)

    def add_interrupt(
        self,
        interrupt_type: str,
        title: str,
        message: str,
        session_id: str,
        fire_at: str,
        priority: int = 3,
    ) -> str:
        normalized_type = InterruptType(interrupt_type.lower())
        normalized_fire_at = normalize_utc_timestamp(fire_at)
        return self.scheduler.add_interrupt(
            interrupt_type=normalized_type,
            title=title,
            message=message,
            session_id=session_id,
            fire_at=datetime.fromisoformat(normalized_fire_at.replace("Z", "+00:00")),
            priority=priority,
        )

    async def list_interrupts(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return await self.scheduler.list_interrupts(session_id)

    async def list_messages(
        self,
        session_id: str,
        limit: int = 50,
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return await self.sessions.get_messages(session_id, limit=limit, kinds=kinds)

    async def list_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        return await self.sessions.list_artifacts(session_id)

    async def add_intervention(
        self,
        session_id: str,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.sessions.add_intervention(
            session_id,
            content=content,
            metadata=metadata,
        )

    async def list_interventions(
        self,
        session_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.sessions.list_interventions(
            session_id,
            status=status,
            limit=limit,
        )

    async def workspace_search(
        self,
        session_id: str,
        query: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        session = await self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        results = await self.retriever.search(session_id, query, limit=limit, work_dir=session.work_dir)
        return [
            {
                "source": item.source,
                "path": item.path,
                "content": item.content,
                "score": item.score,
                "artifact_type": item.artifact_type,
                "title": item.title,
                "summary": item.summary,
                "chunk_index": item.chunk_index,
                "metadata": item.metadata,
            }
            for item in results
        ]

    async def preview_context(self, session_id: str, message: str) -> dict[str, Any]:
        return await self.agent_loop.preview_context(session_id, message)

    async def read_artifact(self, session_id: str, path: str) -> dict[str, Any] | None:
        return await self.workspace_memory.read_artifact(session_id, path)

    async def get_artifact_lineage(self, session_id: str, path: str) -> dict[str, Any] | None:
        artifact = await self.read_artifact(session_id, path)
        if artifact is None:
            return None
        metadata = artifact.get("metadata") or {}
        return {
            "path": path,
            "artifact_type": artifact.get("artifact_type", ""),
            "title": artifact.get("title", ""),
            "lineage": metadata.get("lineage") or {},
            "metadata": metadata,
        }

    async def write_artifact(
        self,
        session_id: str,
        *,
        path: str,
        content: str,
        artifact_type: str = "note",
        title: str = "",
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.workspace_memory.upsert_artifact(
            session_id,
            path=path,
            content=content,
            artifact_type=artifact_type,
            title=title or Path(path).stem,
            summary=summary,
            metadata=metadata,
        )

    async def list_reminders(
        self,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.sessions.list_reminders(session_id=session_id, status=status)

    def create_reminder(
        self,
        *,
        session_id: str,
        reminder_type: str,
        title: str,
        message: str,
        fire_at: str,
        priority: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        normalized_fire_at = normalize_utc_timestamp(fire_at)
        return self.sessions.create_reminder(
            session_id=session_id,
            reminder_type=reminder_type,
            title=title,
            message=message,
            fire_at=normalized_fire_at,
            priority=priority,
            metadata=metadata,
        )

_global_os: AgentOS | None = None


def get_agent_os() -> AgentOS:
    global _global_os
    if _global_os is None:
        _global_os = AgentOS()
    return _global_os
