"""
Academic search tools — arXiv, Crossref, etc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .registry import ToolResult, get_tool_registry


CROSSREF_API = "https://api.crossref.org/works"
_DESC_DIR = Path(__file__).resolve().parent / "descriptions"


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


async def handle_arxiv_search(query: str, max_results: int = 5, **kw) -> ToolResult:
    """arXiv search via official Python library (handles rate limiting)."""
    try:
        import arxiv
        search = arxiv.Search(query=query, max_results=max_results)
        client = arxiv.Client(page_size=max_results)
        results = []
        for r in client.results(search):
            results.append({
                "title": r.title,
                "url": r.entry_id,
                "content": r.summary,
                "published": str(r.published)[:10],
                "authors": [a.name for a in r.authors],
            })
        return ToolResult.ok(data={"query": query, "results": results, "count": len(results)})
    except Exception as e:
        return ToolResult.fail(f"arxiv_search failed: {e}")


async def handle_crossref_search(query: str, max_results: int = 5, **kw) -> ToolResult:
    try:
        params = {"query": query, "rows": max_results}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(CROSSREF_API, params=params)
            r.raise_for_status()
            data = r.json()
        items = data.get("message", {}).get("items", [])
        results = []
        for item in items:
            title_list = item.get("title", ["Untitled"])
            doi = item.get("DOI", "")
            results.append({
                "title": title_list[0] if title_list else "Untitled",
                "url": f"https://doi.org/{doi}" if doi else "",
                "content": f"DOI: {doi} | Publisher: {item.get('publisher', '')} | Type: {item.get('type', '')}",
            })
        return ToolResult.ok(data={"query": query, "results": results, "count": len(results)})
    except Exception as e:
        return ToolResult.fail(f"crossref_search failed: {e}")


def register_academic_tools(r) -> None:
    r.register("arxiv_search", "retrieval", {
        "name": "arxiv_search",
        "description": _load_desc("arxiv_search"),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (title, author, conference)"},
                "max_results": {"type": "integer", "description": "Max results (default 5)"},
            },
            "required": ["query"],
        },
    }, handle_arxiv_search, concurrency_safe=True, read_only=True)

    r.register("crossref_search", "retrieval", {
        "name": "crossref_search",
        "description": _load_desc("crossref_search"),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (title, author, DOI)"},
                "max_results": {"type": "integer", "description": "Max results (default 5)"},
            },
            "required": ["query"],
        },
    }, handle_crossref_search, concurrency_safe=True, read_only=True)
