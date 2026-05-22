from pathlib import Path


def test_agent_system_includes_core_research_infrastructure():
    prompt = Path("agent_os/prompts/agent_system.txt").read_text(encoding="utf-8")

    # Core principles
    assert "<core_principle>" in prompt
    assert "constraint-driven reasoning" in prompt
    assert "reasoning first" in prompt or "reason about what they imply" in prompt

    # Task mode routing
    assert "<task_mode_routing>" in prompt
    assert "short_answer" in prompt
    assert "long_form_report" in prompt
    assert "skill_use" in prompt

    # Universal research loop; mode-specific discipline lives in skills
    assert "<universal_research_loop>" in prompt
    assert "PARSE" in prompt
    assert "VERIFY_PREMISES" in prompt
    assert "REASON" in prompt

    # System prompt should not duplicate the full skills
    assert "<short_answer_discipline>" not in prompt
    assert "<long_form_discipline>" not in prompt
    assert "<report_writing_protocol>" not in prompt
    assert "<reasoning_strategy>" in prompt
    assert "Research requires explicit thinking strategy" in prompt

    # Tool references
    assert "research_state" in prompt
    assert "action_card" in prompt


def test_short_answer_and_long_form_skills_exist():
    sa = Path("skills/short_answer_research/SKILL.md")
    assert sa.exists(), f"Missing skill: {sa}"
    body_sa = sa.read_text(encoding="utf-8")
    assert "Question Model" in body_sa
    assert "Candidate Ledger" in body_sa
    assert "Evidence Ledger" in body_sa
    assert "discriminating query" in body_sa
    assert "Final Review Gate" in body_sa
    assert "Convergence Rules" in body_sa
    assert "PREMISE_CHECK" in body_sa
    assert "Reason-Before-Search" in body_sa

    lf = Path("skills/long_form_research/SKILL.md")
    assert lf.exists(), f"Missing skill: {lf}"
    body_lf = lf.read_text(encoding="utf-8")
    assert "Coverage Map" in body_lf
    assert "Source Strategy" in body_lf
    assert "Report Review Gate" in body_lf
    assert "ECRI" in body_lf


def test_deep_research_protocol_is_not_a_loadable_skill():
    assert not Path("skills/research/deep_research/SKILL.md").exists()


def test_persistent_prompts_absorb_research_discipline():
    agent = Path("agent_os/prompts/AGENT.md").read_text(encoding="utf-8")
    soul = Path("agent_os/prompts/SOUL.md").read_text(encoding="utf-8")
    memory = Path("agent_os/prompts/memory_guidance.txt").read_text(encoding="utf-8")

    assert "short_answer" in agent
    assert "long_form_report" in agent
    assert "skill_use" in agent
    assert "research_state" in agent
    assert "Research Habits" in agent
    # The full discipline (Convergence Rules, Report Review Gate, etc.) lives in skills now
    # AGENT.md is the quick-reference companion, not the complete protocol

    # SOUL.md should still have its quality content
    assert "反确认偏误" in soul
    assert "校准置信度" in soul
    assert "报告效用" in soul
    assert "引用忠实" in soul
    assert "专业表达" in soul

    # memory_guidance.txt should still have research infrastructure
    assert "Candidate Ledger" in memory
    assert "Evidence Ledger" in memory
    assert "Coverage Map" in memory
    assert "Report Assumptions" in memory
    assert "Report Style Preferences" in memory


def test_constraint_reasoning_skill_is_discoverable():
    from agent_os.skills.loader import SkillLoader

    loader = SkillLoader()
    loader.discover_all()
    skill = loader.resolve_skill("constraint_reasoning")

    assert skill is not None
    body = loader.get_skill_body("constraint_reasoning") or ""
    assert "associative" in body
    assert "linguistic" in body
    assert "geographic" in body


def test_short_answer_skill_is_discoverable():
    from agent_os.skills.loader import SkillLoader

    loader = SkillLoader()
    loader.discover_all()
    skill = loader.resolve_skill("short_answer_research")

    assert skill is not None, "short_answer_research skill not found"
    body = loader.get_skill_body("short_answer_research") or ""
    assert "State Machine" in body
    assert "PREMISE_CHECK" in body


def test_long_form_skill_is_discoverable():
    from agent_os.skills.loader import SkillLoader

    loader = SkillLoader()
    loader.discover_all()
    skill = loader.resolve_skill("long_form_research")

    assert skill is not None, "long_form_research skill not found"
    body = loader.get_skill_body("long_form_research") or ""
    assert "Research Lifecycle" in body
    assert "Coverage Map" in body


def test_default_memory_template_is_research_oriented(tmp_path):
    from agent_os.core.session import SessionManager

    manager = SessionManager(data_dir=str(tmp_path))
    created = __import__("asyncio").run(manager.create(name="memory-template"))
    memory = Path(created.work_dir, "MEMORY.md").read_text(encoding="utf-8")

    assert "Question Model" in memory
    assert "Candidate Ledger" in memory
    assert "Evidence Ledger" in memory
    assert "Update Conditions" in memory


def test_workspace_md_is_not_default_dynamic_entrypoint(tmp_path):
    from agent_os.core.session import SessionManager

    manager = SessionManager(data_dir=str(tmp_path))
    created = __import__("asyncio").run(manager.create(name="memory-entrypoint"))

    assert not Path(created.work_dir, "workspace.md").exists()
    assert Path(created.work_dir, "MEMORY.md").exists()
