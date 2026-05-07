from __future__ import annotations

from typing import Any

from dev_orchestrator.v4.models import new_id
from dev_orchestrator.v4.stack_packs import build_product_contract, decide_stack_pack


XLARGE_REQUIRED_DOMAINS = {
    "tenant": ("tenant", "multi-tenant", "租户"),
    "rbac": ("rbac", "role", "权限", "角色"),
    "audit": ("audit", "审计"),
    "security": ("security", "csrf", "xss", "安全"),
    "data_model": ("data model", "schema", "数据库", "数据模型"),
    "api": ("api", "接口"),
    "frontend": ("frontend", "ui", "console", "前端", "页面"),
    "background_jobs": ("background", "queue", "job", "后台任务"),
    "reports": ("report", "analytics", "报表"),
    "tests": ("test", "测试"),
    "deploy": ("deploy", "docker", "部署", "发布"),
    "rollback": ("rollback", "回滚"),
}


BASE_PACKAGES = (
    ("WP-STACK-010", "Stack and product contract", "architect", "stack", 1, 60),
    ("WP-CONFIG-020", "Configuration and environment loading", "backend", "config", 2, 160),
    ("WP-DATA-030", "Database schema, migrations, and seed data", "backend", "data", 2, 220),
    ("WP-BACKEND-040", "Core backend services and routes", "backend", "backend", 2, 280),
    ("WP-UI-050", "Browser home, admin entry, and core workflow", "frontend", "frontend", 2, 260),
    ("WP-FLOW-060", "End-to-end business flow wiring", "integration", "integration", 3, 160),
    ("WP-TEST-070", "Unit, API, browser smoke, and release checks", "qa", "test", 3, 180),
    ("WP-RELEASE-080", "Clean release, deploy guide, and rollback evidence", "release", "release", 4, 120),
)


def _requirement_atoms(requirement_text: str) -> list[dict[str, str]]:
    lines = [line.strip(" -\t") for line in requirement_text.splitlines() if line.strip(" -\t")]
    if not lines:
        lines = ["Build a deployable production product from the submitted requirements."]
    atoms = []
    for index, line in enumerate(lines[:80], start=1):
        atoms.append({"id": f"REQ-{index:04d}", "text": line[:500]})
    return atoms


def _validate_xlarge(requirement_text: str, target_scale: str) -> dict[str, Any]:
    if target_scale != "xlarge_100k":
        return {"ok": True, "missing_domains": []}
    lowered = requirement_text.lower()
    missing = []
    for domain, keywords in XLARGE_REQUIRED_DOMAINS.items():
        if not any(keyword in lowered for keyword in keywords):
            missing.append(domain)
    return {"ok": not missing, "missing_domains": missing}


def _feature_package_count(config: dict[str, Any]) -> int:
    target_scale = str(config.get("target_scale", "small"))
    effective_loc_target = int(config.get("effective_loc_target") or 1000)
    if target_scale == "xlarge_100k":
        return max(22, min(192, effective_loc_target // 600))
    if target_scale == "large":
        return max(8, min(48, effective_loc_target // 500))
    if target_scale == "medium":
        return max(3, min(20, effective_loc_target // 600))
    return max(0, min(8, effective_loc_target // 700 - 1))


def build_blueprint(project: dict[str, Any], requirement_text: str) -> dict[str, Any]:
    config = dict(project.get("config") or {})
    requested_stack = str(config.get("stack_pack") or "auto")
    deployment_mode = str(config.get("deployment_mode") or "")
    api_only = config.get("api_only")
    target_scale = str(config.get("target_scale") or "small")
    stack_decision = decide_stack_pack(requirement_text, requested_stack, deployment_mode, api_only)
    atoms = _requirement_atoms(requirement_text)
    xlarge_validation = _validate_xlarge(requirement_text, target_scale)
    if not stack_decision.get("ok"):
        return {
            "ok": False,
            "no_go_reason": stack_decision.get("no_go_reason", "stack_pack_no_go"),
            "stack_decision": stack_decision,
            "requirements": atoms,
            "xlarge_validation": xlarge_validation,
        }
    if not xlarge_validation["ok"]:
        return {
            "ok": False,
            "no_go_reason": "underspecified_xlarge_100k",
            "stack_decision": stack_decision,
            "requirements": atoms,
            "xlarge_validation": xlarge_validation,
        }

    contract = build_product_contract(
        stack_decision["stack_pack"],
        stack_decision.get("deployment_mode") or deployment_mode,
        api_only,
    )
    packages = []
    for package_key, title, role, domain, wave_number, loc in BASE_PACKAGES:
        packages.append(
            {
                "id": new_id(),
                "package_key": package_key,
                "title": title,
                "role": role,
                "domain": domain,
                "wave_key": f"WAVE-{wave_number:03d}",
                "wave_sequence": wave_number,
                "estimated_effective_loc": loc,
                "status": "queued",
                "requirements": [item["id"] for item in atoms[: min(8, len(atoms))]],
                "scope": [domain],
                "stack_pack": contract["stack_pack"],
            }
        )

    feature_count = _feature_package_count(config)
    roles = ("backend", "frontend", "backend", "qa", "security", "integration")
    domains = ("backend", "frontend", "data", "test", "security", "integration")
    for index in range(feature_count):
        role = roles[index % len(roles)]
        domain = domains[index % len(domains)]
        wave_number = 2 + (index // 8)
        packages.append(
            {
                "id": new_id(),
                "package_key": f"WP-FEATURE-{index + 1:03d}",
                "title": f"Scaled feature slice {index + 1}",
                "role": role,
                "domain": domain,
                "wave_key": f"WAVE-{wave_number:03d}",
                "wave_sequence": wave_number,
                "estimated_effective_loc": 420,
                "status": "queued",
                "requirements": [atoms[index % len(atoms)]["id"]],
                "scope": [domain],
                "stack_pack": contract["stack_pack"],
            }
        )

    wave_keys = sorted({package["wave_key"] for package in packages}, key=lambda item: int(item.split("-")[-1]))
    waves = [
        {
            "id": new_id(),
            "wave_key": wave_key,
            "sequence": int(wave_key.split("-")[-1]),
            "status": "queued",
            "package_count": sum(1 for package in packages if package["wave_key"] == wave_key),
        }
        for wave_key in wave_keys
    ]
    return {
        "ok": True,
        "schema_version": "4.0",
        "project_id": project["id"],
        "requirements": atoms,
        "stack_decision": stack_decision,
        "product_contract": contract,
        "work_packages": packages,
        "waves": waves,
        "xlarge_validation": xlarge_validation,
        "risk_register": [
            "Generated packages must remain single-domain and evidence-backed.",
            "Release is blocked unless clean release and deploy guide gates pass.",
        ],
    }

