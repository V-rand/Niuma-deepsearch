"""
Web search tool with Tavily/Serper fallback. Exposes advanced features:
- include_domains: domain-locked search (Tavily)
- time_range: recency filter (Tavily + Serper)
- source: "web" | "news" | "scholar" (Serper scholar for academic content)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from .registry import ToolResult, get_tool_registry
from .utils import _resolve_serper_api_key, _summarize_error


def _api_key() -> str | None:
    return os.getenv("TAVILY_API_KEY") or None


def _serper_key() -> str | None:
    return _resolve_serper_api_key()


async def handle_web_search(
    query,
    max_results=8,
    include_domains=None,
    exclude_domains=None,
    time_range=None,
    source="web",
    exact_match=False,
    **kw,
) -> ToolResult:
    """Web search with Tavily/Serper dual backends."""
    timeout = 20.0
    errors: list[str] = []

    def _parse_domains(val):
        if not val:
            return None
        if isinstance(val, str):
            return [d.strip() for d in val.split(",") if d.strip()]
        if isinstance(val, list):
            return val
        return None

    include_list = _parse_domains(include_domains)
    exclude_list = _parse_domains(exclude_domains)

    tavily_extra: dict[str, Any] = {}
    if include_list:
        tavily_extra["include_domains"] = include_list[:10]
    if exclude_list:
        tavily_extra["exclude_domains"] = exclude_list[:10]
    if time_range:
        tavily_extra["time_range"] = time_range
    if source == "news":
        tavily_extra["topic"] = "news"
    if exact_match:
        tavily_extra["exact_match"] = True

    serper_extra: dict[str, Any] = {}
    if source == "scholar":
        serper_extra["type"] = "scholar"
    elif source == "news":
        serper_extra["type"] = "news"
    if time_range:
        tbs_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
        serper_extra["tbs"] = tbs_map.get(time_range, "")

    result = await _try_tavily(query=query, max_results=max_results, timeout=timeout, extra=tavily_extra)
    if result["status"] == "success":
        return ToolResult.ok(data={"query": query, "results": result["results"], "count": len(result["results"]), "provider": "tavily"})
    if result["status"] == "error":
        errors.append(result.get("detail", "tavily unknown error"))

    result = await _try_serper(query=query, max_results=max_results, timeout=timeout, extra=serper_extra)
    if result["status"] == "success":
        return ToolResult.ok(data={"query": query, "results": result["results"], "count": len(result["results"]), "provider": "serper"})
    if result["status"] == "error":
        errors.append(result.get("detail", "serper unknown error"))

    msg = "; ".join(errors) if errors else "No search provider available"
    return ToolResult.fail(msg)


async def _try_tavily(*, query: str, max_results: int, timeout: float, extra: dict[str, Any]) -> dict[str, Any]:
    key = _api_key()
    if not key:
        return {"status": "skipped", "detail": "TAVILY_API_KEY missing", "results": []}
    try:
        import aiohttp
        payload: dict[str, Any] = {
            "api_key": key, "query": query, "max_results": max_results,
            "search_depth": "advanced",
        }
        if extra.get("include_domains"):
            payload["include_domains"] = extra["include_domains"]
        if extra.get("time_range"):
            payload["time_range"] = extra["time_range"]
        if extra.get("topic"):
            payload["topic"] = extra["topic"]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=True) as s:
            async with s.post("https://api.tavily.com/search", json=payload) as r:
                p = await r.json()
                if r.status >= 400:
                    return {"status": "error", "detail": f"HTTP {r.status}: {p}", "results": []}
                return {"status": "success", "detail": "ok", "results": [{"title": i.get("title"), "url": i.get("url"), "content": i.get("content")} for i in p.get("results", [])]}
    except asyncio.TimeoutError:
        return {"status": "error", "detail": f"Timed out after {timeout}s", "results": []}
    except Exception as e:
        return {"status": "error", "detail": _summarize_error(e), "results": []}


async def _try_serper(*, query: str, max_results: int, timeout: float, extra: dict[str, Any]) -> dict[str, Any]:
    key = _serper_key()
    if not key:
        return {"status": "skipped", "detail": "SERPER_API_KEY missing", "results": []}
    try:
        import aiohttp
        ep = "https://google.serper.dev/search"
        if extra.get("type") == "scholar":
            ep = "https://google.serper.dev/scholar"
        elif extra.get("type") == "news":
            ep = "https://google.serper.dev/news"
        payload: dict[str, Any] = {"q": query, "num": max_results}
        if extra.get("tbs"):
            payload["tbs"] = extra["tbs"]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=True) as s:
            async with s.post(ep, headers={"X-API-KEY": key}, json=payload) as r:
                p = await r.json()
                if r.status >= 400:
                    return {"status": "error", "detail": f"HTTP {r.status}: {p}", "results": []}
                items = p.get("organic", [])
                return {"status": "success", "detail": "ok", "results": [{"title": i.get("title"), "url": i.get("link"), "content": i.get("snippet")} for i in items]}
    except asyncio.TimeoutError:
        return {"status": "error", "detail": f"Timed out after {timeout}s", "results": []}
    except Exception as e:
        return {"status": "error", "detail": _summarize_error(e), "results": []}


def register_search_tools(r) -> None:
    _p = Path(__file__).resolve().parent / "descriptions"
    def _ld(n):
        return (_p / f"{n}.txt").read_text(encoding="utf-8").strip() if (_p / f"{n}.txt").exists() else ""
    r.register("web_search", "retrieval", {
    "name": "web_search",
    "description": _ld("web_search"),
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "搜索查询，支持 site:domain, \"exact phrase\", -exclude, OR 操作符"},
        "max_results": {"type": "integer", "description": "最大返回结果数（默认 8）"},
        "include_domains": {"type": "string", "description": "限定搜索域名，逗号分隔。如 \"proceedings.mlr.press,arxiv.org\""},
        "exclude_domains": {"type": "string", "description": "排除域名，逗号分隔。如 \"reddit.com,quora.com\""},
        "time_range": {"type": "string", "description": "时间范围：day, week, month, year"},
        "source": {"type": "string", "description": "搜索源：web（默认）, news, scholar（Google Scholar 学术论文）"},
        "exact_match": {"type": "boolean", "description": "true 则要求精确匹配查询短语。适合搜特定人名、实体名，不会拆词。可能返回空。"},
    }, "required": ["query"]},
    }, handle_web_search, concurrency_safe=True, read_only=True)
