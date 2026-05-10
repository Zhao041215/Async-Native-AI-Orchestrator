"""V7 Storage Lifecycle - async storage management."""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from dev_orchestrator.v7.observability import get_logger

log = get_logger(__name__)


class StorageLifecyclePolicy:
    def __init__(self, *, max_run_age_days: int = 30, exported_worktree_retention_days: int = 7) -> None:
        self.max_run_age_days = max_run_age_days
        self.exported_worktree_retention_days = exported_worktree_retention_days


class StorageLifecycleManager:
    def __init__(self, workspace_root: Path, logs_root: Path, policy: StorageLifecyclePolicy) -> None:
        self._workspace = workspace_root.resolve()
        self._logs = logs_root.resolve()
        self._policy = policy

    async def build_report(self, projects: list, runs_by_project: dict, jobs_by_run: dict, artifacts_by_run: dict) -> dict[str, Any]:
        total_bytes = await self._dir_size(self._workspace)
        return {
            "schema_version": "7.0",
            "workspace_root": str(self._workspace),
            "logs_root": str(self._logs),
            "total_bytes": total_bytes,
            "project_count": len(projects),
            "run_count": sum(len(r) for r in runs_by_project.values()),
        }

    async def _dir_size(self, path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total
