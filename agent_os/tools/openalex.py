"""
OpenAlex API tools — structured academic search with auto name-to-ID resolution.

OpenAlex is a free, open catalog of the global research system:
- 270M+ works, 90M+ authors, 250K+ sources (journals/repos/conferences)
- Structured filters: author, institution, venue, topic, year, type — 150+ filters
- Semantic search: find conceptually related works by meaning
- Reference fingerprint search: find papers that cite specific papers
- DOI/external-ID exact lookup
- Free: $1/day with API key
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import requests as _requests

from .registry import ToolResult

OPENALEX_BASE = "https://api.openalex.org"
OPENALEX_KEY = os.getenv("OPENALEX_API_KEY")
OPENALEX_REQUEST_TIMEOUT = 20.0
_DESC_DIR = Path(__file__).resolve().parent / "descriptions"


def _openalex_get(url: str, params: dict | None = None, timeout: float | None = None) -> _requests.Response:
    """Sync GET to OpenAlex via requests. Reliable from China."""
    t = timeout or OPENALEX_REQUEST_TIMEOUT
    r = _requests.get(url, params=params or {}, timeout=t,
                      headers={"User-Agent": "AgentOS/1.0"})
    r.raise_for_status()
    return r

_entity_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ENTITY_CACHE_TTL = 3600


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _short_id(full_id: str) -> str:
    if not full_id:
        return ""
    return full_id.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

async def _resolve_entity(
    entity_type: str, name: str, timeout: float = 10.0
) -> list[dict[str, Any]]:
    cache_key = f"{entity_type}:{name.lower().strip()}"
    now = time.time()
    if cache_key in _entity_cache:
        ts, data = _entity_cache[cache_key]
        if now - ts < _ENTITY_CACHE_TTL:
            return data

    ep_map = {
        "author": "authors", "institution": "institutions",
        "source": "sources", "venue": "sources", "topic": "topics",
    }
    ep = ep_map.get(entity_type, entity_type)
    params: dict[str, Any] = {"search": name, "per_page": 5}
    if OPENALEX_KEY:
        params["api_key"] = OPENALEX_KEY

    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, _openalex_get, f"{OPENALEX_BASE}/{ep}", params, timeout)
        data = r.json()
        results: list[dict[str, Any]] = []
        for item in (data.get("results") or []):
            results.append({
                "id": _short_id(item.get("id", "")),
                "display_name": item.get("display_name", ""),
                "works_count": item.get("works_count", 0),
                "cited_by_count": item.get("cited_by_count", 0),
                "relevance_score": item.get("relevance_score"),
            })
        for i, item in enumerate(data.get("results") or []):
            if i >= len(results):
                break
            if entity_type in ("institution",):
                results[i]["country_code"] = item.get("country_code", "")
                results[i]["type"] = item.get("type", "")
            elif entity_type in ("source", "venue"):
                results[i]["type"] = item.get("type", "")
                results[i]["issn"] = (item.get("issn_l") or [None])[0] if item.get("issn_l") else ""
        _entity_cache[cache_key] = (time.time(), results)
        return results
    except Exception:
        return []


async def _resolve_and_get_id(entity_type: str, name: str) -> tuple[str | None, str]:
    results = await _resolve_entity(entity_type, name)
    if not results:
        return None, f"No {entity_type} found matching '{name}'"
    return results[0]["id"], results[0]["display_name"]


async def _resolve_work_id(identifier: str, timeout: float = 10.0) -> str | None:
    identifier = identifier.strip()
    if not identifier:
        return None
    if identifier.upper().startswith("W") and identifier[1:].isdigit():
        return _short_id(identifier)
    if identifier.startswith("10.") or "doi.org/" in identifier:
        clean = identifier.replace("https://doi.org/", "").replace("doi:", "")
        params = {}
        if OPENALEX_KEY:
            params["api_key"] = OPENALEX_KEY
        try:
            loop = asyncio.get_running_loop()
            r = await loop.run_in_executor(None, _openalex_get,
                f"{OPENALEX_BASE}/works/doi:{clean}", params, timeout)
            return _short_id(r.json().get("id", ""))
        except Exception:
            pass
    params: dict[str, Any] = {"search": identifier, "per_page": 1}
    if OPENALEX_KEY:
        params["api_key"] = OPENALEX_KEY
    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, _openalex_get, f"{OPENALEX_BASE}/works", params)
        results = r.json().get("results", [])
        if results:
            return _short_id(results[0].get("id", ""))
    except Exception:
        pass
    return None


async def _resolve_work_ids(identifiers: str) -> tuple[list[str], list[str]]:
    ids = [s.strip() for s in identifiers.split(",") if s.strip()]
    if not ids:
        return [], []
    tasks = [_resolve_work_id(i) for i in ids]
    results = await asyncio.gather(*tasks)
    found = [r for r, orig in zip(results, ids) if r]
    not_found = [orig for r, orig in zip(results, ids) if not r]
    return found, not_found

# ---------------------------------------------------------------------------
# Work formatting
# ---------------------------------------------------------------------------

def _format_work(w: dict) -> dict[str, Any]:
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in (w.get("authorships") or [])
    ]
    insts: list[str] = []
    for a in (w.get("authorships") or []):
        for inst in (a.get("institutions") or []):
            name = inst.get("display_name", "")
            if name and name not in insts:
                insts.append(name)
    source = (w.get("primary_location") or {}).get("source") or {}
    topics_list = [
        t.get("display_name", "") for t in (w.get("topics") or [])
    ]
    return {
        "title": w.get("title", ""),
        "doi": (w.get("doi", "") or "").replace("https://doi.org/", ""),
        "id": _short_id(w.get("id", "")),
        "publication_date": w.get("publication_date", ""),
        "type": w.get("type", ""),
        "authors": authors,
        "author_count": len(authors),
        "institutions": insts,
        "venue": source.get("display_name", ""),
        "source_type": source.get("type", ""),
        "cited_by_count": w.get("cited_by_count", 0),
        "is_oa": (w.get("open_access") or {}).get("is_oa", False),
        "topics": topics_list,
        "abstract": _truncate_abstract(w),
        "referenced_works_count": w.get("referenced_works_count", 0),
    }


def _truncate_abstract(w: dict) -> str:
    ai = w.get("abstract_inverted_index")
    if not ai:
        return ""
    try:
        words = [""] * (max(max(v) for v in ai.values()) + 1)
        for word, positions in ai.items():
            for p in positions:
                words[p] = word
        return " ".join(words)[:2000]
    except Exception:
        return ""


async def _get_work_by_doi(doi: str) -> ToolResult:
    clean_doi = doi.replace("https://doi.org/", "").strip()
    params = {}
    if OPENALEX_KEY:
        params["api_key"] = OPENALEX_KEY
    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, _openalex_get, f"{OPENALEX_BASE}/works/doi:{clean_doi}", params)
        if r.status_code == 404:
            return ToolResult.fail(f"DOI not found: {clean_doi}")
        return ToolResult.ok(data=_format_work(r.json()))
    except Exception as e:
        return ToolResult.fail(f"OpenAlex: {e}")


async def _get_work_by_id(openalex_id: str) -> ToolResult:
    short = _short_id(openalex_id)
    params = {}
    if OPENALEX_KEY:
        params["api_key"] = OPENALEX_KEY
    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, _openalex_get, f"{OPENALEX_BASE}/works/{short}", params)
        if r.status_code == 404:
            return ToolResult.fail(f"Work not found: {short}")
        return ToolResult.ok(data=_format_work(r.json()))
    except Exception as e:
        return ToolResult.fail(f"OpenAlex: {e}")

# ---------------------------------------------------------------------------
# Main tool handlers
# ---------------------------------------------------------------------------

async def handle_openalex_works(
    author: str = "",
    institution: str = "",
    venue: str = "",
    topic: str = "",
    title: str = "",
    year: str = "",
    type: str = "",
    doi: str = "",
    openalex_id: str = "",
    source_type: str = "",
    references: str = "",
    has_references: bool | None = None,
    language: str = "",
    indexed_in: str = "",
    is_oa: bool | None = None,
    sort: str = "",
    per_page: int = 25,
    **kw,
) -> ToolResult:
    """
    Search OpenAlex works with automatic name-to-ID resolution and reference fingerprinting.

    Key features:
    - references: comma-separated paper titles or DOIs → finds papers that cite ALL of them.
      Example: references="Language Models are Few-Shot Learners,A Simple Framework for Contrastive Learning"
      This is "reference fingerprint" search — uniquely identify a paper by its citation list.
    - source_type: filter by source type (conference, journal, repository, ebook, book series)
    - indexed_in: filter by index source (arxiv, crossref, doaj, pubmed)
    """
    if doi:
        return await _get_work_by_doi(doi)
    if openalex_id:
        return await _get_work_by_id(openalex_id)

    filters: list[str] = []
    resolution_notes: list[str] = []
    venue_expanded = False

    venue_fallbacks = {
        "icml": "International Conference on Machine Learning",
        "iclr": "International Conference on Learning Representations",
        "neurips": "Conference on Neural Information Processing Systems",
        "nips": "Conference on Neural Information Processing Systems",
        "cvpr": "IEEE Conference on Computer Vision and Pattern Recognition",
        "eccv": "European Conference on Computer Vision",
        "acl": "Annual Meeting of the Association for Computational Linguistics",
        "emnlp": "Conference on Empirical Methods in Natural Language Processing",
        "aaai": "AAAI Conference on Artificial Intelligence",
        "ijcai": "International Joint Conference on Artificial Intelligence",
    }

    async def _add_filter(etype: str, name: str, filter_template: str):
        nonlocal venue_expanded
        if not name:
            return
        eid, display = await _resolve_and_get_id(etype, name)
        if not eid and etype == "venue" and name.lower().strip() in venue_fallbacks:
            full_name = venue_fallbacks[name.lower().strip()]
            eid, display = await _resolve_and_get_id(etype, full_name)
            if eid:
                venue_expanded = True
                resolution_notes.append(f"venue '{name}' → auto-expanded to '{full_name}' → {display} ({eid})")
        if eid:
            filters.append(filter_template.format(eid))
            if not (etype == "venue" and venue_expanded):
                resolution_notes.append(f"{etype} '{name}' → {display} ({eid})")
        else:
            resolution_notes.append(f"{etype} '{name}' → NOT FOUND")

    await asyncio.gather(
        _add_filter("author", author, "authorships.author.id:{}"),
        _add_filter("institution", institution, "authorships.institutions.id:{}"),
        _add_filter("venue", venue, "locations.source.id:{}"),
        _add_filter("topic", topic, "topics.id:{}"),
    )

    if year:
        y = str(year).strip()
        if "-" in y:
            try:
                start_s, end_s = [p.strip() for p in y.split("-", 1)]
                start_i = int(start_s)
                end_i = int(end_s)
                if start_i > end_i:
                    return ToolResult.fail("Invalid year range: start year is greater than end year.")
                filters.append(f"from_publication_date:{start_i}-01-01")
                filters.append(f"to_publication_date:{end_i}-12-31")
            except ValueError:
                return ToolResult.fail("Invalid year range. Use YYYY or YYYY-YYYY.")
        else:
            filters.append(f"publication_year:{y}")
    if type:
        filters.append(f"type:{type}")
    if source_type:
        filters.append(f"primary_location.source.type:{source_type}")
    if language:
        filters.append(f"language:{language}")
    if indexed_in:
        filters.append(f"indexed_in:{indexed_in}")
    if is_oa is not None:
        filters.append(f"is_oa:{str(is_oa).lower()}")
    if has_references is not None:
        filters.append(f"has_references:{str(has_references).lower()}")

    # Reference fingerprint — resolve paper identifiers to OpenAlex IDs
    if references:
        ref_ids, not_found = await _resolve_work_ids(references)
        for rid in ref_ids:
            filters.append(f"referenced_works:{rid}")
        if ref_ids:
            resolution_notes.append(f"references: resolved {len(ref_ids)} paper(s) to IDs: {ref_ids}")
        if not_found:
            resolution_notes.append(f"references: {len(not_found)} paper(s) not found: {not_found}")

    params: dict[str, Any] = {"per_page": min(per_page, 100)}
    if OPENALEX_KEY:
        params["api_key"] = OPENALEX_KEY

    if title:
        params["search"] = title
    elif filters:
        if sort:
            params["sort"] = sort
        params["filter"] = ",".join(filters)
    elif doi:
        pass
    else:
        return ToolResult.fail("Provide at least one of: author, institution, venue, topic, title, year, references, doi, openalex_id")

    try:
        loop = asyncio.get_running_loop()
        r = await loop.run_in_executor(None, _openalex_get, f"{OPENALEX_BASE}/works", params)
        data = r.json()
        meta = data.get("meta", {})
        works = data.get("results", [])

        results = [_format_work(w) for w in works]

        return ToolResult.ok(data={
            "total_count": meta.get("count", 0),
            "results": results,
            "count": len(results),
            "resolution": resolution_notes if resolution_notes else None,
            "filters_applied": filters,
        })
    except Exception as e:
        return ToolResult.fail(f"OpenAlex API error: {e}")


async def handle_openalex_entity(
    entity_type: str,
    search: str = "",
    per_page: int = 10,
    **kw,
) -> ToolResult:
    """Search OpenAlex entities: authors, institutions, sources, topics."""
    valid = {"author", "authors", "institution", "institutions",
             "source", "sources", "venue", "topic", "topics"}
    if entity_type not in valid:
        return ToolResult.fail(f"Invalid entity_type: {entity_type}. Use: author, institution, source, topic.")
    results = await _resolve_entity(entity_type.rstrip("s"), search, timeout=30.0)
    if not results:
        return ToolResult.ok(data={"query": search, "results": [], "count": 0, "note": "No matches found"})
    page_size = min(max(int(per_page or 10), 1), 100)
    return ToolResult.ok(data={
        "entity_type": entity_type,
        "query": search,
        "results": results[:page_size],
        "count": min(len(results), page_size),
        "total_found": len(results),
    })


def register_openalex_tools(r) -> None:
    r.register("openalex_works", "retrieval", {
        "name": "openalex_works",
        "description": _load_desc("openalex_works"),
        "parameters": {
            "type": "object",
            "properties": {
                "author": {"type": "string", "description": "作者名，自动解析为 ID 后过滤（如 \"Yoshua Bengio\"）"},
                "institution": {"type": "string", "description": "机构名，自动解析为 ID（如 \"Stanford University\"）"},
                "venue": {"type": "string", "description": '期刊/会议名，自动解析为 ID。"ICML" 等缩写自动扩展'},
                "topic": {"type": "string", "description": "研究主题，自动解析为 ID（如 \"Graph Contrastive Learning\"）"},
                "title": {"type": "string", "description": "标题/摘要关键词全文搜索"},
                "year": {"type": "string", "description": "发表年份（\"2022\"）或范围（\"2020-2024\"）"},
                "type": {"type": "string", "description": "类型：article, book, dataset 等"},
                "source_type": {"type": "string", "description": "源类型：conference, journal, repository, ebook, book series"},
                "references": {"type": "string", "description": "引用指纹搜索。逗号分隔的论文标题或 DOI。查找引用了全部指定论文的论文。如 \"Language Models are Few-Shot Learners,A Simple Framework for Contrastive Learning\""},
                "has_references": {"type": "boolean", "description": "仅返回有参考文献列表的论文"},
                "language": {"type": "string", "description": "语言代码（如 en, zh）"},
                "indexed_in": {"type": "string", "description": "数据来源：arxiv, crossref, doaj, pubmed"},
                "is_oa": {"type": "boolean", "description": "仅开放获取论文"},
                "doi": {"type": "string", "description": "按 DOI 精确查找单篇论文"},
                "openalex_id": {"type": "string", "description": "按 OpenAlex ID 精确查找（如 W2741809807）"},
                "sort": {"type": "string", "description": "排序：cited_by_count:desc, publication_date:desc。title 搜索时不设 sort"},
                "per_page": {"type": "integer", "description": "每页结果数（默认 25，最大 100）"},
            },
            "required": [],
        },
    }, handle_openalex_works, concurrency_safe=True, read_only=True)

    r.register("openalex_entity", "retrieval", {
        "name": "openalex_entity",
        "description": _load_desc("openalex_entity"),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "description": "实体类型：author, institution, source, topic"},
                "search": {"type": "string", "description": "搜索关键词"},
                "per_page": {"type": "integer", "description": "每页结果数（默认 10）"},
            },
            "required": ["entity_type", "search"],
        },
    }, handle_openalex_entity, concurrency_safe=True, read_only=True)
