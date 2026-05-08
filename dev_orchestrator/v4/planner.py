from __future__ import annotations

from typing import Any

from dev_orchestrator.v4.models import new_id
from dev_orchestrator.v4.stack_packs import build_product_contract, decide_stack_pack
from dev_orchestrator.v5.product_intelligence import build_requirements_understanding, build_solution_graph


XLARGE_REQUIRED_DOMAINS = {
    "tenant": ("tenant", "multi-tenant", "\u79df\u6237"),
    "rbac": ("rbac", "role", "\u6743\u9650", "\u89d2\u8272"),
    "audit": ("audit", "\u5ba1\u8ba1"),
    "security": ("security", "csrf", "xss", "\u5b89\u5168"),
    "data_model": ("data model", "schema", "\u6570\u636e\u5e93", "\u6570\u636e\u6a21\u578b"),
    "api": ("api", "\u63a5\u53e3"),
    "frontend": ("frontend", "ui", "console", "\u524d\u7aef", "\u9875\u9762"),
    "background_jobs": ("background", "queue", "job", "\u540e\u53f0\u4efb\u52a1"),
    "reports": ("report", "analytics", "\u62a5\u8868"),
    "tests": ("test", "\u6d4b\u8bd5"),
    "deploy": ("deploy", "docker", "\u90e8\u7f72", "\u53d1\u5e03"),
    "rollback": ("rollback", "\u56de\u6eda"),
}


def _validate_xlarge(requirement_text: str, target_scale: str) -> dict[str, Any]:
    if target_scale != "xlarge_100k":
        return {"ok": True, "missing_domains": []}
    lowered = requirement_text.lower()
    missing = []
    for domain, keywords in XLARGE_REQUIRED_DOMAINS.items():
        if not any(keyword in lowered for keyword in keywords):
            missing.append(domain)
    return {"ok": not missing, "missing_domains": missing}


def _package_contract(
    *,
    package: dict[str, Any],
    requirements: list[dict[str, Any]],
    product_contract: dict[str, Any],
) -> dict[str, Any]:
    estimated_loc = int(package.get("estimated_effective_loc") or 0)
    package_key = str(package.get("package_key") or new_id())
    domain = str(package.get("domain") or "feature")
    allowed_paths = list(package.get("allowed_paths") or ["release/**", ".v4/**"])
    forbidden_paths = list(package.get("forbidden_paths") or [])
    return {
        "id": package.get("id") or new_id(),
        "package_key": package_key,
        "title": package.get("title") or package_key,
        "subsystem": package.get("subsystem") or domain,
        "role": package.get("role") or "backend",
        "domain": domain,
        "wave_key": package.get("wave_key") or "WAVE-001",
        "wave_sequence": int(str(package.get("wave_key") or "WAVE-001").split("-")[-1]),
        "depends_on": list(package.get("depends_on") or []),
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "required_inputs": ["product_contract", "requirements_index", "boundary_rules", "solution_graph"],
        "expected_outputs": ["patch_manifest", "package_evidence", "requirements_mapping", "acceptance_evidence"],
        "interface_contract_refs": ["product_contract"],
        "database_contract_refs": ["database_schema_index"] if domain in {"data", "backend", "integration", "test"} else [],
        "ui_contract_refs": ["ui_flow_index"] if domain in {"frontend", "integration", "test"} else [],
        "test_requirements": ["package evidence exists", "wave gate remains green"],
        "effective_loc_budget": {"target": estimated_loc, "min": max(20, int(estimated_loc * 0.5)), "max": max(80, int(estimated_loc * 1.6))},
        "estimated_effective_loc": estimated_loc,
        "status": "queued",
        "requirements": [item["id"] for item in requirements[: min(8, len(requirements))]],
        "scope": [domain],
        "stack_pack": product_contract["stack_pack"],
        "acceptance_gates": ["package_contract_gate", "path_boundary_gate", "requirements_mapping_gate"],
        "payload": {
            **package,
            "allowed_paths": allowed_paths,
            "forbidden_paths": forbidden_paths,
            "effective_loc_budget": {"target": estimated_loc, "min": max(20, int(estimated_loc * 0.5)), "max": max(80, int(estimated_loc * 1.6))},
            "requirements": [item["id"] for item in requirements[: min(8, len(requirements))]],
            "database_contract_refs": ["database_schema_index"] if domain in {"data", "backend", "integration", "test"} else [],
            "ui_contract_refs": ["ui_flow_index"] if domain in {"frontend", "integration", "test"} else [],
            "expected_outputs": ["patch_manifest", "package_evidence", "requirements_mapping", "acceptance_evidence"],
        },
    }


def build_blueprint(project: dict[str, Any], requirement_text: str, chief_analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(project.get("config") or {})
    requested_stack = str(config.get("stack_pack") or "auto")
    deployment_mode = str(config.get("deployment_mode") or "")
    api_only = config.get("api_only")
    target_scale = str(config.get("target_scale") or "small")
    stack_decision = decide_stack_pack(requirement_text, requested_stack, deployment_mode, api_only)
    requirements = _requirement_atoms(requirement_text)
    xlarge_validation = _validate_xlarge(requirement_text, target_scale)
    if not stack_decision.get("ok"):
        return {
            "ok": False,
            "no_go_reason": stack_decision.get("no_go_reason", "stack_pack_no_go"),
            "stack_decision": stack_decision,
            "requirements": requirements,
            "xlarge_validation": xlarge_validation,
        }
    if not xlarge_validation["ok"]:
        return {
            "ok": False,
            "no_go_reason": "underspecified_xlarge_100k",
            "stack_decision": stack_decision,
            "requirements": requirements,
            "xlarge_validation": xlarge_validation,
        }

    contract = build_product_contract(
        stack_decision["stack_pack"],
        stack_decision.get("deployment_mode") or deployment_mode,
        api_only,
    )
    understanding = build_requirements_understanding(
        project=project,
        requirement_text=requirement_text,
        stack_decision=stack_decision,
        ai_payload=chief_analysis,
    )
    if not understanding.get("ok"):
        return {
            "ok": False,
            "no_go_reason": "requirements_need_clarification",
            "stack_decision": stack_decision,
            "requirements": requirements,
            "xlarge_validation": xlarge_validation,
            "requirements_understanding": understanding,
        }

    solution_graph = build_solution_graph(understanding, contract, config)
    packages = [_package_contract(package=package, requirements=requirements, product_contract=contract) for package in solution_graph["packages"]]
    waves = [
        {
            "id": new_id(),
            "wave_key": wave["wave_key"],
            "sequence": wave["sequence"],
            "status": wave["status"],
            "package_count": wave["package_count"],
        }
        for wave in solution_graph["waves"]
    ]
    return {
        "ok": True,
        "schema_version": "5.0",
        "project_id": project["id"],
        "requirements": requirements,
        "stack_decision": stack_decision,
        "product_contract": contract,
        "requirements_understanding": understanding,
        "solution_graph": solution_graph,
        "work_packages": packages,
        "waves": waves,
        "xlarge_validation": xlarge_validation,
        "risk_register": [
            "Generated packages must remain demand-driven and evidence-backed.",
            "Release is blocked unless anti-template, clean release, and deploy guide gates pass.",
        ],
        "boundary_rules": [
            "Workers may only modify paths listed in package allowed_paths.",
            "Workers must not modify paths listed in package forbidden_paths.",
            "Cross-domain changes require a contract package before implementation.",
            "Core security, database, and interface contracts must not use degraded AI output.",
        ],
    }


def _requirement_atoms(requirement_text: str) -> list[dict[str, Any]]:
    lines = [line.strip(" -\t") for line in requirement_text.splitlines() if line.strip(" -\t")]
    if not lines:
        lines = ["Build a deployable production product from the submitted requirements."]
    atoms = []
    for index, line in enumerate(lines[:80], start=1):
        atoms.append({"id": f"REQ-{index:04d}", "text": line[:500]})
    return atoms
