"""
domain_sites tool — authoritative site directory for domain-locked web_search.

Reads skills/domain_sites/sites.yaml and returns site: patterns
for the requested domain, so the model can lock searches to authoritative sources.
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from agent_os.tools.registry import ToolResult

_SITES_PATH = Path(__file__).resolve().parent.parent.parent.parent / "skills" / "domain_sites" / "sites.yaml"
_sites_cache: dict[str, list[str]] | None = None


def _load_sites() -> dict[str, list[str]]:
    global _sites_cache
    if _sites_cache is not None:
        return _sites_cache
    if _SITES_PATH.exists():
        with open(_SITES_PATH, encoding="utf-8") as f:
            _sites_cache = yaml.safe_load(f) or {}
    else:
        _sites_cache = {}
    return _sites_cache


async def handle_domain_sites(domain: str = "", **kw: Any) -> ToolResult:
    sites = _load_sites()
    domain = domain.strip()

    if not domain:
        return ToolResult.ok(data={
            "available_domains": sorted(sites.keys()),
            "hint": "Call with a specific domain, e.g. domain_sites(domain=\"legal_cn\")",
        })

    if domain not in sites:
        return ToolResult.ok(data={
            "error": f"Unknown domain: {domain}",
            "available_domains": sorted(sites.keys()),
            "hint": "Choose from the available domains above.",
        })

    domain_list = sites[domain]
    return ToolResult.ok(data={
        "domain": domain,
        "sites": domain_list,
        "site_operators": [f"site:{s}" for s in domain_list],
        "include_domains": domain_list,
        "hint": f"Use include_domains={domain_list} or add site: operators to your web_search query.",
    })


def register(r) -> None:
    r.register(
        "domain_sites",
        "reasoning",
        {
            "name": "domain_sites",
            "description": (
                "Look up authoritative website domains for a given research domain. "
                "Returns site: patterns and include_domains lists to lock web_search "
                "to authoritative sources. Call BEFORE web_search when the research "
                "domain is known. For multi-domain questions, call once per domain "
                "and combine the results.\n\n"
                "Available domains:\n"
                "Tier 1 (elite): elite_multidisciplinary\n"
                "Tier 2 (field): cs_conferences, biology, materials, chemistry, "
                "environment, mathematics, physics, legal_cn, legal_en, legal_historical, "
                "medical, academic, historical, scientific, tech_programming, finance, "
                "government_cn, government_en, patents, news_factcheck, encyclopedia, "
                "geography, standards, film_media\n"
                "Tier 3 (common): common_knowledge"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": (
                            "Research domain key. Choose from: elite_multidisciplinary, "
                            "cs_conferences, biology, materials, chemistry, environment, "
                            "mathematics, physics, legal_cn, legal_en, legal_historical, "
                            "medical, academic, historical, scientific, tech_programming, "
                            "finance, government_cn, government_en, patents, news_factcheck, "
                            "encyclopedia, geography, standards, film_media, common_knowledge. "
                            "Leave empty to list all available domains."
                        ),
                    },
                },
                "required": [],
            },
        },
        handle_domain_sites,
        concurrency_safe=True,
        read_only=True,
    )
