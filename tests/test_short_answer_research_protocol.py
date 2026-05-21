import asyncio
from pathlib import Path


def test_agent_system_includes_short_answer_research_protocol():
    prompt = Path("agent_os/prompts/agent_system.txt").read_text(encoding="utf-8")

    assert "<task_mode_router>" in prompt
    assert "answer_contract" in prompt
    assert "short_answer" in prompt
    assert "long_form_report" in prompt
    assert "<short_answer_research>" in prompt
    assert "Question Model" in prompt
    assert "Candidate Ledger" in prompt
    assert "Evidence Ledger" in prompt
    assert "discriminating query" in prompt
    assert "Final Review Gate" in prompt
    assert "<long_form_research>" in prompt
    assert "Coverage Map" in prompt
    assert "Source Strategy" in prompt
    assert "Report Review Gate" in prompt
    assert "<report_writing_protocol>" in prompt
    assert "Citation Rules" in prompt
    assert "Anti-AI-Tone Editing" in prompt
    assert "Formula and Number Rules" in prompt


def test_deep_research_protocol_is_not_a_loadable_skill():
    assert not Path("skills/research/deep_research/SKILL.md").exists()


def test_persistent_prompts_absorb_short_answer_research_discipline():
    agent = Path("agent_os/prompts/AGENT.md").read_text(encoding="utf-8")
    soul = Path("agent_os/prompts/SOUL.md").read_text(encoding="utf-8")
    memory = Path("agent_os/prompts/memory_guidance.txt").read_text(encoding="utf-8")

    assert "Short-Answer Research" in agent
    assert "Task Mode Routing" in agent
    assert "Candidate Ledger" in agent
    assert "Evidence Ledger" in agent
    assert "Convergence Rules" in agent
    assert "Long-Form Research" in agent
    assert "Coverage Map" in agent
    assert "Report Review Gate" in agent
    assert "Report Writing Protocol" in agent
    assert "Remove AI tone" in agent
    assert "Formulas and numbers" in agent
    assert "Do not conflate approved / discovered / put into production / launched / mass-produced" in agent
    assert "反确认偏误" in soul
    assert "校准置信度" in soul
    assert "报告效用" in soul
    assert "引用忠实" in soul
    assert "专业表达" in soul
    assert "Candidate Ledger" in memory
    assert "Evidence Ledger" in memory
    assert "Coverage Map" in memory
    assert "Report Assumptions" in memory
    assert "Report Style Preferences" in memory


def test_default_memory_template_is_research_oriented(tmp_path):
    from agent_os.core.session import SessionManager

    manager = SessionManager(data_dir=str(tmp_path))
    created = asyncio.run(manager.create(name="memory-template"))
    memory = Path(created.work_dir, "MEMORY.md").read_text(encoding="utf-8")

    assert "Question Model" in memory
    assert "Candidate Ledger" in memory
    assert "Evidence Ledger" in memory
    assert "Update Conditions" in memory
