from __future__ import annotations

from typing import Any

from dev_orchestrator.v5.models import sha256_bytes


def build_contract_index(
    *,
    architecture: dict[str, Any],
    package_plan: dict[str, Any],
    patch_sets: list[dict[str, Any]],
    code_index: dict[str, Any],
    code_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = []
    for item in architecture.get("integration_contracts") or []:
        contracts.append(_contract_from_any(item, "architecture"))
    for boundary in architecture.get("module_boundaries") or []:
        contracts.append(_contract_from_any(boundary, "architecture", default_type="module_boundary"))
    for package in package_plan.get("packages") or []:
        for output in package.get("expected_outputs") or []:
            contracts.append(_contract_from_any(output, package.get("package_key", "package"), owner=package.get("package_key", "")))
    for file_info in code_index.get("files") or []:
        owner = _owner_for_file(file_info.get("path", ""), package_plan.get("packages") or [])
        for route in file_info.get("routes") or []:
            contracts.append(_contract("ui_route" if file_info.get("language") in {"html", "css"} else "api_route", route, owner, "code_index", file_info.get("path", "")))
        for table in file_info.get("db_tables") or []:
            contracts.append(_contract("db_table", table, owner, "code_index", file_info.get("path", "")))
    deduped = []
    seen = set()
    for contract in contracts:
        key = (contract["type"], contract["name"], contract.get("owner_package", ""))
        if key in seen or not contract["name"]:
            continue
        seen.add(key)
        deduped.append(contract)
    missing_consumers = _assign_consumers(package_plan.get("packages") or [], deduped)
    result = {
        "schema_version": "5.0",
        "contracts": deduped,
        "missing_consumers": missing_consumers,
        "patch_set_count": len(patch_sets),
        "review_status": (code_review or {}).get("status", ""),
    }
    result["index_hash"] = sha256_bytes(str(result).encode("utf-8"))
    return result


def _contract_from_any(value: Any, source: str, default_type: str = "module_boundary", owner: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        return _contract(str(value.get("type") or default_type), str(value.get("name") or value.get("path") or value.get("route") or value.get("table") or ""), str(value.get("owner_package") or owner), source, str(value.get("source_path") or ""))
    return _contract(default_type, str(value), owner, source, "")


def _contract(contract_type: str, name: str, owner: str, source: str, source_path: str) -> dict[str, Any]:
    return {
        "type": contract_type,
        "name": name,
        "owner_package": owner,
        "consumer_packages": [],
        "status": "active",
        "source_artifact": source,
        "source_path": source_path,
    }


def _owner_for_file(path: str, packages: list[dict[str, Any]]) -> str:
    normalized = str(path).replace("\\", "/")
    for package in packages:
        for pattern in package.get("allowed_paths") or []:
            prefix = str(pattern).replace("\\", "/").replace("**", "").rstrip("/")
            if prefix and normalized.startswith(prefix):
                return package.get("package_key", "")
    return ""


def _assign_consumers(packages: list[dict[str, Any]], contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = {contract["name"] for contract in contracts}
    by_name = {contract["name"]: contract for contract in contracts}
    missing = []
    for package in packages:
        for ref in package.get("consumes") or package.get("contract_refs") or []:
            name = str(ref.get("name") if isinstance(ref, dict) else ref)
            if name and name not in names:
                missing.append({"package_key": package.get("package_key", ""), "contract": name})
            elif name:
                consumers = by_name[name].setdefault("consumer_packages", [])
                if package.get("package_key", "") not in consumers:
                    consumers.append(package.get("package_key", ""))
    return missing
