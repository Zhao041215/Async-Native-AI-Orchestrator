from __future__ import annotations

from pathlib import Path

from dev_orchestrator.metadata import read_release_version


def _encoding_scan(root_dir: Path) -> dict:
    suspicious_markers = ("閿?", "瑜?", "瀹?", "瀵?", "娴犺", "閸氼", "娑擃", "閻樿")
    targets = [
        root_dir / "dev_orchestrator" / "static" / "app.js",
        root_dir / "dev_orchestrator" / "static" / "index.html",
        root_dir / "dev_orchestrator" / "server.py",
    ]
    flagged: list[str] = []
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(marker in text for marker in suspicious_markers):
            flagged.append(str(path))
    return {"ok": not flagged, "flagged_files": flagged}


def build_system_check(root_dir: Path, config: dict, v2_storage: object | None = None) -> dict:
    runtime = config.get("runtime", {})
    identity = config.get("identity", {})
    production = config.get("production", {})
    workspace_root = root_dir / runtime.get("workspace_root", "workspace/projects")
    logs_root = root_dir / runtime.get("logs_path", "logs")
    config_path = root_dir / "orchestrator_config.json"
    static_index = root_dir / "dev_orchestrator" / "static" / "index.html"

    checks = {
        "workspace_root": str(workspace_root),
        "logs_root": str(logs_root),
        "config_path": str(config_path),
        "static_index": str(static_index),
        "deployment_mode": runtime.get("deployment_mode", ""),
        "queue_mode": runtime.get("queue_mode", ""),
        "sandbox_mode": runtime.get("sandbox_mode", ""),
        "identity_mode": identity.get("mode", ""),
        "tenant_mode": identity.get("tenant_mode", ""),
        "queue_backend": production.get("queue_backend", ""),
        "worker_model": production.get("worker_model", ""),
        "v2_only": True,
    }
    encoding_scan = _encoding_scan(root_dir)

    failures: list[str] = []
    if not workspace_root.exists():
        failures.append("workspace root is missing")
    if not logs_root.exists():
        failures.append("logs root is missing")
    if not config_path.exists():
        failures.append("orchestrator_config.json is missing")
    if not static_index.exists():
        failures.append("static index.html is missing")
    if runtime.get("deployment_mode") != "hosted-multi-user-local":
        failures.append("runtime.deployment_mode must be hosted-multi-user-local")
    if runtime.get("queue_mode") not in {"local-adapter", "external-worker", "redis"}:
        failures.append("runtime.queue_mode must be local-adapter, external-worker, or redis")
    if identity.get("tenant_mode") != "required-tenant":
        failures.append("identity.tenant_mode must be required-tenant")
    if not identity.get("require_identity", False):
        failures.append("identity.require_identity must be true")
    if not identity.get("require_tenant", False):
        failures.append("identity.require_tenant must be true")
    if production.get("worker_model") not in {"hosted-worker", "local-thread"}:
        failures.append("production.worker_model must be hosted-worker or local-thread")
    if not encoding_scan["ok"]:
        failures.append("encoding scan found suspicious mojibake markers")
    if v2_storage is not None:
        try:
            tenant = v2_storage.get_or_create_tenant(identity.get("default_tenant", "local-workspace"))
            user = v2_storage.get_or_create_user(identity.get("default_user", "local-operator"), tenant["id"])
            checks["default_tenant_id"] = tenant["id"]
            checks["default_user_id"] = user["id"]
        except Exception as exc:
            failures.append(f"hosted identity bootstrap failed: {exc}")

    return {
        "ok": len(failures) == 0,
        "release_version": read_release_version(root_dir),
        "root_dir": str(root_dir),
        "workspace_exists": workspace_root.exists(),
        "logs_exists": logs_root.exists(),
        "mock_mode": bool(config.get("llm", {}).get("use_mock", True)),
        "v2_only": True,
        "hosted_ready": len(failures) == 0,
        "encoding_scan_ok": encoding_scan["ok"],
        "encoding_scan_flagged_files": encoding_scan["flagged_files"],
        "failures": failures,
        "checks": checks,
    }


def build_acceptance_check(results: list[dict]) -> dict:
    failures = [item for item in results if not item.get("ok", False)]
    return {
        "ok": len(failures) == 0,
        "scenario_count": len(results),
        "failed_count": len(failures),
        "results": results,
    }
