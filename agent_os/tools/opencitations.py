"""
OpenCitations COCI tool — citation graph exploration.

COCI (OpenCitations Index) is a free, open citation graph:
- 1.2B+ citation links between 75M+ bibliographic entities
- All data CC0 — no copyright restrictions
- Sources: Crossref, PubMed Central, DataCite
- Tracks journal self-citation and author self-citation flags
- No API key required (free access token optional for higher rate)

Usage:
  opencitations_search(doi="10.1038/s41586-023-06221-2", mode="citations")
    → who cites this paper
  opencitations_search(doi="10.1038/s41586-023-06221-2", mode="references")
    → what this paper cites
  opencitations_search(pmid="12345678", mode="citation_count")
    → how many citations
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import requests as _requests

from .registry import ToolResult

COCI_BASE = "https://api.opencitations.net/index/v2"
COCI_KEY = None  # optional: set OPEN_CITATIONS_TOKEN env var
if COCI_KEY is None:
    import os
    _token = os.getenv("OPEN_CITATIONS_TOKEN", "")
    COCI_KEY = _token if _token else None
_DESC_DIR = Path(__file__).resolve().parent / "descriptions"
_last_coci_call: float = 0
_coci_lock = asyncio.Lock()
_COCI_RATE = 0.35  # 180 req/min → ~0.34s, use 0.35 for safety


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _normalize_id(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith("pmid:"):
        return identifier
    if identifier.startswith("doi:"):
        return identifier
    if re.match(r"^\d+$", identifier):
        return f"pmid:{identifier}"
    if identifier.startswith("10."):
        return f"doi:{identifier}"
    if "doi.org/" in identifier:
        clean = identifier.split("doi.org/")[-1].split("?")[0]
        return f"doi:{clean}"
    return identifier


def _coci_api_get(url: str, headers: dict | None = None, timeout: float = 20.0) -> _requests.Response:
    h = {"User-Agent": "AgentOS/1.0"}
    if COCI_KEY:
        h["authorization"] = COCI_KEY
    if headers:
        h.update(headers)
    r = _requests.get(url, headers=h, timeout=timeout)
    if r.status_code == 404:
        return r
    r.raise_for_status()
    return r


async def handle_opencitations_search(
    doi: str = "",
    pmid: str = "",
    mode: str = "citations",
    max_results: int = 100,
    **kw,
) -> ToolResult:
    """Query the OpenCitations COCI citation graph."""
    global _last_coci_call

    identifier = doi or pmid
    if not identifier:
        return ToolResult.fail("Provide doi or pmid.")
    pid = _normalize_id(identifier)

    valid_modes = {"citations", "references", "citation_count", "reference_count"}
    if mode not in valid_modes:
        return ToolResult.fail(f"Invalid mode: {mode}. Use: {', '.join(sorted(valid_modes))}.")

    endpoint_map = {
        "citations": f"{COCI_BASE}/citations/{pid}",
        "references": f"{COCI_BASE}/references/{pid}",
        "citation_count": f"{COCI_BASE}/citation-count/{pid}",
        "reference_count": f"{COCI_BASE}/reference-count/{pid}",
    }
    url = endpoint_map[mode]

    try:
        async with _coci_lock:
            elapsed = time.time() - _last_coci_call
            if elapsed < _COCI_RATE:
                await asyncio.sleep(_COCI_RATE - elapsed)
            _last_coci_call = time.time()

        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, _coci_api_get, url)
        if r.status_code == 404:
            return ToolResult.ok(data={
                "identifier": pid,
                "mode": mode,
                "total_count": 0,
                "results": [],
                "count": 0,
                "note": f"Identifier not found in OpenCitations: {pid}",
            })

        data = r.json()

        if mode in ("citation_count", "reference_count"):
            raw = data[0]["count"] if data else "0"
            return ToolResult.ok(data={
                "identifier": pid,
                "mode": mode,
                "count": int(raw),
            })

        results = []
        for item in data:
            citing_raw: str = item.get("citing", "")
            cited_raw: str = item.get("cited", "")
            results.append({
                "oci": item.get("oci", ""),
                "citing": _extract_doi(citing_raw),
                "citing_pids": citing_raw,
                "cited": _extract_doi(cited_raw),
                "cited_pids": cited_raw,
                "creation": item.get("creation", ""),
                "timespan": item.get("timespan", ""),
                "journal_self_citation": item.get("journal_sc", ""),
                "author_self_citation": item.get("author_sc", ""),
            })
            if len(results) >= max_results:
                break

        return ToolResult.ok(data={
            "identifier": pid,
            "mode": mode,
            "total_count": len(data),
            "results": results,
            "count": len(results),
        })

    except Exception as e:
        return ToolResult.fail(f"OpenCitations API error: {e}")


def _extract_doi(pids: str) -> str:
    if not pids:
        return ""
    m = re.search(r'\b(10\.\d{4,}(?:\.\d+)*/[^\s]+)', pids)
    return m.group(1).rstrip(".") if m else pids


def register_opencitations_tools(r) -> None:
    r.register("opencitations_search", "retrieval", {
        "name": "opencitations_search",
        "description": _load_desc("opencitations_search"),
        "parameters": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI（如 10.1038/s41586-023-06221-2）"},
                "pmid": {"type": "string", "description": "PubMed ID（如 12345678）"},
                "mode": {
                    "type": "string",
                    "description": "操作模式：citations（谁引用了此论文）、references（此论文引用谁）、citation_count（被引数）、reference_count（参考文献数）",
                    "enum": ["citations", "references", "citation_count", "reference_count"],
                },
                "max_results": {"type": "integer", "description": "最大返回结果数（默认 100）"},
            },
            "required": [],
        },
    }, handle_opencitations_search, concurrency_safe=True, read_only=True)
