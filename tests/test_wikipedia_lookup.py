import pytest


@pytest.mark.asyncio
async def test_wikipedia_lookup_extracts_infobox_before_client_closes(monkeypatch):
    from agent_os.tools import media

    class _Response:
        def __init__(self, data=None, text="", status_code=200):
            self._data = data
            self.text = text
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class _Client:
        closed = False

        def __init__(self, *args, **kwargs):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def get(self, url, params=None, headers=None, timeout=None):
            assert not self.closed
            self.calls.append((url, params))
            if params and params.get("list") == "search":
                return _Response({
                    "query": {
                        "search": [{"title": "Alexander Fleming"}],
                    },
                })
            if params and params.get("prop") == "extracts|categories":
                return _Response({
                    "query": {
                        "pages": {
                            "1": {
                                "extract": "Alexander Fleming was a Scottish physician.",
                                "categories": [{"title": "Category:Scottish scientists"}],
                            },
                        },
                    },
                })
            return _Response(
                text=(
                    '<table class="infobox">'
                    '<tr><th class="infobox-label">Born</th>'
                    '<td class="infobox-data">6 August 1881, Scotland</td></tr>'
                    '</table>'
                ),
                status_code=200,
            )

    monkeypatch.setattr(media.httpx, "AsyncClient", _Client)

    result = await media.handle_wikipedia_lookup("Alexander Fleming")

    assert result.success
    assert result.data["infobox"]["Born"] == "6 August 1881, Scotland"
