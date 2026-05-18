from types import SimpleNamespace

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

    async def fake_summary(content):
        return "<chronology><event>summary</event></chronology>"

    async def fake_request(request_kwargs, effective_timeout, session_id, iteration):
        captured_messages.extend(request_kwargs["messages"])
        return _FakeStream(), []

    try:
        session = await osys.create_session(name="compression", workspace_profile="legal_case")
        for index in range(12):
            await osys.sessions.add_message(session.id, "user", f"old user {index}", kind="chat")
            await osys.sessions.add_message(session.id, "assistant", f"old assistant {index}", kind="chat")

        osys.agent_loop._CONTEXT_TOKEN_THRESHOLD = 1
        osys.agent_loop._COMPRESS_HEAD_TURNS = 1
        osys.agent_loop._COMPRESS_TAIL_TURNS = 1
        monkeypatch.setattr(osys.agent_loop, "_generate_compression_summary", fake_summary)
        monkeypatch.setattr(osys.agent_loop, "_request_model_with_retry", fake_request)

        current_message = "CURRENT_COMPRESSION_MESSAGE"
        events = []
        async for event in osys.chat(session.id, current_message, max_iterations=1):
            events.append(event)

        assert any(event.get("type") == "session.compressed" for event in events)
        assert [
            message.get("content")
            for message in captured_messages
            if message.get("role") == "user" and message.get("content") == current_message
        ] == [current_message]
    finally:
        await osys.stop()
