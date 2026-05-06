from __future__ import annotations

from collections import defaultdict


AUTOMATION_MODES = {"guided", "supervised_auto", "full_auto_candidate"}
DEFAULT_AUTOMATION_MODE = "supervised_auto"
TARGET_SCALES = {"small", "medium", "large", "xlarge_100k"}
BENCHMARK_TYPES = {"enterprise_saas"}
XLARGE_REQUIRED_DOMAINS = {
    "business_domain": ("workflow", "customer", "tenant", "domain", "journey", "onboarding"),
    "permissions": ("rbac", "sso", "oauth", "permission", "role", "auth"),
    "security": ("security", "privacy", "audit", "gdpr", "pipl", "encryption"),
    "data": ("data", "model", "database", "warehouse", "retention", "report"),
    "frontend": ("frontend", "ui", "dashboard", "console", "web"),
    "backend": ("api", "service", "backend", "integration"),
    "testing": ("test", "qa", "contract", "smoke", "integration"),
    "deployment": ("deploy", "docker", "kubernetes", "helm", "release"),
    "acceptance": ("acceptance", "evidence", "approval", "gate"),
}


def normalize_automation_mode(value: str | None) -> str:
    normalized = (value or DEFAULT_AUTOMATION_MODE).strip().lower()
    return normalized if normalized in AUTOMATION_MODES else DEFAULT_AUTOMATION_MODE


def _normalize_target_scale(value: str | None) -> str:
    normalized = (value or "medium").strip().lower()
    return normalized if normalized in TARGET_SCALES else "medium"


def _normalize_benchmark_type(value: str | None) -> str:
    normalized = (value or "enterprise_saas").strip().lower().replace("-", "_")
    return normalized if normalized in BENCHMARK_TYPES else "enterprise_saas"


def _domain_coverage(raw_text: str, atoms: list[dict]) -> dict:
    lowered = " ".join([raw_text, *[str(item.get("text", "")) for item in atoms]]).lower()
    return {
        domain: any(token in lowered for token in tokens)
        for domain, tokens in XLARGE_REQUIRED_DOMAINS.items()
    }


def _incremental_batches(work_packages: list[dict], batch_size: int = 8) -> list[dict]:
    batches = []
    for index in range(0, len(work_packages), batch_size):
        packages = work_packages[index : index + batch_size]
        batches.append(
            {
                "id": f"BATCH-{len(batches) + 1:03d}",
                "work_package_ids": [item.get("id", "") for item in packages],
                "verification_gate": "contract+unit+integration" if index else "blueprint+contract",
            }
        )
    return batches


def build_project_blueprint(
    requirement_bundle: dict,
    automation_mode: str = DEFAULT_AUTOMATION_MODE,
    target_scale: str = "medium",
    effective_loc_target: int = 100000,
    benchmark_type: str = "enterprise_saas",
) -> dict:
    atoms = requirement_bundle.get("atoms", [])
    subsystems = requirement_bundle.get("subsystems", [])
    work_packages = requirement_bundle.get("work_packages", [])
    coverage = requirement_bundle.get("coverage", {})
    must_total = int(coverage.get("must_total", 0) or 0)
    must_coverage = float(coverage.get("must_coverage_percent", 0) or 0)
    categories = {item.get("category", "product") for item in atoms}
    keyword_count = len(
        {
            str(keyword).lower()
            for atom in atoms
            for keyword in atom.get("keywords", [])
            if str(keyword).strip()
        }
    )
    clause_count = sum(
        max(1, str(atom.get("text", "")).count(",") + str(atom.get("text", "")).count(";") + 1)
        for atom in atoms
    )
    risks: list[dict] = []

    if not atoms:
        risks.append(
            {
                "code": "blueprint_no_requirements",
                "severity": "critical",
                "message": "没有可执行的需求原子，无法自动交付。",
                "repair_prompt": "补充必须需求、验收标准、目标用户、系统边界和测试期望。",
            }
        )
    if must_total < 3 and clause_count < 4:
        risks.append(
            {
                "code": "blueprint_low_must_count",
                "severity": "critical",
                "message": "Must 需求数量过少，项目蓝图不足。",
                "repair_prompt": "至少补充 3 条 Must 需求，覆盖核心功能、安全/数据和验证方式。",
            }
        )
    if must_coverage < 100:
        risks.append(
            {
                "code": "blueprint_must_coverage_gap",
                "severity": "critical",
                "message": "存在未映射到工作包的 Must 需求。",
                "repair_prompt": "补齐需求到子系统和工作包的映射，再启动自动执行。",
            }
        )
    if len(subsystems) < 2 and must_total >= 6:
        risks.append(
            {
                "code": "blueprint_weak_subsystem_boundaries",
                "severity": "high",
                "message": "需求规模较大，但子系统边界不足。",
                "repair_prompt": "明确前端、后端、数据、安全、运维等子系统责任边界。",
            }
        )

    base = 35
    base += min(25, len(atoms) * 3)
    base += min(25, keyword_count * 3)
    base += min(10, max(0, clause_count - len(atoms)) * 2)
    base += min(20, len(subsystems) * 5)
    base += 15 if must_coverage == 100 and must_total else 0
    base += 5 if len(work_packages) >= max(3, len(subsystems)) else 0
    penalty = sum({"critical": 30, "high": 18, "medium": 8}.get(item["severity"], 5) for item in risks)
    score = max(0, min(100, int(base - penalty)))

    category_map: dict[str, list[str]] = defaultdict(list)
    for atom in atoms:
        category_map[atom.get("category", "product")].append(atom.get("id", ""))

    large_project = len(atoms) >= 10 or len(subsystems) >= 4 or len(work_packages) >= 8
    medium_project = not large_project and (len(atoms) >= 5 or len(subsystems) >= 2)
    if large_project:
        size = "large"
    elif medium_project:
        size = "medium"
    else:
        size = "small"

    normalized_scale = _normalize_target_scale(target_scale)
    domain_coverage = _domain_coverage(requirement_bundle.get("raw_text", ""), atoms)
    missing_xlarge_domains = [key for key, covered in domain_coverage.items() if not covered]
    xlarge_blocked = normalized_scale == "xlarge_100k" and (
        bool(missing_xlarge_domains)
        or len(subsystems) < 6
        or len(work_packages) < 10
    )
    if xlarge_blocked:
        risks.append(
            {
                "code": "blueprint_xlarge_scope_gap",
                "severity": "critical",
                "message": "xlarge_100k project scope is missing required enterprise delivery domains.",
                "repair_prompt": "Add business, permissions, security, data, frontend, backend, testing, deployment, and acceptance requirements.",
                "missing_domains": missing_xlarge_domains,
            }
        )
        penalty += 30
        score = max(0, min(100, int(base - penalty)))

    block = bool(
        not atoms
        or any(item["severity"] == "critical" for item in risks)
        or (size == "large" and score < 72)
        or (size == "medium" and score < 55)
        or xlarge_blocked
    )

    return {
        "schema_version": "2.1.0",
        "stage": "项目蓝图",
        "automation_mode": normalize_automation_mode(automation_mode),
        "target_scale": normalized_scale,
        "benchmark_type": _normalize_benchmark_type(benchmark_type),
        "effective_loc_target": int(effective_loc_target or 100000),
        "project_size": size,
        "requirement_count": len(atoms),
        "must_requirement_count": must_total,
        "subsystem_count": len(subsystems),
        "work_package_count": len(work_packages),
        "keyword_count": keyword_count,
        "clause_count": clause_count,
        "requirement_completeness_score": score,
        "decomposition_score": score,
        "subsystem_boundaries": [
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "owner_role": item.get("owner_role", ""),
                "requirement_ids": item.get("requirement_ids", []),
                "dependencies": item.get("dependencies", []),
            }
            for item in subsystems
        ],
        "requirement_categories": {key: values for key, values in sorted(category_map.items())},
        "risk_estimate": {
            "level": "high" if any(item["severity"] in {"critical", "high"} for item in risks) else "normal",
            "items": risks,
        },
        "actionable_repair_prompts": [item["repair_prompt"] for item in risks],
        "blocked": block,
        "block_reason": "项目蓝图不足，请补充需求后重新运行。" if block else "",
        "must_requirements_mapped": must_coverage == 100 and must_total > 0,
        "xlarge_readiness": {
            "domain_coverage": domain_coverage,
            "missing_domains": missing_xlarge_domains,
            "minimum_subsystems_met": len(subsystems) >= 6,
            "minimum_work_packages_met": len(work_packages) >= 10,
        },
        "subsystem_tree": [
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "owner_role": item.get("owner_role", ""),
                "children": [
                    {
                        "id": package.get("id", ""),
                        "title": package.get("title", ""),
                        "outputs": package.get("outputs", []),
                    }
                    for package in work_packages
                    if package.get("subsystem_id") == item.get("id")
                ],
            }
            for item in subsystems
        ],
        "module_budgets": [
            {
                "subsystem_id": item.get("id", ""),
                "owner_role": item.get("owner_role", ""),
                "effective_loc_budget": max(500, int((effective_loc_target or 100000) / max(1, len(subsystems)))),
                "requirement_count": len(item.get("requirement_ids", [])),
            }
            for item in subsystems
        ],
        "interface_contracts": [
            {
                "id": item.get("id", ""),
                "requirement_id": item.get("requirement_id", ""),
                "validation_method": item.get("validation_method", ""),
                "required_evidence": item.get("required_evidence", []),
            }
            for item in requirement_bundle.get("contracts", [])
        ],
        "testing_strategy": {
            "matrix": ["contract", "unit", "integration", "smoke", "security", "anti-template", "effective-loc"],
            "release_gate": "all critical gates must pass before GO",
        },
        "release_strategy": {
            "mode": "auto_publish_with_rollback_ready",
            "requires_base_sha": True,
            "requires_reverse_patch": True,
            "post_apply_smoke_check": True,
        },
        "incremental_batches": _incremental_batches(work_packages),
    }


def initial_run_metadata(
    requirement_bundle: dict,
    automation_mode: str = DEFAULT_AUTOMATION_MODE,
    target_scale: str = "medium",
    effective_loc_target: int = 100000,
    benchmark_type: str = "enterprise_saas",
) -> dict:
    blueprint = build_project_blueprint(
        requirement_bundle,
        automation_mode,
        target_scale=target_scale,
        effective_loc_target=effective_loc_target,
        benchmark_type=benchmark_type,
    )
    autonomy_by_mode = {
        "guided": 1,
        "supervised_auto": 2,
        "full_auto_candidate": 3,
    }
    return {
        "decomposition_score": blueprint["decomposition_score"],
        "autonomy_level": autonomy_by_mode.get(blueprint["automation_mode"], 2),
        "repair_round_count": 0,
        "quality_gate_history": [],
        "continuation_state": {
            "state": "planned",
            "next_action": "execute_waves" if not blueprint["blocked"] else "repair_requirements",
            "completed_waves": [],
            "pending_waves": [],
            "summary": "项目蓝图已生成。",
        },
        "project_blueprint": blueprint,
    }
