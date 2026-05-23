"""
Event shapes yielded by AgentLoop.process() AsyncGenerator.

Each TypedDict corresponds to one ``yield`` event.  There is no runtime
enforcement — the definitions serve as documentation and can be directly
mapped to TypeScript interfaces during migration.
"""

from __future__ import annotations

from typing import Any, TypedDict


class InputEvent(TypedDict):
    """Logged at the start of a user turn (written by ``cli.py``, not process)."""
    type: str           # "run.input"
    session_id: str
    message: str
    created_at: str


class ActivityEvent(TypedDict):
    """Progress marker: context loaded, run started, tools planned, etc."""
    type: str           # "activity"
    phase: str          # e.g. "context.compiled", "run.started", "model.completed"
    detail: str
    payload: dict[str, Any] | None


class ThinkingStreamEvent(TypedDict):
    """Single chunk of reasoning / chain-of-thought from the model."""
    type: str           # "thinking_stream"
    content: str


class ContentStreamEvent(TypedDict):
    """Single chunk of the final answer text from the model."""
    type: str           # "content_stream"
    content: str


class ToolCallEvent(TypedDict):
    """Emitted immediately before a tool starts executing."""
    type: str           # "tool_call"
    name: str
    arguments: dict[str, Any]
    summary: str        # human-readable one-line summary


class ToolResultEvent(TypedDict):
    """Emitted after a tool completes."""
    type: str           # "tool_result"
    result: dict[str, Any]  # ToolResult.to_dict() output


class ContentEvent(TypedDict):
    """Emitted once at the end of a successful run — the final agent answer."""
    type: str           # "content"
    content: str


class ErrorEvent(TypedDict):
    """Emitted when the run fails (content filter, timeout, etc.)."""
    type: str           # "error"
    error: str
    payload: dict[str, Any] | None  # snapshot_path, error_type, etc.


class InterventionEvent(TypedDict):
    """Emitted when a manual intervention is consumed mid-run."""
    type: str           # "intervention"
    content: str
    payload: dict[str, Any] | None


class QuestionEvent(TypedDict):
    """Emitted when a question-type intervention appears."""
    type: str           # "question"
    content: str
    payload: dict[str, Any] | None


class SessionCompressedEvent(TypedDict):
    """Emitted when token budget triggers context compression."""
    type: str           # "session.compressed"
    old_session_id: str
    new_session_id: str
    estimated_tokens_before: int


# Union-like type for documentation — process() yields any of these.
ProcessEvent = (
    ActivityEvent | ThinkingStreamEvent | ContentStreamEvent |
    ToolCallEvent | ToolResultEvent | ContentEvent | ErrorEvent |
    InterventionEvent | QuestionEvent | SessionCompressedEvent
)
