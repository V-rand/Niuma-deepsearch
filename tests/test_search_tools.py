import pytest


@pytest.mark.asyncio
async def test_web_search_passes_exclude_domains_and_exact_match_to_tavily(monkeypatch):
    from agent_os.tools import search

    captured = {}

    async def fake_tavily(*, query, max_results, timeout, extra):
        captured["extra"] = dict(extra)
        return {"status": "success", "detail": "ok", "results": []}

    monkeypatch.setattr(search, "_try_tavily", fake_tavily)

    result = await search.handle_web_search(
        query="test query",
        include_domains="a.com,b.com",
        exclude_domains="c.com,d.com",
        exact_match=True,
    )

    assert result.success
    assert captured["extra"]["include_domains"] == ["a.com", "b.com"]
    assert captured["extra"]["exclude_domains"] == ["c.com", "d.com"]
    assert captured["extra"]["exact_match"] is True


@pytest.mark.asyncio
async def test_serper_news_parses_news_field(monkeypatch):
    from agent_os.tools import search

    class FakeResponse:
        status = 200

        async def json(self):
            return {"news": [{"title": "n1", "link": "u1", "snippet": "s1"}]}

    class FakePostCtx:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return FakePostCtx()

    class FakeTimeout:
        def __init__(self, *args, **kwargs):
            pass

    class FakeAiohttp:
        ClientSession = FakeSession
        ClientTimeout = FakeTimeout

    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "aiohttp":
            return FakeAiohttp
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(search, "_serper_key", lambda: "serper-key")
    monkeypatch.setattr(builtins, "__import__", fake_import)

    out = await search._try_serper(
        query="q",
        max_results=3,
        timeout=1.0,
        extra={"type": "news"},
    )

    assert out["status"] == "success"
    assert out["results"] == [{"title": "n1", "url": "u1", "content": "s1"}]


@pytest.mark.asyncio
async def test_web_search_rejects_invalid_source_and_time_range():
    from agent_os.tools import search

    bad_source = await search.handle_web_search(query="q", source="invalid")
    assert bad_source.success is False
    assert "Invalid source" in (bad_source.error or "")

    bad_range = await search.handle_web_search(query="q", time_range="decade")
    assert bad_range.success is False
    assert "Invalid time_range" in (bad_range.error or "")
