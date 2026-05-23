import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_tui_run_selects_textual_only_for_tty():
    from tui.run import should_launch_tui

    assert should_launch_tui(argv=["agent-os-tui", "--tui-legacy"], stdin_isatty=True)
    assert not should_launch_tui(argv=["agent-os-tui", "--non-tty"], stdin_isatty=True)
    assert not should_launch_tui(argv=["agent-os-tui"], stdin_isatty=False)
    assert not should_launch_tui(argv=["agent-os-tui"], stdin_isatty=True)


@pytest.mark.asyncio
async def test_event_bridge_tracks_model_tool_and_streams():
    from tui.event_bridge import EventBridge, TuiRunMetrics

    class FakeWidgets:
        def __init__(self):
            self.chat = []
            self.tools = []
            self.status = []
            self.errors = []

        def write_chat(self, message, style=""):
            self.chat.append((message, style))

        def write_tool(self, message, style=""):
            self.tools.append((message, style))

        def update_status(self, metrics):
            self.status.append(metrics)

        def write_error(self, message):
            self.errors.append(message)

        def switch_session(self, session_id):
            self.session_id = session_id

    widgets = FakeWidgets()
    bridge = EventBridge(agent=None, widgets=widgets)

    await bridge.handle_chunk({"type": "thinking_stream", "content": "think"})
    await bridge.handle_chunk({"type": "content_stream", "content": "answer"})
    await bridge.handle_chunk({
        "type": "activity",
        "phase": "model.completed",
        "detail": "模型响应完成",
        "payload": {
            "latency_ms": 1200,
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cached_tokens": 80,
            },
        },
    })
    await bridge.handle_chunk({"type": "tool_call", "name": "workspace_search", "summary": "q=foo"})
    await bridge.handle_chunk({
        "type": "tool_result",
        "result": {
            "tool": "workspace_search",
            "success": True,
            "latency_ms": 300,
            "summary": "检索到 1 条结果",
        },
    })
    await bridge.handle_chunk({
        "type": "session.compressed",
        "old_session_id": "old-session",
        "new_session_id": "new-session",
        "estimated_tokens_before": 123,
    })
    await bridge.finish()

    assert "think" in "".join(message for message, _ in widgets.chat)
    assert "answer" in "".join(message for message, _ in widgets.chat)
    assert any("workspace_search" in message for message, _ in widgets.tools)
    assert isinstance(widgets.status[-1], TuiRunMetrics)
    assert widgets.status[-1].model_calls == 1
    assert widgets.status[-1].tool_calls == 1
    assert widgets.status[-1].model_latency_ms_total == 1200
    assert widgets.status[-1].tool_latency_ms_total == 300
    assert widgets.status[-1].cache_rate == 80.0
    assert widgets.session_id == "new-session"


def test_subagent_status_jsonl_contract(tmp_path):
    from agent_os.kernel.sub_agent import SubAgent

    sub = SubAgent(
        api_key="",
        base_url="http://example.invalid",
        model="test-model",
        request_timeout_seconds=1,
        parent_session_id="parent",
        allowed_tools=None,
        tool_registry=None,
        session_manager=None,
        workspace_memory=None,
        retriever=None,
        sub_agent_id="sub_1",
        sub_agent_work_dir=str(tmp_path),
    )

    sub._write_status(
        2,
        ["web_search"],
        "正在检索预重整资料",
        status="running",
        phase="tool.completed",
        latency_ms=345,
        error="",
    )

    status_file = Path(tmp_path) / "raw_search" / "subagents" / "sub_1" / "_status.jsonl"
    rows = [json.loads(line) for line in status_file.read_text(encoding="utf-8").splitlines()]

    assert rows[-1]["sub_agent_id"] == "sub_1"
    assert rows[-1]["status"] == "running"
    assert rows[-1]["phase"] == "tool.completed"
    assert rows[-1]["iteration"] == 2
    assert rows[-1]["tool_names"] == ["web_search"]
    assert rows[-1]["latency_ms"] == 345
    assert rows[-1]["error"] == ""


def test_subagent_status_reader_keeps_history(tmp_path):
    from tui.widgets.subagent_list import read_subagent_statuses

    status_dir = tmp_path / "raw_search" / "subagents" / "sub_1"
    status_dir.mkdir(parents=True)
    status_file = status_dir / "_status.jsonl"
    status_file.write_text(
        "\n".join([
            json.dumps({"sub_agent_id": "sub_1", "iteration": 1, "tool_names": ["law_retrieve"], "thinking": "a", "status": "running"}),
            json.dumps({"sub_agent_id": "sub_1", "iteration": 2, "tool_names": ["web_search"], "thinking": "b", "status": "completed"}),
        ]),
        encoding="utf-8",
    )

    statuses = read_subagent_statuses(str(tmp_path))

    assert len(statuses) == 1
    assert statuses[0]["id"] == "sub_1"
    assert statuses[0]["iteration"] == 2
    assert statuses[0]["status"] == "completed"
    assert [item["iteration"] for item in statuses[0]["history"]] == [1, 2]


@pytest.mark.asyncio
async def test_chat_screen_consumes_fake_agent_stream():
    from textual.app import App

    from tui.screen import ChatScreen

    class FakeAgent:
        def __init__(self):
            self.settings = SimpleNamespace(model="fake-model", context_token_threshold=1000)
            self.session = SimpleNamespace(
                id="s1",
                name="测试工作区",
                stage="intake",
                status="active",
                work_dir="/tmp/agent-os-no-subagents",
            )
            self.injected = []
            self.interrupted = False
            self.messages = []

        async def list_sessions(self):
            return [{"id": "s1", "name": "测试工作区", "stage": "intake", "status": "active", "created_at": "1"}]

        async def create_session(self, name=""):
            self.session.name = name
            return self.session

        async def get_session(self, session_id):
            return self.session

        def inject_message(self, session_id, message):
            self.injected.append(message)

        def request_interrupt(self, session_id):
            self.interrupted = True

        async def chat(self, session_id, message):
            self.messages.append(message)
            yield {
                "type": "activity",
                "phase": "model.completed",
                "detail": "模型响应完成",
                "payload": {
                    "latency_ms": 100,
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "cached_tokens": 7,
                    },
                    "iteration": 1,
                },
            }
            yield {"type": "content_stream", "content": "hello"}

    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(ChatScreen(FakeAgent()))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        assert type(app.screen).__name__ == "ChatScreen"
        await pilot.press("h", "i", "enter")
        await pilot.pause(0.5)
        screen = app.screen
        assert screen.agent.messages == ["hi"]


@pytest.mark.asyncio
async def test_chat_screen_uses_slash_commands_instead_of_keybindings():
    from textual.app import App

    from tui.screen import ChatScreen
    from tui.widgets.tool_log import ToolLog

    class FakeAgent:
        def __init__(self):
            self.settings = SimpleNamespace(model="fake-model", context_token_threshold=1000)
            self.session = SimpleNamespace(
                id="s1",
                name="测试工作区",
                stage="intake",
                status="active",
                work_dir="/tmp/agent-os-no-subagents",
            )
            self.interrupted = False

        async def list_sessions(self):
            return [{"id": "s1", "name": "测试工作区", "stage": "intake", "status": "active", "created_at": "1"}]

        async def create_session(self, name=""):
            return self.session

        async def get_session(self, session_id):
            return self.session

        def inject_message(self, session_id, message):
            pass

        def request_interrupt(self, session_id):
            self.interrupted = True

    class TestApp(App):
        async def on_mount(self):
            await self.push_screen(ChatScreen(FakeAgent()))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        screen = app.screen
        await pilot.press("/", "t", "o", "o", "l", "s", "enter")
        await pilot.pause(0.1)
        assert screen.query_one(ToolLog).has_class("expanded")

        await pilot.press("/", "h", "i", "d", "e", "-", "t", "o", "o", "l", "s", "enter")
        await pilot.pause(0.1)
        assert not screen.query_one(ToolLog).has_class("expanded")

        screen._chat_running = True
        await pilot.press("/", "i", "n", "t", "e", "r", "r", "u", "p", "t", "enter")
        await pilot.pause(0.1)
        assert screen.agent.interrupted
