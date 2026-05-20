from __future__ import annotations

import json
import asyncio
from pathlib import Path
from time import monotonic
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input

from agent_os import AgentOS
from tui.event_bridge import EventBridge, TuiRunMetrics
from tui.widgets.chat_history import ChatHistory
from tui.widgets.input_bar import InputBar
from tui.widgets.status_bar import StatusBar
from tui.widgets.subagent_list import SubAgentList
from tui.widgets.tool_log import ToolLog


class ChatScreen(Screen):
    def __init__(self, agent: AgentOS) -> None:
        super().__init__()
        self.agent = agent
        self.current_session_id: str | None = None
        self.current_work_dir = ""
        self.current_session_name = ""
        self._state_path = Path("./data/cli_state.json")
        self._chat_running = False
        self._chat_task = None
        self._started_at = monotonic()

    def compose(self) -> ComposeResult:
        yield StatusBar()
        with Vertical(id="main"):
            with Horizontal(id="body"):
                yield ChatHistory(id="chat-history")
                yield SubAgentList()
            yield ToolLog(id="tool-log")
        yield InputBar(id="input-bar")

    async def on_mount(self) -> None:
        await self._restore_or_create_session()
        self.set_interval(1, self._tick_status)
        self.set_interval(2, self._poll_subagents)
        self.set_focus(self.query_one(InputBar))

    @on(Input.Submitted, "#input-bar")
    async def handle_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        self.query_one(InputBar).value = ""
        if not text:
            return
        if text.startswith("/"):
            await self._handle_command(text)
            return
        if self._chat_running:
            self.agent.inject_message(self.current_session_id or "", text)
            self.query_one(ChatHistory).write_chat(f"\n[注入] {text}\n", "info")
            return
        self._chat_task = asyncio.create_task(self._start_chat(text))

    async def _start_chat(self, message: str) -> None:
        try:
            if not self.current_session_id:
                await self._restore_or_create_session()
            self._chat_running = True
            self._started_at = monotonic()
            self.call_later(self.query_one(InputBar).set_running, True)
            self.call_later(self.query_one(ChatHistory).write_user, message)
            bridge = EventBridge(
                self.agent,
                _WidgetAdapter(self),
                context_threshold=self.agent.settings.context_token_threshold,
            )
            await bridge.consume(self.current_session_id or "", message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.query_one(ChatHistory).write_error(f"{type(exc).__name__}: {exc}")
        finally:
            self._chat_running = False
            self.query_one(InputBar).set_running(False)
            self.set_focus(self.query_one(InputBar))
            await self._refresh_session_context()

    async def _handle_command(self, text: str) -> None:
        command, _, args = text[1:].partition(" ")
        command = command.lower().strip()
        args = args.strip()
        chat = self.query_one(ChatHistory)
        if command in {"quit", "exit", "q"}:
            self.app.exit()
        elif command in {"interrupt", "stop"}:
            self.action_interrupt()
        elif command == "new":
            if self._chat_running:
                chat.write_chat("\n[命令拒绝] 当前任务运行中，先 /interrupt 或等待完成\n", "info")
                return
            await self._create_session(args)
        elif command == "sessions":
            await self._show_sessions()
        elif command == "switch":
            if self._chat_running:
                chat.write_chat("\n[命令拒绝] 当前任务运行中，先 /interrupt 或等待完成\n", "info")
                return
            await self._switch_session(args)
        elif command == "status":
            await self._show_status()
        elif command == "subagent":
            self._poll_subagents()
            self.query_one(SubAgentList).action_toggle_expanded()
        elif command in {"tools", "tool"}:
            self.query_one(ToolLog).set_expanded(True)
        elif command in {"hide-tools", "hidetools"}:
            self.query_one(ToolLog).set_expanded(False)
        elif command == "help":
            self.action_help()
        elif command == "clear":
            chat.clear()
        else:
            chat.write_chat(f"\n[未知命令] {text}\n", "info")

    async def _restore_or_create_session(self) -> None:
        sessions = await self._ordered_sessions()
        saved_id = self._read_saved_session_id()
        session_ids = {s["id"] for s in sessions}
        if saved_id in session_ids:
            self.current_session_id = saved_id
        elif sessions:
            self.current_session_id = sessions[0]["id"]
        else:
            await self._create_session("研究空间 1", quiet=True)
            return
        self._save_current_session()
        await self._refresh_session_context()

    async def _ordered_sessions(self) -> list[dict[str, Any]]:
        sessions = await self.agent.list_sessions()
        return sorted(sessions, key=lambda s: (str(s.get("created_at", "")), str(s.get("id", ""))))

    async def _create_session(self, name: str, *, quiet: bool = False) -> None:
        if not name:
            sessions = await self.agent.list_sessions()
            name = f"研究空间 {len(sessions) + 1}"
        session = await self.agent.create_session(name=name)
        self.current_session_id = session.id
        self._save_current_session()
        await self._refresh_session_context()
        if not quiet:
            self.query_one(ChatHistory).write_chat(f"\n[新建工作区] {name}\n", "info")

    async def _switch_session(self, args: str) -> None:
        sessions = await self._ordered_sessions()
        try:
            idx = int(args)
            session = sessions[idx - 1]
        except (ValueError, IndexError):
            self.query_one(ChatHistory).write_chat("\n[切换失败] 使用 /switch <编号>\n", "info")
            return
        self.current_session_id = session["id"]
        self._save_current_session()
        await self._refresh_session_context()
        self.query_one(ChatHistory).write_chat(f"\n[切换工作区] {session.get('name') or session['id']}\n", "info")

    async def _show_sessions(self) -> None:
        sessions = await self._ordered_sessions()
        lines = ["\n[工作区列表]"]
        for index, session in enumerate(sessions, start=1):
            mark = "*" if session["id"] == self.current_session_id else " "
            lines.append(f"{mark} {index}. {session.get('name') or session['id']} · {session.get('stage')} · {session.get('status')}")
        self.query_one(ChatHistory).write_chat("\n".join(lines) + "\n", "info")

    async def _show_status(self) -> None:
        session = await self.agent.get_session(self.current_session_id or "")
        if not session:
            return
        self.query_one(ChatHistory).write_chat(
            f"\n[状态] {session.name or session.id} · {session.stage} · {session.status} · {session.work_dir}\n",
            "info",
        )

    async def _refresh_session_context(self) -> None:
        session = await self.agent.get_session(self.current_session_id or "")
        if not session:
            return
        self.current_session_name = session.name or session.id
        self.current_work_dir = session.work_dir
        self.query_one(StatusBar).set_context(
            session_name=self.current_session_name,
            model_name=self.agent.settings.model,
        )
        self._poll_subagents()

    def _tick_status(self) -> None:
        bar = self.query_one(StatusBar)
        elapsed = int(monotonic() - self._started_at) if self._chat_running else 0
        bar.update_metrics(bar.metrics, elapsed_s=elapsed)

    def _poll_subagents(self) -> None:
        if self.current_work_dir:
            self.query_one(SubAgentList).update_from_work_dir(self.current_work_dir)

    def _read_saved_session_id(self) -> str | None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = data.get("current_session_id")
        return value if isinstance(value, str) and value else None

    def _save_current_session(self) -> None:
        if not self.current_session_id:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"current_session_id": self.current_session_id}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def set_current_session_id(self, session_id: str) -> None:
        self.current_session_id = session_id
        self._save_current_session()

    def action_interrupt(self) -> None:
        if self.current_session_id and self._chat_running:
            self.agent.request_interrupt(self.current_session_id)
            self.query_one(ChatHistory).write_chat("\n[中断] 已请求停止当前任务\n", "info")
        else:
            self.query_one(ChatHistory).write_chat("\n[中断] 当前没有运行中的任务\n", "info")

    def action_expand_tools(self) -> None:
        self.query_one(ToolLog).set_expanded(True)

    def action_collapse_tools(self) -> None:
        self.query_one(ToolLog).set_expanded(False)

    def action_clear_chat(self) -> None:
        self.query_one(ChatHistory).clear()

    def action_quit(self) -> None:
        if self._chat_task is not None and not self._chat_task.done():
            self._chat_task.cancel()
        self.app.exit()

    def action_help(self) -> None:
        self.query_one(ChatHistory).write_chat(
            "\n[帮助] /new /sessions /switch <编号> /status /subagent /tools /hide-tools /interrupt /clear /quit\n",
            "info",
        )


class _WidgetAdapter:
    def __init__(self, screen: ChatScreen) -> None:
        self.screen = screen

    def write_chat(self, message: str, style: str = "") -> None:
        self.screen.call_later(self.screen.query_one(ChatHistory).write_chat, message, style)

    def write_tool(self, message: str, style: str = "") -> None:
        self.screen.call_later(self.screen.query_one(ToolLog).write_tool, message, style)

    def update_status(self, metrics: TuiRunMetrics) -> None:
        self.screen.call_later(self.screen.query_one(StatusBar).update_metrics, metrics)

    def write_error(self, message: str) -> None:
        self.screen.call_later(self.screen.query_one(ChatHistory).write_error, message)

    def switch_session(self, session_id: str) -> None:
        self.screen.call_later(self.screen.set_current_session_id, session_id)
