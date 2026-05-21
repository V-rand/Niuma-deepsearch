"""
Wikipedia lookup tool — structured info for movies, actors, dramas.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .registry import ToolResult, get_tool_registry

_DESC_DIR = Path(__file__).resolve().parent / "descriptions"
WIKI_API = "https://en.wikipedia.org/w/api.php"
UA = "AgentOS/1.0"


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _parse_infobox(html: str) -> dict[str, str]:
    """Extract key-value pairs from Wikipedia infobox table."""
    soup = BeautifulSoup(html, "html.parser")
    info: dict[str, str] = {}
    table = soup.find("table", class_="infobox")
    if not table:
        return info
    for row in table.find_all("tr"):
        th = row.find("th", class_="infobox-label")
        td = row.find("td", class_="infobox-data")
        if th and td:
            key = th.get_text(strip=True)
            val = td.get_text(strip=True, separator=", ")
            if key and val:
                info[key] = val[:200]
    return info


async def handle_wikipedia_lookup(query: str, lang: str = "", **kw) -> ToolResult:
    async def _lookup(api_url: str) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                search_params = {
                    "action": "query", "format": "json",
                    "list": "search", "srsearch": query, "srlimit": 5, "srwhat": "text",
                }
                sr = await c.get(api_url, params=search_params, headers={"User-Agent": UA})
                sr.raise_for_status()
                search_data = sr.json()
                search_results = search_data.get("query", {}).get("search", [])
                if not search_results:
                    return None

                best = None
                for r in search_results:
                    t = r.get("title", "")
                    if "disambiguation" not in t and "(消歧义)" not in t:
                        best = r
                        break
                if not best:
                    return None

                title = best["title"]
                extract_params = {
                    "action": "query", "format": "json", "titles": title,
                    "prop": "extracts|categories", "exintro": 1, "explaintext": 1,
                    "exchars": 2000, "cllimit": 20, "clshow": "!hidden",
                }
                pr = await c.get(api_url, params=extract_params, headers={"User-Agent": UA})
                pr.raise_for_status()
                page_data = pr.json()
                pages = page_data.get("query", {}).get("pages", {})
                pid = next(iter(pages)) if pages else "-1"
                page_info = pages.get(pid, {})

                result: dict[str, Any] = {
                    "query": query, "title": title,
                    "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    "summary": page_info.get("extract", "")[:2000],
                    "categories": [c["title"] for c in page_info.get("categories", [])][:15],
                }
                try:
                    hr = await c.get(result["url"], headers={"User-Agent": UA}, timeout=8)
                    if hr.status_code == 200:
                        ib = _parse_infobox(hr.text)
                        if ib:
                            result["infobox"] = ib
                except Exception:
                    pass
                return result
        except Exception:
            return None

    try:
        r = await _lookup(WIKI_API)
        if r:
            return ToolResult.ok(data=r)
        return ToolResult.ok(data={"query": query, "error": "not_found", "summary": f"未找到「{query}」的相关页面"})
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
                "query": {"type": "string", "description": "Page title or search query"},
                "lang": {"type": "string", "description": 'Language code: "zh" for Chinese, "" for auto-detect'},
            },
            "required": ["query"],
        },
    }, handle_wikipedia_lookup, concurrency_safe=True, read_only=True)
