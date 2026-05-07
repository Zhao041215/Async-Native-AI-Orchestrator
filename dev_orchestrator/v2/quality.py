from __future__ import annotations

import json
import re
from pathlib import Path

from dev_orchestrator.code_metrics import measure_codebase
from dev_orchestrator.v2.models import Finding, ValidationRun, utc_now


PRD_LEAK_MARKERS = [
    "Document Type: Requirements Specification",
    "This document fully defines",
    "Functional Requirements",
    "Business Requirements",
    "Non-Functional Requirements",
    "The above constitutes",
]


FALLBACK_MARKERS = [
    "fallback",
    "normalized",
    "baseline source materialized after model failure",
    "model/tool execution failed",
    "governed baseline artifacts",
]


GENERIC_OPERATION_MARKERS = [
    "OperationsService",
    "Track role-and-permission-control workload",
    "LOCAL-FIRST OPERATIONS PLATFORM",
    "Tenant1Service",
    "Project3Service",
    "Task4Service",
    "Approval5Service",
]


DOMAIN_GROUPS = {
    "hr-ai": [
        "resume",
        "candidate",
        "job",
        "interview",
        "employee",
        "attrition",
        "training",
        "policy",
        "workforce",
        "talent",
        "hris",
        "payroll",
        "绩效",
        "招聘",
        "员工",
        "人才",
    ],
    "ai-platform": [
        "rag",
        "llm",
        "model",
        "prompt",
        "embedding",
        "prediction",
        "fairness",
        "explainability",
        "feature",
    ],
    "security": [
        "tenant",
        "rbac",
        "abac",
        "audit",
        "privacy",
        "gdpr",
        "pipl",
        "encryption",
        "sso",
        "mfa",
    ],
    "php-mysql-notification-signing": [
        "php",
        "mysql",
        "sql",
        "schema",
        "notification",
        "notice",
        "receipt",
        "signing",
        "signature",
        "organization",
        "import",
        "audit",
        "admin",
        "password",
        "csrf",
        "通知",
        "公告",
        "签收",
        "签名",
        "手写",
        "组织",
        "部门",
        "管理员",
        "批量导入",
        "阅读时间",
        "数据库",
        "数据表",
        "密码",
        "权限",
    ],
}

TEXT_FILE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".md",
    ".json",
    ".ps1",
    ".sh",
    ".php",
    ".sql",
    ".env",
    ".yml",
    ".yaml",
}

APPLICATION_ROOTS = ("apps", "src", "public", "database", "config")


def _iter_text_files(root: Path, relative_roots: tuple[str, ...]) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for relative_root in relative_roots:
        base = root / relative_root
        if not base.exists():
            continue
        if base.is_file():
            candidates = [base]
        else:
            candidates = [path for path in base.rglob("*") if path.is_file()]
        for path in candidates:
            if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            files.append((path.relative_to(root).as_posix(), text))
    return files


def _count_domain_hits(text: str, requirement_bundle: dict) -> dict:
    lowered = text.lower()
    requirement_keywords = {
        str(keyword).lower()
        for atom in requirement_bundle.get("atoms", [])
        for keyword in atom.get("keywords", [])
        if len(str(keyword)) >= 3
    }
    domain_keywords = {keyword for group in DOMAIN_GROUPS.values() for keyword in group}
    all_keywords = sorted(requirement_keywords | domain_keywords)
    hits = [keyword for keyword in all_keywords if keyword in lowered]
    return {
        "hit_count": len(hits),
        "hits": hits[:40],
        "keyword_count": len(all_keywords),
    }


def _line_repetition_findings(files: list[tuple[str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    repeated_classes: dict[str, list[str]] = {}
    repeated_titles: dict[str, list[str]] = {}
    content_families: dict[str, list[str]] = {}
    for relative, text in files:
        for class_name in re.findall(r"^class\s+([A-Za-z0-9_]+)", text, flags=re.MULTILINE):
            stem = re.sub(r"\d+", "", class_name)
            if stem:
                repeated_classes.setdefault(stem, []).append(relative)
        for title in re.findall(r"title:\s*['\"]([^'\"]+)['\"]", text):
            stem = re.sub(r"\d+", "", title).strip().lower()
            if stem:
                repeated_titles.setdefault(stem, []).append(relative)
        normalized = re.sub(r"\s+", "", text.lower())
        if len(normalized) > 500 and relative.startswith(tuple(f"{root}/" for root in APPLICATION_ROOTS)):
            content_families.setdefault(normalized[:500], []).append(relative)
    class_offenders = {key: value for key, value in repeated_classes.items() if len(value) >= 8}
    title_offenders = {key: value for key, value in repeated_titles.items() if len(value) >= 8}
    content_offenders = {key: value for key, value in content_families.items() if len(value) >= 5}
    if class_offenders:
        findings.append(
            Finding(
                code="template_repeated_classes",
                severity="high",
                category="anti-shit-code",
                message="Code contains large families of mechanically repeated classes.",
                evidence=list(class_offenders.keys())[:8],
                owner="refactor-sheriff",
                repair_role="refactor-sheriff",
            )
        )
    if title_offenders:
        findings.append(
            Finding(
                code="template_repeated_ui_records",
                severity="medium",
                category="anti-slop",
                message="UI/content data appears mechanically generated instead of product-specific.",
                evidence=list(title_offenders.keys())[:8],
                owner="refactor-sheriff",
                repair_role="frontend-lead",
            )
        )
    if content_offenders:
        findings.append(
            Finding(
                code="duplicate_code_family",
                severity="critical",
                category="effective-loc",
                message="Source contains repeated content families that cannot count as effective production LOC.",
                evidence=[path for paths in content_offenders.values() for path in paths[:3]][:12],
                owner="refactor-sheriff",
                repair_role="refactor-sheriff",
            )
        )
    return findings


def _qa_relevance_findings(root: Path, requirement_bundle: dict) -> list[Finding]:
    findings: list[Finding] = []
    qa_files = _iter_text_files(root, ("tests",))
    qa_text = "\n".join(text for _, text in qa_files)
    lowered = qa_text.lower()
    if not qa_text.strip():
        findings.append(
            Finding(
                code="qa_missing",
                severity="critical",
                category="verification",
                message="No QA evidence or tests were found.",
                evidence=["tests/"],
                owner="qa-automation",
                repair_role="qa-automation",
            )
        )
        return findings
    if "test_operations_platform" in lowered or "operationsservice" in lowered:
        findings.append(
            Finding(
                code="qa_tests_fake_operations_template",
                severity="critical",
                category="verification",
                message="QA tests validate the generic operations template instead of the requested product domain.",
                evidence=[relative for relative, _ in qa_files if "operation" in relative.lower()][:5],
                owner="qa-automation",
                repair_role="qa-automation",
            )
        )
    domain_hits = _count_domain_hits(qa_text, requirement_bundle)
    must_total = requirement_bundle.get("coverage", {}).get("must_total", 0)
    if must_total and domain_hits["hit_count"] < min(8, max(3, must_total // 8)):
        findings.append(
            Finding(
                code="qa_low_requirement_relevance",
                severity="high",
                category="verification",
                message="QA evidence has weak overlap with extracted requirement terms.",
                evidence=domain_hits["hits"],
                owner="qa-automation",
                repair_role="qa-automation",
            )
        )
    if any(marker in lowered for marker in FALLBACK_MARKERS):
        findings.append(
            Finding(
                code="qa_fallback_evidence",
                severity="high",
                category="verification",
                message="QA report contains fallback or normalized evidence and cannot certify delivery.",
                evidence=["tests/qa-report.md"],
                owner="qa-automation",
                repair_role="qa-automation",
            )
        )
    return findings


def evaluate_project_quality(project_root: Path, requirement_bundle: dict) -> dict:
    project_root = Path(project_root)
    metrics = measure_codebase(project_root)
    findings: list[Finding] = []
    all_files = [
        item
        for item in _iter_text_files(project_root, (*APPLICATION_ROOTS, "tests", "infra", "docs", "reports", ".agent", "README.md", "composer.json"))
        if not item[0].startswith((".agent/v2/", "reports/v2/"))
    ]
    app_files = [(relative, text) for relative, text in all_files if relative.startswith(tuple(f"{root}/" for root in APPLICATION_ROOTS)) or relative in {"composer.json"}]
    app_text = "\n".join(text for _, text in app_files)
    all_text = "\n".join(text for _, text in all_files)
    app_lower = app_text.lower()
    all_lower = all_text.lower()

    for relative, text in app_files:
        if any(marker in text for marker in PRD_LEAK_MARKERS):
            findings.append(
                Finding(
                    code="frontend_prd_leak",
                    severity="critical",
                    category="anti-slop",
                    message="Application UI contains raw PRD/requirements document text.",
                    evidence=[relative],
                    owner="refactor-sheriff",
                    repair_role="frontend-lead",
                )
            )
            break

    if any(marker.lower() in all_lower for marker in FALLBACK_MARKERS):
        findings.append(
            Finding(
                code="fallback_or_normalized_artifacts",
                severity="critical",
                category="delivery-integrity",
                message="Delivery contains fallback/normalized artifacts and must not be self-certified.",
                evidence=[marker for marker in FALLBACK_MARKERS if marker in all_lower][:6],
                owner="chief",
                repair_role="chief",
            )
        )

    if any(marker.lower() in app_lower for marker in GENERIC_OPERATION_MARKERS):
        findings.append(
            Finding(
                code="generic_operations_template_detected",
                severity="critical",
                category="domain-relevance",
                message="Implementation appears to be a generic operations template rather than the requested domain.",
                evidence=[marker for marker in GENERIC_OPERATION_MARKERS if marker.lower() in app_lower][:6],
                owner="refactor-sheriff",
                repair_role="chief",
            )
        )

    domain_hits = _count_domain_hits(app_text, requirement_bundle)
    if requirement_bundle.get("atoms") and domain_hits["hit_count"] < 8:
        findings.append(
            Finding(
                code="implementation_low_domain_relevance",
                severity="high",
                category="domain-relevance",
                message="Application code has weak overlap with requirement/domain terms.",
                evidence=domain_hits["hits"],
                owner="chief",
                repair_role="chief",
            )
        )

    by_area = metrics.get("by_area", {})
    if metrics.get("source_file_count", 0) == 0:
        findings.append(
            Finding(
                code="no_source_code",
                severity="critical",
                category="implementation-depth",
                message="No source files were found under application, test, or infra roots.",
                owner="chief",
                repair_role="chief",
            )
        )
    if by_area.get("tests", {}).get("source_file_count", 0) == 0:
        findings.append(
            Finding(
                code="no_executable_tests",
                severity="critical",
                category="verification",
                message="No executable tests were found.",
                evidence=["tests/"],
                owner="qa-automation",
                repair_role="qa-automation",
            )
        )
    app_source_lines = sum(int(by_area.get(area, {}).get("source_lines", 0) or 0) for area in APPLICATION_ROOTS)
    app_source_files = sum(int(by_area.get(area, {}).get("source_file_count", 0) or 0) for area in APPLICATION_ROOTS)
    if app_source_files and by_area.get("tests", {}).get("source_file_count", 0):
        app_lines = max(1, app_source_lines)
        test_lines = int(by_area["tests"].get("source_lines", 0))
        if test_lines / app_lines < 0.04:
            findings.append(
                Finding(
                    code="test_depth_too_low",
                    severity="medium",
                    category="verification",
                    message="Executable test depth is too low compared with application code.",
                    evidence=[f"test_lines={test_lines}", f"app_lines={app_lines}"],
                    owner="qa-automation",
                    repair_role="qa-automation",
                )
            )

    for relative, text in app_files:
        line_count = len(text.splitlines())
        if line_count > 1800:
            findings.append(
                Finding(
                    code="oversized_source_file",
                    severity="medium",
                    category="maintainability",
                    message="A source file is too large and likely hides low-cohesion code.",
                    evidence=[f"{relative}:{line_count} lines"],
                    owner="refactor-sheriff",
                    repair_role="refactor-sheriff",
                )
            )
    todo_count = len(re.findall(r"\b(TODO|FIXME|not implemented|placeholder)\b", all_text, flags=re.IGNORECASE))
    if todo_count > 12:
        findings.append(
            Finding(
                code="placeholder_density_high",
                severity="medium",
                category="anti-shit-code",
                message="Placeholder/TODO density is too high for delivery certification.",
                evidence=[str(todo_count)],
                owner="refactor-sheriff",
                repair_role="refactor-sheriff",
            )
        )

    findings.extend(_line_repetition_findings(app_files))
    findings.extend(_qa_relevance_findings(project_root, requirement_bundle))

    requirement_findings = [
        Finding(
            code=item.get("code", "requirement_finding"),
            severity=item.get("severity", "high"),
            category=item.get("category", "requirement"),
            message=item.get("message", ""),
            evidence=list(item.get("evidence", [])),
            owner=item.get("owner", "product-analyst"),
            repair_role=item.get("repair_role", "product-analyst"),
        )
        for item in requirement_bundle.get("findings", [])
    ]
    findings.extend(requirement_findings)

    coverage = requirement_bundle.get("coverage", {})
    if coverage.get("must_total", 0) and coverage.get("must_coverage_percent", 0) < 100:
        findings.append(
            Finding(
                code="must_requirements_uncovered",
                severity="critical",
                category="coverage",
                message="Not all Must requirements are mapped to work packages.",
                evidence=list(coverage.get("uncovered_must", [])),
                owner="chief",
                repair_role="chief",
            )
        )
    if requirement_bundle.get("contracts") and "traceability" not in all_lower and "contract" not in all_lower:
        findings.append(
            Finding(
                code="interface_contract_evidence_missing",
                severity="critical",
                category="contract",
                message="Interface contracts exist but no implementation or test evidence references contract traceability.",
                evidence=["contracts"],
                owner="chief",
                repair_role="system-architect",
            )
        )

    severity_penalty = {
        "critical": 35,
        "high": 20,
        "medium": 10,
        "low": 4,
    }
    score = 100
    for finding in findings:
        score -= severity_penalty.get(finding.severity, 8)
    score = max(0, min(100, score))
    status = "passed" if score >= 85 and not any(item.severity == "critical" for item in findings) else "failed"
    anti_shit_score = score

    validation = ValidationRun(
        id=f"VAL-{utc_now().replace(':', '').replace('.', '-')}",
        run_id="",
        gate="v2-quality-gates",
        status=status,
        score=score,
        findings=findings,
        evidence=[
            f"source_lines={metrics.get('source_lines', 0)}",
            f"source_file_count={metrics.get('source_file_count', 0)}",
            f"must_coverage={coverage.get('must_coverage_percent', 0)}",
            f"anti_shit_score={anti_shit_score}",
            "layers=contract,unit,integration,smoke,security,anti-template,effective-loc",
        ],
    )
    return {
        **validation.to_dict(),
        "metrics": metrics,
        "subsystem_gates": [
            {
                "name": name,
                "status": "passed" if data.get("source_file_count", 0) else "not_present",
                "source_file_count": data.get("source_file_count", 0),
                "source_lines": data.get("source_lines", 0),
            }
            for name, data in sorted(by_area.items())
        ],
        "anti_shit_score": anti_shit_score,
        "release_candidate_allowed": validation.status == "passed",
        "summary": "Quality gates passed." if validation.status == "passed" else "Quality gates failed; repair tasks required.",
    }


def write_quality_report(project_root: Path, payload: dict) -> Path:
    report_dir = Path(project_root) / "reports" / "v2"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "quality-gates.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return path
