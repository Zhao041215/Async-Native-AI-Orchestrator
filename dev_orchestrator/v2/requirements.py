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
        "console",
        "admin",
        "page",
        "html",
        "css",
        "javascript",
        "report",
        "notification",
        "mobile",
        "zh-cn",
        "chinese",
        "accessibility",
        "wcag",
        "页面",
        "前端",
        "控制台",
        "管理员",
        "手写",
        "签名",
    ],
    "backend-lead": [
        "php",
        "api",
        "service",
        "controller",
        "route",
        "endpoint",
        "session",
        "oauth",
        "sso",
        "rbac",
        "abac",
        "permission",
        "auth",
        "login",
        "tenant",
        "workflow",
        "notification",
        "receipt",
        "signing",
        "signature",
        "ticket",
        "登录",
        "认证",
        "签收",
        "通知",
        "批量导入",
        "组织",
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
        "model",
        "database",
        "mysql",
        "sql",
        "schema",
        "migration",
        "table",
        "seed",
        "hris",
        "feature store",
        "warehouse",
        "sftp",
        "retention",
        "audit log",
        "encryption",
        "数据库",
        "数据表",
        "签收记录",
    ],
    "sre-devops": [
        "kubernetes",
        "helm",
        "docker",
        "deploy",
        "release",
        "rollback",
        "manual",
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
        "password",
        "csrf",
        "sql injection",
        "mfa",
        "audit",
        "ethics",
        "tenant isolation",
        "密码",
        "权限",
        "审计",
        "安全",
    ],
}

DOMAIN_TERMS = [
    "php",
    "mysql",
    "sql",
    "notification",
    "notice",
    "receipt",
    "signing",
    "signature",
    "organization",
    "import",
    "audit",
    "admin",
    "user",
    "通知",
    "公告",
    "签收",
    "签名",
    "手写",
    "组织",
    "部门",
    "用户",
    "管理员",
    "批量导入",
    "阅读时间",
    "数据库",
    "数据表",
    "密码",
    "权限",
]


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
        raw = raw.strip()
        if not raw:
            continue
        candidates = [raw]
        if len(raw) > 180 or re.search(r"[。！？；;]", raw):
            split_pattern = r"(?<=[.!?。！？；;])\s+(?=(?:Must|Should|Could|[A-Z]))|(?<=[。！？；;])\s*"
            candidates = [item.strip() for item in re.split(split_pattern, raw) if item.strip()]
        for candidate in candidates:
            line = candidate.strip()
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
    if any(token in line for token in ("必须", "不得", "需要", "要求", "支持")):
        return "must"
    if any(token in line for token in ("应该", "建议")):
        return "should"
    if any(token in line for token in ("可选", "可以")):
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
    for keyword in DOMAIN_TERMS:
        normalized_keyword = keyword.lower()
        if (normalized_keyword in lowered or keyword in line) and normalized_keyword not in tokens and len(tokens) < 16:
            tokens.append(normalized_keyword)
    for candidate in re.findall(r"[A-Za-z][A-Za-z0-9+/#.-]{2,}", line):
        normalized = candidate.lower()
        if normalized not in tokens and len(tokens) < 16:
            tokens.append(normalized)
    return tokens[:16]


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
    work_packages = _expand_php_mysql_packages(raw_text, atoms, work_packages)
    work_packages = _expand_enterprise_saas_packages(raw_text, atoms, work_packages)
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
        implementation_outputs = [
            path for path in subsystem.path_hints if not str(path).replace("\\", "/").startswith("tests/")
        ] or subsystem.path_hints[:1]
        packages.append(
            WorkPackage(
                id=f"WP-{len(packages):03d}-{subsystem.name}",
                title=f"Build {subsystem.name}",
                owner_role=subsystem.owner_role,
                subsystem_id=subsystem.id,
                requirement_ids=subsystem.requirement_ids,
                dependencies=["WP-000-chief-contract"],
                outputs=implementation_outputs,
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


def _package_exists(packages: list[WorkPackage], package_id: str) -> bool:
    return any(item.id == package_id for item in packages)


def _insert_before_review_packages(packages: list[WorkPackage], additions: list[WorkPackage]) -> list[WorkPackage]:
    if not additions:
        return packages
    first_review_index = next(
        (
            index
            for index, package in enumerate(packages)
            if package.id.startswith("WP-900-") or package.owner_role in {"qa-automation", "security-reviewer", "refactor-sheriff", "sre-devops"}
        ),
        len(packages),
    )
    return [*packages[:first_review_index], *additions, *packages[first_review_index:]]


def _replace_review_dependencies(packages: list[WorkPackage], implementation_ids: list[str]) -> list[WorkPackage]:
    if not implementation_ids:
        return packages
    implementation_set = set(implementation_ids)
    updated: list[WorkPackage] = []
    for package in packages:
        if package.id == "WP-900-qa-contract-verification":
            dependencies = sorted(set(package.dependencies) | implementation_set)
            updated.append(WorkPackage(**{**package.to_dict(), "dependencies": dependencies}))
        elif package.id == "WP-930-release-candidate":
            dependencies = sorted(set(package.dependencies) | {"WP-900-qa-contract-verification"})
            updated.append(WorkPackage(**{**package.to_dict(), "dependencies": dependencies}))
        else:
            updated.append(package)
    return updated


def _is_php_mysql_project(raw_text: str, atoms: list[RequirementAtom]) -> bool:
    lowered = raw_text.lower()
    keyword_text = " ".join(keyword for atom in atoms for keyword in atom.keywords).lower()
    has_php = "php" in lowered or "php" in keyword_text
    has_mysql = "mysql" in lowered or "mysql" in keyword_text or "数据库" in raw_text or "数据表" in raw_text
    domain_hit = any(token in lowered or token in raw_text for token in ("notification", "notice", "signing", "signature", "通知", "公告", "签收", "签名"))
    return has_php and has_mysql and domain_hit


def _php_requirement_ids(atoms: list[RequirementAtom], tokens: tuple[str, ...]) -> list[str]:
    matched: list[str] = []
    for atom in atoms:
        text = f"{atom.text} {' '.join(atom.keywords)}".lower()
        if any(token.lower() in text or token in atom.text for token in tokens):
            matched.append(atom.id)
    return matched or [item.id for item in atoms]


def _expand_php_mysql_packages(raw_text: str, atoms: list[RequirementAtom], packages: list[WorkPackage]) -> list[WorkPackage]:
    if not _is_php_mysql_project(raw_text, atoms):
        return packages

    all_requirement_ids = [item.id for item in atoms]
    additions = [
        WorkPackage(
            id="WP-PHP-010-mysql-schema",
            title="MySQL schema and seed data for notification signing",
            owner_role="data-engineer",
            subsystem_id="php-mysql-data",
            requirement_ids=_php_requirement_ids(atoms, ("mysql", "sql", "database", "schema", "数据表", "数据库", "签收记录")),
            dependencies=["WP-000-chief-contract"],
            outputs=["database/schema.sql", "database/seed.sql"],
            status="ready",
            parallel_group="implementation",
        ),
        WorkPackage(
            id="WP-PHP-020-php-domain-services",
            title="PHP domain services and repositories",
            owner_role="backend-lead",
            subsystem_id="php-mysql-backend",
            requirement_ids=_php_requirement_ids(atoms, ("php", "service", "repository", "notification", "签收", "通知", "组织", "密码")),
            dependencies=["WP-000-chief-contract", "WP-PHP-010-mysql-schema"],
            outputs=["composer.json", "src/Domain", "src/Repositories", "src/Services"],
            status="ready",
            parallel_group="implementation",
        ),
        WorkPackage(
            id="WP-PHP-030-php-http-controllers",
            title="PHP HTTP controllers, routing, auth, and session flows",
            owner_role="backend-lead",
            subsystem_id="php-mysql-backend",
            requirement_ids=_php_requirement_ids(atoms, ("api", "controller", "route", "auth", "login", "签收", "通知")),
            dependencies=["WP-PHP-020-php-domain-services"],
            outputs=["public/index.php", "src/Controllers", "src/Middleware", "src/Support"],
            status="ready",
            parallel_group="implementation",
        ),
        WorkPackage(
            id="WP-PHP-040-admin-and-signature-ui",
            title="Admin console and handwritten signature web UI",
            owner_role="frontend-lead",
            subsystem_id="php-mysql-frontend",
            requirement_ids=_php_requirement_ids(atoms, ("frontend", "ui", "web", "admin", "signature", "手写", "签名", "页面", "管理员")),
            dependencies=["WP-PHP-030-php-http-controllers"],
            outputs=["public/assets/app.css", "public/assets/signature.js", "public/views"],
            status="ready",
            parallel_group="implementation",
        ),
        WorkPackage(
            id="WP-PHP-050-import-receipt-workflows",
            title="Organization import, receipt tracking, and audit workflows",
            owner_role="backend-lead",
            subsystem_id="php-mysql-backend",
            requirement_ids=_php_requirement_ids(atoms, ("import", "csv", "receipt", "audit", "批量导入", "阅读时间", "签收")),
            dependencies=["WP-PHP-030-php-http-controllers"],
            outputs=["src/Services/CsvImporter.php", "src/Services/ReceiptWorkflow.php", "src/Services/AuditLogger.php"],
            status="ready",
            parallel_group="implementation",
        ),
        WorkPackage(
            id="WP-PHP-060-php-contract-tests",
            title="Executable PHP/MySQL contract and smoke tests",
            owner_role="qa-automation",
            subsystem_id="php-mysql-verification",
            requirement_ids=all_requirement_ids,
            dependencies=[
                "WP-PHP-010-mysql-schema",
                "WP-PHP-020-php-domain-services",
                "WP-PHP-030-php-http-controllers",
                "WP-PHP-040-admin-and-signature-ui",
                "WP-PHP-050-import-receipt-workflows",
            ],
            outputs=["tests/Feature/NotificationSigningContractTest.php", "tests/test_php_mysql_contract.py"],
            status="ready",
            parallel_group="verification",
        ),
        WorkPackage(
            id="WP-PHP-070-deployment-pack",
            title="PHP/MySQL deployment package and operations handoff",
            owner_role="sre-devops",
            subsystem_id="php-mysql-release",
            requirement_ids=all_requirement_ids,
            dependencies=["WP-PHP-060-php-contract-tests"],
            outputs=["README.md", "docs/deployment.md", "docker-compose.php-mysql.yml"],
            status="ready",
            parallel_group="deployment",
        ),
    ]
    additions = [item for item in additions if not _package_exists(packages, item.id)]
    expanded = _insert_before_review_packages(packages, additions)
    return _replace_review_dependencies(expanded, [item.id for item in additions if item.parallel_group == "implementation"])


ENTERPRISE_PACKAGE_TEMPLATES = [
    ("contract", "Business domain contract map", "system-architect", "docs/architecture/domain-contracts.md"),
    ("contract", "Tenant isolation contract", "system-architect", "docs/architecture/tenant-boundaries.md"),
    ("contract", "RBAC and SSO interface contract", "system-architect", "docs/architecture/auth-contracts.md"),
    ("contract", "Audit event contract", "system-architect", "docs/architecture/audit-contracts.md"),
    ("contract", "Reporting and notification contract", "system-architect", "docs/architecture/reporting-contracts.md"),
    ("backend", "Tenant domain service", "backend-lead", "apps/api/tenancy"),
    ("backend", "RBAC policy service", "backend-lead", "apps/api/rbac"),
    ("backend", "SSO identity adapter", "backend-lead", "apps/api/identity"),
    ("backend", "Audit log service", "backend-lead", "apps/api/audit"),
    ("backend", "Core workflow API", "backend-lead", "apps/api/workflows"),
    ("backend", "Notification API", "backend-lead", "apps/api/notifications"),
    ("backend", "Reporting API", "backend-lead", "apps/api/reports"),
    ("data", "Tenant data model", "data-engineer", "apps/data/tenancy"),
    ("data", "RBAC data model", "data-engineer", "apps/data/rbac"),
    ("data", "Audit retention model", "data-engineer", "apps/data/audit"),
    ("data", "Reporting warehouse model", "data-engineer", "apps/data/reporting"),
    ("frontend", "Admin console shell", "frontend-lead", "apps/web/admin-console"),
    ("frontend", "Tenant management UI", "frontend-lead", "apps/web/tenants"),
    ("frontend", "Role and permission UI", "frontend-lead", "apps/web/rbac"),
    ("frontend", "Audit explorer UI", "frontend-lead", "apps/web/audit"),
    ("frontend", "Reports dashboard UI", "frontend-lead", "apps/web/reports"),
    ("frontend", "Notification center UI", "frontend-lead", "apps/web/notifications"),
    ("ops", "Background job runner", "sre-devops", "apps/jobs"),
    ("ops", "Deployment compose runtime", "sre-devops", "infra/docker"),
    ("ops", "Release automation", "sre-devops", "infra/release"),
    ("ops", "Rollback automation", "sre-devops", "infra/rollback"),
    ("security", "Tenant isolation security review", "security-reviewer", "docs/security/tenant-isolation.md"),
    ("security", "Privacy and compliance review", "security-reviewer", "docs/security/privacy-compliance.md"),
    ("verification", "Contract test suite", "qa-automation", "tests/contract"),
    ("verification", "Unit test suite", "qa-automation", "tests/unit"),
    ("verification", "Integration test suite", "qa-automation", "tests/integration"),
    ("verification", "Smoke test suite", "qa-automation", "tests/smoke"),
    ("verification", "Security test suite", "qa-automation", "tests/security"),
    ("verification", "Anti-template effective LOC gate", "refactor-sheriff", "tests/quality"),
    ("docs", "Operations manual", "sre-devops", "docs/operations/manual.md"),
]


def _enterprise_domain_hits(raw_text: str) -> int:
    lowered = raw_text.lower()
    groups = [
        ("tenant", "multi-tenant", "tenant isolation"),
        ("rbac", "sso", "oauth", "auth"),
        ("audit", "security", "privacy", "compliance"),
        ("data model", "database", "warehouse"),
        ("api", "backend", "service"),
        ("frontend", "console", "dashboard"),
        ("background job", "worker", "scheduler"),
        ("report", "analytics"),
        ("notification", "email", "webhook"),
        ("test", "contract", "smoke"),
        ("deploy", "docker", "kubernetes", "release"),
        ("rollback", "manual", "runbook"),
    ]
    return sum(1 for group in groups if any(token in lowered for token in group))


def _expand_enterprise_saas_packages(
    raw_text: str,
    atoms: list[RequirementAtom],
    packages: list[WorkPackage],
) -> list[WorkPackage]:
    if _enterprise_domain_hits(raw_text) < 8 or len(packages) >= 30:
        return packages
    requirement_ids = [item.id for item in atoms]
    existing_ids = {item.id for item in packages}
    expanded = list(packages)
    previous_contract = "WP-000-chief-contract"
    for index, (domain, title, role, output) in enumerate(ENTERPRISE_PACKAGE_TEMPLATES, start=1):
        package_id = f"WP-X{index:03d}-{domain}"
        while package_id in existing_ids:
            package_id = f"{package_id}-{len(existing_ids)}"
        dependencies = ["WP-000-chief-contract"]
        if domain not in {"contract", "docs"}:
            dependencies.append(previous_contract)
        if domain == "verification":
            dependencies.extend([item.id for item in expanded if item.parallel_group == "implementation"][:12])
        if domain == "ops":
            dependencies.extend([item.id for item in expanded if item.owner_role in {"backend-lead", "data-engineer"}][:4])
        expanded.append(
            WorkPackage(
                id=package_id,
                title=title,
                owner_role=role,
                subsystem_id=f"enterprise-{domain}",
                requirement_ids=requirement_ids,
                dependencies=sorted(set(dependencies)),
                outputs=[output],
                status="ready",
                parallel_group="implementation" if domain in {"backend", "data", "frontend", "ops", "security"} else domain,
            )
        )
        existing_ids.add(package_id)
        if domain == "contract":
            previous_contract = package_id
        if len(expanded) >= 35:
            break
    return expanded


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
