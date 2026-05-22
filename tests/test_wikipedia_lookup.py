import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_wikipedia_lookup_extracts_infobox_before_client_closes():
    from agent_os.tools.media import handle_wikipedia_lookup, _wikipedia_lookup_sync

    def mock_lookup(query: str):
        return {
            "query": "Alexander Fleming",
            "title": "Alexander Fleming",
            "url": "https://en.wikipedia.org/wiki/Alexander_Fleming",
            "summary": "Alexander Fleming was a Scottish physician.",
            "categories": ["Category:Scottish scientists"],
            "infobox": {"Born": "6 August 1881, Scotland"},
        }

    with patch("agent_os.tools.media._wikipedia_lookup_sync", mock_lookup):
        result = await handle_wikipedia_lookup("Alexander Fleming")

    assert result.success
    assert result.data["infobox"]["Born"] == "6 August 1881, Scotland"
