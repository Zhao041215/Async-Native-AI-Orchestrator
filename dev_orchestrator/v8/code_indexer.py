"""V8 Code Indexer - async file symbol extraction."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dev_orchestrator.v8.models import source_line_count
from dev_orchestrator.v8.observability import get_logger

log = get_logger(__name__)

INDEXED_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".sql", ".html", ".md", ".json",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".go", ".rs", ".java", ".rb",
})
MAX_FILES = 500


async def build_code_index(project_root: Path) -> dict[str, Any]:
    if not project_root.exists():
        return {"schema_version": "7.0", "files": [], "index_hash": ""}
    root = project_root.resolve()
    files: list[dict[str, Any]] = []
    skipped_dirs = {".git", ".v6", ".v7", "__pycache__", ".pytest_cache", "node_modules", ".agent"}

    def _scan() -> None:
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(root)
            if any(part in skipped_dirs for part in rel.parts):
                continue
            if path.suffix.lower() not in INDEXED_EXTENSIONS:
                continue
            files.append({"path": str(rel).replace("\\", "/"), "loc": source_line_count(path)})
            if len(files) >= MAX_FILES:
                log.warning("code_index_truncated", max_files=MAX_FILES)
                break

    await asyncio.get_event_loop().run_in_executor(None, _scan)
    return {"schema_version": "7.0", "files": files, "index_hash": ""}
