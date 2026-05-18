import pytest


def test_legal_case_skill_provides_workspace_profile():
    from agent_os.skills.loader import SkillLoader

    loader = SkillLoader()
    loader.discover_all()
    profile = loader.get_profile_config("legal_case")

    assert "evidence" in profile["folders"]
    assert "pleadings" in profile["folders"]
    assert "facts.md" in profile["files"]
    assert "strategy.md" in profile["files"]
    assert "stage_state.md" in profile["files"]


@pytest.mark.asyncio
async def test_legal_case_skill_can_be_activated_into_context(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="legal", workspace_profile="legal_case")
        await osys.activate_skills(session.id, ["legal_case"])
        context = await osys.preview_context(session.id, "查看案件状态")

        assert "该skills未实现" not in context["system_prompt"]
        assert "事实-证据-法律-策略" in context["system_prompt"]
        assert "外部 stage 状态机" in context["system_prompt"]
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_legal_case_profile_is_preserved_with_initial_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS
    from pathlib import Path

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(
            name="legal",
            workspace_profile="legal_case",
            initial_files={"custom.md": "# Custom"},
        )
        work_dir = Path(session.work_dir)

        assert (work_dir / "custom.md").exists()
        assert (work_dir / "facts.md").exists()
        assert (work_dir / "stage_state.md").exists()
        assert (work_dir / "evidence" / "index.md").exists()
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_create_session_rejects_initial_file_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        with pytest.raises(ValueError, match="Unsafe workspace path"):
            await osys.create_session(
                name="legal",
                workspace_profile="legal_case",
                initial_files={"../escape.md": "bad"},
            )

        assert not (tmp_path / "sessions" / "escape.md").exists()
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_skill_use_persists_activation_for_next_context(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS
    from agent_os.tools.registry import set_session_context

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="legal", workspace_profile="legal_case")
        set_session_context(work_dir=session.work_dir, session_id=session.id)
        skill_use = osys.tool_registry.get_entry("skill_use")

        result = await skill_use.handler(name="legal_case", task="案件工作区")
        refreshed = await osys.get_session(session.id)
        context = await osys.preview_context(session.id, "查看案件状态")

        assert result.success
        assert refreshed.metadata["active_skills"] == ["legal_case"]
        assert "事实-证据-法律-策略" in context["system_prompt"]
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_activate_skills_rejects_unknown_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="legal", workspace_profile="legal_case")

        with pytest.raises(ValueError, match="Unknown skill"):
            await osys.activate_skills(session.id, ["legal_case", "missing_skill"])
    finally:
        await osys.stop()
