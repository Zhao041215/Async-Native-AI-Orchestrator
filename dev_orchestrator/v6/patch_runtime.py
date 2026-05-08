from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dev_orchestrator.v6.models import new_id, sha256_file
from dev_orchestrator.v6.runtime import AgentFileRuntime, PatchValidationError


_GLOBAL_LOCK = threading.Lock()
_PATH_LOCKS: set[str] = set()


class PatchLockConflict(RuntimeError):
    def __init__(self, busy: list[str]):
        super().__init__(f"patch paths are locked: {busy}")
        self.busy = busy


@dataclass(frozen=True)
class PatchTarget:
    relative_path: str
    action: str
    content: str
    target: Path
    staging: Path
    base_sha256: str


class TransactionalPatchRuntime:
    def __init__(self, runtime: AgentFileRuntime):
        self.runtime = runtime

    def apply_file_manifest_transaction(
        self,
        *,
        run_id: str,
        project: dict[str, Any],
        layout: dict[str, Any],
        agent_output: dict[str, Any],
        allowed_paths: list[str],
        forbidden_paths: list[str] | None = None,
        replace_conflicts: bool = False,
        conflict_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        project_root = self.runtime.project_root(project)
        project_root.mkdir(parents=True, exist_ok=True)
        layout_roots = self.runtime.layout_roots(project_root, layout)
        files = list(agent_output.get("files") or [])
        if not files:
            raise PatchValidationError("agent output contains no files")

        transaction_id = new_id()
        staging_root = project_root / ".v6" / "patch-staging" / transaction_id
        staging_root.mkdir(parents=True, exist_ok=True)
        targets: list[PatchTarget] = []
        conflicts: list[dict[str, Any]] = []
        locked: list[str] = []
        try:
            seen_paths: set[str] = set()
            for file_spec in files:
                relative_path = self.runtime._normalize_relative_path(str(file_spec.get("path") or ""))
                if relative_path in seen_paths:
                    raise PatchValidationError(f"duplicate path in patch: {relative_path}")
                seen_paths.add(relative_path)
                action = str(file_spec.get("action") or "create").strip().lower()
                content = str(file_spec.get("content") or "")
                if action not in {"create", "replace", "delete"}:
                    raise PatchValidationError(f"unsupported file action: {action}")
                self.runtime._assert_allowed_relative(relative_path, allowed_paths, forbidden_paths or [], layout_roots)
                target = (project_root / relative_path).resolve()
                if not self.runtime._is_within(target, project_root):
                    raise PatchValidationError(f"path escapes project root: {relative_path}")
                base_sha = sha256_file(target) if target.exists() and target.is_file() else ""
                if action == "create" and target.exists() and not replace_conflicts:
                    conflicts.append({"path": relative_path, "reason": "file_exists", "base_sha256": base_sha})
                    continue
                staging = (staging_root / relative_path).resolve()
                if not self.runtime._is_within(staging, staging_root):
                    raise PatchValidationError(f"staging path escapes transaction root: {relative_path}")
                targets.append(PatchTarget(relative_path, action, content, target, staging, base_sha))

            if conflicts:
                return self._conflict_result(transaction_id, project_root, conflicts, conflict_sources or [])

            try:
                locked = self._acquire_locks(run_id, [target.relative_path for target in targets])
            except PatchLockConflict as exc:
                conflicts = [
                    {"path": key.split(":", 1)[1] if ":" in key else key, "reason": "path_locked", "lock_key": key}
                    for key in exc.busy
                ]
                return self._conflict_result(transaction_id, project_root, conflicts, conflict_sources or [])
            for target in targets:
                if target.action != "delete":
                    target.staging.parent.mkdir(parents=True, exist_ok=True)
                    target.staging.write_text(target.content, encoding="utf-8")
            rollback_files: list[dict[str, Any]] = []
            applied_files: list[dict[str, Any]] = []
            rollback_root = project_root / ".v6" / "rollback" / transaction_id
            for target in targets:
                rollback_entry = {"path": target.relative_path, "base_sha256": target.base_sha256, "existed": target.target.exists()}
                if target.target.exists() and target.target.is_file():
                    rollback_path = rollback_root / target.relative_path
                    rollback_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target.target, rollback_path)
                    rollback_entry["rollback_path"] = str(rollback_path)
                rollback_files.append(rollback_entry)
                if target.action == "delete":
                    if target.target.exists() and target.target.is_file():
                        target.target.unlink()
                    applied_files.append({"path": target.relative_path, "action": target.action, "base_sha256": target.base_sha256, "new_sha256": "", "size": 0})
                    continue
                target.target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target.staging), str(target.target))
                applied_files.append({"path": target.relative_path, "action": target.action, "base_sha256": target.base_sha256, "new_sha256": sha256_file(target.target), "size": target.target.stat().st_size})
            return {
                "ok": True,
                "status": "applied",
                "transaction_id": transaction_id,
                "project_root": str(project_root),
                "changed_files": [item["path"] for item in applied_files],
                "files": applied_files,
                "locked_paths": locked,
                "rollback_files": rollback_files,
                "conflicts": [],
            }
        finally:
            self._release_locks(locked)
            shutil.rmtree(staging_root, ignore_errors=True)

    def _acquire_locks(self, run_id: str, paths: list[str]) -> list[str]:
        keys = [f"{run_id}:{path}" for path in paths]
        with _GLOBAL_LOCK:
            busy = [key for key in keys if key in _PATH_LOCKS]
            if busy:
                raise PatchLockConflict(busy)
            for key in keys:
                _PATH_LOCKS.add(key)
        return keys

    def _release_locks(self, keys: list[str]) -> None:
        if not keys:
            return
        with _GLOBAL_LOCK:
            for key in keys:
                _PATH_LOCKS.discard(key)

    def _conflict_result(self, transaction_id: str, project_root: Path, conflicts: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "conflict",
            "transaction_id": transaction_id,
            "project_root": str(project_root),
            "changed_files": [],
            "files": [],
            "locked_paths": [],
            "rollback_files": [],
            "conflicts": conflicts,
            "conflict_sources": sources,
        }
