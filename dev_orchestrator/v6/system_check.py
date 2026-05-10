from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from dev_orchestrator.metadata import read_release_version
from dev_orchestrator.v6.models import DEFAULT_TENANT
from dev_orchestrator.v6.storage_lifecycle import StorageLifecyclePolicy, directory_stats


def default_database_url() -> str:
    return os.environ.get("V6_DATABASE_URL") or os.environ.get("DATABASE_URL") or "postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator"


def build_v6_system_check(root_dir: Path, config: dict[str, Any], strict_db: bool = False) -> dict[str, Any]:
    runtime = config.get("runtime", {})
    workspace_root = (root_dir / runtime.get("workspace_root", "workspace/projects")).resolve()
    logs_root = (root_dir / runtime.get("logs_path", "logs")).resolve()
    database_url = default_database_url()
    checks = {
        "kernel": "v6",
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
    checks["v6_only_scan"] = _scan_v6_only(root_dir)
    checks["single_active_version_gate"] = _scan_retired_versions(root_dir)
    checks["retired_generation_purge"] = checks["single_active_version_gate"]
    checks["storage_lifecycle_gate"] = _storage_lifecycle_gate(root_dir, runtime)
    failures = []
    if not workspace_root.exists():
        failures.append("workspace root is missing")
    if not logs_root.exists():
        failures.append("logs root is missing")
    if checks["database_url_driver"] != "postgres":
        failures.append("V6_DATABASE_URL must be a Postgres URL")
    for dependency in ("fastapi_installed", "sqlalchemy_installed", "psycopg_installed"):
        if not checks[dependency]:
            failures.append(f"{dependency} is false")
    if not checks["v6_only_scan"]["ok"]:
        failures.append("retired source references remain")
    if not checks["single_active_version_gate"]["ok"]:
        failures.append("retired version residue remains in repository")
    if not checks["storage_lifecycle_gate"]["ok"]:
        failures.append("storage lifecycle gate failed")
    if strict_db and not failures:
        try:
            from dev_orchestrator.v6.store import PostgresV6Store

            store = PostgresV6Store(database_url)
            store.bootstrap()
            db_purge = _scan_database_for_retired_generation(store)
            checks["database_retired_generation_purge"] = db_purge
            if not db_purge["ok"]:
                failures.append("retired generation database records remain")
            checks["database_bootstrap"] = "passed"
        except Exception as exc:
            checks["database_bootstrap"] = "failed"
            failures.append(f"database bootstrap failed: {exc}")
    return {
        "ok": not failures,
        "release_version": read_release_version(root_dir),
        "root_dir": str(root_dir),
        "hosted_ready": not failures,
        "v6_only": checks["v6_only_scan"]["ok"],
        "single_active_version": checks["single_active_version_gate"]["ok"],
        "retired_generation_purged": checks["retired_generation_purge"]["ok"],
        "failures": failures,
        "checks": checks,
    }


def _scan_v6_only(root_dir: Path) -> dict[str, Any]:
    patterns = (
        "dev_orchestrator" + ".v" + "5",
        "V" + "5" + "Orchestrator",
        "/api/" + "v" + "5",
        "V" + "5" + "_DATABASE_URL",
        "Dockerfile." + "v" + "5",
        "docker-compose." + "v" + "5",
    )
    source_roots = [
        root_dir / "run_server.py",
        root_dir / "dev_orchestrator" / "server.py",
        root_dir / "dev_orchestrator" / "healthcheck.py",
        root_dir / "dev_orchestrator" / "config.py",
        root_dir / "dev_orchestrator" / "static",
        root_dir / "dev_orchestrator" / "v6",
        root_dir / "Dockerfile.v6",
        root_dir / "docker-compose.v6.yml",
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


def _scan_retired_versions(root_dir: Path) -> dict[str, Any]:
    versions = list(range(1, 6))
    path_patterns: list[str] = []
    line_patterns: list[str] = []
    for version in versions:
        upper = f"V{version}"
        path_patterns.extend(
            [
                f"dev_orchestrator.v{version}",
                f"/api/v{version}",
                f"Dockerfile.v{version}",
                f"docker-compose.v{version}",
                f"requirements-v{version}",
                f"test_v{version}",
                f"test-v{version}",
                f"v{version}-",
                f"v{version}_",
                f"v{version}.",
            ]
        )
        line_patterns.extend(
            [
                upper,
                f"dev_orchestrator.v{version}",
                f"/api/v{version}",
                f"Dockerfile.v{version}",
                f"docker-compose.v{version}",
                f"requirements-v{version}",
                f"tests.test_v{version}",
            ]
        )
    ignored_dirs = {".git"}
    ignored_suffixes = {".pyc"}
    matches: list[dict[str, Any]] = []
    for path in root_dir.rglob("*"):
        if path.is_dir():
            continue
        if path.suffix in ignored_suffixes:
            continue
        relative_parts = set(path.relative_to(root_dir).parts)
        if relative_parts & ignored_dirs:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        relative = str(path.relative_to(root_dir))
        path_hit = any(pattern in relative for pattern in path_patterns)
        line_hits = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if any(pattern in line for pattern in line_patterns):
                line_hits.append({"line": line_no, "text": line.strip()[:240]})
                if len(line_hits) >= 5:
                    break
        if path_hit or line_hits:
            matches.append({"path": relative, "path_hit": path_hit, "line_hits": line_hits})
            if len(matches) >= 100:
                break
    return {"ok": not matches, "patterns": path_patterns[:10], "matches": matches}


def _scan_database_for_retired_generation(store: Any) -> dict[str, Any]:
    patterns = ("v" + "5", "V" + "5")
    matches: list[dict[str, Any]] = []
    for project in store.list_projects(DEFAULT_TENANT):
        blob = str(project)
        if any(pattern in blob for pattern in patterns):
            matches.append({"table": "projects", "id": project.get("id", ""), "name": project.get("name", "")})
        for run in store.list_runs(project.get("id", "")):
            if any(pattern in str(run) for pattern in patterns):
                matches.append({"table": "runs", "id": run.get("id", ""), "project_id": project.get("id", "")})
            for job in store.list_jobs(run_id=run.get("id", "")):
                if any(pattern in str(job) for pattern in patterns):
                    matches.append({"table": "durable_jobs", "id": job.get("id", ""), "run_id": run.get("id", "")})
            for artifact in store.list_artifacts(run.get("id", "")):
                if any(pattern in str(artifact) for pattern in patterns):
                    matches.append({"table": "artifacts", "id": artifact.get("id", ""), "run_id": run.get("id", "")})
            if len(matches) >= 100:
                break
    return {"ok": not matches, "matches": matches[:100]}


def _storage_lifecycle_gate(root_dir: Path, runtime: dict[str, Any]) -> dict[str, Any]:
    workspace_root = (root_dir / (runtime.get("runtime_root") or runtime.get("workspace_root", "workspace/projects"))).resolve()
    logs_root = (root_dir / runtime.get("logs_path", "logs")).resolve()
    policy = StorageLifecyclePolicy.from_runtime_config(runtime)
    runtime_stats = directory_stats(workspace_root)
    logs_stats = directory_stats(logs_root)
    total_bytes = int(runtime_stats["bytes"]) + int(logs_stats["bytes"])
    quota_ratio = total_bytes / policy.workspace_max_bytes if policy.workspace_max_bytes else 0.0
    failures: list[str] = []
    if not workspace_root.exists():
        failures.append("runtime root is missing")
    if not logs_root.exists():
        failures.append("logs root is missing")
    if policy.workspace_max_bytes <= 0:
        failures.append("workspace max bytes must be positive")
    if policy.exported_worktree_retention_days < 1:
        failures.append("exported worktree retention must be at least one day")
    if quota_ratio >= 0.9:
        failures.append("managed runtime storage is above blocking quota")
    return {
        "ok": not failures,
        "schema_version": "6.3",
        "runtime_root": str(workspace_root),
        "logs_root": str(logs_root),
        "policy": policy.to_dict(),
        "runtime_stats": runtime_stats,
        "logs_stats": logs_stats,
        "quota": {
            "used_bytes": total_bytes,
            "max_bytes": policy.workspace_max_bytes,
            "ratio": round(quota_ratio, 6),
            "status": "blocked" if quota_ratio >= 0.9 else ("warning" if quota_ratio >= 0.7 else "ok"),
        },
        "failures": failures,
    }

