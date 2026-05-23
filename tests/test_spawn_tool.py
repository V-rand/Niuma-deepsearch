import pytest


@pytest.mark.asyncio
async def test_spawn_rejects_non_list_tools_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        spawn = osys.tool_registry.get_entry("spawn")

        result = await spawn.handler(task="research", tools="web_search")

        assert not result.success
        assert "tools must be a list" in result.error
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_spawn_explore_rejects_tools_outside_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        spawn = osys.tool_registry.get_entry("spawn")

        result = await spawn.handler(task="research", subagent_type="explore", tools=["bash"])

        assert not result.success
        assert "not allowed for explore" in result.error
    finally:
        await osys.stop()
