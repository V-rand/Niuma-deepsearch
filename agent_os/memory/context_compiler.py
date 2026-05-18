"""
Context compilation — builds XML-structured system prompt from workspace files and skill profiles.

Memory architecture (Claude Code style):
- System prompt: static memory taxonomy guidance (cached KV prefix)
- User context: actual MEMORY.md content (rebuilt per request)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.session import Session
from ..prompts import agent_system, memory_guidance

# Memory entrypoint limits (Claude Code: 200 lines / 25KB)
MAX_MEMORY_LINES = 200
MAX_MEMORY_BYTES = 25_000


@dataclass(slots=True)
class CompiledContext:
    system_prompt: str
    recent_messages: list[dict]
    memory_content: str = ""  # NEW: actual memory content for user context
    memory_snippets: list[str] = None

    def __post_init__(self):
        if self.memory_snippets is None:
            self.memory_snippets = []


class ContextCompiler:
    def __init__(self, *, retriever, skill_loader=None, max_messages: int = 8, max_items: int = 12):
        self.retriever = retriever
        self.skill_loader = skill_loader
        self.max_messages = max_messages
        self.max_items = max_items

    def compile(
        self,
        session: Session,
        *,
        user_message: str,
        recent_messages: list[dict],
        skill_context: str = "",
        skills_index: str = "",
        max_messages_override: int | None = None,
    ) -> CompiledContext:
        max_messages = max_messages_override or self.max_messages

        prompt_sections = [
            agent_system.replace("{{session_name}}", session.name or session.id),
        ]

        work_dir = Path(session.work_dir) if session.work_dir else None
        if work_dir and work_dir.exists():
            for name, tag in [("SOUL.md", "soul"), ("AGENT.md", "agent_config")]:
                path = work_dir / name
                if path.exists():
                    content = path.read_text(encoding="utf-8").strip()
                    if content:
                        prompt_sections.append(f"<{tag}>\n{content}\n</{tag}>")

        profile_name = (session.metadata or {}).get("workspace_profile", "")
        if self.skill_loader and profile_name:
            profile_prompt = self.skill_loader.get_profile_prompt(profile_name)
            if profile_prompt:
                prompt_sections.append(f"<profile>\n{profile_prompt}\n</profile>")

        # Static memory guidance (KV cache prefix - stable)
        if memory_guidance:
            prompt_sections.append(memory_guidance)

        if skill_context:
            prompt_sections.append(f"<skill_context>\n{skill_context}\n</skill_context>")
        if skills_index:
            prompt_sections.append(skills_index)

        # Build memory content (dynamic - goes in user context, not system prompt)
        memory_content = ""
        if work_dir and work_dir.exists():
            memory_path = work_dir / "MEMORY.md"
            if memory_path.exists():
                raw_content = memory_path.read_text(encoding="utf-8")
                content = raw_content.strip()
                if content:
                    # Truncate if exceeds limits
                    lines = content.split('\n')
                    if len(lines) > MAX_MEMORY_LINES or len(content) > MAX_MEMORY_BYTES:
                        original_lines = len(lines)
                        original_bytes = len(content)
                        truncated_lines = lines[:MAX_MEMORY_LINES]
                        truncated = '\n'.join(truncated_lines)
                        if len(truncated) > MAX_MEMORY_BYTES:
                            # Further truncate at last newline before limit
                            cut_at = truncated.rfind('\n', 0, MAX_MEMORY_BYTES)
                            if cut_at > 0:
                                truncated = truncated[:cut_at]
                            else:
                                truncated = truncated[:MAX_MEMORY_BYTES]
                        content = f"{truncated}\n\n> WARNING: MEMORY.md 超过限制（{original_lines} 行 / {original_bytes} 字节），只加载了前 {MAX_MEMORY_LINES} 行 / {MAX_MEMORY_BYTES} 字节。保持索引简洁，每行一个条目。"
                    memory_content = f"<memory>\n{content}\n</memory>"

        return CompiledContext(
            system_prompt="\n\n".join(prompt_sections),
            recent_messages=recent_messages[-max_messages:],
            memory_content=memory_content,
        )
