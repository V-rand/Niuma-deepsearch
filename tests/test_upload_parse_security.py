import pytest


@pytest.mark.asyncio
async def test_upload_parse_rejects_uploads_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS
    from agent_os.tools.registry import set_session_context

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="upload-security")
        set_session_context(work_dir=session.work_dir, session_id=session.id)
        upload_parse = osys.tool_registry.get_entry("upload_parse")

        result = await upload_parse.handler(path="uploads/../AGENT.md")

        assert not result.success
        assert "uploads" in (result.error or "")
    finally:
        await osys.stop()


@pytest.mark.asyncio
async def test_auto_parse_uploads_uses_current_parser_implementation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from pathlib import Path

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        session = await osys.create_session(name="auto-parse")
        upload_path = Path(session.work_dir) / "uploads" / "note.txt"
        upload_path.write_text("自动解析材料正文", encoding="utf-8")

        parsed = await osys.agent_loop._auto_parse_uploads(session)
        hits = await osys.workspace_search(session.id, "自动解析材料正文", limit=5)

        assert parsed == ["note.txt"]
        assert any(hit["path"] == "drafts/derived/note__txt.md" for hit in hits)
    finally:
        await osys.stop()
