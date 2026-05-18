"""
Web reading tool with provider fallback (Jina → Firecrawl → Trafilatura).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .registry import ToolResult, get_tool_registry
from .utils import (
    _extract_pdf_markdown_from_url, _has_proxy_env, _normalize_url,
    _read_provider_order, _resolve_firecrawl_api_key, _resolve_mineru_api_token,
    _skip_jina_without_proxy, _summarize_error,
)
from ..ingest.mineru import MINERU_SUPPORTED_SUFFIXES, MineruClient


def _timeout() -> float:
    raw = (os.getenv("AGENT_OS_WEB_READ_TIMEOUT_SECONDS") or "").strip()
    return float(raw) if raw else 20.0


def _mineru() -> MineruClient | None:
    token = _resolve_mineru_api_token()
    if not token:
        return None
    return MineruClient(
        base_url=os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v1/agent"),
        premium_base_url=os.getenv("MINERU_V4_BASE_URL", "https://mineru.net/api/v4"),
        api_token=token,
        premium_model_version=os.getenv("MINERU_PREMIUM_MODEL_VERSION", "vlm"),
        timeout_seconds=int(os.getenv("MINERU_TIMEOUT_SECONDS", "20")),
        poll_interval_seconds=int(os.getenv("MINERU_POLL_INTERVAL_SECONDS", "3")),
        poll_timeout_seconds=int(os.getenv("MINERU_POLL_TIMEOUT_SECONDS", "180")),
    )


async def handle_web_read(url, **kw) -> ToolResult:
    url = _normalize_url(url)
    mc = _mineru()

    # MinerU for PDFs / Office docs
    if mc and _should_use_mineru(url):
        result = await _exec_mineru(mc, url)
        if result.success or _is_pdf(url):
            return result

    if _is_pdf(url):
        fallback = await _pdf_fallback(url)
        if fallback:
            return fallback

    for provider in _read_provider_order():
        if provider == "jina":
            result = await _try_jina(url)
        elif provider == "firecrawl":
            result = await _try_firecrawl(url)
        elif provider == "trafilatura":
            result = await _try_trafilatura(url)
        else:
            continue
        if result["status"] == "success":
            return ToolResult.ok(data={"url": url, "content": str(result["content"]).strip(), "parser": str(result.get("parser", provider))})

    msg = "No reader provider returned usable content"
    return ToolResult.fail(msg)


async def _exec_mineru(mc: MineruClient, url: str) -> ToolResult:
    try:
        parsed = await asyncio.to_thread(mc.parse_remote_url, url)
        content = str(parsed.get("content", "")).strip()
        if not content:
            raise ValueError("empty content from MinerU")
        return ToolResult.ok(data={"url": url, "content": content, "parser": parsed.get("parser")})
    except Exception as exc:
        if _is_pdf(url):
            fallback = await _pdf_fallback(url)
            if fallback:
                return fallback
        return ToolResult.fail(f"web_read failed: {exc}")


async def _pdf_fallback(url: str) -> ToolResult | None:
    try:
        content = await asyncio.to_thread(_extract_pdf_markdown_from_url, url, timeout_seconds=30.0)
        if content.strip():
            return ToolResult.ok(data={"url": url, "content": content, "parser": "pymupdf4llm"})
    except Exception:
        pass
    return None


async def _try_jina(url: str) -> dict[str, Any]:
    if _skip_jina_without_proxy() and not _has_proxy_env():
        return {"status": "skipped", "detail": "proxy not detected; jina skipped"}
    key = os.getenv("JINA_API_KEY")
    if not key:
        return {"status": "skipped", "detail": "JINA_API_KEY missing"}
    try:
        import aiohttp
        target = _normalize_reader_target(url)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_timeout()), trust_env=True) as s:
            async with s.get(f"https://r.jina.ai/http://{target}", headers={"Authorization": f"Bearer {key}"}) as r:
                content = await r.text()
                if r.status >= 400:
                    return {"status": "error", "detail": f"HTTP {r.status}: {content[:300]}"}
                if not content.strip():
                    return {"status": "error", "detail": "empty content"}
                return {"status": "success", "content": content, "parser": "jina-reader"}
    except asyncio.TimeoutError:
        return {"status": "error", "detail": "timed out"}
    except Exception as e:
        return {"status": "error", "detail": _summarize_error(e)}


async def _try_firecrawl(url: str) -> dict[str, Any]:
    key = _resolve_firecrawl_api_key()
    if not key:
        return {"status": "skipped", "detail": "FIRECRAWL_API_KEY missing"}
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=max(_timeout(), 30)), trust_env=True) as s:
            async with s.post("https://api.firecrawl.dev/v2/scrape", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json={"url": url, "formats": ["markdown"], "onlyMainContent": True}) as r:
                p = await r.json()
                if r.status >= 400:
                    return {"status": "error", "detail": f"HTTP {r.status}"}
                md = str(p.get("data", p).get("markdown", "")).strip()
                if not md:
                    return {"status": "error", "detail": "empty markdown"}
                return {"status": "success", "content": md, "parser": "firecrawl"}
    except asyncio.TimeoutError:
        return {"status": "error", "detail": "timed out"}
    except Exception as e:
        return {"status": "error", "detail": _summarize_error(e)}


async def _try_trafilatura(url: str) -> dict[str, Any]:
    try:
        content = await asyncio.to_thread(_extract_trafilatura, url)
        if not content:
            return {"status": "error", "detail": "empty content"}
        return {"status": "success", "content": content, "parser": "trafilatura"}
    except Exception as e:
        return {"status": "error", "detail": _summarize_error(e)}


def _extract_trafilatura(url: str) -> str:
    import trafilatura
    d = trafilatura.fetch_url(url)
    return (trafilatura.extract(d) or "").strip() if d else ""


def _normalize_reader_target(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme:
        suffix = f"?{parsed.query}" if parsed.query else ""
        return parsed.netloc + parsed.path + suffix
    return url


def _should_use_mineru(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(tuple(MINERU_SUPPORTED_SUFFIXES))


def _is_pdf(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def register_web_tools(r) -> None:
    _p = Path(__file__).resolve().parent / "descriptions"
    def _ld(n):
        return (_p / f"{n}.txt").read_text(encoding="utf-8").strip() if (_p / f"{n}.txt").exists() else ""
    r.register("web_read", "retrieval", {
    "name": "web_read",
    "description": _ld("web_read"),
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string", "description": "要读取的网页 URL"},
    }, "required": ["url"]},
    }, handle_web_read, concurrency_safe=True, read_only=True)
