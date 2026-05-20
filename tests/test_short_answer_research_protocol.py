from pathlib import Path


def test_agent_system_includes_short_answer_research_protocol():
    prompt = Path("agent_os/prompts/agent_system.txt").read_text(encoding="utf-8")

    assert "<short_answer_research>" in prompt
    assert "Question Model" in prompt
    assert "Candidate Ledger" in prompt
    assert "Evidence Ledger" in prompt
    assert "discriminating query" in prompt
    assert "Final Review Gate" in prompt


def test_deep_research_skill_describes_short_answer_mode():
    skill = Path("skills/research/deep_research/SKILL.md").read_text(encoding="utf-8")

    assert "短答案研究模式" in skill
    assert "候选账本" in skill
    assert "证据账本" in skill
    assert "反证检查" in skill
    assert "不要把获批、发现、投产、上市、量产混为一谈" in skill
