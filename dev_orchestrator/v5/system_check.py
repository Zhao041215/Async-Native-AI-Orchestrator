from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from dev_orchestrator.metadata import read_release_version
from dev_orchestrator.v5.models import DEFAULT_TENANT


def default_database_url() -> str:
    return os.environ.get("V5_DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator"


def build_v5_system_check(root_dir: Path, config: dict[str, Any], strict_db: bool = False) -> dict[str, Any]:
    runtime = config.get("runtime", {})
    workspace_root = (root_dir / runtime.get("workspace_root", "workspace/projects")).resolve()
    logs_root = (root_dir / runtime.get("logs_path", "logs")).resolve()
    database_url = default_database_url()
    checks = {
        "kernel": "v5",
        "storage": "postgres",
        "tenant": DEFAULT_TENANT,
        "workspace_root": str(workspace_root),
        "logs_root": str(logs_root),
        "database_url_configured": bool(database_url),
        "database_url_driver": "postgres" if database_url.startswith(("postgresql://", "postgresql+psycopg://")) else "unsupported",
        "ai_agent_native": True,
        "fastapi_installed": importlib.util.find_spec("fastapi") is not None,
        "sqlalchemy_installed": importlib.util.find_spec("sqlalchemy") is not None,
        "psycopg_installed": importlib.util.find_spec("psycopg") is not None,
    }
    checks["v5_only_scan"] = _scan_v5_only(root_dir)
    failures = []
    if not workspace_root.exists():
        failures.append("workspace root is missing")
    if not logs_root.exists():
        failures.append("logs root is missing")
    if checks["database_url_driver"] != "postgres":
        failures.append("V5_DATABASE_URL must be a Postgres URL")
    for dependency in ("fastapi_installed", "sqlalchemy_installed", "psycopg_installed"):
        if not checks[dependency]:
            failures.append(f"{dependency} is false")
    if not checks["v5_only_scan"]["ok"]:
        failures.append("active V4 source references remain")
    if strict_db and not failures:
        try:
            from dev_orchestrator.v5.store import PostgresV5Store

            store = PostgresV5Store(database_url)
            store.bootstrap()
            checks["database_bootstrap"] = "passed"
        except Exception as exc:
            checks["database_bootstrap"] = "failed"
            failures.append(f"database bootstrap failed: {exc}")
    return {
        "ok": not failures,
        "release_version": read_release_version(root_dir),
        "root_dir": str(root_dir),
        "hosted_ready": not failures,
        "v5_only": checks["v5_only_scan"]["ok"],
        "failures": failures,
        "checks": checks,
    }


def _scan_v5_only(root_dir: Path) -> dict[str, Any]:
    patterns = (
        "dev_orchestrator" + ".v4",
        "V4" + "Orchestrator",
        "/api/" + "v4",
        "V4" + "_DATABASE_URL",
    )
    source_roots = [
        root_dir / "run_server.py",
        root_dir / "dev_orchestrator" / "server.py",
        root_dir / "dev_orchestrator" / "healthcheck.py",
        root_dir / "dev_orchestrator" / "config.py",
        root_dir / "dev_orchestrator" / "static",
        root_dir / "dev_orchestrator" / "v5",
        root_dir / "Dockerfile.v5",
        root_dir / "docker-compose.v5.yml",
        root_dir / "orchestrator_config.json",
    ]
    ignored_parts = {"__pycache__"}
    ignored_suffixes = {".pyc", ".log"}
    matches: list[dict[str, Any]] = []
    for root in source_roots:
        paths = [root] if root.is_file() else list(root.rglob("*")) if root.exists() else []
        for path in paths:
            if path.is_dir() or path.suffix in ignored_suffixes or any(part in ignored_parts for part in path.parts):
                continue
            if path.name in {"system_check.py"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if any(pattern in line for pattern in patterns):
                    matches.append({"path": str(path.relative_to(root_dir)), "line": line_no, "text": line.strip()[:240]})
    return {"ok": not matches, "patterns": list(patterns), "matches": matches[:100]}

