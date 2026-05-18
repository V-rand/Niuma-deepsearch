from __future__ import annotations

import httpx
import sys
from pathlib import Path

import pytest
from openai import BadRequestError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_content_filter_error() -> BadRequestError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    body = {
        "code": "DataInspectionFailed",
        "message": "Input data may contain inappropriate content.",
    }
    return BadRequestError(
        "Error code: 400 - Input data may contain inappropriate content.",
        response=response,
        body=body,
    )


async def _noop_async() -> None:
    return None


class _ContentThenFilterStream:
    def __aiter__(self):
        async def gen():
            from types import SimpleNamespace

            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            reasoning_content=None,
                            content="partial output",
                            tool_calls=[],
                        )
                    )
                ],
            )
            raise _make_content_filter_error()

        return gen()


def test_format_exception_coalesces_provider_content_filter_error():
    from agent_os.kernel.helpers import format_exception

    detail = format_exception(_make_content_filter_error())

    assert "Provider content filter blocked the request [DataInspectionFailed]" in detail
    assert "raw_message=Error code: 400 - Input data may contain inappropriate content." in detail
    assert "body={'code': 'DataInspectionFailed', 'message': 'Input data may contain inappropriate content.'}" in detail


@pytest.mark.asyncio
async def test_agent_loop_process_emits_error_event_for_content_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="provider-error", workspace_profile="legal_case")

        async def _raise_content_filter(*args, **kwargs):
            raise _make_content_filter_error()

        monkeypatch.setattr(osys.agent_loop, "_request_model_with_retry", _raise_content_filter)

        events = [event async for event in osys.chat(session.id, "测试敏感内容处理")]

        assert any(
            event.get("type") == "activity" and event.get("phase") == "run.failed"
            for event in events
        )
        assert any(
            event.get("type") == "activity" and event.get("phase") == "context.recovered"
            for event in events
        )
        assert any(
            event.get("type") == "error"
            and "Provider content filter blocked the request [DataInspectionFailed]" in str(event.get("error", ""))
            for event in events
        )
        assert any(
            event.get("type") == "error"
            and "raw_message=" in str(event.get("error", ""))
            for event in events
        )
        assert any(
            event.get("type") == "activity"
            and event.get("phase") == "run.failed"
            and (event.get("payload") or {}).get("snapshot_path")
            for event in events
        )
        assert any(
            event.get("type") == "error"
            and (event.get("payload") or {}).get("snapshot_path")
            for event in events
        )
        messages = await osys.sessions.get_messages(session.id, limit=10, kinds=["chat"])
        assert all(item["content"] != "测试敏感内容处理" for item in messages)
        assert session.id in osys.agent_loop._content_filter_quarantine_sessions
        logs_dir = Path(session.work_dir) / "logs"
        snapshots = sorted(logs_dir.glob("provider_failure_*.json"))
        assert snapshots
        assert "DataInspectionFailed" in snapshots[-1].read_text(encoding="utf-8")
    finally:
        monkeypatch.setattr(osys.workspace_memory, "drain_background_tasks", _noop_async)
        await osys.stop()


@pytest.mark.asyncio
async def test_quarantine_context_skips_prior_chat_history(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="quarantine", workspace_profile="legal_case")
        await osys.sessions.add_message(session.id, "user", "历史敏感词", kind="chat")
        await osys.sessions.add_message(session.id, "assistant", "历史回复", kind="chat")
        await osys.sessions.add_message(session.id, "user", "系统提醒", kind="system")

        osys.agent_loop._content_filter_quarantine_sessions.add(session.id)
        _, messages, _, _ = await osys.agent_loop._get_or_init_session_context(session.id, "后续安全问题")

        contents = [str(message.get("content", "")) for message in messages]
        assert any("系统提醒" in content for content in contents)
        assert all("历史敏感词" not in content for content in contents)
        assert all("历史回复" not in content for content in contents)
    finally:
        monkeypatch.setattr(osys.workspace_memory, "drain_background_tasks", _noop_async)
        await osys.stop()


@pytest.mark.asyncio
async def test_quarantine_context_skips_prior_tool_history(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="quarantine-tool", workspace_profile="legal_case")
        await osys.sessions.add_message(
            session.id,
            "tool",
            "网络原文里有敏感词 TOKEN_SENSITIVE",
            kind="tool",
            metadata={"tool_call_id": "call_1"},
        )
        await osys.sessions.add_message(session.id, "user", "系统提醒", kind="system")

        osys.agent_loop._content_filter_quarantine_sessions.add(session.id)
        _, messages, _, _ = await osys.agent_loop._get_or_init_session_context(session.id, "后续安全问题")

        contents = [str(message.get("content", "")) for message in messages]
        assert any("系统提醒" in content for content in contents)
        assert all("TOKEN_SENSITIVE" not in content for content in contents)
    finally:
        monkeypatch.setattr(osys.workspace_memory, "drain_background_tasks", _noop_async)
        await osys.stop()


@pytest.mark.asyncio
async def test_stream_time_content_filter_recovers_session(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="stream-filter", workspace_profile="legal_case")

        async def _stream_then_filter(*args, **kwargs):
            return _ContentThenFilterStream(), []

        monkeypatch.setattr(osys.agent_loop, "_request_model_with_retry", _stream_then_filter)

        events = [event async for event in osys.chat(session.id, "测试流式敏感输出")]

        assert any(event.get("type") == "content_stream" for event in events)
        assert any(event.get("type") == "activity" and event.get("phase") == "context.recovered" for event in events)
        assert any(event.get("type") == "activity" and event.get("phase") == "run.failed" for event in events)
        assert any(
            event.get("type") == "error"
            and "Provider content filter blocked the request [DataInspectionFailed]" in str(event.get("error", ""))
            for event in events
        )
        messages = await osys.sessions.get_messages(session.id, limit=10, kinds=["chat"])
        assert all(item["content"] != "测试流式敏感输出" for item in messages)
        assert all(item["content"] != "partial output" for item in messages)
    finally:
        monkeypatch.setattr(osys.workspace_memory, "drain_background_tasks", _noop_async)
        await osys.stop()
