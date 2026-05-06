from __future__ import annotations

from pathlib import Path


def read_release_version(root_dir: Path) -> str:
    version_path = root_dir / "VERSION"
    if not version_path.exists():
        return "unknown"
    return version_path.read_text(encoding="utf-8").strip() or "unknown"
