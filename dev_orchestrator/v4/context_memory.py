from __future__ import annotations

from pathlib import Path
from typing import Any

from dev_orchestrator.v4.models import sha256_bytes, source_line_count


def build_context_snapshot_v2(
    *,
    requirements: list[dict[str, Any]],
    blueprint: dict[str, Any],
    project_root: Path | None = None,
) -> dict[str, Any]:
    packages = blueprint.get("work_packages", [])
    product_contract = blueprint.get("product_contract") or {}
    boundary_rules = blueprint.get("boundary_rules") or []
    snapshot = {
        "schema_version": "4.5",
        "requirements_index": {item["id"]: item.get("text", "") for item in requirements},
        "adr_index": [
            {
                "id": "ADR-0001",
                "decision": f"Use stack pack {product_contract.get('stack_pack', 'unknown')} with release root {product_contract.get('release_root', 'release')}.",
            }
        ],
        "boundary_rules": boundary_rules,
        "interface_contract_index": {
            "home": product_contract.get("home_entry", "/"),
            "health": product_contract.get("health_entry", "/health"),
            "admin": product_contract.get("admin_entry", "/admin/login"),
        },
        "database_schema_index": {
            "strategy": product_contract.get("database_strategy", ""),
            "migration_files": ["database/migrations/001_init.sql"],
            "seed_files": ["database/seeders/001_seed.sql"],
        },
        "route_index": [product_contract.get("home_entry", "/"), product_contract.get("health_entry", "/health"), product_contract.get("admin_entry", "/admin/login")],
        "ui_flow_index": ["home", "admin_login", "core_business_flow"],
        "code_symbol_index": _code_index(project_root) if project_root else [],
        "test_index": ["browser_smoke", "release_structure", "quality_gate"],
        "failure_history": [],
        "repair_history": [],
        "release_history": [],
        "package_index": {package["package_key"]: _package_context(package) for package in packages},
    }
    snapshot["index_hash"] = sha256_bytes(str(snapshot).encode("utf-8"))
    return snapshot


def package_context(snapshot: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    payload = package.get("payload") or package
    package_key = payload.get("package_key", package.get("package_key", ""))
    requirement_ids = payload.get("requirements", [])
    requirements = snapshot.get("requirements_index", {})
    return {
        "schema_version": "4.5",
        "index_hash": snapshot.get("index_hash", ""),
        "package_contract": snapshot.get("package_index", {}).get(package_key, _package_context(payload)),
        "requirements": [{"id": req_id, "text": requirements.get(req_id, "")} for req_id in requirement_ids],
        "adr": snapshot.get("adr_index", []),
        "boundary_rules": snapshot.get("boundary_rules", []),
        "interface_contracts": snapshot.get("interface_contract_index", {}),
        "database_contracts": snapshot.get("database_schema_index", {}) if payload.get("database_contract_refs") else {},
        "ui_contracts": snapshot.get("ui_flow_index", []) if payload.get("ui_contract_refs") else [],
        "failure_history": snapshot.get("failure_history", [])[-5:],
    }


def validate_context_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    required = (
        "requirements_index",
        "adr_index",
        "boundary_rules",
        "interface_contract_index",
        "database_schema_index",
        "route_index",
        "ui_flow_index",
        "code_symbol_index",
        "test_index",
        "failure_history",
        "repair_history",
        "release_history",
        "package_index",
        "index_hash",
    )
    missing = [key for key in required if not snapshot or key not in snapshot]
    return {"ok": not missing, "missing": missing, "schema_version": (snapshot or {}).get("schema_version", "")}


def _package_context(package: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "package_key",
        "subsystem",
        "domain",
        "role",
        "wave_key",
        "depends_on",
        "allowed_paths",
        "forbidden_paths",
        "required_inputs",
        "expected_outputs",
        "interface_contract_refs",
        "database_contract_refs",
        "ui_contract_refs",
        "test_requirements",
        "effective_loc_budget",
        "acceptance_gates",
    )
    return {key: package.get(key, [] if key.endswith("s") else "") for key in keys}


def _code_index(root: Path | None) -> list[dict[str, Any]]:
    if not root or not root.exists():
        return []
    indexed: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or ".v4" in path.parts:
            continue
        if path.suffix.lower() not in {".php", ".py", ".js", ".ts", ".tsx", ".css", ".sql"}:
            continue
        indexed.append({"path": str(path.relative_to(root)), "loc": source_line_count(path)})
        if len(indexed) >= 200:
            break
    return indexed
