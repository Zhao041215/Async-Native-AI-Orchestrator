"""V7 System Check - health checks for the orchestrator."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from dev_orchestrator.v7.models import DEFAULT_TENANT


def default_database_url() -> str:
    return os.environ.get("V7_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def build_v7_system_check(root_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    runtime = config.get("runtime", {})
    workspace_root = (root_dir / runtime.get("workspace_root", "workspace/projects")).resolve()
    logs_root = (root_dir / runtime.get("logs_path", "logs")).resolve()
    database_url = default_database_url()
    checks = {
        "kernel": "v7",
        "storage": "in_memory" if not database_url else "postgres",
        "tenant": DEFAULT_TENANT,
        "workspace_root": str(workspace_root),
        "logs_root": str(logs_root),
        "database_url_configured": bool(database_url),
        "ai_agent_native": True,
        "fastapi_installed": importlib.util.find_spec("fastapi") is not None,
        "httpx_installed": importlib.util.find_spec("httpx") is not None,
        "structlog_installed": importlib.util.find_spec("structlog") is not None,
        "pydantic_installed": importlib.util.find_spec("pydantic") is not None,
    }
    failures = []
    if not workspace_root.exists():
        failures.append("workspace root is missing")
    if not logs_root.exists():
        failures.append("logs root is missing")
    for dep in ("fastapi_installed", "httpx_installed", "structlog_installed", "pydantic_installed"):
        if not checks[dep]:
            failures.append(f"{dep} is false")
    checks["ok"] = len(failures) == 0
    checks["failures"] = failures
    return checks
