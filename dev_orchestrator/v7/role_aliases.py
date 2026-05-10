"""V7 Role name normalization and aliasing."""
from __future__ import annotations

from dev_orchestrator.v7.models import ROLES


PACKAGE_ROLE_ALIASES = {
    "database": "db", "database_engineer": "db", "data_engineer": "db",
    "schema": "db", "storage": "db", "persistence": "db",
    "backend_engineer": "backend", "service": "backend", "server": "backend",
    "api": "backend", "fullstack": "backend", "full_stack": "backend",
    "frontend_engineer": "frontend", "ui": "frontend", "web": "frontend", "client": "frontend",
    "qa_engineer": "qa", "testing": "qa", "test": "qa", "quality_assurance": "qa",
    "security_engineer": "security", "security_reviewer": "security", "appsec": "security",
    "docs_engineer": "docs", "technical_writer": "docs",
    "reviewer": "review", "review_engineer": "review",
    "release_manager": "release", "release_engineer": "release",
    "ops": "release", "devops": "release", "infrastructure": "release",
}


def normalize_role_name(role: str) -> str:
    return str(role or "").strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_").replace(".", "_").strip("_")


def canonical_worker_role(role: str, domain: str = "", subsystem: str = "", objective: str = "") -> str:
    normalized = normalize_role_name(role)
    if normalized in ROLES:
        return normalized
    alias = PACKAGE_ROLE_ALIASES.get(normalized)
    if alias in ROLES:
        return alias
    hints = " ".join([normalized, normalize_role_name(domain), normalize_role_name(subsystem), normalize_role_name(objective)])
    if any(token in hints for token in ("database", "schema", "storage", "persistence", "data")):
        return "db"
    if any(token in hints for token in ("frontend", "ui", "web", "client", "browser")):
        return "frontend"
    if any(token in hints for token in ("security", "sec", "auth", "risk", "threat")):
        return "security"
    if any(token in hints for token in ("quality", "qa", "test", "testing", "verification")):
        return "qa"
    if any(token in hints for token in ("review", "audit")):
        return "review"
    if any(token in hints for token in ("release", "deploy", "ops", "devops", "infra")):
        return "release"
    if any(token in hints for token in ("documentation", "docs", "manual", "guide", "readme", "writer")):
        return "docs"
    return "backend"
