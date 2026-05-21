from types import SimpleNamespace
from pathlib import Path

import pytest


class _FakeStream:
    def __aiter__(self):
        async def gen():
            yield SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            reasoning_content=None,
                            content="done",
                            tool_calls=[],
                        )
                    )
                ],
            )

        return gen()


@pytest.mark.asyncio
async def test_compression_does_not_duplicate_current_user_message(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    captured_messages = []

    async def fake_summary(content, *, previous_summary=""):
        osys.agent_loop._CONTEXT_TOKEN_THRESHOLD = 999_999
        return "<chronology><event>summary</event></chronology>"

    async def fake_request(request_kwargs, effective_timeout, session_id, iteration):
        captured_messages.extend(request_kwargs["messages"])
        return _FakeStream(), []

    try:
        session = await osys.create_session(name="compression")
        for index in range(12):
            await osys.sessions.add_message(session.id, "user", f"old user {index}", kind="chat")
            await osys.sessions.add_message(session.id, "assistant", f"old assistant {index}", kind="chat")

        osys.agent_loop._CONTEXT_TOKEN_THRESHOLD = 1
        monkeypatch.setattr(osys.agent_loop, "_generate_compression_summary", fake_summary)
        monkeypatch.setattr(osys.agent_loop, "_request_model_with_retry", fake_request)

        current_message = "CURRENT_COMPRESSION_MESSAGE"
        events = []
        async for event in osys.chat(session.id, current_message, max_iterations=2):
            events.append(event)

        assert any(event.get("type") == "session.compressed" for event in events)
        assert [
            message.get("content")
            for message in captured_messages
            if message.get("role") == "user" and message.get("content") == current_message
        ] == [current_message]
        state_path = Path(session.work_dir) / "compression_state.md"
        assert state_path.exists()
        state_text = state_path.read_text(encoding="utf-8")
        assert "compression_version: 2" in state_text
        assert f"parent_session_id: {session.id}" in state_text
        assert "<chronology><event>summary</event></chronology>" in state_text
    finally:
        await osys.stop()
