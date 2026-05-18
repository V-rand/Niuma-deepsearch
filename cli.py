"""
Agent OS CLI - 命令行界面 (Rich-enhanced)
"""

import os
import sys
import asyncio
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
try:
    import readline
except Exception:  # pragma: no cover
    readline = None

from dotenv import load_dotenv
load_dotenv()

from prompt_toolkit import PromptSession
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.text import Text

from agent_os import AgentOS, check_api_keys
from agent_os.kernel.display import (
    console,
    echo_user_input,
    print_thinking,
    print_activity,
    print_tool_call,
    print_tool_result,
    print_todo_summary,
    print_agent_response,
    print_error,
    print_intervention,
    print_compression_event,
    print_welcome,
    print_session_list,
    print_session_status,
    print_help,
    print_success,
    print_warning,
    print_fail,
    print_info,
    RunDashboard,
)

class AgentOSCLI:
    """Agent OS 命令行界面"""

    def __init__(self):
        self.os: Optional[AgentOS] = None
        self.current_session_id: Optional[str] = None
        self.session_index_map: Dict[int, str] = {}
        self.prompt_session: Optional[PromptSession] = None
        self._use_prompt_toolkit = False
        self._dashboard: Optional[RunDashboard] = None
        self._state_path = Path("./data/cli_state.json")
        self._thinking_buffer: list[str] = []
        self._thinking_partial = ""
        self._thinking_started = False
        self._content_buffer: list[str] = []
        self._content_stream_started = False
        self._content_stream_open = False
        self._run_log_path: Optional[Path] = None
        self._stream_log_buf: str = ""        # 合并 thinking/content stream 后再写 log
        self._stream_log_type: str = ""
        self._run_id: Optional[str] = None
        self._history_file_path: Optional[Path] = None
        self._last_stream_progress_at: Optional[datetime] = None
        self._thinking_chars = 0
        self._content_chars = 0
        self._tool_log_mode = "normal"
        self._exit_after_current_run = False
        self._run_started_at: Optional[datetime] = None
        self._current_iteration: Optional[int] = None
        self._last_model_usage: Dict[str, Any] = {}
        self._last_model_latency_ms: Optional[float] = None
        self._poll_exit_event: Optional[asyncio.Event] = None

    async def init(self):
        """初始化"""
        # 检查 API 密钥
        ok, error = check_api_keys()
        if not ok:
            console.print(f"[red]{error}[/]")
            sys.exit(1)

        # 初始化 AgentOS
        self.os = AgentOS(data_dir="./data")
        await self.os.start()

        # 交互输入默认走稳定的纯 stdin 模式，避免部分终端出现 ANSI 控制码污染。
        # 需要 prompt_toolkit 能力时可显式启用: AGENT_OS_CLI_PROMPT_TOOLKIT=1
        prefer_ptk = os.getenv("AGENT_OS_CLI_PROMPT_TOOLKIT", "").lower() in {"1", "true", "yes", "on"}
        if sys.stdin.isatty() and prefer_ptk:
            self.prompt_session = PromptSession(
                history=self._make_history(),
                color_depth=ColorDepth.DEPTH_1_BIT,
            )
            self._use_prompt_toolkit = True
        else:
            self.prompt_session = None
            self._use_prompt_toolkit = False
            self._setup_readline_history()

        # 加载会话列表
        await self._update_session_index_map()

    async def close(self):
        """关闭"""
        if self.os:
            await self.os.stop()
        self._restore_tty()

    @staticmethod
    def _restore_tty() -> None:
        try:
            import subprocess
            subprocess.run(["stty", "sane"], stderr=subprocess.DEVNULL, timeout=1)
        except Exception:
            try:
                from termios import TCSADRAIN, tcgetattr, tcsetattr
                tcsetattr(sys.stdin, TCSADRAIN, tcgetattr(sys.stdin))
            except Exception:
                pass

    @staticmethod
    def _ensure_cooked_tty() -> None:
        try:
            import termios, tty
            fd = sys.stdin.fileno()
            attrs = termios.tcgetattr(fd)
            if not (attrs[3] & termios.ECHO):
                AgentOSCLI._restore_tty()
        except Exception:
            pass

    async def _update_session_index_map(self):
        """更新会话索引映射"""
        sessions = await self._ordered_sessions()
        display_sessions = self._sessions_for_display(sessions)
        self.session_index_map = {}
        for i, s in enumerate(display_sessions, 1):
            self.session_index_map[i] = s["id"]

    def _sessions_for_display(self, sessions: list[dict[str, Any]], limit: int = 9) -> list[dict[str, Any]]:
        """Keep the current session visible in numbered CLI lists."""
        visible = list(sessions[:limit])
        if not self.current_session_id or any(s.get("id") == self.current_session_id for s in visible):
            return visible
        current = next((s for s in sessions if s.get("id") == self.current_session_id), None)
        if current is None:
            return visible
        if len(visible) >= limit:
            visible[-1] = current
        else:
            visible.append(current)
        return visible

    async def _ordered_sessions(self) -> list[dict[str, Any]]:
        """Stable CLI numbering: oldest session keeps the smaller number."""
        sessions = await self.os.list_sessions()
        return sorted(
            sessions,
            key=lambda s: (str(s.get("created_at", "")), str(s.get("id", ""))),
        )

    async def _restore_session(self):
        """恢复上次使用的工作区；没有状态文件时恢复最近更新的工作区。"""
        sessions = await self.os.list_sessions()
        if not sessions:
            return

        saved_id = self._read_saved_session_id()
        session_ids = {s["id"] for s in sessions}
        if saved_id in session_ids:
            self.current_session_id = saved_id
            return

        self.current_session_id = sessions[0]["id"]
        self._save_current_session()

    def _make_history(self):
        history_file = Path("./data/cli_history.txt")
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            with history_file.open("ab"):
                pass
            return FileHistory(str(history_file))
        except OSError:
            return InMemoryHistory()

    def _setup_readline_history(self) -> None:
        if readline is None:
            return
        history_file = Path("./data/cli_history.txt")
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            with history_file.open("ab"):
                pass
            readline.read_history_file(str(history_file))
            readline.set_history_length(5000)
            self._history_file_path = history_file
        except OSError:
            self._history_file_path = None

    def _append_readline_history(self, line: str) -> None:
        if readline is None:
            return
        text = (line or "").strip()
        if not text:
            return
        try:
            readline.add_history(text)
            if self._history_file_path is not None:
                readline.write_history_file(str(self._history_file_path))
        except OSError:
            pass

    def _read_saved_session_id(self) -> Optional[str]:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        session_id = data.get("current_session_id")
        return session_id if isinstance(session_id, str) and session_id else None

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

    def _clear_current_session(self) -> None:
        """Clear the selected session and remove stale persisted CLI state."""
        self.current_session_id = None
        try:
            self._state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _live_dashboard_enabled() -> bool:
        """Rich Live is opt-in; transcript mode is safer in VS Code terminals."""
        live_env = os.getenv("AGENT_OS_CLI_LIVE", "").strip().lower()
        return live_env in {"1", "true", "yes", "on"}

    def _get_session_display(self) -> str:
        """获取当前会话显示文本"""
        if not self.current_session_id:
            return "未选择"

        for idx, sid in self.session_index_map.items():
            if sid == self.current_session_id:
                return f"[{idx}]"

        return self.current_session_id[:8]

    async def _show_welcome(self):
        """显示欢迎界面"""
        print_welcome()

        sessions = await self._ordered_sessions()
        if sessions:
            console.print("[bold]当前工作区:[/]")
            print_session_list(self._sessions_for_display(sessions), current_id=self.current_session_id)
            if self.current_session_id:
                session = await self.os.get_session(self.current_session_id)
                if session:
                    print_success(f"已恢复工作区: {session.name or session.id}")
                await self._show_history("", title="恢复的最近对话", empty_ok=True)
        else:
            console.print("[dim]暂无工作区，输入 /new 创建新的法律工作区[/]")

        console.print()
        console.print("[dim]提示：直接输入自然语言对话；输入 /help 查看命令；运行中可输入 /status 或 /interrupt。[/]")
        console.print()

    async def _handle_command(self, cmd: str) -> bool:
        """处理命令"""
        cmd = cmd.strip()

        if not cmd:
            return True

        # 命令以 / 开头
        if cmd.startswith("/"):
            parts = cmd[1:].split(maxsplit=1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if command in ["quit", "exit", "q"]:
                return False

            elif command == "help":
                print_help()

            elif command == "new":
                await self._create_session(args)

            elif command == "switch":
                await self._switch_session(args)

            elif command == "resume":
                await self._resume_session(args)

            elif command == "sessions":
                await self._list_sessions()

            elif command == "status":
                await self._show_status()

            elif command == "history":
                await self._show_history(args)

            elif command == "todos":
                await self._show_todos()

            elif command == "reminders":
                await self._show_reminders()

            elif command == "close":
                await self._close_current_session()

            elif command == "delete":
                await self._delete_session(args)

            elif command == "compress":
                await self._compress_session()

            elif command == "subagent":
                await self._show_subagent(args)

            elif command == "tools":
                self._set_tool_log_mode(args)

            else:
                print_fail(f"未知命令: {command}，输入 /help 查看帮助")

        else:
            # 自然语言输入，交给 Agent 处理
            await self._chat(cmd)

        return True

    async def _create_session(self, name: str):
        """创建新会话"""
        if not name.strip():
            sessions = await self.os.list_sessions()
            next_num = len(sessions) + 1
            name = f"法律工作区 {next_num}"

        session = await self.os.create_session(name=name, workspace_profile="legal_case")
        self.current_session_id = session.id
        self._save_current_session()
        await self._update_session_index_map()
        print_success(f"创建工作区: {name}")
        await self._show_status()

    async def _switch_session(self, args: str):
        """切换会话"""
        await self._update_session_index_map()

        if not args:
            await self._list_sessions()
            return

        try:
            idx = int(args.strip())
            if idx in self.session_index_map:
                self.current_session_id = self.session_index_map[idx]
                self._save_current_session()
                session = await self.os.get_session(self.current_session_id)
                print_success(f"切换到: {session.name if session else 'Unknown'}")
                await self._show_status()
                await self._show_history("100", title="对话历史", empty_ok=True)
            else:
                print_fail(f"无效编号: {idx}")
        except ValueError:
            print_fail("请输入数字编号")

    async def _resume_session(self, args: str):
        """恢复工作区并显示最近对话。"""
        if args.strip():
            await self._switch_session(args)
        elif not self.current_session_id:
            await self._restore_session()

        if not self.current_session_id:
            print_fail("没有可恢复的工作区")
            return

        self._save_current_session()
        session = await self.os.get_session(self.current_session_id)
        print_success(f"已恢复工作区: {session.name if session else self.current_session_id}")
        await self._show_history("", title="恢复的最近对话", empty_ok=True)

    async def _list_sessions(self):
        """列出会话"""
        sessions = await self._ordered_sessions()
        if not sessions:
            print_info("暂无工作区")
            return

        console.print()
        console.print("[bold]工作区列表[/]")
        print_session_list(self._sessions_for_display(sessions), current_id=self.current_session_id)
        console.print("[dim]提示: 使用 /switch [编号] 切换工作区[/]")

    async def _show_status(self):
        """显示当前工作区摘要"""
        if not self.current_session_id:
            print_fail("请先选择或创建工作区")
            return

        session = await self.os.get_session(self.current_session_id)
        if not session:
            print_fail("当前工作区不存在")
            return

        reminders = await self.os.list_reminders(session_id=self.current_session_id, status="pending")
        artifacts = await self.os.list_artifacts(self.current_session_id)

        next_reminder_text = None
        if reminders:
            nr = reminders[0]
            fire = nr.get("fire_at_display") or nr.get("fire_at") or ""
            next_reminder_text = f"{nr.get('title')} @ {fire}"

        print_session_status(
            name=session.name or session.id,
            stage=session.stage,
            status=session.status,
            work_dir=session.work_dir,
            artifact_count=len(artifacts),
            todo_count=len(session.todo_list),
            reminder_count=len(reminders),
            next_reminder=next_reminder_text,
        )

    async def _show_history(self, args: str, *, title: str = "最近对话记录", empty_ok: bool = False):
        """显示当前工作区最近的对话记录"""
        if not self.current_session_id:
            print_fail("请先选择或创建工作区")
            return

        limit = 0
        if args.strip():
            try:
                limit = max(0, int(args.strip()))
            except ValueError:
                print_fail("请输入数字条数，例如 /history 20")
                return

        messages = await self.os.list_messages(self.current_session_id, limit=limit if limit else None, kinds=["chat"])
        if not messages:
            if not empty_ok:
                print_info("当前工作区暂无对话记录")
            return

        from rich.panel import Panel
        from rich.text import Text
        from rich import box

        console.print()
        console.print(f"[bold]{title}[/]")
        for item in messages:
            role = item.get("role") or ""
            if role == "user":
                title = "[bold bright_cyan]您[/]"
                border_style = "bright_cyan"
            elif role == "assistant":
                title = "[bold bright_green]Agent[/]"
                border_style = "bright_green"
            else:
                title = f"[bold]{role}[/]"
                border_style = "bright_black"

            timestamp = item.get("timestamp_display") or item.get("timestamp") or ""
            content = item.get("content") or ""
            if len(content) > 4000:
                content = content[:4000] + "\n\n...（已截断）"
            body = Text()
            if timestamp:
                body.append(f"{timestamp}\n", style="dim")
            body.append(content)
            console.print(Panel(
                body,
                title=title,
                title_align="left",
                border_style=border_style,
                box=box.ROUNDED,
                padding=(1, 2),
            ))

    async def _submit_intervention(self, content: str):
        """已废弃，使用新注入系统代替。"""
        pass

    async def _show_interventions(self):
        """已废弃。"""
        pass

    async def _show_todos(self):
        """显示待办事项"""
        if not self.current_session_id:
            print_fail("请先选择或创建工作区")
            return

        session = await self.os.get_session(self.current_session_id)
        if not session or not session.todo_list:
            print_info("暂无待办事项")
            return

        from rich.table import Table
        from rich import box
        table = Table(
            box=box.SIMPLE_HEAD,
            header_style="bold bright_black",
            show_edge=False,
            padding=(0, 1),
        )
        table.add_column("状态", width=4)
        table.add_column("优先级", width=4)
        table.add_column("内容")
        for todo in session.todo_list:
            status = "[green]✓[/]" if todo.get("completed") else "[dim]○[/]"
            p = todo.get("priority", 3)
            priority = "[red]🔴[/]" if p == 1 else "[yellow]🟠[/]" if p == 2 else "[blue]🔵[/]"
            table.add_row(status, priority, todo.get("content", ""))
        console.print()
        console.print("[bold]待办事项[/]")
        console.print(table)

    async def _show_reminders(self):
        """显示当前工作区提醒"""
        if not self.current_session_id:
            print_fail("请先选择或创建工作区")
            return

        reminders = await self.os.list_reminders(session_id=self.current_session_id)
        if not reminders:
            print_info("暂无提醒")
            return

        from rich.table import Table
        from rich import box
        table = Table(box=box.SIMPLE_HEAD, header_style="bold bright_black", show_edge=False, padding=(0, 1))
        table.add_column("状态", width=10)
        table.add_column("标题")
        table.add_column("触发时间", width=20)
        for r in reminders:
            fire = r.get("fire_at_display") or r.get("fire_at") or ""
            status_style = "green" if r.get("status") == "fired" else "yellow"
            table.add_row(f"[{status_style}]{r.get('status')}[/]", r.get("title", ""), f"[dim]{fire}[/]")
        console.print()
        console.print("[bold]提醒列表[/]")
        console.print(table)

    async def _close_current_session(self):
        """关闭当前会话"""
        if not self.current_session_id:
            print_fail("当前没有选中的工作区")
            return

        session = await self.os.get_session(self.current_session_id)
        if session:
            await self.os.close_session(self.current_session_id)
            print_success(f"关闭工作区: {session.name}")

        self._clear_current_session()
        await self._update_session_index_map()

    async def _delete_session(self, args: str):
        """删除会话"""
        await self._update_session_index_map()

        if not args:
            await self._list_sessions()
            console.print("\n[dim]提示: 使用 /delete <编号> 删除指定工作区[/]")
            return

        try:
            idx = int(args.strip())
            if idx not in self.session_index_map:
                print_fail(f"无效编号: {idx}")
                return

            session_id = self.session_index_map[idx]
            session = await self.os.get_session(session_id)

            if not session:
                print_fail("工作区不存在")
                return

            print_warning(f"即将永久删除工作区 '{session.name}'")
            print_info("此操作将删除所有相关数据（包括文件、数据库记录等）")
            print_info("该操作不可恢复！")

            success = await self.os.delete_session(session_id)
            if success:
                print_success(f"已删除工作区: {session.name}")
                if self.current_session_id == session_id:
                    self._clear_current_session()
                await self._update_session_index_map()
            else:
                print_fail("删除失败")

        except ValueError:
            print_fail("请输入数字编号")

    async def _compress_session(self):
        """手动压缩上下文"""
        if not self.current_session_id:
            print_fail("请先选择或创建工作区")
            return
        print_info("正在压缩上下文...")
        new_sid = await self.os.compress_session(self.current_session_id)
        if new_sid:
            self.current_session_id = new_sid
            self._save_current_session()
            print_success(f"上下文已压缩，切换到新 session: {new_sid[:8]}")
        else:
            print_fail("上下文不足，无法压缩")

    def _set_tool_log_mode(self, args: str) -> None:
        """Configure tool display verbosity for the current CLI process."""
        mode = args.strip().lower()
        aliases = {
            "": self._tool_log_mode,
            "on": "normal",
            "normal": "normal",
            "off": "off",
            "hide": "off",
            "verbose": "verbose",
            "debug": "verbose",
        }
        if mode not in aliases:
            print_fail("用法: /tools [on|off|normal|verbose]")
            return
        self._tool_log_mode = aliases[mode]
        print_success(f"工具显示模式: {self._tool_log_mode}")

    async def _chat(self, message: str):
        """与 Agent 对话"""
        session = await self.os.get_session(self.current_session_id) if self.current_session_id else None
        if self.current_session_id and not session:
            print_fail(f"当前工作区已不存在，请重新创建或切换（使用 /new 或 /switch）")
            self._clear_current_session()
            return

        # Truncated user input echo
        echo_user_input(message)

        # Default to a stable transcript-style CLI. Rich Live can be enabled
        # explicitly for terminals where animated status rendering is desired.
        from agent_os.config import Settings
        settings = Settings.from_env()
        live_enabled = self._live_dashboard_enabled()
        if live_enabled:
            self._dashboard = RunDashboard(console, compress_threshold=settings.context_token_threshold,
                                           show_cache=True)
            self._dashboard.start()
        else:
            self._dashboard = None
        self._thinking_buffer = []
        self._thinking_partial = ""
        self._thinking_started = False
        self._content_buffer = []
        self._content_stream_started = False
        self._content_stream_open = False
        self._run_log_path = await self._open_run_log(message)
        self._last_stream_progress_at = datetime.now()
        self._thinking_chars = 0
        self._content_chars = 0
        self._run_started_at = datetime.now()
        self._current_iteration = None
        self._last_model_usage = {}
        self._last_model_latency_ms = None

        try:
            await self._run_chat_with_injection(message)
        except Exception as e:
            exc_type = type(e).__name__
            print_error(f"[{exc_type}] {e}")
        finally:
            if self._dashboard:
                self._dashboard.stop()
                self._dashboard = None
            self._finish_content_stream()
            self._flush_stream_buffer()
            if self._run_log_path:
                print_info(f"运行日志: {self._run_log_path}")
            print_success("本轮完成，可继续输入")

    async def _run_chat_with_injection(self, message: str) -> None:
        assert self.os is not None
        if self.current_session_id is None:
            session = await self.os.create_session(name="default", stage="intake")
            self.current_session_id = session.id
            self._save_current_session()
            print_success(f"已自动创建新工作区: {session.name} ({session.id[:8]})")

        if not self._is_interactive():
            async def poll_subagents():
                last_status = ""
                while True:
                    await asyncio.sleep(5)
                    status = await self._read_subagent_status()
                    if status and status != last_status:
                        last_status = status
                        print_info(f"子Agent进展: {status}")
                    if self._poll_exit_event.is_set():
                        break
            self._poll_exit_event = asyncio.Event()
            poller = asyncio.create_task(poll_subagents())
            try:
                async for chunk in self.os.chat(self.current_session_id, message):
                    self._write_run_log(chunk)
                    await self._render_chat_chunk(chunk)
            finally:
                self._poll_exit_event.set()
                await poller
            return

        last_event_at = datetime.now()
        done = asyncio.Event()
        self._exit_after_current_run = False

        async def consume_chat() -> None:
            nonlocal last_event_at
            async for chunk in self.os.chat(self.current_session_id, message):
                last_event_at = datetime.now()
                self._last_stream_progress_at = last_event_at
                self._write_run_log(chunk)
                await self._render_chat_chunk(chunk)
            done.set()

        async def heartbeat() -> None:
            last_sub_status = ""
            last_idle_notice_at: Optional[datetime] = None
            while not done.is_set():
                await asyncio.sleep(5)
                if done.is_set():
                    return
                idle_s = (datetime.now() - last_event_at).total_seconds()
                should_print_idle = idle_s >= 8 and (
                    last_idle_notice_at is None
                    or (datetime.now() - last_idle_notice_at).total_seconds() >= 15
                )
                if should_print_idle:
                    last_idle_notice_at = datetime.now()
                    print_info(f"仍在运行，已等待 {idle_s:.0f}s；输入 /status 查看状态，/interrupt 中断，或直接输入文字注入。")
                if self._content_stream_open and self._last_stream_progress_at is not None:
                    writing_s = (datetime.now() - self._last_stream_progress_at).total_seconds()
                    if writing_s >= 10 and should_print_idle:
                        print_info(f"仍在持续生成输出，最近{writing_s:.0f}s无新增；可继续等待或 /interrupt。")
                status = await self._read_subagent_status()
                if status and status != last_sub_status:
                    last_sub_status = status
                    print_info(f"子Agent进展: {status}")

        async def handle_running_input(injected: str) -> None:
            if not injected:
                print_info("仍在运行；输入 /status、/subagent、/interrupt，或直接输入文字注入。")
                return
            self._append_readline_history(injected)
            if injected.startswith("/"):
                parts = injected[1:].split(maxsplit=1)
                command = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ""
                if command in {"interrupt", "stop"}:
                    self.os.request_interrupt(self.current_session_id)
                    print_warning("已请求中断当前任务")
                elif command == "status":
                    print_info(self._format_running_status(last_event_at))
                    status = await self._read_subagent_status()
                    if status:
                        print_info(f"子Agent进展: {status}")
                elif command == "subagent":
                    await self._show_subagent(args)
                elif command == "help":
                    print_help()
                elif command == "tools":
                    self._set_tool_log_mode(args)
                elif command in {"q", "quit", "exit"}:
                    self._exit_after_current_run = True
                    self.os.request_interrupt(self.current_session_id)
                    print_warning("已请求中断当前任务；本轮结束后退出")
                else:
                    print_fail(f"运行中不支持命令: /{command}；输入 /help 查看可用命令")
                return
            self.os.inject_message(self.current_session_id, injected)
            self._write_run_log({"type": "user.injected", "content": injected})
            print_success(f"已注入并排队给下一轮模型/工具边界: {injected[:80]}")

        async def _interaction_loop() -> None:
            chat_task = asyncio.create_task(consume_chat())
            heartbeat_task = asyncio.create_task(heartbeat())
            console.print("[dim]运行中：可输入文字注入；/status 查看状态，/subagent 查看子Agent，/interrupt 中断，/q 中断后退出。[/]")
            input_task: asyncio.Task[str] | None = asyncio.create_task(self._prompt_injection())
            try:
                while not chat_task.done():
                    tasks = {chat_task}
                    if input_task is not None:
                        tasks.add(input_task)
                    done_tasks, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                    if chat_task in done_tasks:
                        break

                    if input_task in done_tasks:
                        try:
                            injected = input_task.result().strip()
                        except (EOFError, KeyboardInterrupt):
                            injected = "/interrupt"
                        input_task = None
                        await handle_running_input(injected)
                        if not chat_task.done():
                            input_task = asyncio.create_task(self._prompt_injection())

                await chat_task
            finally:
                done.set()
                heartbeat_task.cancel()
                if input_task is not None and not input_task.done():
                    input_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                if input_task is not None:
                    try:
                        await input_task
                    except (asyncio.CancelledError, EOFError, KeyboardInterrupt):
                        pass

        try:
            if self._use_prompt_toolkit:
                with patch_stdout():
                    await _interaction_loop()
            else:
                await _interaction_loop()
        except KeyboardInterrupt:
            self.os.request_interrupt(self.current_session_id)
            print_warning("收到 Ctrl+C，已请求中断当前任务")
        finally:
            self._restore_tty()

    async def _async_stdin_input(self, prompt: str) -> str:
        self._ensure_cooked_tty()
        sys.stdout.write(prompt)
        sys.stdout.flush()
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str] = loop.create_future()

        def _on_read() -> None:
            try:
                line = sys.stdin.readline()
            except Exception:
                line = ""
            if not future.done():
                future.set_result(line.rstrip("\n") if line else "")
            try:
                loop.remove_reader(sys.stdin.fileno())
            except Exception:
                pass

        loop.add_reader(sys.stdin.fileno(), _on_read)
        try:
            return await future
        except asyncio.CancelledError:
            try:
                loop.remove_reader(sys.stdin.fileno())
            except Exception:
                pass
            raise

    async def _prompt_injection_no_patch(self) -> str:
        assert self.prompt_session is not None
        return await self.prompt_session.prompt_async("AgentOS 运行中> ")

    async def _prompt_injection(self) -> str:
        if self._use_prompt_toolkit:
            assert self.prompt_session is not None
            return await self.prompt_session.prompt_async("AgentOS 运行中> ")
        return await self._async_stdin_input("AgentOS 运行中> ")

    async def _main_stdin_input(self, prompt: str) -> str:
        """Main prompt input via readline-friendly blocking input in a worker thread."""
        self._ensure_cooked_tty()
        return await asyncio.to_thread(input, prompt)

    def _is_interactive(self) -> bool:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())

    def _format_running_status(self, last_event_at: Optional[datetime] = None) -> str:
        now = datetime.now()
        elapsed = (now - self._run_started_at).total_seconds() if self._run_started_at else 0
        idle = (now - last_event_at).total_seconds() if last_event_at else 0
        parts = [
            f"运行中: 已耗时 {elapsed:.0f}s",
            f"最近事件 {idle:.0f}s 前",
        ]
        if self._current_iteration is not None:
            parts.append(f"第 {self._current_iteration} 轮")
        if self._last_model_latency_ms is not None:
            parts.append(f"上轮模型 {self._last_model_latency_ms / 1000:.1f}s")
        usage = self._last_model_usage or {}
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        cached_tokens = usage.get("cached_tokens", 0) or 0
        if total_tokens:
            cache_rate = round(cached_tokens / prompt_tokens * 100, 1) if prompt_tokens else 0
            parts.append(f"tokens {total_tokens:,} / cache {cache_rate}%")
        parts.append(f"思考 {self._thinking_chars:,} 字符")
        parts.append(f"输出 {self._content_chars:,} 字符")
        return "，".join(parts) + "。"

    @staticmethod
    def _flush_stdin() -> None:
        try:
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass

    async def _show_subagent(self, sub_id: str) -> None:
        if not self.current_session_id or not self.os:
            print_fail("请先选择工作区")
            return
        session = await self.os.get_session(self.current_session_id)
        if not session or not session.work_dir:
            return
        agents_dir = Path(session.work_dir) / "raw_search" / "subagents"
        if not agents_dir.exists() or not any(agents_dir.iterdir()):
            print_info("当前工作区没有子 Agent 记录")
            return
        if sub_id:
            target = agents_dir / sub_id
            if not target.exists():
                print_fail(f"未找到子 Agent: {sub_id}")
                return
            status_file = target / "_status.jsonl"
            if not status_file.exists():
                print_fail(f"子 Agent {sub_id} 没有状态记录")
                return
            from rich.table import Table
            from rich import box
            from rich.panel import Panel
            table = Table(box=box.SIMPLE, show_edge=False, padding=(0, 2))
            table.add_column("轮次", style="bold", width=6)
            table.add_column("工具", style="bright_yellow")
            table.add_column("思考摘要", style="dim")
            try:
                with status_file.open("r", encoding="utf-8") as f:
                    for row in f:
                        row = row.strip()
                        if not row:
                            continue
                        data = json.loads(row)
                        it = data.get("iteration", "?")
                        tools = ", ".join(data.get("tool_names", [])) or "[empty]"
                        think = data.get("thinking", "")[:100] or "-"
                        table.add_row(str(it), tools, think)
            except (OSError, json.JSONDecodeError) as e:
                print_fail(f"读取状态文件失败: {e}")
                return
            console.print()
            console.print(f"[bold]子 Agent: {sub_id}[/]")
            console.print(Panel(table, border_style="bright_black"))
            console.print()
            return
        from rich.table import Table
        from rich import box
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 2))
        table.add_column("ID", style="bold", width=14)
        table.add_column("轮次", width=5)
        table.add_column("当前工具", style="bright_yellow")
        table.add_column("最新摘要", style="dim")
        for entry in sorted(agents_dir.iterdir()):
            if not entry.is_dir():
                continue
            status_file = entry / "_status.jsonl"
            if not status_file.exists():
                table.add_row(entry.name, "-", "-", "无状态")
                continue
            try:
                with status_file.open("r", encoding="utf-8") as f:
                    latest = None
                    for row in f:
                        row = row.strip()
                        if row:
                            latest = json.loads(row)
            except (OSError, json.JSONDecodeError):
                table.add_row(entry.name, "?", "?", "读取失败")
                continue
            if not latest:
                table.add_row(entry.name, "-", "-", "空")
                continue
            it = str(latest.get("iteration", "?"))
            tools = ", ".join(latest.get("tool_names", [])) or "-"
            think = latest.get("thinking", "")[:80] or "-"
            table.add_row(entry.name, it, tools, think)
        console.print()
        console.print("[bold]子 Agent 列表[/]")
        console.print(table)
        console.print("[dim]使用 /subagent <id> 查看详细轨迹[/]")
        console.print()

    async def _read_subagent_status(self) -> str:
        if not self.current_session_id or not self.os:
            return ""
        session = await self.os.get_session(self.current_session_id)
        if not session or not session.work_dir:
            return ""
        agents_dir = Path(session.work_dir) / "raw_search" / "subagents"
        if not agents_dir.exists():
            return ""
        lines: list[str] = []
        for entry in sorted(agents_dir.iterdir()):
            if not entry.is_dir():
                continue
            status_file = entry / "_status.jsonl"
            if not status_file.exists():
                continue
            try:
                with status_file.open("r", encoding="utf-8") as f:
                    latest = None
                    for row in f:
                        row = row.strip()
                        if row:
                            latest = json.loads(row)
            except (OSError, json.JSONDecodeError):
                continue
            if not latest:
                continue
            ts = latest.get("ts", "")[-5:]
            tools = latest.get("tool_names", [])
            think = latest.get("thinking", "")[:60]
            tool_str = ", ".join(tools) if tools else "思考中"
            lines.append(f"{entry.name}(轮{latest.get('iteration',0)}): {tool_str}")
        return " | ".join(lines) if lines else ""

    async def _open_run_log(self, message: str) -> Optional[Path]:
        if not self.current_session_id or not self.os:
            return None
        session = await self.os.get_session(self.current_session_id)
        if not session:
            return None
        log_dir = Path(session.work_dir) / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / "run_events.jsonl"
            self._run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "run_id": self._run_id,
                    "event": {
                        "type": "run.input",
                        "session_id": self.current_session_id,
                        "message": self._clean_surrogates(message),
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    },
                }, ensure_ascii=False) + "\n")
            return path
        except (OSError, UnicodeEncodeError):
            return None

    def _write_run_log(self, chunk: Dict[str, Any]) -> None:
        if not self._run_log_path:
            return
        event_type = chunk.get("type", "")
        if event_type in ("thinking_stream", "content_stream"):
            self._append_stream_buffer(event_type, str(chunk.get("content", "")))
            return
        self._flush_stream_buffer()
        self._emit_log_line(chunk)

    def _append_stream_buffer(self, event_type: str, content: str) -> None:
        if self._stream_log_type and self._stream_log_type != event_type:
            self._flush_stream_buffer()
        self._stream_log_type = event_type
        self._stream_log_buf += content
        if len(self._stream_log_buf) >= 200:
            self._flush_stream_buffer()

    def _flush_stream_buffer(self) -> None:
        if not self._stream_log_buf:
            return
        self._emit_log_line({"type": self._stream_log_type, "content": self._stream_log_buf})
        self._stream_log_buf = ""
        self._stream_log_type = ""

    def _emit_log_line(self, event: Dict[str, Any]) -> None:
        try:
            with self._run_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "run_id": self._run_id,
                    "event": self._sanitize_for_json(event),
                }, ensure_ascii=False, default=str) + "\n")
        except (OSError, UnicodeEncodeError):
            self._run_log_path = None

    _SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

    @classmethod
    def _clean_surrogates(cls, value: str) -> str:
        if not value:
            return value
        if cls._SURROGATE_RE.search(value):
            return cls._SURROGATE_RE.sub("\ufffd", value)
        return value

    @classmethod
    def _sanitize_for_json(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._clean_surrogates(value)
        if isinstance(value, list):
            return [cls._sanitize_for_json(item) for item in value]
        if isinstance(value, dict):
            return {k: cls._sanitize_for_json(v) for k, v in value.items()}
        return value

    def _stop_dashboard(self) -> None:
        if self._dashboard and self._dashboard.live is not None:
            self._dashboard.stop()

    def _record_stream_chunk(self, kind: str, content: str) -> None:
        if not content:
            return
        content = self._clean_surrogates(content)
        if kind == "thinking":
            self._thinking_buffer.append(content)
            self._print_thinking_stream(content)
        elif kind == "content":
            self._content_buffer.append(content)
            self._print_content_stream(content)

    def _print_thinking_stream(self, content: str) -> None:
        self._stop_dashboard()
        if not self._thinking_started:
            console.print("  [dim]💭 思考中[/]")
            self._thinking_started = True
        self._thinking_chars += len(content)

        pending = self._thinking_partial + content
        self._thinking_partial = ""
        for part in pending.splitlines(keepends=True):
            if part.endswith(("\n", "\r")):
                line = part.rstrip("\r\n")
                if line.strip():
                    console.print(Text(f"    {line}", style="dim"))
            else:
                self._thinking_partial = part

    def _flush_thinking_stream(self) -> None:
        if self._thinking_partial.strip():
            console.print(Text(f"    {self._thinking_partial}", style="dim"))
        self._thinking_partial = ""

    def _print_content_stream(self, content: str) -> None:
        self._stop_dashboard()
        content = self._clean_surrogates(content)
        if not self._content_stream_started:
            console.print()
            console.print("[bold bright_green]🤖 Agent 输出中[/]")
            self._content_stream_started = True
        self._content_stream_open = True
        self._content_chars += len(content)
        console.file.write(content)
        console.file.flush()

    def _finish_content_stream(self) -> None:
        if self._content_stream_open:
            console.file.write("\n")
            console.file.flush()
            self._content_stream_open = False

    async def _render_chat_chunk(self, chunk: Dict[str, Any]):
        msg_type = chunk.get("type")

        if msg_type == "thinking_stream":
            self._record_stream_chunk("thinking", chunk.get("content", ""))

        elif msg_type == "content_stream":
            self._flush_thinking_stream()
            self._record_stream_chunk("content", chunk.get("content", ""))

        elif msg_type == "thinking":
            # Full thinking block (non-streaming fallback)
            self._flush_thinking_stream()
            content = (chunk.get("content") or "").strip()
            if content:
                self._stop_dashboard()
                print_thinking(content)

        elif msg_type == "activity":
            self._flush_thinking_stream()
            self._finish_content_stream()
            phase = chunk.get("phase") or ""
            detail = chunk.get("detail") or ""
            payload = chunk.get("payload") or {}
            if payload.get("iteration") is not None:
                self._current_iteration = payload.get("iteration")
            if phase == "model.completed":
                self._last_model_usage = payload.get("usage") or {}
                self._last_model_latency_ms = payload.get("latency_ms")
            print_activity(phase, detail)

            # Update dashboard
            if self._dashboard:
                usage = payload.get("usage") or {}
                tokens = usage.get("total_tokens")
                await self._dashboard.update(
                    iteration=payload.get("iteration"),
                    phase=phase,
                    tokens=tokens,
                )
                await self._dashboard.add_token_usage(usage)

        elif msg_type == "intervention":
            self._flush_thinking_stream()
            self._finish_content_stream()
            self._stop_dashboard()
            print_intervention(chunk.get("content", ""))

        elif msg_type == "tool_call":
            self._flush_thinking_stream()
            self._finish_content_stream()
            summary = chunk.get("summary") or ""
            if self._tool_log_mode != "off":
                print_tool_call(chunk.get("name", ""), summary, verbose=self._tool_log_mode == "verbose")

            # Update dashboard tool count on call (not on result)
            if self._dashboard:
                await self._dashboard.update(tool_count=self._dashboard.tool_count + 1)

        elif msg_type == "tool_result":
            self._flush_thinking_stream()
            self._finish_content_stream()
            result = chunk.get("result", {})
            latency = result.get("latency_ms")
            success = result.get("success", False)
            summary = result.get("summary") or ""
            if self._tool_log_mode != "off":
                print_tool_result(success, latency, summary, verbose=self._tool_log_mode == "verbose")
            if result.get("tool") == "todowrite":
                data = result.get("data") or {}
                todos = data.get("todos") or []
                if todos:
                    print_todo_summary(todos)

            # Track sub-agent spawn
            if result.get("tool") == "spawn" and self._dashboard:
                data = result.get("data") or {}
                sub_id = data.get("sub_task_id", "")
                task_desc = data.get("task", "子任务")
                await self._dashboard.tracker.add(sub_id, task_desc)
                # Mark as completed immediately since spawn returns synchronously
                await self._dashboard.tracker.complete(sub_id)

        elif msg_type == "content":
            self._flush_thinking_stream()
            self._stop_dashboard()
            content = chunk.get("content", "") or "".join(self._content_buffer)
            if self._content_stream_started:
                self._finish_content_stream()
            else:
                print_agent_response(content)

        elif msg_type == "session.compressed":
            self._flush_thinking_stream()
            self._finish_content_stream()
            self._stop_dashboard()
            print_compression_event(
                chunk.get("old_session_id", ""),
                chunk.get("new_session_id", ""),
                chunk.get("estimated_tokens_before", 0),
                chunk.get("summary", ""),
            )
            # Update current session ID for subsequent commands
            self.current_session_id = chunk.get("new_session_id", self.current_session_id)
            self._save_current_session()

        elif msg_type == "error":
            self._flush_thinking_stream()
            self._finish_content_stream()
            self._stop_dashboard()
            error = chunk.get("error", "")
            payload = chunk.get("payload") or {}
            snapshot_path = payload.get("snapshot_path")
            if snapshot_path:
                error = f"{error}\n\nprovider failure snapshot: {snapshot_path}"
            print_error(error)

    async def run(self):
        """运行交互式界面"""
        await self._show_welcome()

        if not sys.stdin.isatty():
            for raw in sys.stdin:
                cmd = raw.rstrip("\n")
                if not cmd.strip():
                    continue
                console.print(f"AgentOS {self._get_session_display()}> {cmd}")
                should_continue = await self._handle_command(cmd)
                if not should_continue:
                    break
            console.print("\n[dim]再见！[/]")
            return

        while True:
            try:
                # 显示提示符
                session_info = self._get_session_display()
                prompt_text = f"AgentOS {session_info}> "

                # 获取输入
                if self._use_prompt_toolkit:
                    with patch_stdout():
                        cmd = await self.prompt_session.prompt_async(prompt_text)
                else:
                    cmd = await self._main_stdin_input(prompt_text)
                self._append_readline_history(cmd)

                # 处理命令
                should_continue = await self._handle_command(cmd)
                if not should_continue:
                    break
                if self._exit_after_current_run:
                    break

            except KeyboardInterrupt:
                console.print()
                continue
            except EOFError:
                break
            except Exception as e:
                print_error(str(e))

        console.print("\n[dim]再见！[/]")


async def main():
    """主函数"""
    cli = AgentOSCLI()

    try:
        await cli.init()
        await cli.run()
    finally:
        await cli.close()


if __name__ == "__main__":
    asyncio.run(main())
