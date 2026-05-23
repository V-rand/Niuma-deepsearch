from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_memory_content_is_truncated_user_context_not_system_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="memory-kv")
        long_memory = "\n".join(f"- memory line {index}" for index in range(260))
        Path(session.work_dir, "MEMORY.md").write_text(long_memory, encoding="utf-8")

        _, messages, system_prompt, _ = await osys.agent_loop._get_or_init_session_context(session.id, "hello")

        assert "memory line 0" not in system_prompt
        memory_messages = [
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "user" and str(message.get("content", "")).startswith("<memory>")
        ]
        assert len(memory_messages) == 1
        assert "memory line 0" in memory_messages[0]
        assert "memory line 199" in memory_messages[0]
        assert "memory line 220" not in memory_messages[0]
        assert "MEMORY.md 超过限制" in memory_messages[0]
    finally:
        monkeypatch.setattr(osys.workspace_memory, "drain_background_tasks", _noop_async)
        await osys.stop()


@pytest.mark.asyncio
async def test_memory_update_refreshes_dynamic_block_without_dirtying_system_context(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="memory-refresh")
        _, messages, system_prompt, _ = await osys.agent_loop._get_or_init_session_context(session.id, "hello")
        assert "ORIGINAL_MEMORY_MARKER" not in "\n".join(str(message.get("content", "")) for message in messages)

        result = await osys.agent_loop._execute_tool(
            {
                "name": "file_write",
                "arguments": {
                    "path": "MEMORY.md",
                    "content": "# Memory\n\nORIGINAL_MEMORY_MARKER\n",
                },
                "id": "call_memory",
            },
            session.id,
        )

        assert result.success
        assert session.id not in osys.agent_loop._context_dirty_sessions
        assert session.id in osys.agent_loop._memory_dirty_sessions

        _, refreshed, refreshed_system_prompt, _ = await osys.agent_loop._get_or_init_session_context(session.id, "again")

        assert refreshed_system_prompt == system_prompt
        assert session.id not in osys.agent_loop._memory_dirty_sessions
        memory_blocks = [
            str(message.get("content", ""))
            for message in refreshed
            if message.get("role") == "user" and str(message.get("content", "")).startswith("<memory>")
        ]
        assert len(memory_blocks) == 1
        assert "ORIGINAL_MEMORY_MARKER" in memory_blocks[0]
    finally:
        monkeypatch.setattr(osys.workspace_memory, "drain_background_tasks", _noop_async)
        await osys.stop()


async def _noop_async() -> None:
    return None
