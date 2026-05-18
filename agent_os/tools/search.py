"""
Web search tool with Tavily/Serper fallback.
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


async def handle_web_search(query, max_results=8, **kw) -> ToolResult:
    timeout = 20.0
    errors: list[str] = []
    result = await _try_tavily(query=query, max_results=max_results, timeout=timeout)
    if result["status"] == "success":
        return ToolResult.ok(data={"query": query, "results": result["results"], "count": len(result["results"])})
    if result["status"] == "error":
        errors.append(result.get("detail", "tavily unknown error"))

    result = await _try_serper(query=query, max_results=max_results, timeout=timeout)
    if result["status"] == "success":
        return ToolResult.ok(data={"query": query, "results": result["results"], "count": len(result["results"])})
    if result["status"] == "error":
        errors.append(result.get("detail", "serper unknown error"))

    msg = "; ".join(errors) if errors else "No search provider available"
    return ToolResult.fail(msg)


async def _try_tavily(*, query: str, max_results: int, timeout: float) -> dict[str, Any]:
    key = _api_key()
    if not key:
        return {"status": "skipped", "detail": "TAVILY_API_KEY missing", "results": []}
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=True) as s:
            async with s.post("https://api.tavily.com/search", json={"api_key": key, "query": query, "max_results": max_results}) as r:
                p = await r.json()
                if r.status >= 400:
                    return {"status": "error", "detail": f"HTTP {r.status}: {p}", "results": []}
                return {"status": "success", "detail": "ok", "results": [{"title": i.get("title"), "url": i.get("url"), "content": i.get("content")} for i in p.get("results", [])]}
    except asyncio.TimeoutError:
        return {"status": "error", "detail": f"Timed out after {timeout}s", "results": []}
    except Exception as e:
        return {"status": "error", "detail": _summarize_error(e), "results": []}


async def _try_serper(*, query: str, max_results: int, timeout: float) -> dict[str, Any]:
    key = _serper_key()
    if not key:
        return {"status": "skipped", "detail": "SERPER_API_KEY missing", "results": []}
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=True) as s:
            async with s.post("https://google.serper.dev/search", headers={"X-API-KEY": key}, json={"q": query, "num": max_results}) as r:
                p = await r.json()
                if r.status >= 400:
                    return {"status": "error", "detail": f"HTTP {r.status}: {p}", "results": []}
                return {"status": "success", "detail": "ok", "results": [{"title": i.get("title"), "url": i.get("link"), "content": i.get("snippet")} for i in p.get("organic", [])]}
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
        "query": {"type": "string", "description": "搜索查询内容"},
        "max_results": {"type": "integer", "description": "最大返回结果数"},
    }, "required": ["query"]},
    }, handle_web_search, concurrency_safe=True, read_only=True)
