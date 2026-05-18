"""
Skill tool — loads a skill's full instructions into the conversation.

Skills are instruction bundles (pure Markdown), not kernel workflows.
Only names and descriptions appear in the system prompt; the full body
is retrieved via this tool and enters the chat as a tool result, leaving
the KV cache prefix undisturbed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .registry import ToolResult, get_session_work_dir, get_tool_dep
from ..kernel.helpers import slug


def _load_desc(name: str) -> str:
    p = Path(__file__).resolve().parent / "descriptions" / f"{name}.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


if TYPE_CHECKING:
    from ..skills.loader import SkillLoader


def _skill_loader() -> SkillLoader | None:
    return get_tool_dep("skill_loader")


async def handle_skill_use(name, **kw) -> ToolResult:
    """Return the full body of a skill wrapped in <skill_content>."""
    sl = _skill_loader()
    if sl is None:
        return ToolResult.fail("Skill loader not available")
    skill = sl.resolve_skill(name)
    if skill is None:
        return ToolResult.fail(f"Skill not found: {name}")
    content = str(skill.get("content", ""))
    body = str(sl.get_skill_body(name) or content)
    base = skill.get("path", "")
    files = _supporting_files(skill)
    lines = [
        f"<skill_content name=\"{skill.get('name', name)}\">",
        body,
        "",
        f"Base directory: {base}",
        "Relative paths in this skill are relative to this directory.",
    ]
    if files:
        lines.append("<skill_files>")
        lines.extend(f"  {f}" for f in files)
        lines.append("</skill_files>")
    lines.append("</skill_content>")
    return ToolResult.ok(data={
        "name": skill.get("name", name),
        "description": skill.get("description", ""),
        "content": "\n".join(lines),
    })


async def handle_skill_propose(name, content, description="", **kw) -> ToolResult:
    """Propose a new skill or improvement. Writes to research/skill_proposals/ for human review."""
    wd = get_session_work_dir()
    if not wd:
        return ToolResult.fail("Missing session work directory")
    safe_name = str(name or "").strip()
    if not safe_name:
        return ToolResult.fail("Skill name is required")
    proposals_dir = Path(wd) / "research" / "skill_proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    skill_md = f"""---
name: {safe_name}
description: {description or "由 AI 自动提议的 skill"}
---

{content}
"""
    proposal_path = proposals_dir / f"{slug(safe_name)}.md"
    proposal_path.write_text(skill_md, encoding="utf-8")
    return ToolResult.ok(data={
        "path": f"research/skill_proposals/{slug(safe_name)}.md",
        "name": safe_name,
        "description": description,
        "note": "提案已写入 research/skill_proposals/，需人工审核后方可移入 skills/ 目录生效。",
    })


def _supporting_files(skill: dict[str, Any]) -> list[str]:
    base = Path(str(skill.get("path", "")))
    if not base.exists():
        return []
    files: list[str] = []
    for sub in ("references", "templates", "scripts", "assets"):
        d = base / sub
        if d.exists():
            for item in sorted(d.rglob("*")):
                if item.is_file() and not item.is_symlink():
                    files.append(str(item.relative_to(base)))
    return files[:80]


def register_skill_tools(r) -> None:
    r.register("skill_use", "skills", {
        "name": "skill_use",
        "description": _load_desc("skill_use"),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name as shown in <available_skills>"},
            },
            "required": ["name"],
        },
    }, handle_skill_use, concurrency_safe=True, read_only=True)
    r.register("skill_propose", "skills", {
        "name": "skill_propose",
        "description": _load_desc("skill_propose"),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill 名称（建议英文小写+下划线）"},
                "content": {"type": "string", "description": "Skill 的 Markdown 正文"},
                "description": {"type": "string", "description": "Skill 简短描述（可选）"},
            },
            "required": ["name", "content"],
        },
    }, handle_skill_propose)