from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from dev_orchestrator.v6.models import FORBIDDEN_RELEASE_NAMES, effective_loc
from dev_orchestrator.v6.quality import augment_quality_report


HR_FORBIDDEN_TERMS = ("employee", "employees", "personnel", "department", "hire_date")


def _gate(name: str, ok: bool, severity: str = "critical", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "ok": ok, "severity": severity, "details": details or {}}


def validate_ai_native_project(
    *,
    project_root: Path,
    layout: dict[str, Any],
    run_context: dict[str, Any],
) -> dict[str, Any]:
    project_root = project_root.resolve()
    gates: list[dict[str, Any]] = []
    gates.append(_gate("project_root_gate", project_root.exists() and project_root.is_dir(), details={"path": str(project_root)}))

    layout_report = _validate_layout(project_root, layout)
    gates.append(_gate("ai_project_layout_gate", layout_report["ok"], details=layout_report))

    forbidden_found = sorted({part for path in project_root.rglob("*") for part in path.relative_to(project_root).parts if part in FORBIDDEN_RELEASE_NAMES}) if project_root.exists() else []
    gates.append(_gate("workspace_hygiene_gate", not forbidden_found, details={"forbidden_found": forbidden_found}))

    patch_sets = run_context.get("patch_sets", [])
    gates.append(_gate("patch_set_gate", bool(patch_sets), details={"patch_set_count": len(patch_sets)}))
    gates.append(_gate("ai_content_origin_gate", _all_core_files_from_patches(project_root, patch_sets), details={"project_files": _project_files(project_root)}))

    boundary_failures = _path_boundary_failures(run_context.get("packages", []))
    gates.append(_gate("path_boundary_gate", not boundary_failures, details={"failures": boundary_failures[:50]}))

    required_task_kinds = {
        "requirements_analysis",
        "architecture_design",
        "package_planning",
        "code_generation",
        "test_generation",
        "code_review",
    }
    observed_task_kinds = {
        (artifact.get("payload") or {}).get("task_kind")
        for artifact in run_context.get("agent_runs", [])
        if (artifact.get("payload") or {}).get("ok", False)
    }
    gates.append(_gate("agent_coverage_gate", required_task_kinds.issubset(observed_task_kinds), details={"required": sorted(required_task_kinds), "observed": sorted(item for item in observed_task_kinds if item)}))

    over_budget = [item for item in run_context.get("agent_runs", []) if (item.get("payload") or {}).get("over_budget")]
    gates.append(_gate("ai_budget_gate", not over_budget, details={"over_budget_agent_runs": [item["id"] for item in over_budget]}))
    degraded_core = [
        item
        for item in run_context.get("agent_runs", [])
        if (item.get("payload") or {}).get("degraded") and (item.get("payload") or {}).get("budget", {}).get("critical")
    ]
    gates.append(_gate("model_tier_gate", not degraded_core, details={"degraded_core_agent_runs": [item["id"] for item in degraded_core]}))

    review_reports = run_context.get("code_reviews", [])
    latest_review = (review_reports[-1].get("payload") if review_reports else {}) or {}
    gates.append(_gate("ai_review_gate", bool(latest_review.get("ok", False)), details=latest_review))

    test_reports = run_context.get("test_reports", [])
    latest_test = (test_reports[-1].get("payload") if test_reports else {}) or {}
    gates.append(_gate("test_evidence_gate", bool(latest_test.get("ok", False)), details=latest_test))

    test_execution_reports = run_context.get("test_execution_reports", [])
    latest_execution = run_context.get("test_execution_report") or ((test_execution_reports[-1].get("payload") if test_execution_reports else {}) or {})
    safety_failures = latest_execution.get("safety_failures") or []
    gates.append(_gate("validation_command_coverage_gate", bool(latest_execution.get("command_count", 0)), details={"source": latest_execution.get("source", ""), "coverage": latest_execution.get("coverage", {})}))
    gates.append(_gate("test_command_safety_gate", not safety_failures, details={"safety_failures": safety_failures}))
    gates.append(_gate("test_execution_gate", bool(latest_execution.get("ok", False)), details=latest_execution))

    agent_contract_reports = run_context.get("agent_contract_reports", [])
    latest_contract_report = (agent_contract_reports[-1].get("payload") if agent_contract_reports else {}) or {}
    if latest_contract_report:
        gates.append(_gate("agent_contract_gate", bool(latest_contract_report.get("ok", False)), details=latest_contract_report))

    patch_transactions = run_context.get("patch_transactions", [])
    transaction_failures = [artifact.get("payload") or {} for artifact in patch_transactions if not (artifact.get("payload") or {}).get("ok", False)]
    gates.append(_gate("patch_transaction_gate", not transaction_failures, details={"transaction_count": len(patch_transactions), "failures": transaction_failures[:20]}))

    code_index = run_context.get("code_index") or {}
    contract_index = run_context.get("contract_index") or {}
    gates.append(_gate("code_index_freshness_gate", bool(code_index.get("index_hash")) and bool(code_index.get("files")), details={"index_hash": code_index.get("index_hash", ""), "file_count": len(code_index.get("files") or [])}, severity="major"))
    contracts = contract_index.get("contracts") or []
    ownerless = [contract for contract in contracts if not contract.get("owner_package")]
    missing_consumers = contract_index.get("missing_consumers") or []
    gates.append(_gate("contract_owner_gate", not ownerless, details={"ownerless": ownerless[:50]}, severity="major"))
    gates.append(_gate("contract_consumer_gate", not missing_consumers, details={"missing_consumers": missing_consumers[:50]}))
    gates.append(_gate("cross_package_contract_gate", bool(contract_index.get("index_hash")) and not ownerless and not missing_consumers, details={"contract_count": len(contracts), "index_hash": contract_index.get("index_hash", "")}, severity="major"))

    template_report = build_template_leak_report(project_root, str(run_context.get("requirements_text") or ""))
    gates.append(_gate("anti_template_gate", template_report["ok"], details=template_report))

    loc = effective_loc(project_root)
    target = int(run_context.get("effective_loc_target") or 0)
    if target > 0:
        minimum = max(1, int(target * 0.5))
        gates.append(_gate("effective_loc_target_gate", loc["total"] >= minimum, details={"effective_loc": loc["total"], "target": target, "minimum": minimum}, severity="major"))
    gates.append(_gate("effective_loc_gate", loc["total"] > 0, details={"effective_loc": loc["total"]}, severity="major"))

    critical_failures = [gate for gate in gates if not gate["ok"] and gate["severity"] == "critical"]
    report = {
        "schema_version": "6.0",
        "ok": not critical_failures,
        "status": "GO" if not critical_failures else "NO_GO",
        "project_root": str(project_root),
        "delivery_root": layout.get("delivery_root", ""),
        "gates": gates,
        "effective_loc": loc,
        "template_leak_report": template_report,
    }
    return augment_quality_report(report, run_context)


def build_deploy_guide(layout: dict[str, Any], release_notes: dict[str, Any], quality_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "6.0",
        "generated_by": "release_agent",
        "delivery_root": layout.get("delivery_root", ""),
        "entrypoints": layout.get("entrypoints", []),
        "validation_commands": layout.get("validation_commands", []),
        "deployment": release_notes.get("deployment") or release_notes.get("deploy_steps") or [],
        "verification": {
            "quality_status": quality_report.get("status", "NO_GO"),
            "gates_passed": [gate["name"] for gate in quality_report.get("gates", []) if gate.get("ok")],
            "gates_failed": [gate["name"] for gate in quality_report.get("gates", []) if not gate.get("ok")],
        },
        "rollback": release_notes.get("rollback") or [],
    }


def build_template_leak_report(project_root: Path, requirements_text: str) -> dict[str, Any]:
    text = _project_text(project_root).lower()
    lowered_requirements = str(requirements_text or "").lower()
    hr_allowed = any(term in lowered_requirements for term in ("hr", "human resource", "employee", "personnel", "员工", "人事", "人员"))
    leaks = []
    if not hr_allowed:
        leaks = [term for term in HR_FORBIDDEN_TERMS if term in text]
    return {
        "schema_version": "6.0",
        "ok": not leaks,
        "leaks": leaks,
        "hr_allowed": hr_allowed,
        "checked_files": len(_project_files(project_root)),
    }


def _validate_layout(project_root: Path, layout: dict[str, Any]) -> dict[str, Any]:
    required = ("source_root", "delivery_root", "entrypoints", "directories", "validation_commands")
    missing = [key for key in required if key not in layout]
    invalid_roots = []
    for key in ("source_root", "delivery_root"):
        value = str(layout.get(key) or "").strip()
        if not value:
            invalid_roots.append({"key": key, "reason": "empty"})
            continue
        if Path(value).is_absolute() or value.startswith("../") or "/../" in f"/{value}/":
            invalid_roots.append({"key": key, "reason": "unsafe", "value": value})
            continue
        candidate = (project_root / value).resolve()
        try:
            inside = candidate.is_relative_to(project_root)
        except AttributeError:  # pragma: no cover
            inside = str(candidate).startswith(str(project_root))
        if not inside:
            invalid_roots.append({"key": key, "reason": "escapes_project", "value": value})
    return {
        "ok": not missing and not invalid_roots,
        "missing": missing,
        "invalid_roots": invalid_roots,
        "source_root": layout.get("source_root", ""),
        "delivery_root": layout.get("delivery_root", ""),
    }


def _all_core_files_from_patches(project_root: Path, patch_sets: list[dict[str, Any]]) -> bool:
    if not project_root.exists():
        return False
    patch_files = {
        path
        for artifact in patch_sets
        for path in ((artifact.get("payload") or {}).get("changed_files") or [])
    }
    project_files = _project_files(project_root)
    source_files = [path for path in project_files if Path(path).suffix.lower() in {".php", ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".sql", ".md", ".json", ".html"}]
    return bool(source_files) and set(source_files).issubset(patch_files)


def _path_boundary_failures(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = []
    for package in packages:
        payload = package.get("payload") or {}
        result = package.get("result") or {}
        allowed = [str(pattern).replace("\\", "/").lstrip("/") for pattern in payload.get("allowed_paths", [])]
        forbidden = [str(pattern).replace("\\", "/").lstrip("/") for pattern in payload.get("forbidden_paths", [])]
        for changed in ((result.get("patch_manifest") or {}).get("changed_files") or []):
            normalized = str(changed).replace("\\", "/").lstrip("/")
            if allowed and not any(fnmatch(normalized, pattern) for pattern in allowed):
                failures.append({"package_key": package.get("package_key"), "path": changed, "reason": "outside_allowed_paths"})
            if forbidden and any(fnmatch(normalized, pattern) for pattern in forbidden):
                failures.append({"package_key": package.get("package_key"), "path": changed, "reason": "matches_forbidden_paths"})
    return failures


def _project_files(project_root: Path) -> list[str]:
    if not project_root.exists():
        return []
    files = []
    for path in sorted(project_root.rglob("*")):
        if path.is_dir():
            continue
        relative = str(path.relative_to(project_root)).replace("\\", "/")
        if relative.startswith(".v6/"):
            continue
        files.append(relative)
    return files


def _project_text(project_root: Path) -> str:
    chunks = []
    for relative in _project_files(project_root):
        path = project_root / relative
        if path.suffix.lower() not in {".php", ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".sql", ".md", ".json", ".html", ".txt"}:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)
