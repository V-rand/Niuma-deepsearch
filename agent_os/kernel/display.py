"""
Rich-based terminal display layer for AgentOS CLI.

Provides:
- Live dashboard for long-running tasks (sub-agent tracking, progress)
- Compact, color-coded output for tool calls / results
- Truncated input echo for long prompts
- Session status panels

Inspired by Hermes agent/display.py, Kimi Agent, and Codex CLI patterns.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.align import Align
from rich import box

# ---------------------------------------------------------------------------
# Global console — used by CLI and display helpers
# ---------------------------------------------------------------------------
console = Console(highlight=False)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_INPUT_ECHO_LEN = 120
_TOOL_PREVIEW_MAX_LEN = 80
_TOOL_VERBOSE_PREVIEW_MAX_LEN = 400
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
_ACTIVITY_DETAIL_MAX_LEN = 260


def _clean_text(text: str) -> str:
    return _SURROGATE_RE.sub("\ufffd", text or "")


def _compact_content_filter_error(detail: str) -> str:
    low = detail.lower()
    if "content filter" not in low and "data_inspection_failed" not in low and "inappropriate content" not in low:
        return detail
    req_match = re.search(r"request_id[:=]\s*([a-zA-Z0-9\\-]+)", detail)
    if req_match is None:
        req_match = re.search(r"['\"]request_id['\"]\s*:\s*['\"]([a-zA-Z0-9\\-]+)['\"]", detail)
    code_match = re.search(r"(data_inspection_failed|DataInspectionFailed|content_filter)", detail)
    req = req_match.group(1) if req_match else "-"
    code = code_match.group(1) if code_match else "content_filter"
    return f"Provider 内容审查拦截（code={code}, request_id={req}）。原始报错与完整上下文已写入 run log / provider snapshot。"

# Color palette (semantic)
_C_USER = "bright_cyan"
_C_AGENT = "bright_green"
_C_THINK = "dim"
_C_TOOL = "bright_yellow"
_C_TOOL_OK = "green"
_C_TOOL_ERR = "red"
_C_ACTIVITY = "bright_blue"
_C_WARN = "yellow"
_C_ERROR = "red"
_C_PANEL_BORDER = "bright_black"
_C_PANEL_TITLE = "bright_white"


# ---------------------------------------------------------------------------
# Sub-agent tracker (for spawn tasks)
# ---------------------------------------------------------------------------
@dataclass
class SubAgentTask:
    task_id: str
    status: str = "pending"  # pending | running | completed | failed
    description: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class SubAgentTracker:
    """Tracks sub-agent tasks spawned during a run."""

    def __init__(self):
        self.tasks: Dict[str, SubAgentTask] = {}
        self._lock = asyncio.Lock()

    async def add(self, task_id: str, description: str = "") -> None:
        async with self._lock:
            self.tasks[task_id] = SubAgentTask(
                task_id=task_id,
                status="running",
                description=description,
                started_at=datetime.now(),
            )

    async def complete(self, task_id: str, error: Optional[str] = None) -> None:
        async with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id].status = "failed" if error else "completed"
                self.tasks[task_id].completed_at = datetime.now()
                self.tasks[task_id].error = error

    def render_table(self) -> Optional[Table]:
        if not self.tasks:
            return None
        table = Table(
            box=box.SIMPLE_HEAD,
            header_style="bold bright_black",
            border_style="bright_black",
            padding=(0, 1),
            show_edge=False,
        )
        table.add_column("状态", width=4, justify="center")
        table.add_column("子任务", min_width=20)
        table.add_column("耗时", width=10, justify="right")

        for t in self.tasks.values():
            if t.status == "running":
                status_icon = "🟡"
                status_style = "yellow"
            elif t.status == "completed":
                status_icon = "🟢"
                status_style = "green"
            elif t.status == "failed":
                status_icon = "🔴"
                status_style = "red"
            else:
                status_icon = "⚪"
                status_style = "dim"

            desc = (t.description or t.task_id)[:40]
            if t.error:
                desc += f" [red]({t.error})[/]"

            if t.completed_at and t.started_at:
                delta = t.completed_at - t.started_at
                elapsed = f"{delta.total_seconds():.1f}s"
            elif t.started_at:
                elapsed = "..."
            else:
                elapsed = "-"

            table.add_row(
                Text(status_icon, style=status_style),
                Text(desc, style=status_style if t.status != "running" else ""),
                Text(elapsed, style="dim"),
            )
        return table


# ---------------------------------------------------------------------------
# Run dashboard (Live display for a single user message processing)
# ---------------------------------------------------------------------------
class RunDashboard:
    """
    Live-updating dashboard shown while the agent processes a user message.
    Collapses when the run completes.
    """

    def __init__(self, console: Console, compress_threshold: int = 250_000, show_cache: bool = False):
        self.console = console
        self.live: Optional[Live] = None
        self.tracker = SubAgentTracker()
        self.iteration = 0
        self.total_tokens = 0
        self.compress_threshold = compress_threshold
        self.tool_count = 0
        self.model_latency_ms = 0.0
        self.current_phase = "初始化"
        self.started_at = datetime.now()
        self.session_prompt_tokens: int = 0
        self.session_completion_tokens: int = 0
        self.session_cached_tokens: int = 0
        self.last_prompt_tokens: int = 0
        self.last_cached_tokens: int = 0
        self.show_cache = show_cache
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self.live is not None:
            return
        self.live = Live(
            self._build_renderable(),
            console=self.console,
            refresh_per_second=4,
            transient=True,  # disappears when stopped
        )
        self.live.start()

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None

    async def update(
        self,
        *,
        iteration: Optional[int] = None,
        phase: Optional[str] = None,
        tokens: Optional[int] = None,
        tool_count: Optional[int] = None,
        model_latency_ms: Optional[float] = None,
    ) -> None:
        async with self._lock:
            if iteration is not None:
                self.iteration = iteration
            if phase is not None:
                self.current_phase = phase
            if tokens is not None:
                self.total_tokens = tokens
            if tool_count is not None:
                self.tool_count = tool_count
            if model_latency_ms is not None:
                self.model_latency_ms = model_latency_ms
        if self.live:
            self.live.update(self._build_renderable())

    async def add_token_usage(self, usage: dict) -> None:
        """Accumulate token usage from a model call."""
        async with self._lock:
            if usage:
                pt = usage.get("prompt_tokens", 0) or 0
                ct = usage.get("completion_tokens", 0) or 0
                cat = usage.get("cached_tokens", 0) or 0
                self.last_prompt_tokens = pt
                self.last_cached_tokens = cat
                self.session_prompt_tokens = max(self.session_prompt_tokens, pt)
                self.session_completion_tokens += ct
                self.session_cached_tokens += cat
        if self.live:
            self.live.update(self._build_renderable())

    def _build_renderable(self) -> Group:
        # Header line
        elapsed = (datetime.now() - self.started_at).total_seconds()
        header = Text()
        header.append("▶ ", style="bright_cyan")
        header.append(f"第 {self.iteration} 轮", style="bold")
        header.append(f"  ·  {self.current_phase}", style="dim")
        header.append(f"  ·  已运行 {elapsed:.0f}s", style="dim")

        # Stats line
        stats = Text()
        pct = round(self.total_tokens / self.compress_threshold * 100, 1) if self.compress_threshold else 0
        stats.append(f"  tokens: {self.total_tokens:,} (压缩阈值 {pct}%)", style="dim")
        stats.append(f"  ·  工具: {self.tool_count}", style="dim")
        if self.model_latency_ms:
            stats.append(f"  ·  上轮模型: {self.model_latency_ms / 1000:.1f}s", style="dim")

        # Sub-agent table
        sub_table = self.tracker.render_table()

        parts: List[Any] = [header, stats]

        # Session token stats line
        if self.show_cache and (self.last_prompt_tokens > 0 or self.session_completion_tokens > 0):
            hit_rate = (
                round(self.last_cached_tokens / self.last_prompt_tokens * 100, 1)
                if self.last_prompt_tokens > 0
                else 0
            )
            session_line = Text()
            session_line.append(
                f"  Session: prompt={self.session_prompt_tokens:,} "
                f"completion={self.session_completion_tokens:,} "
                f"cached={self.session_cached_tokens:,} "
                f"(本{chr(36724)}缓存率 {hit_rate}%)",
                style="dim cyan",
            )
            parts.append(session_line)

        if sub_table:
            parts.append("")
            parts.append(sub_table)

        return Group(*parts)


# ---------------------------------------------------------------------------
# Compact output helpers (used by CLI _render_chat_chunk)
# ---------------------------------------------------------------------------

def echo_user_input(message: str, max_len: int = _MAX_INPUT_ECHO_LEN) -> None:
    """Print truncated user input with clear visual marker."""
    message = _clean_text(message)
    truncated = message
    suffix = ""
    if len(message) > max_len:
        truncated = message[:max_len]
        suffix = f" [dim]… ({len(message)} 字符)[/]"
    console.print()
    console.print(Panel(
        truncated + suffix,
        title="[bold bright_cyan]🙋 您[/bold bright_cyan]",
        title_align="left",
        border_style="bright_cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_thinking(content: str) -> None:
    """Full thinking process — dim, multiline preserved."""
    text = content.strip()
    if not text:
        return
    console.print(Text(f"  💭 {text}", style="dim"))


def print_thinking_chunk(content: str) -> None:
    """Streaming thinking chunk — no newline, just append."""
    if not content:
        return
    # Print without newline for streaming effect
    console.print(f"[dim]{content}[/dim]", end="")


def print_content_chunk(content: str) -> None:
    """Streaming content chunk — no newline, just append."""
    if not content:
        return
    console.print(content, end="")


def print_activity(phase: str, detail: str) -> None:
    """Activity line with semantic coloring."""
    detail = _clean_text(detail)
    if phase == "run.failed":
        detail = _compact_content_filter_error(detail)
    if len(detail) > _ACTIVITY_DETAIL_MAX_LEN:
        detail = detail[: _ACTIVITY_DETAIL_MAX_LEN - 3] + "..."
    # Phase-based icon
    icon_map = {
        "run.started": "▶",
        "run.completed": "✓",
        "run.failed": "✗",
        "context.compiled": "📋",
        "context.recovered": "🛟",
        "context.compressed": "📦",
        "uploads.parsed": "📎",
        "model.requested": "🧠",
        "model.completed": "⚡",
        "model.retry": "🔄",
        "tools.planned": "🔧",
        "tool.executing": "⚙️",
        "tool.completed": "✓",
        "tool.arguments_stream": "📝",
        "artifact.saved": "💾",
        "intervention.applied": "🧭",
        "message.injected": "✉",
        "report.generated": "📄",
        "report.failed": "⚠️",
    }
    icon = icon_map.get(phase, "·")
    # Color by phase category
    if phase.startswith("run."):
        style = "bold bright_blue"
    elif phase.startswith("model."):
        style = "bright_magenta"
    elif phase.startswith("tool."):
        style = "bright_yellow"
    elif phase.startswith("report."):
        style = "bright_green"
    else:
        style = "dim"
    console.print(f"  [{style}]{icon} {phase}[/]: {detail}")


def print_tool_call(name: str, summary: str, *, verbose: bool = False) -> None:
    """Single-line tool call announcement."""
    # Emoji map for common tools
    emoji_map = {
        "web_search": "🔍",
        "web_read": "📄",
        "law_retrieve": "⚖️",
        "case_retrieve": "🏛️",
        "file_read": "📖",
        "file_write": "📝",
        "file_append": "➕",
        "file_delete": "🗑️",
        "bash": "💻",
        "spawn": "🚀",
        "workspace_search": "🔎",
        "reminder_create": "⏰",
        "skill_use": "🎯",
        "skill_propose": "💡",
    }
    emoji = emoji_map.get(name, "🔧")
    preview = summary
    max_len = _TOOL_VERBOSE_PREVIEW_MAX_LEN if verbose else _TOOL_PREVIEW_MAX_LEN
    if len(preview) > max_len:
        preview = preview[:max_len - 3] + "..."
    console.print(f"  [bright_yellow]{emoji} {name}[/] [dim]{preview}[/]")


def print_tool_result(success: bool, latency_ms: Optional[float], summary: str, *, verbose: bool = False) -> None:
    """Compact tool result — one line, timing in seconds."""
    latency_s = (latency_ms or 0) / 1000
    latency = f" {latency_s:.1f}s" if latency_ms else ""
    max_len = _TOOL_VERBOSE_PREVIEW_MAX_LEN if verbose else 100
    if success:
        short = summary.split("\n")[0] if summary else ""
        if len(short) > max_len:
            short = short[:max_len - 3] + "..."
        console.print(f"    [green]✓{latency}[/] [dim]{short}[/]")
    else:
        console.print(f"    [red]✗{latency} {summary}[/]")


def print_todo_summary(todos: list[dict[str, Any]]) -> None:
    """Render a compact todo list after todowrite updates."""
    if not todos:
        return
    table = Table(
        box=box.SIMPLE,
        show_edge=False,
        padding=(0, 1),
        expand=False,
    )
    table.add_column("状态", width=4)
    table.add_column("优先级", width=6)
    table.add_column("任务", min_width=20)
    for todo in todos[:12]:
        status = str(todo.get("status") or ("completed" if todo.get("completed") else "pending"))
        if status == "completed":
            marker = "[green]✓[/]"
        elif status == "in_progress":
            marker = "[yellow]▶[/]"
        elif status == "blocked":
            marker = "[red]![/]"
        else:
            marker = "[dim]○[/]"
        priority = str(todo.get("priority", "-"))
        content = str(todo.get("content") or "")
        table.add_row(marker, priority, content)
    if len(todos) > 12:
        table.add_row("[dim]…[/]", "", f"[dim]另有 {len(todos) - 12} 项，输入 /todos 查看全部[/]")
    console.print()
    console.print(Panel(
        table,
        title="[bold bright_yellow]Todo 更新[/]",
        title_align="left",
        border_style="bright_yellow",
        box=box.ROUNDED,
        padding=(0, 1),
    ))


def print_agent_response(content: str) -> None:
    """Agent final response — clear visual separation."""
    console.print()
    console.print(Panel(
        Text(content),
        title="[bold bright_green]🤖 Agent[/bold bright_green]",
        title_align="left",
        border_style="bright_green",
        box=box.ROUNDED,
        padding=(1, 2),
    ))


def print_error(error: str) -> None:
    """Error panel."""
    error = _clean_text(error)
    error = _compact_content_filter_error(error)
    console.print()
    console.print(Panel(
        error,
        title="[bold red]❌ 错误[/bold red]",
        title_align="left",
        border_style="red",
        box=box.ROUNDED,
        padding=(1, 2),
    ))


def print_intervention(content: str) -> None:
    """Intervention notice."""
    console.print(f"  [yellow]🧭 人工修正已应用[/]: [dim]{content}[/]")


def print_compression_event(old_session_id: str, new_session_id: str, tokens_before: int, summary: str = "") -> None:
    """Context compression notification with optional summary preview."""
    console.print()
    console.print(Panel(
        f"上下文已压缩（~{tokens_before:,} tokens → 新 session）\n"
        f"旧 session: [dim]{old_session_id}[/]\n"
        f"新 session: [bright_cyan]{new_session_id}[/]",
        title="[bold bright_yellow]📦 上下文压缩[/bold bright_yellow]",
        title_align="left",
        border_style="bright_yellow",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    if summary:
            console.print(Panel(
                summary.strip(),
                title="[bold]📋 压缩摘要[/bold]",
                border_style="dim",
                padding=(1, 2),
            ))


# ---------------------------------------------------------------------------
# Welcome / Status panels
# ---------------------------------------------------------------------------

def print_welcome(title: str = "Agent OS - 您的法律智能助手") -> None:
    """Rich welcome banner."""
    console.print()
    console.print(Align.center(
        Text(title, style="bold bright_cyan", justify="center")
    ))
    console.print(Align.center(
        Text("─" * 40, style="dim")
    ))
    console.print()


def print_session_list(sessions: List[Dict[str, Any]], current_id: Optional[str] = None) -> None:
    """Rich session list table."""
    if not sessions:
        console.print("[dim]暂无工作区[/]")
        return

    table = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold bright_black",
        border_style="bright_black",
        show_edge=False,
        padding=(0, 2),
    )
    table.add_column("#", width=3, justify="center")
    table.add_column("名称", min_width=18, ratio=1)
    table.add_column("阶段", width=10)
    table.add_column("状态", width=8)
    table.add_column("更新", width=16)

    for i, s in enumerate(sessions[:9], 1):
        is_current = s.get("id") == current_id
        name = s.get("name") or "未命名"
        stage = s.get("stage", "")
        status = s.get("status", "")
        updated = _compact_time(s.get("updated_at_display") or s.get("updated_at") or "")

        if is_current:
            table.add_row(
                f"[bright_cyan]{i}[/]",
                f"[bold bright_cyan]{name}[/] [bright_cyan]←[/]",
                f"[bright_cyan]{stage}[/]",
                f"[bright_cyan]{status}[/]",
                f"[dim]{updated}[/]",
            )
        else:
            table.add_row(
                str(i),
                name,
                stage,
                status,
                f"[dim]{updated}[/]",
            )
    console.print(table)


def print_session_status(
    name: str,
    stage: str,
    status: str,
    work_dir: str,
    artifact_count: int,
    todo_count: int,
    reminder_count: int,
    next_reminder: Optional[str] = None,
) -> None:
    """Rich session status panel."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", width=10)
    grid.add_column()

    grid.add_row("名称", name)
    grid.add_row("阶段", f"[bright_yellow]{stage}[/]")
    grid.add_row("状态", f"[bright_green]{status}[/]" if status == "active" else status)
    grid.add_row("工作区", f"[dim]{work_dir}[/]")

    stats = Table.grid(padding=(0, 2))
    stats.add_column()
    stats.add_column()
    stats.add_row(
        f"[bold]{artifact_count}[/] 工件",
        f"[bold]{todo_count}[/] 待办",
        f"[bold]{reminder_count}[/] 提醒",
    )

    extras: List[Text] = []
    if next_reminder:
        extras.append(Text(f"⏰ {next_reminder}", style="dim"))

    content = Group(grid, "", stats)
    if extras:
        content = Group(content, "", *extras)

    console.print()
    console.print(Panel(
        content,
        title="[bold]工作区摘要[/]",
        title_align="left",
        border_style="bright_black",
        box=box.ROUNDED,
        padding=(1, 2),
    ))


def print_help() -> None:
    """Rich help panel."""
    idle_commands = [
        ("/new [名称]", "创建新工作区", "/new 劳动合同纠纷"),
        ("/resume [编号]", "恢复工作区并显示最近对话", "/resume 1"),
        ("/switch <编号>", "切换工作区", "/switch 1"),
        ("/sessions", "列出所有工作区", ""),
        ("/status", "显示当前工作区摘要", ""),
        ("/history [条数]", "显示最近对话记录", "/history 20"),
        ("/todos", "显示待办事项", ""),
        ("/reminders", "显示提醒", ""),
        ("/subagent [id]", "显示子 Agent 状态或轨迹", "/subagent"),
        ("/tools [模式]", "工具日志: on/off/normal/verbose", "/tools verbose"),
        ("/close", "关闭当前工作区", ""),
        ("/compress", "手动压缩上下文", "/compress"),
        ("/delete <编号>", "删除工作区", "/delete 2"),
        ("/help", "显示此帮助", ""),
        ("/q 或 /quit", "退出程序", ""),
    ]
    running_commands = [
        ("普通文字", "注入当前任务，下轮模型/工具边界生效"),
        ("/status", "显示当前模型/工具、输出字符数、子 Agent 摘要"),
        ("/subagent [id]", "查看子 Agent 状态或详细轨迹"),
        ("/interrupt 或 /stop", "请求软中断当前任务"),
        ("/tools [模式]", "临时调整工具日志详细程度"),
        ("/q", "请求中断，并在本轮结束后退出"),
    ]

    console.print()
    console.print("[bold]命令列表[/]")
    console.print("[dim]直接输入自然语言即可与 Agent 对话；命令都以 / 开头。[/]")
    console.print()
    _print_command_rows("空闲命令", idle_commands)
    console.print()
    _print_command_rows("运行中命令", [(cmd, desc, "") for cmd, desc in running_commands])
    console.print("[dim]提示: 直接输入自然语言即可与 Agent 对话[/]")


def _print_command_rows(title: str, rows: list[tuple[str, str, str]]) -> None:
    show_examples = any(example for _, _, example in rows)
    table = Table(
        box=box.SIMPLE,
        header_style="bold bright_black",
        show_edge=False,
        padding=(0, 2),
        expand=True,
    )
    table.add_column(title, style="bright_cyan", width=20, no_wrap=True)
    table.add_column("说明", ratio=1)
    if show_examples:
        table.add_column("示例", style="dim", width=18)
    for cmd, desc, example in rows:
        if show_examples:
            table.add_row(cmd, desc, example)
        else:
            table.add_row(cmd, desc)
    console.print(table)


def _compact_time(value: str) -> str:
    text = str(value or "").replace("北京时间", "").strip()
    if len(text) > 16:
        return text[:16]
    return text


# ---------------------------------------------------------------------------
# Simple text helpers
# ---------------------------------------------------------------------------

def print_success(msg: str) -> None:
    console.print(f"[green]✓[/] {msg}")


def print_warning(msg: str) -> None:
    console.print(f"[yellow]⚠️[/] {msg}")


def print_fail(msg: str) -> None:
    console.print(f"[red]✗[/] {msg}")


def print_info(msg: str) -> None:
    console.print(f"[dim]{msg}[/]")


def print_plain(msg: str) -> None:
    console.print(msg)
