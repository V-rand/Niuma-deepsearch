from pathlib import Path


def test_skills_index_includes_full_descriptions(tmp_path):
    from agent_os.skills.loader import SkillLoader

    long_description = "A" * 160 + "TAIL_MARKER"
    skill_dir = tmp_path / "long_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: long_skill\ndescription: {long_description}\n---\n\n# Long Skill\n",
        encoding="utf-8",
    )

    loader = SkillLoader(skills_dir=str(tmp_path))
    loader.discover_all()

    index = loader.build_skills_index_prompt()

    assert long_description in index
    assert "TAIL_MARKER" in index
