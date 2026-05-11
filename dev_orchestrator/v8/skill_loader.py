import re
from pathlib import Path

SKILL_SEARCH_PATHS = [
    Path(".claude/skills"),  # project-local skills (relative to cwd)
    Path(__file__).parent.parent.parent / ".claude" / "skills",  # repo-root local skills
    Path("~/.claude/plugins/cache/superpowers-dev/superpowers/5.1.0/skills").expanduser(),
    Path("~/.claude/plugins/cache/claude-plugins-official/skills").expanduser(),
]

SKILL_MAP: dict[str, list[str]] = {
    "frontend":     ["frontend-design"],
    "backend":      ["test-driven-development"],
    "db":           ["test-driven-development"],
    "qa":           ["test-driven-development", "systematic-debugging"],
    "architect":    ["brainstorming", "writing-plans"],
    "requirements": ["brainstorming"],
    "review":       ["requesting-code-review", "receiving-code-review"],
    "security":     ["systematic-debugging"],
    "docs":         ["writing-skills"],
    "repair":       ["systematic-debugging"],
}

_FRONTMATTER_RE = re.compile(r"^---.*?---\s*", re.DOTALL)


def load_skills_for_role(role: str, max_chars_per_skill: int = 2000) -> str:
    snippets: list[str] = []
    for skill_name in SKILL_MAP.get(role, []):
        for base in SKILL_SEARCH_PATHS:
            skill_file = base / skill_name / "SKILL.md"
            if skill_file.exists():
                try:
                    content = skill_file.read_text(encoding="utf-8")
                    content = _FRONTMATTER_RE.sub("", content).strip()
                    snippets.append(f"[{skill_name}]\n{content[:max_chars_per_skill]}")
                except Exception:
                    pass
                break
    return "\n\n".join(snippets)
