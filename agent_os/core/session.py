"""
Session management for long-running research workspaces.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo
import re

from ..storage import SQLiteStore
from ..config import _y

_ws = _y("workspace", {}) or {}
_DEFAULT_PROFILE = {
    "folders": _ws.get("folders", ["uploads", "research", "drafts", "raw_search", "logs"]),
    "files": _ws.get("files", {}),
}


def _safe_workspace_path(value: str) -> str:
    """Validate and normalize a relative path within the session workspace.
    
    Absolute paths and ``..`` traversal are rejected — this is the sandbox perimeter
    that prevents tool calls from escaping the session's work_dir.
    """
    raw = str(value).strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {".."} for part in path.parts):
        raise ValueError(f"Unsafe workspace path: {value}")
    return path.as_posix()


def _slugify(name: str, max_len: int = 30) -> str:
    safe = re.sub(r'[^\w\-_\.\u4e00-\u9fff]', '_', name)
    return safe[:max_len].rstrip('_.') or 'unnamed'


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _int_priority_to_str(priority: int) -> str:
    if priority <= 1:
        return "high"
    if priority >= 3:
        return "low"
    return "medium"


def _normalize_priority(priority: Any) -> str:
    if isinstance(priority, int):
        return _int_priority_to_str(priority)
    if isinstance(priority, str):
        p = priority.lower().strip()
        if p in ("high", "medium", "low"):
            return p
    return "medium"


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""
    description: str = ""
    status: str = "active"
    stage: str = "intake"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    work_dir: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    todo_list: list[dict[str, Any]] = field(default_factory=list)
    parent_session_id: str | None = None
    compression_version: int = 1

    def __post_init__(self) -> None:
        if self.work_dir:
            Path(self.work_dir).mkdir(parents=True, exist_ok=True)

    def add_todo(self, content: str, priority: int = 3) -> str:
        todo_id = str(uuid.uuid4())[:8]
        self.todo_list.append({
            "id": todo_id, "content": content, "status": "pending",
            "priority": _int_priority_to_str(priority),
            "created_at": datetime.now().isoformat(), "completed": False,
        })
        self.updated_at = datetime.now().isoformat()
        return todo_id

    def complete_todo(self, todo_id: str) -> dict[str, Any] | None:
        for todo in self.todo_list:
            if todo.get("id") == todo_id and todo.get("status") != "completed":
                todo["status"] = "completed"
                todo["completed"] = True
                self.updated_at = datetime.now().isoformat()
                return todo
        return None

    def replace_todos(self, todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        validated = []
        for item in todos:
            if not isinstance(item, dict) or "content" not in item:
                continue
            status = str(item.get("status", "pending")).lower().strip()
            if status not in ("pending", "in_progress", "completed", "blocked", "cancelled"):
                status = "pending"
            validated.append({
                "id": item.get("id") or str(uuid.uuid4())[:8],
                "content": str(item["content"]),
                "activeForm": str(item.get("activeForm", "")),
                "status": status,
                "priority": _normalize_priority(item.get("priority", "medium")),
                "category": str(item.get("category", "")),
                "created_at": item.get("created_at") or datetime.now().isoformat(),
            })
        self.todo_list = validated
        self.updated_at = datetime.now().isoformat()
        return self.todo_list

    def to_store_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.id, "name": self.name,
            "description": self.description, "status": self.status,
            "stage": self.stage, "work_dir": self.work_dir,
            "todo_list": self.todo_list, "metadata": self.metadata,
            "parent_session_id": self.parent_session_id,
            "compression_version": self.compression_version,
        }


def normalize_utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone information")
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def format_local_timestamp(value: str | None) -> str | None:
    if not value:
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    local_dt = parsed.astimezone(LOCAL_TIMEZONE)
    return local_dt.strftime("%Y-%m-%d %H:%M:%S 北京时间")


class SessionManager:
    def __init__(self, *, data_dir: str = "./data", store: SQLiteStore | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.data_dir / "agent_os.db")

    async def create(
        self, name: str = "", description: str = "", stage: str = "intake",
        metadata: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        compression_version: int = 1,
        initial_files: dict[str, str] | None = None,
    ) -> Session:
        session = Session(name=name, description=description, stage=stage,
                          parent_session_id=parent_session_id, compression_version=compression_version)
        session.work_dir = str(self.sessions_dir / f"{session.id[:8]}_{_slugify(session.name or session.id)}")
        files, folders = self._resolve_workspace_template(initial_files=initial_files)
        Path(session.work_dir).mkdir(parents=True, exist_ok=True)
        session.metadata = dict(metadata or {})
        self.store.create_session(
            session_id=session.id, name=session.name, description=session.description,
            stage=session.stage, work_dir=session.work_dir, metadata=session.metadata,
            parent_session_id=parent_session_id, compression_version=compression_version,
        )
        self._ensure_default_workspace(session, files=files, folders=folders)
        return session

    async def fork_session(
        self, parent_session_id: str, *, summary_content: str = "", summary_type: str = "compression",
    ) -> Session | None:
        parent = await self.get(parent_session_id)
        if parent is None:
            return None
        parent.status = "compressed"
        await self.update(parent)
        new_session = await self.create(
            name=parent.name, description=parent.description, stage=parent.stage,
            metadata=parent.metadata,
            parent_session_id=parent_session_id, compression_version=parent.compression_version + 1,
        )
        # Inherit parent's work_dir to keep file access seamless
        new_session.work_dir = parent.work_dir
        await self.update(new_session)
        if summary_content:
            await self.add_message(new_session.id, role="user",
                                   content=f"[CONTEXT COMPACTION]: {summary_content}", kind="system")
        return new_session

    async def get(self, session_id: str) -> Session | None:
        row = self.store.get_session_row(session_id)
        if row is None:
            return None
        data = self.store.row_to_json(row) or {}
        return Session(
            id=data["id"], name=data.get("name", ""), description=data.get("description", ""),
            status=data.get("status", "active"), stage=data.get("stage", "intake"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            work_dir=data.get("work_dir", ""), metadata=data.get("metadata", {}) or {},
            todo_list=data.get("todo", []) or [],
            parent_session_id=data.get("parent_session_id"),
            compression_version=data.get("compression_version", 1),
        )

    async def list(self) -> list[dict[str, str]]:
        rows = self.store.list_sessions()
        return [{
            "id": (data := self.store.row_to_json(row) or {})["id"],
            "name": data.get("name", ""), "status": data.get("status", ""),
            "stage": data.get("stage", ""), "created_at": data.get("created_at", ""),
            "created_at_display": format_local_timestamp(data.get("created_at")),
            "updated_at": data.get("updated_at", ""),
            "updated_at_display": format_local_timestamp(data.get("updated_at")),
        } for row in rows]

    async def list_active_sessions(self) -> list[Session]:
        rows = self.store.list_sessions_by_status("active")
        return [Session(
            id=(data := self.store.row_to_json(row) or {})["id"],
            name=data.get("name", ""), description=data.get("description", ""),
            status=data.get("status", "active"), stage=data.get("stage", "intake"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            work_dir=data.get("work_dir", ""), metadata=data.get("metadata", {}) or {},
            todo_list=data.get("todo", []) or [],
            parent_session_id=data.get("parent_session_id"),
            compression_version=data.get("compression_version", 1),
        ) for row in rows]

    async def update(self, session: Session) -> None:
        self.store.update_session(**session.to_store_payload())

    async def update_stage(self, session_id: str, *, stage: str,
                           metadata_patch: dict[str, Any] | None = None) -> Session | None:
        session = await self.get(session_id)
        if session is None:
            return None
        session.stage = stage
        if metadata_patch:
            session.metadata.update(metadata_patch)
        await self.update(session)
        return session

    async def add_todo(self, session_id: str, *, content: str, priority: int = 3) -> dict[str, Any] | None:
        session = await self.get(session_id)
        if session is None:
            return None
        todo_id = session.add_todo(content, priority)
        await self.update(session)
        for todo in session.todo_list:
            if todo.get("id") == todo_id:
                return todo
        return None

    async def complete_todo(self, session_id: str, todo_id: str) -> dict[str, Any] | None:
        session = await self.get(session_id)
        if session is None:
            return None
        result = session.complete_todo(todo_id)
        if result:
            await self.update(session)
        return result

    async def close(self, session_id: str) -> None:
        session = await self.get(session_id)
        if not session:
            return
        session.status = "closed"
        await self.update(session)

    async def delete(self, session_id: str) -> bool:
        session = await self.get(session_id)
        if not session:
            return False
        self.store.delete_session(session_id)
        # Only remove work_dir if no other session uses it
        work_dir = Path(session.work_dir)
        if work_dir.exists():
            all_sessions = self.store.list_sessions()
            shared = any(
                (row_data := self.store.row_to_json(row) or {}).get("work_dir") == str(work_dir)
                and row_data.get("id") != session_id
                for row in all_sessions
            )
            if not shared:
                import shutil
                shutil.rmtree(work_dir)
        return True

    # -- Messages --

    async def add_message(self, session_id: str, role: str, content: str, *,
                          kind: str = "chat", metadata: dict[str, Any] | None = None) -> int:
        return self.store.add_message(session_id=session_id, role=role, content=content, kind=kind, metadata=metadata)

    async def delete_message(self, message_id: int) -> None:
        self.store.delete_message(message_id)

    async def get_messages(self, session_id: str, *, limit: int | None = None,
                           kinds: list[str] | None = None) -> list[dict[str, Any]]:
        rows = self.store.list_messages(session_id, limit=limit, kinds=kinds)
        return [{
            "id": (data := self.store.row_to_json(row) or {}).get("id"),
            "role": data.get("role"), "content": data.get("content"),
            "kind": data.get("kind"), "timestamp": data.get("created_at"),
            "timestamp_display": format_local_timestamp(data.get("created_at")),
            "metadata": data.get("metadata", {}) or {},
        } for row in rows]

    async def get_chat_messages(self, session_id: str) -> list[dict[str, Any]]:
        return await self.get_messages(session_id, kinds=["chat"])

    # -- Reminders --

    def create_reminder(self, *, session_id: str, reminder_type: str, title: str, message: str,
                        fire_at: str, priority: int = 3, metadata: dict[str, Any] | None = None) -> str:
        return self.store.create_reminder(
            session_id=session_id, reminder_type=reminder_type, title=title,
            message=message, fire_at=normalize_utc_timestamp(fire_at),
            priority=priority, metadata=metadata,
        )

    def mark_reminder_fired(self, reminder_id: str, fired_at: str) -> None:
        self.store.mark_reminder_fired(reminder_id, fired_at)

    async def list_reminders(self, *, session_id: str | None = None,
                             status: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list_reminders(session_id=session_id, status=status)
        return [{
            "id": (data := self.store.row_to_json(row) or {})["id"],
            "session_id": data.get("session_id"), "type": data.get("reminder_type"),
            "title": data.get("title"), "message": data.get("message"),
            "fire_at": data.get("fire_at"),
            "fire_at_display": format_local_timestamp(data.get("fire_at")),
            "priority": data.get("priority"), "status": data.get("status"),
            "metadata": data.get("metadata", {}) or {},
            "created_at": data.get("created_at"),
            "created_at_display": format_local_timestamp(data.get("created_at")),
            "fired_at": data.get("fired_at"),
            "fired_at_display": format_local_timestamp(data.get("fired_at")),
        } for row in rows]

    # -- Artifacts --

    async def list_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        session = await self.get(session_id)
        if session is None:
            return []
        rows = self.store.list_artifacts_by_work_dir(session.work_dir)
        seen: set[str] = set()
        artifacts: list[dict[str, Any]] = []
        for row in rows:
            data = self.store.row_to_json(row) or {}
            path = str(data.get("path", ""))
            if path in seen:
                continue
            seen.add(path)
            artifacts.append(data)
        return artifacts

    # -- Interventions --

    async def add_intervention(self, session_id: str, *, content: str,
                               metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        intervention_id = self.store.create_intervention(session_id=session_id, content=content, metadata=metadata)
        rows = self.store.list_interventions(session_id, limit=1)
        data = self.store.row_to_json(rows[0]) or {}
        data["created_at_display"] = format_local_timestamp(data.get("created_at"))
        data["applied_at_display"] = format_local_timestamp(data.get("applied_at"))
        return data

    async def list_interventions(self, session_id: str, *, status: str | None = None,
                                 limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store.list_interventions(session_id, status=status, limit=limit)
        return [{**data, "created_at_display": format_local_timestamp(data.get("created_at")),
                 "applied_at_display": format_local_timestamp(data.get("applied_at"))}
                for row in rows if (data := self.store.row_to_json(row) or {})]

    async def consume_pending_interventions(self, session_id: str) -> list[dict[str, Any]]:
        pending = await self.list_interventions(session_id, status="pending", limit=20)
        if not pending:
            return []
        self.store.mark_interventions_applied(session_id, [item["id"] for item in pending if item.get("id")])
        for item in pending:
            item["status"] = "applied"
        return pending

    # -- Workspace --

    @staticmethod
    def _resolve_workspace_template(
        *,
        initial_files: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], list[str]]:
        cfg = _DEFAULT_PROFILE
        files = {
            _safe_workspace_path(relative_path): str(content)
            for relative_path, content in (cfg.get("files") or _DEFAULT_PROFILE["files"]).items()
        }
        for relative_path, content in (initial_files or {}).items():
            files[_safe_workspace_path(relative_path)] = str(content)
        folders = [
            _safe_workspace_path(folder)
            for folder in (cfg.get("folders") or _DEFAULT_PROFILE["folders"])
        ]
        return files, folders

    def _ensure_default_workspace(self, session: Session, *, files: dict[str, str], folders: list[str]) -> None:
        work_dir = Path(session.work_dir)
        for relative_path, content in files.items():
            resolved = content.replace("{{session_name}}", session.name or session.id).replace("{{stage}}", session.stage)
            full_path = work_dir / relative_path
            if not full_path.exists():
                try:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(resolved, encoding="utf-8")
                except (OSError, IOError) as exc:
                    import logging
                    logging.warning("Failed to create workspace file %s: %s", relative_path, exc)
        for folder in folders:
            (work_dir / folder).mkdir(parents=True, exist_ok=True)
        self._create_workspace_snapshot_files(work_dir, session.name or session.id)

    @staticmethod
    def _create_workspace_snapshot_files(work_dir: Path, session_name: str) -> None:
        from pathlib import Path as _Path
        prompts_dir = _Path(__file__).resolve().parents[1] / "prompts"
        defaults = {
            "SOUL.md": prompts_dir / "SOUL.md",
            "AGENT.md": prompts_dir / "AGENT.md",
        }
        for name, template_path in defaults.items():
            target = work_dir / name
            if not target.exists() and template_path.exists():
                try:
                    target.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
                except (OSError, IOError):
                    pass
        memory_path = work_dir / "MEMORY.md"
        if not memory_path.exists():
            tree = [
                "# 研究记忆索引",
                "",
                "MEMORY.md 是索引，不是全文笔记。详细研究状态写入 `research/memory/*.md`，这里保留短 hook。",
                "",
                "## Question Model",
                "",
                "- 当前问题解释、硬约束、歧义词、最终输出字段。",
                "",
                "## Candidate Ledger",
                "",
                "- 候选、排除原因、获胜候选为什么胜出。",
                "",
                "## Evidence Ledger",
                "",
                "- 关键 claim、来源、支持的约束、可信度。",
                "",
                "## Update Conditions",
                "",
                "- 什么新证据会改变当前结论。",
                "",
                "## 用户与项目偏好",
                "",
                "- 长期偏好、工作方法反馈、项目决策。",
                "",
            ]
            try:
                memory_path.write_text("\n".join(tree), encoding="utf-8")
            except (OSError, IOError):
                pass
