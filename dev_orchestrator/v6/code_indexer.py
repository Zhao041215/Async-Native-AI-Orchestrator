from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dev_orchestrator.v6.models import sha256_bytes, sha256_file, source_line_count


LANG_BY_SUFFIX = {
    ".py": "python",
    ".php": "php",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".md": "markdown",
}


def build_code_index(project_root: Path, limit: int = 1000) -> dict[str, Any]:
    project_root = project_root.resolve()
    files = []
    if project_root.exists():
        for path in sorted(project_root.rglob("*")):
            if path.is_dir() or ".v6" in path.relative_to(project_root).parts:
                continue
            suffix = path.suffix.lower()
            if suffix not in LANG_BY_SUFFIX:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            files.append(
                {
                    "path": str(path.relative_to(project_root)).replace("\\", "/"),
                    "language": LANG_BY_SUFFIX[suffix],
                    "loc": source_line_count(path),
                    "sha256": sha256_file(path),
                    "symbols": _symbols(suffix, text),
                    "imports": _imports(suffix, text),
                    "routes": _routes(suffix, text),
                    "db_tables": _db_tables(suffix, text),
                }
            )
            if len(files) >= limit:
                break
    index = {"schema_version": "6.0", "project_root": str(project_root), "files": files}
    index["index_hash"] = sha256_bytes(str(files).encode("utf-8"))
    return index


def _symbols(suffix: str, text: str) -> list[str]:
    patterns = []
    if suffix == ".py":
        patterns = [r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"]
    elif suffix == ".php":
        patterns = [r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)", r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)"]
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        patterns = [r"\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)", r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", r"\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*="]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.MULTILINE))
    return sorted(set(found))[:100]


def _imports(suffix: str, text: str) -> list[str]:
    found: list[str] = []
    if suffix == ".py":
        found.extend(re.findall(r"^\s*(?:from|import)\s+([A-Za-z0-9_\.]+)", text, flags=re.MULTILINE))
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        found.extend(re.findall(r"\bfrom\s+['\"]([^'\"]+)['\"]", text))
        found.extend(re.findall(r"\brequire\(['\"]([^'\"]+)['\"]\)", text))
    elif suffix == ".php":
        found.extend(re.findall(r"\brequire(?:_once)?\s+['\"]([^'\"]+)['\"]", text))
    return sorted(set(found))[:100]


def _routes(suffix: str, text: str) -> list[str]:
    found = []
    found.extend(re.findall(r"['\"](\/[A-Za-z0-9_\-\/{}:.]*)['\"]", text))
    found.extend(re.findall(r"@(app|router)\.(?:get|post|put|delete|patch)\(['\"]([^'\"]+)['\"]", text))
    routes = []
    for item in found:
        if isinstance(item, tuple):
            routes.append(item[-1])
        else:
            routes.append(item)
    return sorted(set(routes))[:100]


def _db_tables(suffix: str, text: str) -> list[str]:
    patterns = [
        r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z0-9_]+)`?",
        r"\bINSERT\s+INTO\s+`?([A-Za-z0-9_]+)`?",
        r"\bFROM\s+`?([A-Za-z0-9_]+)`?",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return sorted(set(found))[:100]
