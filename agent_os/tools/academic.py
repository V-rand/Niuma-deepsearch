"""
Academic search tools — arXiv, Crossref, etc.
"""

from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests as _requests

from .registry import ToolResult, get_tool_registry


ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
_DESC_DIR = Path(__file__).resolve().parent / "descriptions"
_arxiv_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_ARXIV_CACHE_TTL = 86400  # 24h — results don't change until midnight
_last_arxiv_call: float = 0  # rate limiter: 3s between requests per API docs
_arxiv_lock = asyncio.Lock()


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def _arxiv_request(params: dict) -> str:
    """Sync arXiv API request via requests. Reliable from China."""
    r = _requests.get(ARXIV_API, params=params, headers={"User-Agent": "AgentOS/1.0"}, timeout=20)
    r.raise_for_status()
    return r.text


def _parse_arxiv_response(xml_text: str, max_results: int) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    entries = root.findall("atom:entry", ARXIV_NS)

    def _txt(entry: ET.Element, tag: str) -> str:
        el = entry.find(tag, ARXIV_NS)
        return el.text.strip() if el is not None and el.text else ""

    # Check for API-level error entries (section 3.4)
    # Errors come as 200 OK with <title>Error</title> entry
    for entry in entries:
        if _txt(entry, "atom:title") == "Error":
            err_id = _txt(entry, "atom:id")
            err_summary = _txt(entry, "atom:summary")
            raise ValueError(f"arXiv API error: {err_summary} ({err_id})")

    results = []
    for entry in entries[:max_results]:
        title = _txt(entry, "atom:title").replace("\n", " ").strip()
        summary = _txt(entry, "atom:summary").replace("\n", " ").strip()
        published = _txt(entry, "atom:published")[:10]
        url = _txt(entry, "atom:id")

        authors = []
        for au in entry.findall("atom:author", ARXIV_NS):
            name_el = au.find("atom:name", ARXIV_NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        categories = []
        for cat in entry.findall("atom:category", ARXIV_NS):
            term = cat.get("term", "")
            if term:
                categories.append(term)

        comment_el = entry.find("arxiv:comment", ARXIV_NS)
        comment = comment_el.text.strip() if comment_el is not None and comment_el.text else ""

        jr_el = entry.find("arxiv:journal_ref", ARXIV_NS)
        journal_ref = jr_el.text.strip() if jr_el is not None and jr_el.text else ""

        results.append({
            "title": title,
            "url": url,
            "content": summary,
            "published": published,
            "authors": authors,
            "categories": categories,
            "comment": comment,
            "journal_ref": journal_ref,
        })
    return results


async def handle_arxiv_search(
    query: str = "",
    author: str = "",
    title: str = "",
    category: str = "",
    venue: str = "",
    year: str = "",
    max_results: int = 5,
    **kw,
) -> ToolResult:
    """arXiv search via direct HTTP API with retry and Semantic Scholar fallback."""
    global _last_arxiv_call
    date_filter = f"submittedDate:[{year}01010000+TO+{year}12312359]" if year else ""
    parts = []
    if author:
        parts.append(f"au:{author}")
    if title:
        parts.append(f"ti:{title}")
    if category:
        parts.append(f"cat:{category}")
    if venue:
        parts.append(f'(all:"{venue}")')
    if date_filter:
        parts.append(date_filter)
    if query:
        parts.append(f"({query})")
    full_query = " AND ".join(parts) if parts else query
    if not full_query:
        return ToolResult.fail("At least one of query, author, title, or category is required.")

    # Build a plain-text fallback query for Semantic Scholar
    ss_query = " ".join(p for p in [author, title, query] if p).strip()
    if not ss_query:
        ss_query = category or ""

    cache_key = f"arxiv:{full_query}:{year}:{max_results}"
    now = time.time()
    if cache_key in _arxiv_cache:
        ts, data = _arxiv_cache[cache_key]
        if now - ts < _ARXIV_CACHE_TTL:
            return ToolResult.ok(data=data)

    # Primary: arXiv API with exponential backoff
    params = {
        "search_query": full_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    for attempt in range(3):
        try:
            async with _arxiv_lock:
                elapsed = time.time() - _last_arxiv_call
                if elapsed < 3:
                    await asyncio.sleep(3 - elapsed)
                _last_arxiv_call = time.time()
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, _arxiv_request, params)
            parsed = _parse_arxiv_response(text, max_results)
            data = {"query": full_query, "results": parsed, "count": len(parsed)}
            _arxiv_cache[cache_key] = (time.time(), data)
            return ToolResult.ok(data=data)
        except ValueError as e:
            return ToolResult.fail(f"arxiv_search: {e}")
        except Exception:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt * 3)
                continue

    # Fallback: Semantic Scholar API (free, no key, more reliable from China)
    if not ss_query:
        return ToolResult.ok(data={
            "query": full_query, "error": "unavailable",
            "summary": "arXiv API unavailable, and no fallback query available. Use web_search.",
        })
    try:
        loop = asyncio.get_running_loop()
        ss_params = {"query": ss_query, "limit": max_results, "fields": "title,authors,year,abstract,externalIds,venue"}
        if venue:
            ss_params["venue"] = venue
        if year:
            ss_params["year"] = year
        ss_response = await loop.run_in_executor(
            None,
            lambda: _requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=ss_params, headers={"User-Agent": "AgentOS/1.0"}, timeout=15
            )
        )
        if ss_response.status_code == 200:
            data = ss_response.json()
            results = []
            for p in (data.get("data") or []):
                results.append({
                    "title": p.get("title", ""),
                    "url": f"https://www.semanticscholar.org/paper/{p.get('paperId', '')}",
                    "content": (p.get("abstract") or "")[:2000],
                    "published": str(p.get("year", "")),
                    "authors": [a.get("name", "") for a in (p.get("authors") or [])],
                    "categories": [],
                })
            if results:
                data = {"query": full_query, "results": results, "count": len(results), "source": "semantic_scholar"}
                _arxiv_cache[cache_key] = (time.time(), data)
                return ToolResult.ok(data=data)
    except Exception:
        pass

    return ToolResult.ok(data={
        "query": full_query, "error": "unavailable",
        "summary": "arXiv and Semantic Scholar both unavailable. Use web_search as fallback.",
    })


async def handle_crossref_search(query: str, max_results: int = 5, **kw) -> ToolResult:
    try:
        loop = asyncio.get_running_loop()
        params = {"query": query, "rows": max_results}
        r = await loop.run_in_executor(
            None,
            lambda: _requests.get(CROSSREF_API, params=params, timeout=20)
        )
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
                "query": {"type": "string", "description": "通用文本搜索（标题、作者、摘要中的关键词）"},
                "author": {"type": "string", "description": "按作者名搜索（如 \"Yoshua_Bengio\" 或 \"Bengio\"）"},
                "title": {"type": "string", "description": "按标题词搜索（如 \"transformer\"）"},
                "category": {"type": "string", "description": "arXiv 分类（如 \"cs.LG\"、\"stat.ML\"、\"cs.AI\"、\"math.ST\"）"},
                "venue": {"type": "string", "description": "会议/期刊名（如 \"ICML 2022\"、\"NeurIPS 2020\"、\"Nature\"）。搜索论文的 comment 和 abstract 字段。与 author 等参数组合使用效果更好。"},
                "year": {"type": "string", "description": "发表年份（如 \"2022\"）。过滤 submittedDate，精确到年。"},
                "max_results": {"type": "integer", "description": "最大返回数（默认 5）"},
            },
            "required": [],
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
