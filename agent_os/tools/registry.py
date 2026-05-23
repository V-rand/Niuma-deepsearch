"""
Tool registry modelled after Hermes.

Each tool file calls ``registry.register(name, toolset, schema, handler)``
at module-import time.  The handler is an async function that receives tool
arguments as ``**kwargs`` and returns a ``ToolResult``.

Session context (work_dir, session_id) is made available via contextvars
so handlers don't need special registration treatment.  Infrastructure
objects (retriever, workspace_memory, …) are module-level globals set
by AgentOS at startup, not per-session construction.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session context — set by AgentLoop/SubAgent before each tool dispatch
# ---------------------------------------------------------------------------

_session_work_dir: ContextVar[str] = ContextVar("_session_work_dir", default="")
_session_id: ContextVar[str] = ContextVar("_session_id", default="")

def set_session_context(*, work_dir: str, session_id: str) -> None:
    _session_work_dir.set(work_dir)
    _session_id.set(session_id)

def get_session_work_dir() -> str:
    return _session_work_dir.get()

def get_session_id() -> str:
    return _session_id.get()

# ---------------------------------------------------------------------------
# Infrastructure deps — set once by AgentOS at startup, shared across all
# sessions.  Handlers import these instead of being constructed per-session.
# ---------------------------------------------------------------------------

_tool_deps: dict[str, Any] = {}

def set_tool_deps(**kwargs: Any) -> None:
    _tool_deps.update(kwargs)

def get_tool_dep(name: str) -> Any:
    return _tool_deps.get(name)

# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None

    @classmethod
    def ok(cls, data: Any = None) -> ToolResult:
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str, data: Any = None) -> ToolResult:
        return cls(success=False, data=data, error=error)

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "data": self.data, "error": self.error}

# ---------------------------------------------------------------------------
# ToolEntry — one per registered tool
# ---------------------------------------------------------------------------

@dataclass
class ToolEntry:
    name: str
    toolset: str
    schema: dict[str, Any]      # JSON schema (name + description + parameters)
    handler: Callable            # async def handler(**kwargs) -> ToolResult
    max_result_chars: int = 0
    concurrency_safe: bool = False  # safe for parallel execution with other tools
    read_only: bool = False         # does not modify files or external state

    def to_openai_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": self.schema}

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class Tool:
    """Minimal base for skill custom tools (backward compat).
    New tools should use plain handler functions + registry.register()."""
    name: str = ""
    toolset: str = "general"

    async def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def to_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name}}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}
        self._schemas_cache: list[dict[str, Any]] | None = None

    def register(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Callable,
        *,
        max_result_chars: int = 0,
        concurrency_safe: bool = False,
        read_only: bool = False,
    ) -> None:
        if name in self._tools:
            logger.warning("Tool '%s' already registered, overwriting", name)
        self._tools[name] = ToolEntry(
            name=name, toolset=toolset, schema=schema,
            handler=handler, max_result_chars=max_result_chars,
            concurrency_safe=concurrency_safe, read_only=read_only,
        )
        self._schemas_cache = None

    def get_entry(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def get_all_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_tool_names_for_toolset(self, toolset: str) -> list[str]:
        return [n for n, t in self._tools.items() if t.toolset == toolset]

    def retain_only(self, keep: set[str]) -> None:
        self._tools = {n: t for n, t in self._tools.items() if n in keep}
        self._schemas_cache = None

    def get_all_schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def get_available_schemas(self, toolset: str | None = None) -> list[dict[str, Any]]:
        if toolset is not None:
            selected = sorted(self._tools.values(), key=lambda t: t.name)
            return [t.to_openai_schema() for t in selected if t.toolset == toolset]
        if self._schemas_cache is None:
            sorted_tools = sorted(self._tools.values(), key=lambda t: t.name)
            self._schemas_cache = [t.to_openai_schema() for t in sorted_tools]
        return list(self._schemas_cache)


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
