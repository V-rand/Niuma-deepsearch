import pytest


@pytest.mark.asyncio
async def test_session_list_filters_subagents_by_metadata_and_legacy_name(tmp_path):
    from agent_os.core.session import SessionManager

    manager = SessionManager(data_dir=str(tmp_path))
    root = await manager.create(name="main")
    await manager.create(name="child_flagged", parent_session_id=root.id, metadata={"is_subagent": True})
    await manager.create(name="legacy__subagent_xxx", parent_session_id=root.id)

    sessions = await manager.list()
    names = {s["name"] for s in sessions}
    assert "main" in names
    assert "child_flagged" not in names
    assert "legacy__subagent_xxx" not in names
