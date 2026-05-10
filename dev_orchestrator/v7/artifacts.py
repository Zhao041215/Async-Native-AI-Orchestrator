"""Async artifact writer for the V7 orchestrator.

Persists Artifact objects to a deterministic directory layout under a
configurable base directory.
"""
from __future__ import annotations

from pathlib import Path

from dev_orchestrator.v7.models import Artifact, new_id, stable_json


class ArtifactWriter:
    """Writes and reads artifact content from the local filesystem."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir.resolve()

    def _artifact_path(self, tenant: str, project: str, run: str, kind: str, key: str) -> Path:
        """Return the canonical filesystem path for an artifact."""
        return self._base / tenant / project / run / kind / key

    async def write(self, artifact: Artifact) -> Path:
        """Persist *artifact* content to disk and return the written path."""
        dest = self._artifact_path(
            artifact.tenant_id,
            artifact.project_id,
            artifact.run_id,
            artifact.kind,
            artifact.key,
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(artifact.content, encoding="utf-8")
        return dest

    async def read(self, run_id: str, kind: str, key: str) -> str | None:
        """Read artifact content by run_id/kind/key, or ``None`` if missing.

        Searches across all tenants/projects since only ``run_id`` is given.
        """
        # Walk the tree to find the matching run_id/kind/key.
        # Layout: base / {tenant} / {project} / {run_id} / {kind} / {key}
        for tenant_dir in self._base.iterdir():
            if not tenant_dir.is_dir():
                continue
            for project_dir in tenant_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                target = project_dir / run_id / kind / key
                if target.is_file():
                    return target.read_text(encoding="utf-8")
        return None

    async def list_artifacts(
        self, run_id: str, kind: str | None = None
    ) -> list[Path]:
        """List artifact file paths for *run_id*, optionally filtered by *kind*."""
        results: list[Path] = []
        for tenant_dir in self._base.iterdir():
            if not tenant_dir.is_dir():
                continue
            for project_dir in tenant_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                run_dir = project_dir / run_id
                if not run_dir.is_dir():
                    continue
                if kind:
                    kind_dir = run_dir / kind
                    if kind_dir.is_dir():
                        results.extend(kind_dir.iterdir())
                else:
                    for kind_dir in run_dir.iterdir():
                        if kind_dir.is_dir():
                            results.extend(kind_dir.iterdir())
        return results

    async def write_json(
        self,
        project_id: str,
        run_id: str,
        job_id: str,
        kind: str,
        key: str,
        payload: dict,
        tenant_id: str = "local-workspace",
        content_type: str = "application/json",
    ) -> Path:
        """Convenience: write a JSON artifact from a dict payload."""
        import json as _json
        artifact = Artifact(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            job_id=job_id,
            kind=kind,
            key=key,
            content_type=content_type,
            content=_json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2),
        )
        return await self.write(artifact)
