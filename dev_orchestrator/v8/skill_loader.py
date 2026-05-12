import re
from pathlib import Path


def _versioned_superpowers_path() -> Path | None:
    """Return the skills dir from the highest-versioned superpowers cache entry."""
    cache = Path("~/.claude/plugins/cache/superpowers-dev/superpowers").expanduser()
    if not cache.exists():
        return None
    candidates = sorted(
        (d / "skills" for d in cache.iterdir() if d.is_dir() and (d / "skills").is_dir()),
        key=lambda p: p.parent.name,
    )
    return candidates[-1] if candidates else None


def _build_search_paths() -> list[Path]:
    paths = [
        Path(".claude/skills"),
        Path(__file__).parent.parent.parent / ".claude" / "skills",
        Path("~/.claude/plugins/cache/claude-plugins-official/skills").expanduser(),
    ]
    versioned = _versioned_superpowers_path()
    if versioned:
        paths.insert(2, versioned)
    return paths


SKILL_SEARCH_PATHS = _build_search_paths()

SKILL_MAP: dict[str, list[str]] = {
    "frontend":       ["frontend-design"],
    "backend":        ["test-driven-development"],
    "db":             ["test-driven-development"],
    "qa":             ["test-driven-development", "systematic-debugging"],
    "architect":      ["brainstorming", "writing-plans"],
    "requirements":   ["brainstorming"],
    "review":         ["requesting-code-review", "receiving-code-review"],
    "security":       ["systematic-debugging"],
    "docs":           ["writing-skills"],
    "repair":         ["systematic-debugging"],
    "infrastructure": ["writing-plans"],
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
