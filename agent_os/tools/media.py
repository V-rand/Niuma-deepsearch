"""
Wikipedia lookup tool — quick structured info for people, events, works.
Uses the REST API (/api/rest_v1/page/summary/) for single-call fast retrieval,
with query API fallback for search. No HTML infobox parsing (too slow from China).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import requests as _requests

from .registry import ToolResult, get_tool_registry

_DESC_DIR = Path(__file__).resolve().parent / "descriptions"
UA = "AgentOS/1.0"
API_TIMEOUT = 3.0
TOTAL_TIMEOUT = 6.0


def _wiki_rest_base(lang: str) -> str:
    code = (lang or "en").strip().lower()
    if code in {"zh-cn", "zh-hans", "cn"}:
        code = "zh"
    if code not in {"en", "zh"}:
        code = "en"
    return f"https://{code}.wikipedia.org/api/rest_v1"


def _wiki_query_api(lang: str) -> str:
    code = (lang or "en").strip().lower()
    if code in {"zh-cn", "zh-hans", "cn"}:
        code = "zh"
    if code not in {"en", "zh"}:
        code = "en"
    return f"https://{code}.wikipedia.org/w/api.php"


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _wikipedia_lookup_sync(query: str, lang: str = "") -> dict[str, Any] | None:
    code = (lang or "en").strip().lower()
    if code in {"zh-cn", "zh-hans", "cn"}:
        code = "zh"
    if code not in {"en", "zh"}:
        code = "en"

    try:
        # Primary: try REST API summary (fast — single call)
        rest_base = f"https://{code}.wikipedia.org/api/rest_v1"
        safe_title = query.strip().replace(" ", "_")
        rest_url = f"{rest_base}/page/summary/{safe_title}"
        rr = _requests.get(rest_url, headers={"User-Agent": UA}, timeout=API_TIMEOUT)

        if rr.status_code == 200:
            d = rr.json()
            return {
                "query": query,
                "lang": code,
                "title": d.get("title", safe_title),
                "url": f"https://{code}.wikipedia.org/wiki/{safe_title}",
                "summary": (d.get("extract") or "")[:2000],
                "description": d.get("description") or "",
                "pageid": d.get("pageid"),
            }

        # Fallback: search API
        api = _wiki_query_api(code)
        sr = _requests.get(api, params={
            "action": "query", "format": "json",
            "list": "search", "srsearch": query, "srlimit": 3,
        }, headers={"User-Agent": UA}, timeout=API_TIMEOUT)
        sr.raise_for_status()
        search_results = sr.json().get("query", {}).get("search", [])
        if not search_results:
            return None

        best = None
        for r in search_results:
            t = r.get("title", "")
            if "disambiguation" not in t.lower() and "(消歧义)" not in t:
                best = r
                break
        if not best:
            return None

        title = best["title"]
        # Fetch via REST API using the search-discovered title
        safe_t = title.replace(" ", "_")
        rest_url2 = f"https://{code}.wikipedia.org/api/rest_v1/page/summary/{safe_t}"
        rr2 = _requests.get(rest_url2, headers={"User-Agent": UA}, timeout=API_TIMEOUT)
        if rr2.status_code == 200:
            d2 = rr2.json()
            return {
                "query": query,
                "lang": code,
                "title": d2.get("title", title),
                "url": f"https://{code}.wikipedia.org/wiki/{safe_t}",
                "summary": (d2.get("extract") or "")[:2000],
                "description": d2.get("description") or "",
                "pageid": d2.get("pageid"),
            }

        # Last resort: just return search result info without extract
        return {
            "query": query,
            "lang": code,
            "title": title,
            "url": f"https://{code}.wikipedia.org/wiki/{title.replace(' ', '_')}",
            "summary": (best.get("snippet") or "")[:500],
            "description": "",
        }

    except _requests.Timeout:
        return {
            "query": query, "lang": code or "en",
            "error": "unavailable",
            "summary": "Wikipedia request timed out; use another retrieval source.",
        }
    except Exception:
        return None


async def handle_wikipedia_lookup(query: str, lang: str = "", **kw) -> ToolResult:
    try:
        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(None, _wikipedia_lookup_sync, query, lang)
        r = await asyncio.wait_for(task, timeout=TOTAL_TIMEOUT)
        if r:
            return ToolResult.ok(data=r)
        return ToolResult.ok(data={"query": query, "error": "not_found", "summary": f"未找到「{query}」的相关页面"})
    except asyncio.TimeoutError:
        return ToolResult.ok(data={
            "query": query, "lang": lang or "en",
            "error": "unavailable",
            "summary": "Wikipedia 查询超时；请改用 web_search 或其他结构化来源。",
        })
    except Exception as e:
        return ToolResult.ok(data={
            "query": query, "error": "error",
            "summary": f"Wikipedia 查询出错: {e}",
        })


def register_media_tools(r) -> None:
    r.register("wikipedia_lookup", "retrieval", {
        "name": "wikipedia_lookup",
        "description": _load_desc("wikipedia_lookup"),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "页面标题或搜索关键词"},
                "lang": {"type": "string", "description": '语言代码: "zh" 或 "en"（默认 en）'},
            },
            "required": ["query"],
        },
    }, handle_wikipedia_lookup, concurrency_safe=True, read_only=True)
