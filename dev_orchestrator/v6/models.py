from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TENANT = "local-workspace"

ROLES = (
    "requirements",
    "planner",
    "architect",
    "db",
    "frontend",
    "backend",
    "docs",
    "qa",
    "security",
    "integration",
    "review",
    "repair",
    "release",
)

JOB_TYPES = (
    "requirements_analysis",
    "architecture_design",
    "package_planning",
    "code_generation",
    "test_generation",
    "security_review",
    "code_review",
    "integration",
    "quality",
    "release_candidate",
    "release_notes",
    "apply",
    "rollback",
    "repair",
)

JOB_STATUSES = (
    "queued",
    "leased",
    "running",
    "completed",
    "retry",
    "dead_letter",
    "cancelled",
    "paused",
)

CHECKPOINTS = (
    "run_created",
    "requirements_completed",
    "architecture_completed",
    "package_planning_completed",
    "wave_queued",
    "package_completed",
    "wave_completed",
    "integration_completed",
    "review_completed",
    "quality_completed",
    "release_notes_completed",
    "release_candidate_completed",
    "apply_completed",
    "rollback_completed",
)

FORBIDDEN_RELEASE_NAMES = {
    ".agent",
    ".git",
    ".pytest_cache",
    "__pycache__",
    "b",
    "reports",
    "tmp",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str, fallback: str = "project") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or fallback


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def source_line_count(path: Path) -> int:
    if not path.exists() or path.is_dir():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "//", "/*", "*", "<!--")):
            continue
        count += 1
    return count


def effective_loc(root: Path) -> dict[str, Any]:
    source_suffixes = {
        ".php",
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".css",
        ".sql",
        ".json",
        ".yml",
        ".yaml",
    }
    total = 0
    files: list[dict[str, Any]] = []
    if not root.exists():
        return {"total": 0, "files": []}
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in FORBIDDEN_RELEASE_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in source_suffixes:
            continue
        loc = source_line_count(path)
        total += loc
        files.append({"path": str(path.relative_to(root)), "loc": loc})
    return {"total": total, "files": files}


