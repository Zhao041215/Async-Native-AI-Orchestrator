from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from dev_orchestrator.code_metrics import measure_codebase
from dev_orchestrator.v2.models import utc_now


TARGET_SCALES = {"small", "medium", "large", "xlarge_100k"}
UNATTENDED_MODES = {"off", "candidate_only", "auto_publish_with_rollback"}
BENCHMARK_TYPES = {"enterprise_saas"}


ENTERPRISE_SAAS_BENCHMARK = {
    "id": "enterprise-saas",
    "label": "企业 SaaS 平台",
    "benchmark_type": "enterprise_saas",
    "effective_loc_target": 100000,
    "required_capabilities": [
        "tenant_isolation",
        "rbac_or_sso",
        "audit_logs",
        "frontend_console",
        "api_services",
        "background_jobs",
        "reports",
        "contract_tests",
        "deployment",
        "release_rollback",
    ],
    "quality_rules": [
        "must_requirements_mapped",
        "tests_exist",
        "quality_gates_pass",
        "no_fallback_artifacts",
        "effective_loc_only",
    ],
}


TEMPLATE_MARKERS = (
    "fallback",
    "normalized",
    "placeholder",
    "todo",
    "lorem ipsum",
    "OperationsService",
    "Tenant1Service",
)


LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".vue": "vue",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".sh": "shell",
    ".ps1": "powershell",
}


def normalize_target_scale(value: str | None) -> str:
    normalized = (value or "medium").strip().lower()
    return normalized if normalized in TARGET_SCALES else "medium"


def normalize_benchmark_type(value: str | None) -> str:
    normalized = (value or "enterprise_saas").strip().lower().replace("-", "_")
    return normalized if normalized in BENCHMARK_TYPES else "enterprise_saas"


def normalize_unattended_mode(value: str | None) -> str:
    normalized = (value or "off").strip().lower()
    return normalized if normalized in UNATTENDED_MODES else "off"


def normalize_effective_loc_target(value: object) -> int:
    try:
        target = int(value)
    except (TypeError, ValueError):
        target = 100000
    return max(1000, min(target, 500000))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifact_file(root: Path, tenant_id: str, project_id: str, run_id: str, kind: str, name: str, payload: object) -> dict:
    safe_kind = re.sub(r"[^a-zA-Z0-9_.-]+", "-", kind).strip("-") or "artifact"
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-") or "artifact.json"
    tenant_segment = re.sub(r"[^a-zA-Z0-9_.-]+", "-", tenant_id).strip("-")[:24] or "tenant"
    project_segment = re.sub(r"[^a-zA-Z0-9_.-]+", "-", project_id).strip("-")[:12] or "project"
    run_segment = re.sub(r"[^a-zA-Z0-9_.-]+", "-", run_id or "project").strip("-")[:12] or "run"
    directory = Path(root) / "workspace" / "artifacts" / tenant_segment / project_segment / run_segment / safe_kind
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return {
        "kind": safe_kind,
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "payload": {"name": safe_name},
    }


def build_artifact_manifest(artifacts: list[dict]) -> dict:
    return {
        "schema_version": "2.2.0",
        "kind": "v2-artifact-manifest",
        "generated_at": utc_now(),
        "items": [
            {
                "id": item.get("id", ""),
                "kind": item.get("kind", ""),
                "path": item.get("path", ""),
                "sha256": item.get("sha256", ""),
                "size_bytes": item.get("size_bytes", 0),
                "created_at": item.get("created_at", ""),
            }
            for item in artifacts
        ],
    }


def measure_effective_loc(project_root: Path, requirement_bundle: dict) -> dict:
    metrics = measure_codebase(project_root)
    contentful_files = metrics.get("contentful_files", [])
    requirement_keywords = {
        str(keyword).lower()
        for atom in requirement_bundle.get("atoms", [])
        for keyword in atom.get("keywords", [])
        if len(str(keyword)) >= 3
    }
    effective_lines = 0
    excluded_lines = 0
    counted_files: list[str] = []
    excluded_files: list[str] = []
    for relative in contentful_files:
        path = Path(project_root) / relative
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        line_count = len(text.splitlines())
        lowered = text.lower()
        marker_hit = any(marker.lower() in lowered for marker in TEMPLATE_MARKERS)
        keyword_hits = [keyword for keyword in requirement_keywords if keyword in lowered]
        is_test_or_infra = relative.startswith(("tests/", "infra/"))
        if marker_hit or (requirement_keywords and not keyword_hits and not is_test_or_infra):
            excluded_lines += line_count
            excluded_files.append(relative)
            continue
        effective_lines += line_count
        counted_files.append(relative)
    return {
        "schema_version": "1.0.0",
        "kind": "effective-loc-metrics",
        "generated_at": utc_now(),
        "source_lines": metrics.get("source_lines", 0),
        "effective_loc": effective_lines,
        "excluded_loc": excluded_lines,
        "effective_file_count": len(counted_files),
        "excluded_file_count": len(excluded_files),
        "counted_files": counted_files,
        "excluded_files": excluded_files[:200],
        "target_ready": False,
    }


def build_code_index(project_root: Path, tenant_id: str, project_id: str, run_id: str, snapshot_id: str = "") -> list[dict]:
    metrics = measure_codebase(project_root)
    entries: list[dict] = []
    for relative in metrics.get("contentful_files", []):
        path = Path(project_root) / relative
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        suffix = path.suffix.lower()
        symbols = re.findall(r"^\s*(?:class|def|function|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.MULTILINE)
        keywords = sorted({item.lower() for item in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text)})[:30]
        parts = relative.split("/")
        entries.append(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "snapshot_id": snapshot_id,
                "path": relative,
                "area": parts[0] if parts else "",
                "language": LANGUAGE_BY_EXTENSION.get(suffix, suffix.strip(".")),
                "symbol_count": len(symbols),
                "line_count": len(text.splitlines()),
                "keywords": keywords,
                "summary": ", ".join(symbols[:12]) or relative,
            }
        )
    return entries


def build_context_snapshot_payload(project: dict, requirement_bundle: dict, run: dict | None, code_index: list[dict]) -> dict:
    return {
        "schema_version": "1.0.0",
        "kind": "v2-context-snapshot",
        "project": {
            "id": project.get("id", ""),
            "name": project.get("name", ""),
            "title": project.get("title", ""),
            "target_scale": project.get("target_scale", "medium"),
            "benchmark_type": project.get("benchmark_type", "enterprise_saas"),
            "unattended_mode": project.get("unattended_mode", "off"),
        },
        "requirements": {
            "atom_count": len(requirement_bundle.get("atoms", [])),
            "must_coverage_percent": requirement_bundle.get("coverage", {}).get("must_coverage_percent", 0),
            "work_package_count": len(requirement_bundle.get("work_packages", [])),
        },
        "run": {
            "id": (run or {}).get("id", ""),
            "status": (run or {}).get("status", ""),
            "continuation_state": (run or {}).get("continuation_state", {}),
            "quality_gate_history": (run or {}).get("quality_gate_history", []),
        },
        "code_index": {
            "file_count": len(code_index),
            "files": [
                {
                    "path": item.get("path", ""),
                    "area": item.get("area", ""),
                    "language": item.get("language", ""),
                    "line_count": item.get("line_count", 0),
                    "summary": item.get("summary", ""),
                }
                for item in code_index[:300]
            ],
        },
    }


def score_enterprise_saas_benchmark(project: dict, requirement_bundle: dict, effective_loc: dict, quality: dict | None = None) -> dict:
    quality = quality or {}
    coverage = requirement_bundle.get("coverage", {})
    score = 0
    if coverage.get("must_coverage_percent", 0) == 100 and coverage.get("must_total", 0) > 0:
        score += 25
    if effective_loc.get("effective_loc", 0) >= min(int(project.get("effective_loc_target", 100000)), 100000):
        score += 25
    elif effective_loc.get("effective_loc", 0) >= 10000:
        score += 12
    if quality.get("status") == "passed":
        score += 25
    if not any(item.get("severity") == "critical" for item in quality.get("findings", [])):
        score += 10
    if requirement_bundle.get("work_packages"):
        score += 15
    status = "passed" if score >= 85 else "partial" if score >= 50 else "failed"
    return {
        "benchmark": ENTERPRISE_SAAS_BENCHMARK,
        "status": status,
        "score": min(100, score),
        "effective_loc": effective_loc.get("effective_loc", 0),
        "must_coverage_percent": coverage.get("must_coverage_percent", 0),
        "quality_status": quality.get("status", "not_run"),
    }
