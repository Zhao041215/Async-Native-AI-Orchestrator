"""V7 Layered Mission Memory - provides structured context for agents."""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v7.models import sha256_bytes, stable_json


def build_layered_memory(
    *,
    project: dict[str, Any],
    requirements: dict[str, Any],
    architecture: dict[str, Any],
    package_plan: dict[str, Any],
    context_snapshot: dict[str, Any],
    code_index: dict[str, Any] | None = None,
    contract_index: dict[str, Any] | None = None,
    change_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    packages = package_plan.get("packages") or []
    subsystems: dict[str, dict[str, Any]] = {}
    for package in packages:
        subsystem = str(package.get("subsystem") or package.get("domain") or package.get("role") or "general")
        item = subsystems.setdefault(subsystem, {"subsystem": subsystem, "package_keys": [], "roles": [], "allowed_paths": []})
        item["package_keys"].append(package.get("package_key", ""))
        item["roles"].append(package.get("role", ""))
        item["allowed_paths"].extend(package.get("allowed_paths") or [])

    changes = []
    for artifact in (change_artifacts or [])[-80:]:
        payload = artifact.get("payload") or {}
        changes.append({
            "artifact_id": artifact.get("id", ""),
            "kind": artifact.get("kind", ""),
            "path": artifact.get("path", ""),
            "changed_files": payload.get("changed_files", []),
            "transaction_id": payload.get("transaction_id", ""),
            "ok": payload.get("ok", True),
        })

    memory = {
        "schema_version": "7.0",
        "kernel_generation": "v7_ai_native",
        "project_id": project.get("id", ""),
        "project_name": project.get("name", ""),
        "layers": {
            "project_charter": {
                "title": project.get("title", project.get("name", "")),
                "description": project.get("description", ""),
                "goals": requirements.get("goals", []),
                "users": requirements.get("users", []),
                "constraints": requirements.get("constraints", []),
                "acceptance_criteria": requirements.get("acceptance_criteria", []),
            },
            "architecture_memory": {
                "summary": architecture.get("architecture_summary", ""),
                "technology_choices": architecture.get("technology_choices", []),
                "module_boundaries": architecture.get("module_boundaries", []),
                "integration_contracts": architecture.get("integration_contracts", []),
                "project_layout": architecture.get("project_layout", {}),
            },
            "subsystem_memory": sorted(subsystems.values(), key=lambda item: item["subsystem"]),
            "package_memory": {
                str(pkg.get("package_key", "")): {
                    "role": pkg.get("role", ""),
                    "subsystem": pkg.get("subsystem", ""),
                    "wave_key": pkg.get("wave_key", ""),
                    "depends_on": pkg.get("depends_on", []),
                    "allowed_paths": pkg.get("allowed_paths", []),
                    "objective": pkg.get("objective", ""),
                    "expected_outputs": pkg.get("expected_outputs", []),
                    "acceptance_gates": pkg.get("acceptance_gates", []),
                }
                for pkg in packages
            },
            "change_memory": changes,
        },
        "retrieval_policy": {
            "mode": "layered_selective_context",
            "never_prompt_entire_repo": True,
            "package_context_keys": ["project_charter", "architecture_memory", "package_memory", "contract_index", "code_index"],
            "context_snapshot_hash": context_snapshot.get("index_hash", ""),
        },
    }
    memory["memory_hash"] = sha256_bytes(stable_json(memory).encode("utf-8"))
    return memory


def memory_for_package(memory: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    payload = package.get("payload") or package
    package_key = str(payload.get("package_key") or package.get("package_key") or "")
    package_memory = ((memory.get("layers") or {}).get("package_memory") or {}).get(package_key, {})
    subsystem = package_memory.get("subsystem") or payload.get("subsystem") or payload.get("domain") or ""
    subsystem_memory = [
        item for item in ((memory.get("layers") or {}).get("subsystem_memory") or [])
        if item.get("subsystem") == subsystem or package_key in (item.get("package_keys") or [])
    ]
    return {
        "schema_version": "7.0",
        "memory_hash": memory.get("memory_hash", ""),
        "project_charter": (memory.get("layers") or {}).get("project_charter", {}),
        "architecture_memory": (memory.get("layers") or {}).get("architecture_memory", {}),
        "subsystem_memory": subsystem_memory,
        "package_memory": package_memory,
        "change_memory": ((memory.get("layers") or {}).get("change_memory") or [])[-20:],
        "retrieval_policy": memory.get("retrieval_policy", {}),
    }
