from unittest.mock import patch, Mock


def _mock_response(status=200, json_data=None, raise_on_status=False):
    m = Mock(status_code=status)
    m.json.return_value = json_data or {}
    m.raise_for_status = Mock()
    if raise_on_status and status >= 400:
        m.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return m


def test_wikipedia_lookup_uses_rest_api_for_exact_title():
    from agent_os.tools import media

    mock_json = {
        "title": "Alexander Fleming",
        "extract": "Alexander Fleming was a Scottish physician.",
        "description": "Scottish biologist",
        "pageid": 1234,
    }

    with patch("agent_os.tools.media._requests.get", return_value=_mock_response(200, mock_json)):
        result = media._wikipedia_lookup_sync("Alexander Fleming", "en")

    assert result is not None
    assert result["title"] == "Alexander Fleming"
    assert "Scottish physician" in result["summary"]
    assert result["description"] == "Scottish biologist"
    assert result["pageid"] == 1234


def test_wikipedia_lookup_falls_back_to_search_when_rest_404():
    from agent_os.tools import media

    search_json = {
        "query": {"search": [
            {"title": "First Boer War", "snippet": "The First Boer War was fought..."},
        ]}
    }
    rest_json = {
        "title": "First Boer War",
        "extract": "The First Boer War was fought from 1880 to 1881.",
        "description": "War in South Africa",
        "pageid": 5678,
    }

    def url_matcher(url, **kw):
        if "/page/summary/" in url:
            return _mock_response(404, {})
        if "w/api.php" in url:
            return _mock_response(200, search_json)
        return _mock_response(200, rest_json)

    with patch("agent_os.tools.media._requests.get", side_effect=url_matcher):
        result = media._wikipedia_lookup_sync("First Boer War", "en")

    assert result is not None
    assert result["title"] == "First Boer War"
    assert "fought" in result["summary"]


def test_wikipedia_lookup_returns_not_found_when_search_empty():
    from agent_os.tools import media

    search_json = {"query": {"search": []}}

    def url_matcher(url, **kw):
        if "/page/summary/" in url:
            return _mock_response(404, {})
        return _mock_response(200, search_json)

    with patch("agent_os.tools.media._requests.get", side_effect=url_matcher):
        result = media._wikipedia_lookup_sync("xyznonexistent12345", "en")

    assert result is None


def test_wikipedia_lookup_handles_timeout_gracefully():
    from agent_os.tools import media
    import requests

    with patch("agent_os.tools.media._requests.get", side_effect=requests.Timeout("timeout")):
        result = media._wikipedia_lookup_sync("Any Query", "en")

    assert result is not None
    assert result.get("error") == "unavailable"


def test_wikipedia_lookup_uses_zh_when_lang_is_zh():
    from agent_os.tools import media

    mock_json = {
        "title": "第一次布尔战争",
        "extract": "第一次布尔战争是...",
        "description": "南非战争",
        "pageid": 999,
    }

    def url_matcher(url, **kw):
        if "zh.wikipedia.org" in url and "/page/summary/" in url:
            return _mock_response(200, mock_json)
        return _mock_response(404, {})

    with patch("agent_os.tools.media._requests.get", side_effect=url_matcher):
        result = media._wikipedia_lookup_sync("第一次布尔战争", "zh")

    assert result is not None
    assert result["lang"] == "zh"
    assert result["title"] == "第一次布尔战争"
