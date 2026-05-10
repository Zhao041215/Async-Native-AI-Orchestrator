from __future__ import annotations

import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dev_orchestrator.v6.models import sha256_file, stable_json, utc_now


ACTIVE_RUN_STATUSES = {"queued", "running", "recovering", "paused"}
ACTIVE_JOB_STATUSES = {"queued", "retry", "leased", "running"}
BLOCKING_JOB_STATUSES = {"dead_letter"}
FINAL_LIFECYCLE_STATES = {"pruned", "purged"}


class StorageSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageLifecyclePolicy:
    workspace_max_bytes: int = 50 * 1024 * 1024 * 1024
    gc_dry_run_default: bool = True
    exported_worktree_retention_days: int = 7
    artifact_retention_days: int = 30
    failed_run_retention_days: int = 30
    log_retention_days: int = 14
    pressure_retention_days: int = 7
    archive_enabled: bool = True

    @classmethod
    def from_runtime_config(cls, runtime: Any | None = None) -> "StorageLifecyclePolicy":
        payload = asdict(runtime) if hasattr(runtime, "__dataclass_fields__") else dict(runtime or {})
        defaults = cls()
        values = {}
        for key, default in asdict(defaults).items():
            value = payload.get(key, default)
            if isinstance(default, bool):
                values[key] = _as_bool(value, default)
            else:
                values[key] = max(0, int(value or 0))
        if values["workspace_max_bytes"] <= 0:
            values["workspace_max_bytes"] = defaults.workspace_max_bytes
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StorageLifecycleManager:
    def __init__(self, runtime_root: Path, logs_root: Path, policy: StorageLifecyclePolicy | None = None):
        self.runtime_root = runtime_root.resolve()
        self.logs_root = logs_root.resolve()
        self.policy = policy or StorageLifecyclePolicy()

    def build_report(self, projects: list[dict[str, Any]], runs_by_project: dict[str, list[dict[str, Any]]], jobs_by_run: dict[str, list[dict[str, Any]]], artifacts_by_run: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        run_items: list[dict[str, Any]] = []
        for project in projects:
            for run in runs_by_project.get(project["id"], []):
                run_items.append(self.run_summary(project, run, jobs_by_run.get(run["id"], []), artifacts_by_run.get(run["id"], [])))
        runtime_stats = directory_stats(self.runtime_root)
        logs_stats = directory_stats(self.logs_root)
        total_bytes = runtime_stats["bytes"] + logs_stats["bytes"]
        quota_ratio = total_bytes / self.policy.workspace_max_bytes if self.policy.workspace_max_bytes else 0.0
        quota_status = "blocked" if quota_ratio >= 0.9 else ("warning" if quota_ratio >= 0.7 else "ok")
        candidates = [item for item in run_items if item["prune"]["eligible"]]
        return {
            "schema_version": "6.3",
            "generated_at": utc_now().isoformat(),
            "runtime_root": str(self.runtime_root),
            "logs_root": str(self.logs_root),
            "policy": self.policy.to_dict(),
            "totals": {
                "runtime_bytes": runtime_stats["bytes"],
                "logs_bytes": logs_stats["bytes"],
                "total_bytes": total_bytes,
                "file_count": runtime_stats["file_count"] + logs_stats["file_count"],
                "dir_count": runtime_stats["dir_count"] + logs_stats["dir_count"],
            },
            "quota": {
                "max_bytes": self.policy.workspace_max_bytes,
                "used_bytes": total_bytes,
                "ratio": round(quota_ratio, 6),
                "status": quota_status,
                "warn_at_ratio": 0.7,
                "block_at_ratio": 0.9,
            },
            "runs": sorted(run_items, key=lambda item: item["total_bytes"], reverse=True),
            "cleanup_candidates": candidates,
            "largest_runs": sorted(run_items, key=lambda item: item["total_bytes"], reverse=True)[:20],
        }

    def run_summary(self, project: dict[str, Any], run: dict[str, Any], jobs: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        project_root = self.project_root(project, run)
        artifact_root = self.artifact_root(project, run)
        lifecycle = dict((run.get("metadata") or {}).get("storage_lifecycle") or {})
        exported_at = _parse_time(lifecycle.get("exported_at") or (run.get("metadata") or {}).get("delivery_exported_at"))
        active_jobs = [job for job in jobs if job.get("status") in ACTIVE_JOB_STATUSES]
        dead_letters = [job for job in jobs if job.get("status") in BLOCKING_JOB_STATUSES]
        root_stats = directory_stats(project_root)
        artifact_stats = directory_stats(artifact_root)
        state = self._lifecycle_state(run, lifecycle, exported_at, active_jobs, dead_letters)
        safe_project_root = self.is_safe_runtime_child(project_root) and project_root != self.runtime_root
        retention_elapsed = bool(exported_at and utc_now() >= exported_at + timedelta(days=self.policy.exported_worktree_retention_days))
        eligible = (
            state in {"exported", "prunable", "archived"}
            and not active_jobs
            and not dead_letters
            and bool((run.get("metadata") or {}).get("delivery_export_target"))
            and root_stats["exists"]
            and safe_project_root
            and retention_elapsed
            and lifecycle.get("state") not in FINAL_LIFECYCLE_STATES
        )
        blocked_reasons: list[str] = []
        if active_jobs or run.get("status") in ACTIVE_RUN_STATUSES:
            blocked_reasons.append("active_run_or_job")
        if dead_letters:
            blocked_reasons.append("dead_letter_unresolved")
        if not (run.get("metadata") or {}).get("delivery_export_target"):
            blocked_reasons.append("not_exported")
        if not safe_project_root:
            blocked_reasons.append("project_root_outside_managed_runtime")
        if exported_at and not retention_elapsed:
            blocked_reasons.append("retention_window_active")
        if lifecycle.get("state") in FINAL_LIFECYCLE_STATES:
            blocked_reasons.append(f"already_{lifecycle.get('state')}")
        return {
            "schema_version": "6.3",
            "project_id": project.get("id", ""),
            "project_name": project.get("name", ""),
            "run_id": run.get("id", ""),
            "run_status": run.get("status", ""),
            "state": state,
            "project_root": str(project_root),
            "artifact_root": str(artifact_root),
            "project_root_stats": root_stats,
            "artifact_stats": artifact_stats,
            "total_bytes": root_stats["bytes"] + artifact_stats["bytes"],
            "export": {
                "target_path": (run.get("metadata") or {}).get("delivery_export_target", ""),
                "exported_at": exported_at.isoformat() if exported_at else "",
                "receipt_artifact_id": lifecycle.get("delivery_export_receipt_id", ""),
            },
            "prune": {
                "eligible": eligible,
                "retention_elapsed": retention_elapsed,
                "prune_after": (exported_at + timedelta(days=self.policy.exported_worktree_retention_days)).isoformat() if exported_at else "",
                "blocked_reasons": sorted(set(blocked_reasons)),
                "estimated_reclaim_bytes": root_stats["bytes"] if eligible else 0,
            },
            "jobs": {
                "active_count": len(active_jobs),
                "dead_letter_count": len(dead_letters),
            },
            "artifact_count": len(artifacts),
        }

    def project_root(self, project: dict[str, Any], run: dict[str, Any]) -> Path:
        metadata = run.get("metadata") or {}
        configured = str(metadata.get("project_root") or project.get("resolved_project_root") or "").strip()
        if configured:
            return Path(configured).resolve()
        name = str(project.get("name") or project.get("title") or project.get("id") or "project")
        from dev_orchestrator.v6.models import slugify

        return (self.runtime_root / slugify(name)).resolve()

    def artifact_root(self, project: dict[str, Any], run: dict[str, Any]) -> Path:
        from dev_orchestrator.v6.models import slugify

        tenant = str(run.get("tenant_id") or project.get("tenant_id") or "local-workspace")
        project_name = slugify(str(project.get("name") or project.get("title") or project.get("id") or "project"))
        return (self.runtime_root / "artifacts" / tenant / project_name / str(run.get("id") or "")).resolve()

    def build_run_prune_plan(self, project: dict[str, Any], run: dict[str, Any], jobs: list[dict[str, Any]], artifacts: list[dict[str, Any]], force: bool = False) -> dict[str, Any]:
        summary = self.run_summary(project, run, jobs, artifacts)
        blocked = list(summary["prune"]["blocked_reasons"])
        if force:
            blocked = [reason for reason in blocked if reason not in {"retention_window_active"}]
        if summary["state"] not in {"exported", "prunable", "archived"}:
            blocked.append(f"state_{summary['state']}_is_not_prunable")
        project_root = Path(summary["project_root"]).resolve()
        if not self.is_safe_runtime_child(project_root):
            blocked.append("unsafe_project_root")
        if project_root == self.runtime_root:
            blocked.append("refuse_runtime_root_delete")
        targets = []
        if not blocked and summary["project_root_stats"]["exists"]:
            targets.append(
                {
                    "kind": "worktree",
                    "path": str(project_root),
                    "bytes": summary["project_root_stats"]["bytes"],
                    "reason": "exported_worktree_prune",
                }
            )
        return {
            "schema_version": "6.3",
            "ok": not blocked,
            "run_id": run.get("id", ""),
            "project_id": project.get("id", ""),
            "state": summary["state"],
            "blocked_reasons": sorted(set(blocked)),
            "targets": targets,
            "estimated_reclaim_bytes": sum(int(item["bytes"]) for item in targets),
            "summary": summary,
        }

    def apply_delete_plan(self, plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
        deleted: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        reclaimed_bytes = 0
        for target in plan.get("targets", []):
            path = Path(target["path"]).resolve()
            try:
                self.assert_safe_delete_target(path)
                before = directory_stats(path)
                if not dry_run and path.exists():
                    if path.is_file() or path.is_symlink():
                        path.unlink()
                    else:
                        shutil.rmtree(path)
                    reclaimed_bytes += int(before.get("bytes") or 0)
                deleted.append({**target, "deleted": bool(not dry_run and before.get("exists"))})
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
        return {
            "schema_version": "6.3",
            "ok": not errors and bool(plan.get("ok", True)),
            "dry_run": dry_run,
            "deleted": deleted,
            "errors": errors,
            "reclaimed_bytes": 0 if dry_run else reclaimed_bytes,
        }

    def archive_run(self, project: dict[str, Any], run: dict[str, Any], artifacts: list[dict[str, Any]], include_worktree: bool = False, dry_run: bool = True) -> dict[str, Any]:
        if not self.policy.archive_enabled:
            return {"schema_version": "6.3", "ok": False, "reason": "archive_disabled"}
        archive_root = (self.runtime_root / "archives" / str(run.get("tenant_id") or project.get("tenant_id") or "local-workspace") / str(run.get("id"))).resolve()
        archive_path = archive_root / "run-evidence.zip"
        files = self._archive_file_candidates(project, run, artifacts, include_worktree=include_worktree)
        manifest = {
            "schema_version": "6.3",
            "run_id": run.get("id", ""),
            "project_id": project.get("id", ""),
            "include_worktree": include_worktree,
            "created_at": utc_now().isoformat(),
            "files": files,
        }
        if not dry_run:
            archive_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("storage-archive-manifest.json", stable_json(manifest))
                for item in files:
                    path = Path(item["path"])
                    if path.exists() and path.is_file():
                        archive.write(path, arcname=item["archive_name"])
        stats = directory_stats(archive_path)
        return {
            "schema_version": "6.3",
            "ok": True,
            "dry_run": dry_run,
            "archive_path": str(archive_path),
            "include_worktree": include_worktree,
            "file_count": len(files),
            "manifest": manifest,
            "archive_stats": stats,
        }

    def old_log_delete_plan(self) -> dict[str, Any]:
        targets = []
        cutoff = utc_now() - timedelta(days=self.policy.log_retention_days)
        seen: set[str] = set()
        pressure_root = (self.logs_root / "pressure").resolve()
        for root, reason, days in (
            (self.logs_root, "expired_log_file", self.policy.log_retention_days),
            (pressure_root, "expired_pressure_report", self.policy.pressure_retention_days),
        ):
            cutoff = utc_now() - timedelta(days=days)
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_dir():
                    continue
                resolved = path.resolve()
                if root == self.logs_root and is_within(resolved, pressure_root):
                    continue
                if str(resolved) in seen:
                    continue
                try:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                except OSError:
                    continue
                if mtime < cutoff:
                    stats = directory_stats(path)
                    targets.append({"kind": "log", "path": str(resolved), "bytes": stats["bytes"], "reason": reason})
                    seen.add(str(resolved))
        return {
            "schema_version": "6.3",
            "ok": True,
            "targets": targets,
            "estimated_reclaim_bytes": sum(int(item.get("bytes") or 0) for item in targets),
        }

    def is_safe_runtime_child(self, path: Path) -> bool:
        return is_within(path, self.runtime_root)

    def assert_safe_delete_target(self, path: Path) -> None:
        resolved = path.resolve()
        allowed = is_within(resolved, self.runtime_root) or is_within(resolved, self.logs_root)
        if not allowed:
            raise StorageSafetyError(f"delete target is outside managed roots: {resolved}")
        if resolved in {self.runtime_root, self.logs_root}:
            raise StorageSafetyError(f"refusing to delete managed root: {resolved}")

    def _lifecycle_state(self, run: dict[str, Any], lifecycle: dict[str, Any], exported_at: datetime | None, active_jobs: list[dict[str, Any]], dead_letters: list[dict[str, Any]]) -> str:
        explicit = str(lifecycle.get("state") or "").strip()
        if explicit in FINAL_LIFECYCLE_STATES or explicit == "archived":
            return explicit
        if run.get("status") in ACTIVE_RUN_STATUSES or active_jobs:
            return "active"
        if dead_letters:
            return "blocked_dead_letter"
        if exported_at or (run.get("metadata") or {}).get("delivery_export_target"):
            retention_elapsed = bool(exported_at and utc_now() >= exported_at + timedelta(days=self.policy.exported_worktree_retention_days))
            return "prunable" if retention_elapsed else "exported"
        if run.get("status") in {"release_ready", "completed"}:
            return "release_ready"
        if run.get("status") in {"blocked", "no_go", "cancelled", "rolled_back"}:
            return "retained_for_debug"
        return "retained"

    def _archive_file_candidates(self, project: dict[str, Any], run: dict[str, Any], artifacts: list[dict[str, Any]], include_worktree: bool) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for artifact in artifacts:
            path = Path(str(artifact.get("path") or ""))
            if not path.exists() or not path.is_file():
                continue
            if not (is_within(path, self.runtime_root) or is_within(path, self.logs_root)):
                continue
            items.append(_archive_item(path, f"artifacts/{artifact.get('kind', 'artifact')}/{path.name}"))
        if include_worktree:
            project_root = self.project_root(project, run)
            if self.is_safe_runtime_child(project_root) and project_root.exists():
                for path in project_root.rglob("*"):
                    if path.is_file():
                        items.append(_archive_item(path, f"worktree/{path.relative_to(project_root).as_posix()}"))
        return items


def directory_stats(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.exists():
        return {"exists": False, "bytes": 0, "file_count": 0, "dir_count": 0}
    if path.is_file() or path.is_symlink():
        try:
            return {"exists": True, "bytes": path.stat().st_size, "file_count": 1, "dir_count": 0}
        except OSError:
            return {"exists": True, "bytes": 0, "file_count": 1, "dir_count": 0}
    total = 0
    file_count = 0
    dir_count = 1
    for item in path.rglob("*"):
        try:
            if item.is_dir() and not item.is_symlink():
                dir_count += 1
                continue
            total += item.stat().st_size
            file_count += 1
        except OSError:
            continue
    return {"exists": True, "bytes": total, "file_count": file_count, "dir_count": dir_count}


def is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _archive_item(path: Path, archive_name: str) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "archive_name": archive_name,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value in {None, ""}:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
