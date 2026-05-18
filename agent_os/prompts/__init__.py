"""Prompt templates loaded from .txt files. Edit .txt to change prompts without touching Python."""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


def _load(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


class Prompts:
    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._dir = prompts_dir or _PROMPTS_DIR

    @property
    def agent_system(self) -> str:
        return _load("agent_system")

    def compression_prompt(self, content: str, previous_summary: str = "") -> str:
        raw = _load("compression")
        if previous_summary:
            raw = raw.replace("{previous_summary}", previous_summary)
        else:
            raw = raw.replace("<previous-summary>\n{previous_summary}\n</previous-summary>\n\n", "")
        return raw.format(content=content)

    @property
    def result_filter(self) -> str:
        return _load("result_filter")

    @property
    def sub_agent(self) -> str:
        return _load("sub_agent")

    @property
    def memory_guidance(self) -> str:
        return _load("memory_guidance")


_default_prompts = Prompts()

agent_system = _default_prompts.agent_system
compression_prompt = _default_prompts.compression_prompt
result_filter = _default_prompts.result_filter
sub_agent = _default_prompts.sub_agent
memory_guidance = _default_prompts.memory_guidance
