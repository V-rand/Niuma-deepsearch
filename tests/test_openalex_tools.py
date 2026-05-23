import pytest


@pytest.mark.asyncio
async def test_openalex_entity_does_not_send_api_key_when_unset(monkeypatch):
    from agent_os.tools import openalex

    monkeypatch.setattr(openalex, "OPENALEX_KEY", None)

    captured = {}

    class FakeResponse:
        def json(self):
            return {"results": []}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = dict(params or {})
        return FakeResponse()

    monkeypatch.setattr(openalex, "_openalex_get", fake_get)

    await openalex._resolve_entity("author", "test", timeout=1.0)
    assert "api_key" not in captured["params"]


@pytest.mark.asyncio
async def test_resolve_work_id_uses_running_loop_executor(monkeypatch):
    from agent_os.tools import openalex

    monkeypatch.setattr(openalex, "OPENALEX_KEY", None)

    class FakeResponse:
        def json(self):
            return {"results": [{"id": "https://openalex.org/W123"}]}

    monkeypatch.setattr(openalex, "_openalex_get", lambda *args, **kwargs: FakeResponse())

    out = await openalex._resolve_work_id("paper title", timeout=1.0)
    assert out == "W123"


@pytest.mark.asyncio
async def test_openalex_entity_per_page_is_clamped(monkeypatch):
    from agent_os.tools import openalex

    async def fake_resolve(entity_type, name, timeout=30.0):
        return [{"id": "A1"}, {"id": "A2"}, {"id": "A3"}]

    monkeypatch.setattr(openalex, "_resolve_entity", fake_resolve)
    out = await openalex.handle_openalex_entity(entity_type="author", search="x", per_page=1)
    assert out.success
    assert out.data["count"] == 1
    assert len(out.data["results"]) == 1


@pytest.mark.asyncio
async def test_openalex_works_year_range_builds_date_filters(monkeypatch):
    from agent_os.tools import openalex

    async def fake_add(*args, **kwargs):
        return None

    async def fake_resolve_ids(identifiers: str):
        return [], []

    class FakeResp:
        def json(self):
            return {"meta": {"count": 0}, "results": []}

    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = dict(params or {})
        return FakeResp()

    monkeypatch.setattr(openalex, "_resolve_and_get_id", lambda *a, **k: fake_add())  # no-op awaitable
    monkeypatch.setattr(openalex, "_resolve_work_ids", fake_resolve_ids)
    monkeypatch.setattr(openalex, "_openalex_get", fake_get)

    out = await openalex.handle_openalex_works(year="2020-2021", title="")
    assert out.success
    filt = out.data["filters_applied"]
    assert "from_publication_date:2020-01-01" in filt
    assert "to_publication_date:2021-12-31" in filt
