from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".scss",
    ".vue",
    ".java",
    ".go",
    ".rs",
    ".cs",
    ".php",
    ".sql",
    ".ps1",
    ".sh",
}

CODE_AREAS = {"apps", "tests", "infra", "src", "public", "database", "config"}

CONFIG_ONLY_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "README.md",
    ".env",
    ".env.example",
}


def is_source_file(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_EXTENSIONS and path.name not in CONFIG_ONLY_NAMES


def is_code_area(relative_parts: tuple[str, ...]) -> bool:
    return bool(relative_parts) and relative_parts[0] in CODE_AREAS


def measure_codebase(project_root: Path) -> dict:
    project_root = Path(project_root)
    total_lines = 0
    source_file_count = 0
    coverage_areas: set[str] = set()
    contentful_files: list[str] = []
    by_area: dict[str, dict[str, int]] = {}
    by_extension: dict[str, dict[str, int]] = {}

    if not project_root.exists():
        return {
            "schema_version": "1.0.0",
            "kind": "code-metrics",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(project_root),
            "source_lines": 0,
            "source_file_count": 0,
            "coverage_areas": [],
            "contentful_files": [],
            "by_area": {},
            "by_extension": {},
        }

    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(project_root).parts
        if not is_code_area(relative_parts):
            continue
        coverage_areas.add(relative_parts[0])
        if not is_source_file(path):
            continue
        try:
            line_count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            continue
        relative = str(path.relative_to(project_root)).replace("\\", "/")
        total_lines += line_count
        source_file_count += 1
        if line_count >= 5:
            contentful_files.append(relative)

        area = relative_parts[0]
        by_area.setdefault(area, {"source_lines": 0, "source_file_count": 0})
        by_area[area]["source_lines"] += line_count
        by_area[area]["source_file_count"] += 1

        extension = path.suffix.lower()
        by_extension.setdefault(extension, {"source_lines": 0, "source_file_count": 0})
        by_extension[extension]["source_lines"] += line_count
        by_extension[extension]["source_file_count"] += 1

    return {
        "schema_version": "1.0.0",
        "kind": "code-metrics",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "source_lines": total_lines,
        "source_file_count": source_file_count,
        "coverage_areas": sorted(coverage_areas),
        "contentful_files": sorted(contentful_files),
        "by_area": by_area,
        "by_extension": by_extension,
    }


def measure_role_surface(project_root: Path, role: str) -> dict:
    surface = "web" if role == "frontend" else "api"
    target_root = Path(project_root) / "apps" / surface
    metrics = measure_codebase(Path(project_root))
    files = [
        item
        for item in metrics.get("contentful_files", [])
        if item.startswith(f"apps/{surface}/")
    ]
    source_lines = 0
    source_file_count = 0
    for relative in files:
        path = Path(project_root) / relative
        try:
            source_lines += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
            source_file_count += 1
        except OSError:
            continue
    return {
        "target_root": str(target_root),
        "surface": f"apps/{surface}",
        "source_lines": source_lines,
        "source_file_count": source_file_count,
        "contentful_files": files,
    }
