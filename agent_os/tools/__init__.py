"""
Tool system — registration functions and shared exports.
"""

from pathlib import Path
from typing import Any

from .registry import Tool, ToolEntry, ToolResult, ToolRegistry, get_tool_registry


def register_all(r: ToolRegistry | None = None) -> ToolRegistry:
    """Register all built-in tools and plugins into *r* (or the global registry)."""
    if r is None:
        r = get_tool_registry()
    from . import base_tools, web, search, skills, academic, media, research, openalex, pubmed
    base_tools.register_base_tools(r)
    web.register_web_tools(r)
    search.register_search_tools(r)
    skills.register_skill_tools(r)
    academic.register_academic_tools(r)
    media.register_media_tools(r)
    research.register_research_tools(r)
    openalex.register_openalex_tools(r)
    pubmed.register_pubmed_tools(r)
    from .plugins import discover_plugins
    n = discover_plugins(r)
    if n:
        import logging
        logging.getLogger(__name__).info("Loaded %d tool plugin(s)", n)
    return r
