from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from agent_os.tools.academic import (
    _arxiv_cache,
    _last_arxiv_call,
    _parse_arxiv_response,
    handle_arxiv_search,
)

VALID_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>2</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>2</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/2301.00001</id>
    <published>2023-01-01T00:00:00Z</published>
    <updated>2023-01-01T00:00:00Z</updated>
    <title>Test Paper One</title>
    <summary>Abstract of paper one.</summary>
    <author><name>Alice</name></author>
    <author><name>Bob</name></author>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:primary_category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:comment>Accepted at ICML 2022, 10 pages</arxiv:comment>
    <arxiv:journal_ref>Proceedings of the 39th International Conference on Machine Learning</arxiv:journal_ref>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2301.00002</id>
    <published>2023-01-02T00:00:00Z</published>
    <updated>2023-01-02T00:00:00Z</updated>
    <title>Test Paper Two</title>
    <summary>Abstract of paper two.</summary>
    <author><name>Charlie</name></author>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:primary_category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""

ERROR_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <entry>
    <id>http://arxiv.org/api/errors#incorrect_id_format_for_1234.12345</id>
    <title>Error</title>
    <summary>incorrect id format for 1234.12345</summary>
    <updated>2024-01-01T00:00:00Z</updated>
    <link href="http://arxiv.org/api/errors#incorrect_id_format_for_1234.12345" rel="alternate"/>
    <author><name>arXiv api core</name></author>
  </entry>
</feed>"""

EMPTY_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>0</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>0</opensearch:itemsPerPage>
</feed>"""


def _mock_client(text: str, status: int = 200):
    m = AsyncMock()
    m.status_code = status
    m.text = text
    m.raise_for_status = lambda: None
    return m


@pytest.fixture(autouse=True)
def reset_state():
    _arxiv_cache.clear()
    global _last_arxiv_call
    _last_arxiv_call = 0


class TestParseArxivResponse:
    def test_valid(self):
        results = _parse_arxiv_response(VALID_XML, 5)
        assert len(results) == 2
        assert results[0]["title"] == "Test Paper One"
        assert results[0]["comment"] == "Accepted at ICML 2022, 10 pages"
        assert results[0]["journal_ref"] == "Proceedings of the 39th International Conference on Machine Learning"
        assert results[1]["comment"] == ""
        assert results[1]["journal_ref"] == ""

    def test_max_results_limit(self):
        results = _parse_arxiv_response(VALID_XML, 1)
        assert len(results) == 1

    def test_error_entry(self):
        with pytest.raises(ValueError, match="incorrect id format for 1234.12345"):
            _parse_arxiv_response(ERROR_XML, 5)

    def test_empty(self):
        results = _parse_arxiv_response(EMPTY_XML, 5)
        assert results == []


class TestHandleArxivSearch:
    @patch("agent_os.tools.academic._arxiv_request")
    async def test_success(self, mock_request):
        mock_request.return_value = VALID_XML
        result = await handle_arxiv_search(query="test")
        assert result.success
        assert result.data["count"] == 2
        assert result.data["results"][0]["title"] == "Test Paper One"
        assert result.data["results"][1]["authors"] == ["Charlie"]

    @patch("agent_os.tools.academic._arxiv_request")
    async def test_error_entry_returns_fail(self, mock_request):
        mock_request.side_effect = ValueError("arXiv API error: incorrect id format for 1234.12345")
        result = await handle_arxiv_search(query="1234.12345")
        assert not result.success
        assert "incorrect id format" in result.error

    @patch("agent_os.tools.academic._arxiv_request")
    async def test_empty_results(self, mock_request):
        mock_request.return_value = EMPTY_XML
        result = await handle_arxiv_search(query="nonexistent_query_xyz")
        assert result.success
        assert result.data["count"] == 0

    @patch("agent_os.tools.academic._arxiv_request")
    async def test_cache_hit_skips_http(self, mock_request):
        mock_request.return_value = VALID_XML
        r1 = await handle_arxiv_search(query="cache_test")
        assert r1.success and r1.data["count"] == 2
        call_count = mock_request.call_count
        r2 = await handle_arxiv_search(query="cache_test")
        assert r2.success and r2.data["count"] == 2
        assert mock_request.call_count == call_count

    @patch("agent_os.tools.academic._arxiv_request")
    async def test_venue_param_builds_query(self, mock_request):
        mock_request.return_value = VALID_XML
        result = await handle_arxiv_search(author="Bengio", venue="ICML 2022")
        assert result.success
        args = mock_request.call_args[0][0]
        assert 'au:Bengio' in args["search_query"]
        assert 'all:"ICML 2022"' in args["search_query"]

    @patch("agent_os.tools.academic._arxiv_request")
    async def test_rate_limiter(self, mock_request):
        mock_request.return_value = VALID_XML
        await handle_arxiv_search(query="rate_test_a")
        t0 = time.time()
        await handle_arxiv_search(query="rate_test_b")
        elapsed = time.time() - t0
        assert elapsed >= 3.0, f"Rate limiter didn't wait: elapsed={elapsed:.2f}s"

    @patch("agent_os.tools.academic._arxiv_request")
    async def test_429_retry(self, mock_request):
        class Fake429(Exception):
            pass
        mock_request.side_effect = [Fake429(), Fake429(), VALID_XML]
        result = await handle_arxiv_search(query="retry_test")
        assert result.success
        assert result.data["count"] == 2
        assert mock_request.call_count >= 2

    async def test_no_query_fails(self):
        result = await handle_arxiv_search()
        assert not result.success
        assert "required" in result.error
