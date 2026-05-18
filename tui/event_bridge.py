"""Bridge AgentOS streaming events into TUI widgets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol


@dataclass
class TuiRunMetrics:
    model_calls: int = 0
    tool_calls: int = 0
    model_latency_ms_total: int = 0
    tool_latency_ms_total: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    compression_pct: float = 0.0
    current_iteration: int = 0

    @property
    def cache_rate(self) -> float:
        if self.prompt_tokens <= 0:
            return 0.0
        return round(self.cached_tokens / self.prompt_tokens * 100, 1)


class TuiWidgets(Protocol):
    def write_chat(self, message: str, style: str = "") -> None: ...
    def write_tool(self, message: str, style: str = "") -> None: ...
    def update_status(self, metrics: TuiRunMetrics) -> None: ...
    def write_error(self, message: str) -> None: ...
    def switch_session(self, session_id: str) -> None: ...


class EventBridge:
    def __init__(self, agent: Any, widgets: TuiWidgets, *, context_threshold: int = 210000):
        self.agent = agent
        self.widgets = widgets
        self.metrics = TuiRunMetrics()
        self.context_threshold = context_threshold
        self._buffer: list[tuple[str, str]] = []
        self._buffer_chars = 0
        self._last_flush = monotonic()

    async def consume(self, session_id: str, message: str) -> None:
        if self.agent is None:
            return
        async for chunk in self.agent.chat(session_id, message):
            await self.handle_chunk(chunk)
        await self.finish()

    async def handle_chunk(self, chunk: dict[str, Any]) -> None:
        msg_type = chunk.get("type")
        if msg_type == "thinking_stream":
            self._append_chat(chunk.get("content", ""), "dim")
        elif msg_type == "content_stream":
            self._append_chat(chunk.get("content", ""), "agent")
        elif msg_type == "thinking":
            content = (chunk.get("content") or "").strip()
            if content:
                self._append_chat(f"\n[thinking] {content}\n", "dim")
        elif msg_type == "content":
            content = chunk.get("content") or ""
            if content:
                self._append_chat(f"\n{content}\n", "agent")
        elif msg_type == "activity":
            await self._handle_activity(chunk)
        elif msg_type == "tool_call":
            name = chunk.get("name", "")
            summary = chunk.get("summary", "")
            suffix = f" {summary}" if summary else ""
            self.widgets.write_tool(f"CALL {name}{suffix}", "tool")
        elif msg_type == "tool_result":
            result = chunk.get("result", {}) or {}
            latency = int(result.get("latency_ms") or 0)
            self.metrics.tool_calls += 1
            self.metrics.tool_latency_ms_total += latency
            name = result.get("tool") or "tool"
            state = "OK" if result.get("success") else "FAIL"
            summary = result.get("summary") or result.get("error") or ""
            self.widgets.write_tool(f"{state} {name} {latency / 1000:.1f}s {summary}", "ok" if result.get("success") else "error")
            self.widgets.update_status(self.metrics)
        elif msg_type == "session.compressed":
            old_id = chunk.get("old_session_id", "")
            new_id = chunk.get("new_session_id", "")
            before = chunk.get("estimated_tokens_before", 0)
            self.widgets.write_tool(f"COMPRESS {old_id[:8]} -> {new_id[:8]} before={before}", "warn")
            if new_id:
                self.widgets.switch_session(str(new_id))
        elif msg_type == "intervention":
            self.widgets.write_tool(f"INTERVENTION {chunk.get('content', '')}", "info")
        elif msg_type == "error":
            self.widgets.write_error(str(chunk.get("error") or "Unknown error"))
        await self._maybe_flush()

    async def finish(self) -> None:
        self._flush()
        self.widgets.write_tool(
            "SUMMARY "
            f"models={self.metrics.model_calls} tools={self.metrics.tool_calls} "
            f"model={self.metrics.model_latency_ms_total / 1000:.1f}s "
            f"tools={self.metrics.tool_latency_ms_total / 1000:.1f}s "
            f"tokens={self.metrics.total_tokens:,} cache={self.metrics.cache_rate}%",
            "summary",
        )

    async def _handle_activity(self, chunk: dict[str, Any]) -> None:
        phase = chunk.get("phase") or ""
        detail = chunk.get("detail") or ""
        payload = chunk.get("payload") or {}
        if phase == "model.completed":
            self.metrics.model_calls += 1
            self.metrics.model_latency_ms_total += int(payload.get("latency_ms") or 0)
            usage = payload.get("usage") or {}
            self.metrics.prompt_tokens = int(usage.get("prompt_tokens") or 0)
            self.metrics.completion_tokens = int(usage.get("completion_tokens") or 0)
            self.metrics.total_tokens = int(usage.get("total_tokens") or 0)
            self.metrics.cached_tokens = int(usage.get("cached_tokens") or 0)
            if self.context_threshold:
                self.metrics.compression_pct = round(self.metrics.total_tokens / self.context_threshold * 100, 1)
            self.metrics.current_iteration = int(payload.get("iteration") or self.metrics.current_iteration)
            self.widgets.update_status(self.metrics)
            self.widgets.write_tool(detail, "model")
        elif phase in {"tool.completed", "tool.arguments_stream", "model.requested", "message.injected", "run.interrupted"}:
            self.widgets.write_tool(detail, "info")
            if phase in {"tool.arguments_stream", "message.injected", "run.interrupted"}:
                self._append_chat(f"\n[{phase}] {detail}\n", "info")
        else:
            self.widgets.write_tool(f"{phase}: {detail}", "info")

    def _append_chat(self, message: str, style: str) -> None:
        if not message:
            return
        self._buffer.append((message, style))
        self._buffer_chars += len(message)

    async def _maybe_flush(self) -> None:
        if self._buffer_chars >= 256 or monotonic() - self._last_flush >= 0.1:
            self._flush()
        await asyncio.sleep(0)

    def _flush(self) -> None:
        if not self._buffer:
            return
        merged: list[tuple[str, str]] = []
        for message, style in self._buffer:
            if merged and merged[-1][1] == style:
                merged[-1] = (merged[-1][0] + message, style)
            else:
                merged.append((message, style))
        for message, style in merged:
            self.widgets.write_chat(message, style)
        self._buffer.clear()
        self._buffer_chars = 0
        self._last_flush = monotonic()
