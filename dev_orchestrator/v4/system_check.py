from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from dev_orchestrator.metadata import read_release_version
from dev_orchestrator.v4.models import DEFAULT_TENANT
from dev_orchestrator.v4.stack_packs import STACK_PACKS


def default_database_url() -> str:
    return os.environ.get("V4_DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator"


def build_v4_system_check(root_dir: Path, config: dict[str, Any], strict_db: bool = False) -> dict[str, Any]:
    runtime = config.get("runtime", {})
    workspace_root = (root_dir / runtime.get("workspace_root", "workspace/projects")).resolve()
    logs_root = (root_dir / runtime.get("logs_path", "logs")).resolve()
    database_url = default_database_url()
    checks = {
        "kernel": "v4",
        "storage": "postgres",
        "tenant": DEFAULT_TENANT,
        "workspace_root": str(workspace_root),
        "logs_root": str(logs_root),
        "database_url_configured": bool(database_url),
        "database_url_driver": "postgres" if database_url.startswith(("postgresql://", "postgresql+psycopg://")) else "unsupported",
        "stack_pack_count": len(STACK_PACKS),
        "fastapi_installed": importlib.util.find_spec("fastapi") is not None,
        "sqlalchemy_installed": importlib.util.find_spec("sqlalchemy") is not None,
        "psycopg_installed": importlib.util.find_spec("psycopg") is not None,
    }
    failures = []
    if not workspace_root.exists():
        failures.append("workspace root is missing")
    if not logs_root.exists():
        failures.append("logs root is missing")
    if checks["database_url_driver"] != "postgres":
        failures.append("V4_DATABASE_URL must be a Postgres URL")
    for dependency in ("fastapi_installed", "sqlalchemy_installed", "psycopg_installed"):
        if not checks[dependency]:
            failures.append(f"{dependency} is false")
    if strict_db and not failures:
        try:
            from dev_orchestrator.v4.store import PostgresV4Store

            store = PostgresV4Store(database_url)
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
        "v4_only": True,
        "failures": failures,
        "checks": checks,
    }

