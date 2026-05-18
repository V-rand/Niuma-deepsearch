from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_live_dashboard_is_opt_in(monkeypatch):
    from cli import AgentOSCLI

    monkeypatch.delenv("AGENT_OS_CLI_LIVE", raising=False)
    assert AgentOSCLI._live_dashboard_enabled() is False

    monkeypatch.setenv("AGENT_OS_CLI_LIVE", "1")
    assert AgentOSCLI._live_dashboard_enabled() is True

    monkeypatch.setenv("AGENT_OS_CLI_LIVE", "false")
    assert AgentOSCLI._live_dashboard_enabled() is False


def test_help_only_lists_supported_display_commands(monkeypatch):
    from agent_os.kernel import display

    capture = Console(record=True, width=140)
    monkeypatch.setattr(display, "console", capture)

    display.print_help()
    rendered = capture.export_text()

    assert "/subagent" in rendered
    assert "/interrupt" in rendered
    assert "/tools" in rendered
    assert "/events" not in rendered
    assert "/traces" not in rendered
    assert "/stagepack" not in rendered


def test_clearing_current_session_removes_saved_cli_state(tmp_path):
    from cli import AgentOSCLI

    cli = AgentOSCLI()
    cli._state_path = tmp_path / "cli_state.json"
    cli.current_session_id = "session_1"
    cli._save_current_session()
    assert cli._read_saved_session_id() == "session_1"

    cli._clear_current_session()

    assert cli.current_session_id is None
    assert cli._read_saved_session_id() is None


def test_todo_summary_renders_current_statuses(monkeypatch):
    from agent_os.kernel import display

    capture = Console(record=True, width=120)
    monkeypatch.setattr(display, "console", capture)

    display.print_todo_summary([
        {"content": "检索法规", "status": "completed", "priority": 1},
        {"content": "整理案例", "status": "in_progress", "priority": 2},
        {"content": "生成报告", "status": "pending", "priority": 3},
    ])
    rendered = capture.export_text()

    assert "Todo 更新" in rendered
    assert "检索法规" in rendered
    assert "整理案例" in rendered
    assert "生成报告" in rendered


def test_running_status_hides_phase_and_tools():
    from cli import AgentOSCLI
    from datetime import datetime, timedelta

    cli = AgentOSCLI()
    cli._run_started_at = datetime.now() - timedelta(seconds=12)
    cli._current_iteration = 3
    cli._last_model_latency_ms = 2150
    cli._last_model_usage = {"prompt_tokens": 1000, "total_tokens": 1250, "cached_tokens": 820}

    status = cli._format_running_status(datetime.now() - timedelta(seconds=4))

    assert "阶段" not in status
    assert "工具" not in status
    assert "已耗时" in status
    assert "tokens" in status


def test_surrogate_text_is_sanitized_for_display_and_logs():
    from cli import AgentOSCLI

    bad = "ok\ud800\udc00bad"
    cleaned = AgentOSCLI._clean_surrogates(bad)
    assert "\ud800" not in cleaned
    assert "\udc00" not in cleaned
    payload = AgentOSCLI._sanitize_for_json({"k": bad, "arr": [bad]})
    assert "\ud800" not in payload["k"]
    assert "\udc00" not in payload["arr"][0]


def test_content_filter_error_is_compacted_in_display(monkeypatch):
    from agent_os.kernel import display

    capture = Console(record=True, width=140)
    monkeypatch.setattr(display, "console", capture)
    detail = (
        "Provider content filter blocked the request . raw_message=Error code: 400 - "
        "{'error': {'message': 'Input data may contain inappropriate content.', 'code': 'data_inspection_failed'}, "
        "'request_id': 'abc-123'} body={'code': 'data_inspection_failed'}"
    )
    display.print_activity("run.failed", detail)
    rendered = capture.export_text()
    assert "Provider 内容审查拦截" in rendered
    assert "request_id=abc-123" in rendered
