from __future__ import annotations

import re

from dev_orchestrator.v2.models import (
    AcceptanceContract,
    ArchitectureDecision,
    Finding,
    RequirementAtom,
    Subsystem,
    WorkPackage,
)


ROLE_KEYWORDS = {
    "frontend-lead": [
        "frontend",
        "ui",
        "dashboard",
        "web",
        "mobile",
        "zh-cn",
        "chinese",
        "accessibility",
        "wcag",
    ],
    "backend-lead": [
        "api",
        "service",
        "oauth",
        "sso",
        "rbac",
        "abac",
        "permission",
        "tenant",
        "workflow",
        "ticket",
    ],
    "ai-ml-engineer": [
        "ai",
        "llm",
        "rag",
        "nlp",
        "model",
        "prediction",
        "matching",
        "resume parsing",
        "attrition",
        "fairness",
        "explainability",
    ],
    "data-engineer": [
        "data",
        "hris",
        "feature store",
        "warehouse",
        "sftp",
        "retention",
        "audit log",
        "encryption",
    ],
    "sre-devops": [
        "kubernetes",
        "helm",
        "docker",
        "observability",
        "tracing",
        "latency",
        "sla",
        "availability",
    ],
    "security-reviewer": [
        "gdpr",
        "pipl",
        "privacy",
        "pii",
        "compliance",
        "encryption",
        "mfa",
        "audit",
        "ethics",
    ],
}


CATEGORY_KEYWORDS = {
    "frontend": ROLE_KEYWORDS["frontend-lead"],
    "backend": ROLE_KEYWORDS["backend-lead"],
    "ai": ROLE_KEYWORDS["ai-ml-engineer"],
    "data": ROLE_KEYWORDS["data-engineer"],
    "ops": ROLE_KEYWORDS["sre-devops"],
    "security": ROLE_KEYWORDS["security-reviewer"],
}


def _normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        line = re.sub(r"^[#*\-\d.\s]+", "", line).strip()
        if len(line) < 18:
            continue
        if line.lower().startswith(("appendix", "document type", "version", "author")):
            continue
        lines.append(line)
    return lines


def _priority_for_line(line: str) -> str:
    lowered = line.lower()
    if any(token in lowered for token in ("must", "shall", "required", "must have", "p0", "gdpr", "pipl", "sso", "tenant", "privacy")):
        return "must"
    if any(token in lowered for token in ("should", "p1", "recommended")):
        return "should"
    if any(token in lowered for token in ("could", "optional", "p2")):
        return "could"
    return "must" if any(ch in line for ch in ("必须", "不得", "需要", "支持")) else "should"


def _category_for_line(line: str) -> str:
    lowered = line.lower()
    best_category = "product"
    best_score = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category


def _role_for_category(category: str) -> str:
    return {
        "frontend": "frontend-lead",
        "backend": "backend-lead",
        "ai": "ai-ml-engineer",
        "data": "data-engineer",
        "ops": "sre-devops",
        "security": "security-reviewer",
        "product": "product-analyst",
    }.get(category, "product-analyst")


def _keywords_for_line(line: str) -> list[str]:
    lowered = line.lower()
    tokens: list[str] = []
    for keywords in CATEGORY_KEYWORDS.values():
        for keyword in keywords:
            if keyword in lowered and keyword not in tokens:
                tokens.append(keyword)
    for candidate in re.findall(r"[A-Za-z][A-Za-z0-9+/#.-]{2,}", line):
        normalized = candidate.lower()
        if normalized not in tokens and len(tokens) < 12:
            tokens.append(normalized)
    return tokens[:12]


def _line_limit_for_priority(priority: str) -> int:
    if priority == "must":
        return 36
    if priority == "should":
        return 24
    return 12


def parse_requirements(raw_text: str) -> dict:
    lines = _normalize_lines(raw_text)
    selected: list[str] = []
    counts = {"must": 0, "should": 0, "could": 0}
    seen = set()
    for line in lines:
        compact = re.sub(r"\s+", " ", line)
        if compact.lower() in seen:
            continue
        priority = _priority_for_line(compact)
        if counts[priority] >= _line_limit_for_priority(priority):
            continue
        selected.append(compact)
        counts[priority] += 1
        seen.add(compact.lower())
        if len(selected) >= 60:
            break

    atoms: list[RequirementAtom] = []
    contracts: list[AcceptanceContract] = []
    for index, line in enumerate(selected, start=1):
        priority = _priority_for_line(line)
        category = _category_for_line(line)
        atom = RequirementAtom(
            id=f"REQ-{index:04d}",
            text=line[:900],
            priority=priority,
            category=category,
            source_section="prd",
            owner_role=_role_for_category(category),
            keywords=_keywords_for_line(line),
        )
        contract = AcceptanceContract(
            id=f"AC-{index:04d}",
            requirement_id=atom.id,
            statement=f"Implementation and tests demonstrate: {line[:300]}",
            validation_method="traceability+runtime+review",
            required_evidence=["code_reference", "test_reference", "review_finding_or_clearance"],
        )
        atom.acceptance_refs.append(contract.id)
        atoms.append(atom)
        contracts.append(contract)

    subsystems = _build_subsystems(atoms)
    work_packages = _build_work_packages(subsystems)
    decisions = _build_decisions(raw_text, subsystems)
    findings = _requirement_findings(raw_text, atoms)
    coverage = _coverage(atoms, work_packages)
    return {
        "raw_text": raw_text,
        "atoms": [item.to_dict() for item in atoms],
        "contracts": [item.to_dict() for item in contracts],
        "subsystems": [item.to_dict() for item in subsystems],
        "work_packages": [item.to_dict() for item in work_packages],
        "decisions": [item.to_dict() for item in decisions],
        "coverage": coverage,
        "findings": [item.to_dict() for item in findings],
    }


def _build_subsystems(atoms: list[RequirementAtom]) -> list[Subsystem]:
    grouped: dict[str, list[RequirementAtom]] = {}
    for atom in atoms:
        grouped.setdefault(atom.category, []).append(atom)
    if not grouped:
        grouped["product"] = []
    subsystems: list[Subsystem] = []
    order = ["product", "frontend", "backend", "ai", "data", "security", "ops"]
    for category in order:
        items = grouped.get(category, [])
        if not items:
            continue
        subsystem_id = f"SS-{len(subsystems) + 1:02d}"
        owner = _role_for_category(category)
        subsystems.append(
            Subsystem(
                id=subsystem_id,
                name={
                    "product": "product-requirements-and-journeys",
                    "frontend": "frontend-experience",
                    "backend": "backend-domain-and-api",
                    "ai": "ai-capability-platform",
                    "data": "data-governance-and-integration",
                    "security": "security-compliance-and-privacy",
                    "ops": "delivery-runtime-and-observability",
                }[category],
                owner_role=owner,
                path_hints={
                    "product": ["docs/product", "docs/coverage"],
                    "frontend": ["apps/web", "tests/frontend"],
                    "backend": ["apps/api", "tests/backend"],
                    "ai": ["apps/ai", "tests/ai"],
                    "data": ["apps/data", "tests/data"],
                    "security": ["docs/security", "tests/security"],
                    "ops": ["infra", "tests/ops"],
                }[category],
                requirement_ids=[item.id for item in items],
            )
        )
    for index, subsystem in enumerate(subsystems):
        if index and subsystem.owner_role in {"security-reviewer", "sre-devops"}:
            subsystem.dependencies.append(subsystems[index - 1].id)
    return subsystems


def _build_work_packages(subsystems: list[Subsystem]) -> list[WorkPackage]:
    packages: list[WorkPackage] = [
        WorkPackage(
            id="WP-000-chief-contract",
            title="Chief contract and architecture kickoff",
            owner_role="chief",
            subsystem_id="global",
            requirement_ids=[],
            outputs=[".agent/v2/acceptance-contract.json", ".agent/v2/dag.json"],
            status="ready",
        )
    ]
    for subsystem in subsystems:
        packages.append(
            WorkPackage(
                id=f"WP-{len(packages):03d}-{subsystem.name}",
                title=f"Build {subsystem.name}",
                owner_role=subsystem.owner_role,
                subsystem_id=subsystem.id,
                requirement_ids=subsystem.requirement_ids,
                dependencies=["WP-000-chief-contract"],
                outputs=subsystem.path_hints,
                status="ready",
                parallel_group="implementation",
            )
        )
    packages.extend(
        [
            WorkPackage(
                id="WP-900-qa-contract-verification",
                title="Contract-aware QA automation",
                owner_role="qa-automation",
                subsystem_id="verification",
                requirement_ids=[req for subsystem in subsystems for req in subsystem.requirement_ids],
                dependencies=[item.id for item in packages if item.id != "WP-000-chief-contract"],
                outputs=["tests/contract", "reports/v2/qa-evidence.json"],
                status="ready",
            ),
            WorkPackage(
                id="WP-910-security-review",
                title="Security and privacy review",
                owner_role="security-reviewer",
                subsystem_id="review",
                requirement_ids=[
                    req
                    for subsystem in subsystems
                    if subsystem.owner_role == "security-reviewer"
                    for req in subsystem.requirement_ids
                ],
                dependencies=["WP-900-qa-contract-verification"],
                outputs=["reports/v2/security-review.json"],
                status="ready",
            ),
            WorkPackage(
                id="WP-920-refactor-sheriff",
                title="Anti-shit-code refactor review",
                owner_role="refactor-sheriff",
                subsystem_id="review",
                requirement_ids=[],
                dependencies=["WP-900-qa-contract-verification"],
                outputs=["reports/v2/anti-shit-score.json"],
                status="ready",
            ),
            WorkPackage(
                id="WP-930-release-candidate",
                title="Release candidate assembly",
                owner_role="sre-devops",
                subsystem_id="release",
                requirement_ids=[],
                dependencies=["WP-910-security-review", "WP-920-refactor-sheriff"],
                outputs=["reports/v2/release-candidate.json"],
                status="ready",
            ),
        ]
    )
    return packages


def _build_decisions(raw_text: str, subsystems: list[Subsystem]) -> list[ArchitectureDecision]:
    decisions = [
        ArchitectureDecision(
            id="ADR-0001",
            title="Chief-controlled delivery",
            decision="Only the chief control plane may advance DAG phases or mark a release candidate.",
            rationale="Prevents role agents from self-certifying incomplete work.",
            impacted_subsystems=[item.id for item in subsystems],
        ),
        ArchitectureDecision(
            id="ADR-0002",
            title="Patch-first implementation",
            decision="Work packages produce patch sets from isolated workspaces instead of mutating the main tree directly.",
            rationale="Keeps parallel development auditable and conflict-aware.",
            impacted_subsystems=[item.id for item in subsystems],
        ),
    ]
    if "kubernetes" in raw_text.lower() or "helm" in raw_text.lower():
        decisions.append(
            ArchitectureDecision(
                id="ADR-0003",
                title="Production deployment baseline",
                decision="Local Docker Compose remains the first productionization target with Kubernetes extension boundaries.",
                rationale="Supports immediate production-like operation without blocking on hosted cluster setup.",
                impacted_subsystems=[item.id for item in subsystems if item.owner_role == "sre-devops"],
            )
        )
    return decisions


def _coverage(atoms: list[RequirementAtom], packages: list[WorkPackage]) -> dict:
    package_requirement_ids = {req for package in packages for req in package.requirement_ids}
    must_atoms = [item for item in atoms if item.priority == "must"]
    uncovered = [item.id for item in must_atoms if item.id not in package_requirement_ids]
    covered = len(must_atoms) - len(uncovered)
    total = len(must_atoms)
    return {
        "must_total": total,
        "must_covered": covered,
        "must_coverage_percent": 100 if total == 0 else round(covered * 100 / total, 2),
        "uncovered_must": uncovered,
        "traceability_ready": not uncovered and bool(atoms),
    }


def _requirement_findings(raw_text: str, atoms: list[RequirementAtom]) -> list[Finding]:
    findings: list[Finding] = []
    if "鈥" in raw_text or "涓" in raw_text or "鍗" in raw_text:
        findings.append(
            Finding(
                code="prd_encoding_corruption",
                severity="high",
                category="requirement-ingest",
                message="Requirement text contains mojibake/corrupted encoding markers.",
                evidence=["鈥/涓/鍗 markers"],
                owner="product-analyst",
                repair_role="product-analyst",
            )
        )
    if not atoms:
        findings.append(
            Finding(
                code="no_requirement_atoms",
                severity="critical",
                category="requirement-ingest",
                message="No actionable requirement atoms could be extracted.",
                owner="product-analyst",
                repair_role="product-analyst",
            )
        )
    if any("zh-cn" in atom.text.lower() or "simplified chinese" in atom.text.lower() for atom in atoms):
        return findings
    if "frontend" in raw_text.lower() and "chinese" in raw_text.lower():
        return findings
    return findings
