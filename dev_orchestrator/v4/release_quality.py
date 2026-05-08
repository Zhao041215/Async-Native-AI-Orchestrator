from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from fnmatch import fnmatch

from dev_orchestrator.v4.models import FORBIDDEN_RELEASE_NAMES, effective_loc


def _gate(name: str, ok: bool, severity: str = "critical", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": ok, "severity": severity, "details": details or {}}


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _db_names_from_env(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        match = re.match(r"\s*DB_(?:NAME|DATABASE)\s*=\s*([A-Za-z0-9_\-]+)\s*$", line)
        if match:
            names.add(match.group(1))
    return names


def _db_names_from_sql(text: str) -> set[str]:
    names = set()
    patterns = (
        r"\bCREATE\s+DATABASE\s+`?([A-Za-z0-9_\-]+)`?",
        r"\bUSE\s+`?([A-Za-z0-9_\-]+)`?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            names.add(match.group(1))
    return names


def validate_release_structure(
    release_root: Path,
    product_contract: dict[str, Any],
    stack_pack_id: str,
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_context = run_context or {}
    gates: list[dict[str, Any]] = []
    gates.append(_gate("release_structure_gate", release_root.exists() and release_root.is_dir(), details={"path": str(release_root)}))
    forbidden_found = sorted({part for path in release_root.rglob("*") for part in path.relative_to(release_root).parts if part in FORBIDDEN_RELEASE_NAMES}) if release_root.exists() else []
    gates.append(_gate("release_no_temp_gate", not forbidden_found, details={"forbidden_found": forbidden_found}))

    required_files = [str(item) for item in product_contract.get("required_files", [])]
    missing = [item for item in required_files if not (release_root / item).exists()]
    gates.append(_gate("release_required_files_gate", not missing, details={"missing": missing}))

    root_entries = ("index.php", "index.html")
    has_root_entry = any((release_root / entry).exists() for entry in root_entries)
    gates.append(_gate("root_entry_gate", has_root_entry, details={"accepted": root_entries}))
    env_text = _read(release_root / ".env.example")
    gates.append(_gate("config_gate", bool(env_text) or product_contract.get("config_strategy") == "static_config"))

    sql_text = "\n".join(_read(path) for path in (release_root / "database").rglob("*.sql")) if (release_root / "database").exists() else ""
    env_names = _db_names_from_env(env_text)
    sql_names = _db_names_from_sql(sql_text)
    db_ok = True
    if env_names and sql_names:
        db_ok = bool(env_names & sql_names)
    gates.append(_gate("database_consistency_gate", db_ok, details={"env_db_names": sorted(env_names), "sql_db_names": sorted(sql_names)}))

    if not product_contract.get("api_only", False):
        index_text = _read(release_root / "index.php") + _read(release_root / "index.html")
        browser_ok = bool(index_text and ("<form" in index_text.lower() or "fetch(" in index_text.lower() or "admin/login" in index_text.lower()))
        gates.append(_gate("browser_ui_gate", browser_ok))

    readme_text = _read(release_root / "README.md").lower()
    deploy_ok = "upload" in readme_text and ("database" in readme_text or product_contract.get("database_strategy") == "external_api")
    gates.append(_gate("deployment_simplicity_gate", deploy_ok))

    if stack_pack_id == "php_mysql_single_dir":
        gates.append(_gate("php_root_index_gate", (release_root / "index.php").exists()))
        gates.append(_gate("php_sensitive_file_protection_gate", all((release_root / item).exists() for item in (".htaccess", ".user.ini", "nginx.sample.conf"))))
        gates.append(_gate("php_no_public_runtime_gate", not (release_root / "public").exists()))

    agent_runs = run_context.get("agent_runs", [])
    over_budget = [item for item in agent_runs if (item.get("payload") or {}).get("over_budget")]
    gates.append(_gate("ai_budget_gate", not over_budget, details={"over_budget_agent_runs": [item["id"] for item in over_budget]}))

    degraded_core = [
        item
        for item in agent_runs
        if (item.get("payload") or {}).get("degraded") and (item.get("payload") or {}).get("budget", {}).get("critical")
    ]
    gates.append(_gate("model_tier_gate", not degraded_core, details={"degraded_core_agent_runs": [item["id"] for item in degraded_core]}))

    packages = run_context.get("packages", [])
    package_failures = []
    boundary_failures = []
    for package in packages:
        payload = package.get("payload") or {}
        result = package.get("result") or {}
        if package.get("status") != "completed":
            package_failures.append({"package_key": package.get("package_key"), "reason": "not_completed"})
        if not payload.get("allowed_paths"):
            package_failures.append({"package_key": package.get("package_key"), "reason": "missing_allowed_paths"})
        if not result.get("requirements_mapping"):
            package_failures.append({"package_key": package.get("package_key"), "reason": "missing_requirements_mapping"})
        changed_files = ((result.get("patch_manifest") or {}).get("changed_files") or [])
        allowed = [_normalize_pattern(pattern) for pattern in payload.get("allowed_paths", [])]
        forbidden = [_normalize_pattern(pattern) for pattern in payload.get("forbidden_paths", [])]
        for changed in changed_files:
            normalized = changed.replace("\\", "/").lstrip("/")
            if allowed and not any(fnmatch(normalized, pattern) for pattern in allowed):
                boundary_failures.append({"package_key": package.get("package_key"), "path": changed, "reason": "outside_allowed_paths"})
            if forbidden and any(fnmatch(normalized, pattern) for pattern in forbidden):
                boundary_failures.append({"package_key": package.get("package_key"), "path": changed, "reason": "matches_forbidden_paths"})
    gates.append(_gate("package_gate", not package_failures, details={"failures": package_failures}))
    gates.append(_gate("path_boundary_gate", not boundary_failures, details={"failures": boundary_failures[:50]}))

    context_status = run_context.get("context_index_status") or {}
    context_required = bool(run_context)
    gates.append(_gate("context_index_gate", (not context_required) or bool(context_status.get("ok", False)), details=context_status))

    wave_reports = run_context.get("wave_reports", [])
    failed_waves = [item for item in wave_reports if (item.get("payload") or {}).get("status") != "passed"]
    gates.append(_gate("wave_gate", not failed_waves, details={"failed_wave_reports": [item["id"] for item in failed_waves]}))

    loc = effective_loc(release_root)
    effective_loc_target = int(run_context.get("effective_loc_target") or 0)
    if effective_loc_target > 0:
        minimum = max(1, int(effective_loc_target * 0.8))
        gates.append(
            _gate(
                "effective_loc_target_gate",
                loc["total"] >= minimum,
                details={"effective_loc": loc["total"], "target": effective_loc_target, "minimum": minimum},
            )
        )
    gates.append(_gate("effective_loc_gate", loc["total"] > 0, details={"effective_loc": loc["total"]}, severity="major"))
    critical_failures = [gate for gate in gates if not gate["ok"] and gate["severity"] == "critical"]
    return {
        "schema_version": "4.0",
        "ok": not critical_failures,
        "status": "GO" if not critical_failures else "NO_GO",
        "release_root": str(release_root),
        "stack_pack": stack_pack_id,
        "gates": gates,
        "effective_loc": loc,
    }


def _normalize_pattern(pattern: str) -> str:
    normalized = str(pattern or "").replace("\\", "/").lstrip("/")
    if normalized.startswith("release/"):
        normalized = normalized.removeprefix("release/")
    return normalized


def build_deploy_guide(product_contract: dict[str, Any], release_root: Path, quality_report: dict[str, Any]) -> dict[str, Any]:
    stack_pack = product_contract["stack_pack"]
    database_files = sorted(str(path.relative_to(release_root)) for path in (release_root / "database").rglob("*.sql")) if (release_root / "database").exists() else []
    baota_steps = [
        "Upload every file inside release/ to the website root directory.",
        "Select PHP 8.2 for the site when using php_mysql_single_dir.",
        "Import database/migrations/*.sql and database/seeders/*.sql in order.",
        "Copy .env.example to .env and fill DB_HOST, DB_NAME, DB_USER, and DB_PASSWORD.",
        "Open /health and then / in the browser.",
    ]
    return {
        "schema_version": "4.0",
        "release_root": product_contract.get("release_root", "release"),
        "upload_directory": "release",
        "home_url": product_contract.get("home_entry", "/"),
        "health_url": product_contract.get("health_entry", "/health"),
        "admin_url": product_contract.get("admin_entry", "/admin/login"),
        "database": {
            "env_keys": ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"],
            "migration_files": [item for item in database_files if "/migrations/" in f"/{item}"],
            "seed_files": [item for item in database_files if "/seeders/" in f"/{item}"],
        },
        "deployment": {
            "mode": product_contract.get("deployment_mode"),
            "baota_steps": baota_steps if stack_pack == "php_mysql_single_dir" else [],
            "nginx_required": False,
            "apache_htaccess_ready": (release_root / ".htaccess").exists(),
        },
        "verification": {
            "browser_smoke": "passed" if quality_report.get("ok") else "blocked",
            "api_smoke": "passed" if quality_report.get("ok") else "blocked",
            "db_consistency": "passed"
            if any(gate["name"] == "database_consistency_gate" and gate["ok"] for gate in quality_report.get("gates", []))
            else "blocked",
        },
        "rollback": {
            "manifest_path": "",
            "reverse_patch_path": "",
        },
    }
