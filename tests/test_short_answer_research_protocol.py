import asyncio
from pathlib import Path


def test_agent_system_includes_short_answer_research_protocol():
    prompt = Path("agent_os/prompts/agent_system.txt").read_text(encoding="utf-8")

    assert "<short_answer_research>" in prompt
    assert "Question Model" in prompt
    assert "Candidate Ledger" in prompt
    assert "Evidence Ledger" in prompt
    assert "discriminating query" in prompt
    assert "Final Review Gate" in prompt


def test_deep_research_protocol_is_not_a_loadable_skill():
    assert not Path("skills/research/deep_research/SKILL.md").exists()


def test_persistent_prompts_absorb_short_answer_research_discipline():
    agent = Path("agent_os/prompts/AGENT.md").read_text(encoding="utf-8")
    soul = Path("agent_os/prompts/SOUL.md").read_text(encoding="utf-8")
    memory = Path("agent_os/prompts/memory_guidance.txt").read_text(encoding="utf-8")

    assert "短答案研究" in agent
    assert "候选账本" in agent
    assert "证据账本" in agent
    assert "反证检查" in agent
    assert "不要把获批、发现、投产、上市、量产混为一谈" in agent
    assert "反确认偏误" in soul
    assert "校准置信度" in soul
    assert "Candidate Ledger" in memory
    assert "Evidence Ledger" in memory


def test_default_memory_template_is_research_oriented(tmp_path):
    from agent_os.core.session import SessionManager

    manager = SessionManager(data_dir=str(tmp_path))
    created = asyncio.run(manager.create(name="memory-template"))
    memory = Path(created.work_dir, "MEMORY.md").read_text(encoding="utf-8")

    assert "Question Model" in memory
    assert "Candidate Ledger" in memory
    assert "Evidence Ledger" in memory
    assert "Update Conditions" in memory
