import pytest


@pytest.mark.asyncio
async def test_workspace_search_uses_shared_work_dir_after_fork(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        parent = await osys.create_session(name="parent")
        await osys.write_artifact(
            parent.id,
            path="research/parent.md",
            content="Parent UNIQUE_CHAIN_TOKEN_12345",
            artifact_type="research",
        )
        child = await osys.sessions.fork_session(parent.id)

        results = await osys.workspace_search(child.id, "UNIQUE_CHAIN_TOKEN_12345", limit=5)

        assert child is not None
        assert child.work_dir == parent.work_dir
        assert [item["path"] for item in results] == ["research/parent.md"]
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_artifact_access_uses_shared_work_dir_after_fork(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        parent = await osys.create_session(name="parent")
        await osys.write_artifact(
            parent.id,
            path="research/parent.md",
            content="Parent artifact",
            artifact_type="research",
        )
        child = await osys.sessions.fork_session(parent.id)

        child_artifacts = await osys.list_artifacts(child.id)
        child_artifact = await osys.read_artifact(child.id, "research/parent.md")

        assert child is not None
        assert child.work_dir == parent.work_dir
        assert "research/parent.md" in [item["path"] for item in child_artifacts]
        assert child_artifact is not None
        assert child_artifact["content"] == "Parent artifact"
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_workspace_search_handles_chinese_multi_term_queries(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="search")
        await osys.write_artifact(
            session.id,
            path="research/search.md",
            content="本案涉及预重整程序，并且监管账户为预售资金监管账户。",
            artifact_type="research",
        )

        results = await osys.workspace_search(session.id, "预重整 预售资金监管账户", limit=5)

        assert any(item["path"] == "research/search.md" for item in results)
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_workspace_search_filters_fts_false_positive_chunks(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="search")
        await osys.write_artifact(
            session.id,
            path="research/benchmark.md",
            content="Benchmark unique workspace search token",
            artifact_type="research",
        )

        results = await osys.workspace_search(session.id, "Benchmark", limit=5)

        assert results
        assert all("Benchmark" in item["content"] for item in results if item["source"] == "artifact")
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_remove_artifact_clears_shared_work_dir_artifact_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        parent = await osys.create_session(name="parent")
        await osys.write_artifact(
            parent.id,
            path="research/shared.md",
            content="shared delete token",
            artifact_type="research",
        )
        child = await osys.sessions.fork_session(parent.id)

        await osys.workspace_memory.remove_artifact(child.id, "research/shared.md")
        parent_artifacts = await osys.list_artifacts(parent.id)
        child_artifacts = await osys.list_artifacts(child.id)
        hits = await osys.workspace_search(child.id, "shared delete token", limit=5)

        assert "research/shared.md" not in [item["path"] for item in parent_artifacts]
        assert "research/shared.md" not in [item["path"] for item in child_artifacts]
        assert not hits
    finally:
        await osys.stop()
