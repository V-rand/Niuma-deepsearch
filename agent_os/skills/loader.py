"""
Skill Loader - 加载和管理 Skills

Skills 是纯 Markdown 提示词，不含可执行代码。
工具注册在公司层通过 registry.register() 完成，不在 skills 目录内执行。
"""

import re
from typing import Dict, List, Optional, Any
from pathlib import Path

import yaml


class SkillLoader:
    """Skill 加载器"""

    def __init__(
        self,
        skills_dir: Optional[str] = None,
        extra_skill_dirs: Optional[List[str | Path]] = None,
    ):
        if skills_dir is None:
            skills_dir = Path(__file__).parent.parent.parent / "skills"
        primary_dir = Path(skills_dir)
        self.skill_dirs: List[Path] = [primary_dir]
        for extra_dir in extra_skill_dirs or []:
            path = Path(extra_dir)
            if path not in self.skill_dirs:
                self.skill_dirs.append(path)
        self._skills: Dict[str, Dict[str, Any]] = {}
        self._conditional_skills: Dict[str, Dict[str, Any]] = {}
        self._activated_names: set[str] = set()

    def discover_all(self) -> Dict[str, Dict[str, Any]]:
        """发现所有 Skills"""
        self._skills = {}
        for skills_dir in self.skill_dirs:
            if not skills_dir.exists():
                continue
            direct_skill = self._resolve_skill_file(skills_dir)
            if direct_skill.exists():
                self._load_skill(skills_dir.name, skills_dir)
                continue
            self._walk_skills_dir(skills_dir)
        return self._skills

    def _walk_skills_dir(self, root: Path) -> None:
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = self._resolve_skill_file(entry)
            if skill_md.exists():
                self._load_skill(entry.name, entry)
            else:
                for sub_entry in sorted(entry.iterdir()):
                    if sub_entry.is_dir():
                        sub_md = self._resolve_skill_file(sub_entry)
                        if sub_md.exists():
                            self._load_skill(sub_entry.name, sub_entry)

    def _load_skill(self, name: str, path: Path):
        """加载单个 Skill"""
        skill_md = self._resolve_skill_file(path)
        if not skill_md.exists():
            return
        
        content = skill_md.read_text(encoding="utf-8")
        metadata = self._parse_frontmatter(content)
        
        skill = {
            "name": name,
            "path": str(path),
            "content": content,
            "description": metadata.get("description", ""),
            "when_to_use": metadata.get("when_to_use", ""),
            **metadata,
        }
        
        paths = metadata.get("paths")
        if paths and isinstance(paths, list) and len(paths) > 0:
            self._conditional_skills[name] = skill
        else:
            self._skills[name] = skill
    
    def _parse_frontmatter(self, content: str) -> Dict[str, Any]:
        """解析 Markdown frontmatter"""
        metadata: Dict[str, Any] = {}
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if match:
            parsed = yaml.safe_load(match.group(1))
            if isinstance(parsed, dict):
                metadata = parsed
        return metadata
    
    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """获取 Skill"""
        return self.resolve_skill(name)

    def resolve_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """按目录名、frontmatter name 或 slug 解析 skill。"""
        normalized = self._normalize_skill_name(name)
        # Check active skills first, then conditional
        for pool in (self._skills, self._conditional_skills):
            direct = pool.get(name) or pool.get(normalized)
            if direct is not None:
                return direct
            for skill in pool.values():
                fm_name = self._normalize_skill_name(str(skill.get("name", "")))
                if normalized == fm_name:
                    return skill
        return None

    def list_skills(self) -> List[str]:
        """列出所有 Skills"""
        return list(self._skills.keys())

    def list_skill_metadata(self) -> list[dict[str, Any]]:
        """返回适合模型发现的轻量 skill 元数据。"""
        items: list[dict[str, Any]] = []
        for key, skill in sorted(self._skills.items()):
            entry = {
                "name": key,
                "description": skill.get("description", ""),
                "path": skill.get("path", ""),
            }
            # Include all frontmatter fields except the full content body
            for k, v in skill.items():
                if k not in ("name", "description", "path", "content"):
                    entry[k] = v
            items.append(entry)
        return items

    def get_skill_body(self, name: str) -> str | None:
        """Return the Markdown body of a skill without YAML frontmatter."""
        skill = self.resolve_skill(name)
        if skill is None:
            return None
        content = str(skill.get("content", ""))
        match = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
        if match:
            return content[match.end():].strip()
        return content.strip()

    def activate_for_paths(self, file_paths: list[str]) -> list[str]:
        """Activate conditional skills whose paths frontmatter matches *file_paths*.
        Returns names of newly activated skills.
        """
        from fnmatch import fnmatch
        activated: list[str] = []
        for name, skill in list(self._conditional_skills.items()):
            patterns = skill.get("paths", [])
            if not isinstance(patterns, list):
                continue
            for fp in file_paths:
                for pattern in patterns:
                    if fnmatch(fp, pattern) or fnmatch(fp.lower(), pattern.lower()):
                        self._skills[name] = skill
                        del self._conditional_skills[name]
                        self._activated_names.add(name)
                        activated.append(name)
                        break
                if name not in self._conditional_skills:
                    break
        return activated

    def has_pending_conditional(self) -> bool:
        """是否有未激活的条件技能。"""
        return len(self._conditional_skills) > 0

    def read_skill_file(self, name: str, file_path: str) -> dict[str, Any]:
        """读取 skill 目录内的辅助文件，禁止越界。"""
        skill = self.resolve_skill(name)
        if skill is None:
            raise ValueError(f"Skill not found: {name}")
        base = Path(str(skill.get("path", ""))).resolve()
        target = (base / file_path).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"Path traversal detected: {file_path}") from exc
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"Skill file not found: {file_path}")
        return {
            "name": skill.get("name", name),
            "file_path": file_path,
            "content": target.read_text(encoding="utf-8"),
        }

    def build_skills_index_prompt(self) -> str:
        if not self._skills:
            return ""
        lines = [
            "<available_skills>",
            "Use skill_use to load a skill when a task matches its description.",
        ]
        for key, skill in sorted(self._skills.items()):
            name = skill.get("name", key)
            desc = skill.get("description", "")
            when = skill.get("when_to_use", "")
            path = skill.get("path", "")
            lines.append(f"  <skill>")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{str(desc)}</description>")
            if when:
                lines.append(f"    <when_to_use>{str(when)}</when_to_use>")
            lines.append(f"    <location>file://{path}</location>")
            lines.append(f"  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    @staticmethod
    def _normalize_skill_name(name: str) -> str:
        return name.strip().lower().replace("-", "_")

    @staticmethod
    def _resolve_skill_file(path: Path) -> Path:
        uppercase = path / "SKILL.md"
        if uppercase.exists():
            return uppercase
        plural_uppercase = path / "SKILLS.md"
        if plural_uppercase.exists():
            return plural_uppercase
        return path / "skill.md"


def get_skill_loader() -> SkillLoader:
    """获取 SkillLoader 实例"""
    return SkillLoader()
