from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dev_orchestrator.v5.models import new_id, sha256_file, slugify


class ArtifactWriter:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def run_root(self, tenant: str, project_name: str, run_id: str, kind: str) -> Path:
        safe_project = slugify(project_name)
        path = self.workspace_root / "artifacts" / tenant / safe_project / run_id / kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(
        self,
        tenant: str,
        project_name: str,
        run_id: str,
        kind: str,
        filename: str,
        payload: Any,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self.run_root(tenant, project_name, run_id, kind) / filename
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        return self._record(run_id, kind, path, metadata or {})

    def write_text(
        self,
        tenant: str,
        project_name: str,
        run_id: str,
        kind: str,
        filename: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self.run_root(tenant, project_name, run_id, kind) / filename
        path.write_text(text, encoding="utf-8")
        return self._record(run_id, kind, path, metadata or {})

    def _record(self, run_id: str, kind: str, path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": new_id(),
            "run_id": run_id,
            "kind": kind,
            "path": str(path),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
            "metadata": metadata,
        }


def build_manifest(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "5.0",
        "artifact_count": len(artifacts),
        "items": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "path": item["path"],
                "sha256": item["sha256"],
                "size": item["size"],
                "metadata": item.get("metadata", {}),
            }
            for item in artifacts
        ],
    }


