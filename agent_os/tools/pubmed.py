"""
PubMed E-utilities API — biomedical literature search.

PubMed is the U.S. National Library of Medicine's database of biomedical literature:
- ~37 million citations (MEDLINE + PubMed Central + Bookshelf)
- Free, no API key required for moderate use
- NCBI E-utilities: esearch (search) + efetch (retrieve) + esummary (summaries)
- Rate limit: 3 requests/sec without key, 10/sec with API key (NCBI_API_KEY env var)
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import requests as _requests

from .registry import ToolResult

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_KEY = os.getenv("NCBI_API_KEY", "")
_DESC_DIR = Path(__file__).resolve().parent / "descriptions"
_last_pubmed_call: float = 0  # NCBI asks 3 req/sec without key
_pubmed_lock = asyncio.Lock()


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _pubmed_api(url: str, params: dict[str, Any]) -> _requests.Response:
    """Sync NCBI API call via requests."""
    p = dict(params)
    p.setdefault("tool", "AgentOS")
    p.setdefault("email", "agentos@example.com")
    if NCBI_KEY:
        p["api_key"] = NCBI_KEY
    r = _requests.get(url, params=p, timeout=20,
                      headers={"User-Agent": "AgentOS/1.0"})
    r.raise_for_status()
    return r


async def handle_pubmed_search(
    query: str = "",
    author: str = "",
    title: str = "",
    journal: str = "",
    year: str = "",
    mesh: str = "",
    max_results: int = 10,
    **kw,
) -> ToolResult:
    """Search PubMed for biomedical literature."""
    global _last_pubmed_call

    # Build query using PubMed field tags
    parts: list[str] = []
    if author:
        parts.append(f"{author}[Author]")
    if title:
        parts.append(f"{title}[Title]")
    if journal:
        parts.append(f"{journal}[Journal]")
    if mesh:
        parts.append(f"{mesh}[MeSH Terms]")
    if year:
        parts.append(f"{year}[Publication Date]")
    if query:
        parts.append(f"({query})")

    full_query = " AND ".join(parts) if parts else query
    if not full_query:
        return ToolResult.fail("At least one of query, author, title, journal, mesh, or year is required.")

    # Step 1: search for IDs
    retmax = min(max_results, 20)
    search_params: dict[str, Any] = {
        "db": "pubmed", "term": full_query, "retmax": retmax,
        "retmode": "json", "sort": "relevance",
    }

    try:
        async with _pubmed_lock:
            elapsed = time.time() - _last_pubmed_call
            rate = 0.34 if NCBI_KEY else 0.35  # ~3 req/sec
            if elapsed < rate:
                await asyncio.sleep(rate - elapsed)
            _last_pubmed_call = time.time()

        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, _pubmed_api, f"{NCBI_BASE}/esearch.fcgi", search_params)
        search_data = r.json()
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        total = int(search_data.get("esearchresult", {}).get("count", 0))

        if not id_list:
            return ToolResult.ok(data={
                "query": full_query, "total_count": 0, "results": [], "count": 0,
            })

        # Step 2: fetch summaries
        ids = ",".join(id_list)
        summary_params: dict[str, Any] = {
            "db": "pubmed", "id": ids, "retmode": "json",
        }
        r2 = await loop.run_in_executor(None, _pubmed_api, f"{NCBI_BASE}/esummary.fcgi", summary_params)
        summary_data = r2.json()
        summaries = summary_data.get("result", {})

        results = []
        for pmid in id_list:
            info = summaries.get(pmid, {})
            authors_raw = info.get("authors", [])
            authors = [a.get("name", "") for a in authors_raw if a.get("name")]
            results.append({
                "pmid": pmid,
                "title": info.get("title", ""),
                "authors": authors,
                "author_count": len(authors),
                "journal": info.get("source", ""),
                "pubdate": info.get("pubdate", ""),
                "doi": info.get("elocationid", "").replace("doi: ", "") if "doi:" in str(info.get("elocationid", "")) else "",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })

        return ToolResult.ok(data={
            "query": full_query,
            "total_count": total,
            "results": results,
            "count": len(results),
        })
    except Exception as e:
        return ToolResult.fail(f"PubMed API error: {e}")


def register_pubmed_tools(r) -> None:
    r.register("pubmed_search", "retrieval", {
        "name": "pubmed_search",
        "description": _load_desc("pubmed_search"),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "通用搜索查询（关键词）"},
                "author": {"type": "string", "description": "作者名（如 \"Fauci AS\"）"},
                "title": {"type": "string", "description": "标题关键词"},
                "journal": {"type": "string", "description": "期刊名（如 \"Nature\"）"},
                "year": {"type": "string", "description": "发表年份（如 \"2023\"）"},
                "mesh": {"type": "string", "description": "MeSH 主题词（如 \"Diabetes Mellitus\"）"},
                "max_results": {"type": "integer", "description": "最大结果数（默认 10，最大 20）"},
            },
            "required": [],
        },
    }, handle_pubmed_search, concurrency_safe=True, read_only=True)
