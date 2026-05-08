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


DOMAIN_ALLOWED_PATHS = {
    "stack": ["release/index.*", "release/README.md", "release/.htaccess", "release/.user.ini", "release/nginx.sample.conf", ".v4/**"],
    "config": ["release/.env.example", "release/config/**", "release/app/Support/Env.php", ".v4/**"],
    "data": ["release/database/**", "release/app/Support/Database.php", ".v4/**"],
    "backend": ["release/app/**", "release/index.php", ".v4/**"],
    "frontend": ["release/assets/**", "release/index.php", "release/app/Controllers/**", ".v4/**"],
    "integration": ["release/**", ".v4/**"],
    "test": ["release/tests/**", ".v4/**"],
    "security": ["release/.htaccess", "release/.user.ini", "release/nginx.sample.conf", "release/app/**", ".v4/**"],
    "release": ["release/README.md", "release/**", ".v4/**"],
}

DOMAIN_FORBIDDEN_PATHS = {
    "frontend": ["release/database/**"],
    "backend": ["release/assets/**"],
    "data": ["release/assets/**"],
    "test": ["release/database/migrations/**"],
    "security": ["release/database/seeders/**"],
}


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


def _package_contract(
    *,
    package_key: str,
    title: str,
    role: str,
    domain: str,
    wave_key: str,
    requirement_ids: list[str],
    stack_pack: str,
    estimated_loc: int,
    subsystem: str | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "package_key": package_key,
        "title": title,
        "subsystem": subsystem or domain,
        "role": role,
        "domain": domain,
        "wave_key": wave_key,
        "wave_sequence": int(wave_key.split("-")[-1]),
        "depends_on": depends_on or [],
        "allowed_paths": DOMAIN_ALLOWED_PATHS.get(domain, ["release/**", ".v4/**"]),
        "forbidden_paths": DOMAIN_FORBIDDEN_PATHS.get(domain, []),
        "required_inputs": ["product_contract", "requirements_index", "boundary_rules"],
        "expected_outputs": ["patch_manifest", "package_evidence", "requirements_mapping", "acceptance_evidence"],
        "interface_contract_refs": ["product_contract"],
        "database_contract_refs": ["database_schema_index"] if domain in {"data", "backend", "integration", "test"} else [],
        "ui_contract_refs": ["ui_flow_index"] if domain in {"frontend", "integration", "test"} else [],
        "test_requirements": ["package evidence exists", "wave gate remains green"],
        "effective_loc_budget": {"target": estimated_loc, "min": max(20, int(estimated_loc * 0.5)), "max": max(80, int(estimated_loc * 1.6))},
        "estimated_effective_loc": estimated_loc,
        "status": "queued",
        "requirements": requirement_ids,
        "scope": [domain],
        "stack_pack": stack_pack,
        "acceptance_gates": ["package_contract_gate", "path_boundary_gate", "requirements_mapping_gate"],
    }


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
    previous_wave_contracts: list[str] = []
    for package_key, title, role, domain, wave_number, loc in BASE_PACKAGES:
        depends_on = previous_wave_contracts if wave_number > 1 else []
        package = _package_contract(
            package_key=package_key,
            title=title,
            role=role,
            domain=domain,
            wave_key=f"WAVE-{wave_number:03d}",
            requirement_ids=[item["id"] for item in atoms[: min(8, len(atoms))]],
            stack_pack=contract["stack_pack"],
            estimated_loc=loc,
            subsystem=domain,
            depends_on=depends_on,
        )
        packages.append(package)
        if wave_number == 1:
            previous_wave_contracts.append(package_key)

    feature_count = _feature_package_count(config)
    roles = ("backend", "frontend", "backend", "qa", "security", "integration")
    domains = ("backend", "frontend", "data", "test", "security", "integration")
    for index in range(feature_count):
        role = roles[index % len(roles)]
        domain = domains[index % len(domains)]
        wave_number = 2 + (index // 8)
        packages.append(
            _package_contract(
                package_key=f"WP-FEATURE-{index + 1:03d}",
                title=f"Scaled feature slice {index + 1}",
                role=role,
                domain=domain,
                wave_key=f"WAVE-{wave_number:03d}",
                requirement_ids=[atoms[index % len(atoms)]["id"]],
                stack_pack=contract["stack_pack"],
                estimated_loc=420,
                subsystem=f"{domain}-features",
                depends_on=["WP-STACK-010"],
            )
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
        "boundary_rules": [
            "Workers may only modify paths listed in package allowed_paths.",
            "Workers must not modify paths listed in package forbidden_paths.",
            "Cross-domain changes require a contract package before implementation.",
            "Core security, database, and interface contracts must not use degraded AI output.",
        ],
    }
