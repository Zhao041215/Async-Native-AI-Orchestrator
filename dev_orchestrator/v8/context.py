"""V8 Context Builder - builds context snapshots for agent tasks without silent truncation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dev_orchestrator.v8.models import sha256_bytes, source_line_count

SCHEMA_VERSION = "7.0"

# Extended file extension whitelist (no more silent dropping of .yaml, .toml, .sh, etc.)
INDEXED_EXTENSIONS = frozenset({
    ".php", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".css", ".sql", ".html", ".md", ".json",
    ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".sh", ".bash", ".zsh",
    ".env", ".dockerfile", ".docker-compose",
    ".graphql", ".proto", ".xml",
    ".rs", ".go", ".java", ".kt", ".swift", ".rb",
    ".vue", ".svelte",
})

MAX_FILES_INDEXED = 500  # Increased from 300, with warning on truncation


def build_context_snapshot(
    *,
    requirements: dict[str, Any],
    architecture: dict[str, Any],
    package_plan: dict[str, Any],
    project_root: Path | None = None,
    code_index: dict[str, Any] | None = None,
    contract_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packages = package_plan.get("packages") or []
    effective_code_index = code_index or {
        "schema_version": SCHEMA_VERSION,
        "files": _code_index(project_root) if project_root else [],
        "index_hash": "",
    }
    effective_contract_index = contract_index or {
        "schema_version": SCHEMA_VERSION,
        "contracts": [],
        "missing_consumers": [],
        "index_hash": "",
    }
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "requirements_analysis": requirements,
        "architecture_design": architecture,
        "project_layout": architecture.get("project_layout") or architecture.get("layout") or {},
        "package_dag": {
            "waves": package_plan.get("waves") or [],
            "packages": packages,
        },
        "boundary_rules": [
            "Every file must be produced by an AI agent patch set.",
            "System code may validate and apply patches but must not render product code.",
            "Agents may only write package allowed_paths inside the AI-generated project layout.",
        ],
        "package_index": {pkg.get("package_key", ""): _package_context(pkg) for pkg in packages},
        "code_symbol_index": effective_code_index.get("files", []),
        "code_index": effective_code_index,
        "contract_index": effective_contract_index,
        "failure_history": [],
        "repair_history": [],
    }
    # Use deterministic JSON serialization for hashing (not str(dict))
    snapshot["index_hash"] = sha256_bytes(json.dumps(snapshot, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    return snapshot


def package_context(snapshot: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    payload = package.get("payload") or package
    package_key = payload.get("package_key", package.get("package_key", ""))
    return {
        "schema_version": SCHEMA_VERSION,
        "index_hash": snapshot.get("index_hash", ""),
        "requirements_analysis": snapshot.get("requirements_analysis", {}),
        "project_layout": snapshot.get("project_layout", {}),
        "package_contract": snapshot.get("package_index", {}).get(package_key, _package_context(payload)),
        "package_dag": snapshot.get("package_dag", {}),
        "boundary_rules": snapshot.get("boundary_rules", []),
        "code_symbol_index": snapshot.get("code_symbol_index", []),
        "code_index": _filter_code_index(snapshot.get("code_index", {}), payload),
        "contract_index": _filter_contract_index(snapshot.get("contract_index", {}), package_key),
        "failure_history": snapshot.get("failure_history", [])[-5:],
    }


def validate_context_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    required = (
        "requirements_analysis", "architecture_design", "project_layout",
        "package_dag", "boundary_rules", "package_index",
        "code_symbol_index", "code_index", "contract_index",
        "failure_history", "repair_history", "index_hash",
    )
    missing = [key for key in required if not snapshot or key not in snapshot]
    return {"ok": not missing, "missing": missing, "schema_version": (snapshot or {}).get("schema_version", "")}


def _package_context(package: dict[str, Any]) -> dict[str, Any]:
    list_keys = ("depends_on", "allowed_paths", "forbidden_paths", "requirements_mapping", "expected_outputs", "acceptance_gates")
    str_keys = ("package_key", "role", "agent", "domain", "subsystem", "wave_key", "objective")
    result: dict[str, Any] = {}
    for key in str_keys:
        result[key] = package.get(key, "")
    for key in list_keys:
        result[key] = package.get(key, [])
    return result


def _code_index(root: Path | None) -> list[dict[str, Any]]:
    if not root or not root.exists():
        return []
    indexed: list[dict[str, Any]] = []
    truncated = False
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if any(part in {".v6", ".v7", ".git", "__pycache__", ".pytest_cache"} for part in rel.parts):
            continue
        if path.suffix.lower() not in INDEXED_EXTENSIONS:
            continue
        indexed.append({"path": str(rel).replace("\\", "/"), "loc": source_line_count(path)})
        if len(indexed) >= MAX_FILES_INDEXED:
            truncated = True
            break
    if truncated:
        import logging
        logging.getLogger(__name__).warning(
            "Code index truncated at %d files - some files not indexed", MAX_FILES_INDEXED
        )
    return indexed


def _filter_code_index(code_index: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    allowed = [str(pattern).replace("\\", "/").replace("**", "").rstrip("/") for pattern in (package.get("allowed_paths") or [])]
    files = []
    for file_info in code_index.get("files") or []:
        path = str(file_info.get("path") or "").replace("\\", "/")
        if not allowed or any(prefix and path.startswith(prefix) for prefix in allowed):
            files.append(file_info)
    return {**code_index, "files": files}


def _filter_contract_index(contract_index: dict[str, Any], package_key: str) -> dict[str, Any]:
    contracts = []
    for contract in contract_index.get("contracts") or []:
        consumers = contract.get("consumer_packages") or []
        if contract.get("owner_package") == package_key or package_key in consumers:
            contracts.append(contract)
    missing = [item for item in contract_index.get("missing_consumers") or [] if item.get("package_key") == package_key]
    return {**contract_index, "contracts": contracts, "missing_consumers": missing}
