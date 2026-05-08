from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from dev_orchestrator.v5.models import sha256_bytes, sha256_file, slugify


class PatchValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppliedFile:
    path: str
    action: str
    sha256: str
    size: int


class AgentFileRuntime:
    _WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")
    _FORBIDDEN_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".v5"}

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def project_root(self, project: dict[str, Any]) -> Path:
        status = self.project_path_status(project)
        if not status["ok"]:
            raise PatchValidationError(status["reason"])
        return Path(status["project_root"]).resolve()

    def project_path_status(self, project: dict[str, Any]) -> dict[str, Any]:
        configured = str(project.get("project_path") or "").strip()
        if not configured:
            root = self._fallback_project_root(project)
            return {"ok": True, "source": "default_workspace", "configured": "", "project_root": str(root), "reason": ""}
        if self._can_use_configured_project_path(configured):
            root = Path(configured).expanduser().resolve()
            return {"ok": True, "source": "absolute_path", "configured": configured, "project_root": str(root), "reason": ""}
        if self._looks_like_windows_absolute_path(configured):
            mapped = self._map_windows_workspace_path(configured)
            if mapped is not None:
                return {
                    "ok": True,
                    "source": "windows_workspace_mapped_path",
                    "configured": configured,
                    "project_root": str(mapped),
                    "reason": "",
                }
            return {
                "ok": False,
                "source": "unsupported_windows_absolute_path",
                "configured": configured,
                "project_root": "",
                "reason": "This Windows path is outside the mounted workspace visible to the API runtime. Use a relative path such as v5-test01, a path under E:\\...\\workspace\\projects\\..., or an in-container path under /app/workspace/projects.",
            }
        candidate = (self.workspace_root / Path(configured)).expanduser().resolve()
        if self._is_within(candidate, self.workspace_root):
            return {"ok": True, "source": "workspace_relative_path", "configured": configured, "project_root": str(candidate), "reason": ""}
        return {
            "ok": False,
            "source": "invalid_relative_path",
            "configured": configured,
            "project_root": "",
            "reason": "Project path must be absolute for this runtime or stay inside the configured workspace root.",
        }

    def apply_file_manifest(
        self,
        *,
        project: dict[str, Any],
        layout: dict[str, Any],
        agent_output: dict[str, Any],
        allowed_paths: list[str],
        forbidden_paths: list[str] | None = None,
        replace_conflicts: bool = False,
    ) -> dict[str, Any]:
        project_root = self.project_root(project)
        project_root.mkdir(parents=True, exist_ok=True)
        layout_roots = self.layout_roots(project_root, layout)
        files = list(agent_output.get("files") or [])
        if not files:
            raise PatchValidationError("agent output contains no files")

        applied: list[AppliedFile] = []
        conflicts: list[dict[str, Any]] = []
        for file_spec in files:
            relative_path = self._normalize_relative_path(str(file_spec.get("path") or ""))
            action = str(file_spec.get("action") or "create").strip().lower()
            if action not in {"create", "replace", "delete"}:
                raise PatchValidationError(f"unsupported file action: {action}")
            self._assert_allowed_relative(relative_path, allowed_paths, forbidden_paths or [], layout_roots)
            target = (project_root / relative_path).resolve()
            if not self._is_within(target, project_root):
                raise PatchValidationError(f"path escapes project root: {relative_path}")
            if action == "create" and target.exists() and not replace_conflicts:
                conflicts.append({"path": relative_path, "reason": "file_exists"})
                continue
            if action == "delete":
                if target.exists() and target.is_file():
                    target.unlink()
                applied.append(AppliedFile(relative_path, action, "", 0))
                continue
            content = str(file_spec.get("content") or "")
            if len(content.encode("utf-8")) > 512_000:
                raise PatchValidationError(f"file is too large for a single agent patch: {relative_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            applied.append(AppliedFile(relative_path, action, sha256_file(target), target.stat().st_size))

        if conflicts:
            return {
                "ok": False,
                "status": "conflict",
                "project_root": str(project_root),
                "changed_files": [item.path for item in applied],
                "conflicts": conflicts,
            }
        return {
            "ok": True,
            "status": "applied",
            "project_root": str(project_root),
            "changed_files": [item.path for item in applied],
            "files": [item.__dict__ for item in applied],
            "content_hash": sha256_bytes(str([item.__dict__ for item in applied]).encode("utf-8")),
        }

    def layout_roots(self, project_root: Path, layout: dict[str, Any]) -> list[str]:
        roots = []
        for key in ("source_root", "delivery_root"):
            value = str(layout.get(key) or "").strip()
            if value:
                roots.append(self._normalize_relative_path(value))
        for directory in layout.get("directories") or []:
            if isinstance(directory, dict):
                value = directory.get("path") or directory.get("name") or ""
            else:
                value = directory
            value = str(value or "").strip()
            if value:
                roots.append(self._normalize_relative_path(value))
        roots = list(dict.fromkeys(root.rstrip("/") for root in roots if root not in {"", "."}))
        if not roots:
            roots = ["."]
        for root in roots:
            candidate = (project_root / root).resolve()
            if not self._is_within(candidate, project_root):
                raise PatchValidationError(f"layout root escapes project: {root}")
            candidate.mkdir(parents=True, exist_ok=True)
        return roots

    def list_project_files(self, project: dict[str, Any], limit: int = 400) -> list[str]:
        project_root = self.project_root(project)
        if not project_root.exists():
            return []
        files: list[str] = []
        for path in sorted(project_root.rglob("*")):
            if path.is_dir() or any(part in self._FORBIDDEN_PARTS for part in path.relative_to(project_root).parts):
                continue
            files.append(str(path.relative_to(project_root)).replace("\\", "/"))
            if len(files) >= limit:
                break
        return files

    def reset_project_root(self, project: dict[str, Any]) -> None:
        project_root = self.project_root(project)
        if project_root.exists():
            shutil.rmtree(project_root)
        project_root.mkdir(parents=True, exist_ok=True)

    def _assert_allowed_relative(self, relative_path: str, allowed_paths: list[str], forbidden_paths: list[str], layout_roots: list[str]) -> None:
        parts = Path(relative_path).parts
        if any(part in self._FORBIDDEN_PARTS for part in parts):
            raise PatchValidationError(f"forbidden path part in {relative_path}")
        normalized = relative_path.replace("\\", "/").lstrip("/")
        normalized_allowed = [self._normalize_glob(pattern) for pattern in allowed_paths]
        normalized_forbidden = [self._normalize_glob(pattern) for pattern in forbidden_paths]
        if normalized_forbidden and any(fnmatch(normalized, pattern) for pattern in normalized_forbidden):
            raise PatchValidationError(f"path matches forbidden pattern: {relative_path}")
        if normalized_allowed and not any(fnmatch(normalized, pattern) for pattern in normalized_allowed):
            raise PatchValidationError(f"path outside allowed paths: {relative_path}")
        if layout_roots and "." not in layout_roots:
            if not any(normalized == root or normalized.startswith(root.rstrip("/") + "/") for root in layout_roots):
                raise PatchValidationError(f"path outside AI project layout roots: {relative_path}")

    def _normalize_relative_path(self, value: str) -> str:
        raw = str(value or "").replace("\\", "/").strip()
        if not raw:
            raise PatchValidationError("empty path")
        path = Path(raw)
        if path.is_absolute() or self._looks_like_windows_absolute_path(raw):
            raise PatchValidationError(f"absolute paths are not allowed: {value}")
        normalized = str(Path(raw)).replace("\\", "/")
        if normalized in {"", "."}:
            return "."
        if normalized.startswith("../") or "/../" in f"/{normalized}/":
            raise PatchValidationError(f"path traversal is not allowed: {value}")
        return normalized.lstrip("/")

    def _normalize_glob(self, value: str) -> str:
        raw = str(value or "").replace("\\", "/").lstrip("/")
        if not raw:
            return ""
        if raw.endswith("/"):
            raw += "**"
        return raw

    def _fallback_project_root(self, project: dict[str, Any]) -> Path:
        return (self.workspace_root / slugify(project.get("name") or project.get("title") or project["id"])).resolve()

    def _can_use_configured_project_path(self, configured: str) -> bool:
        candidate = Path(configured)
        if os.name == "nt":
            return candidate.is_absolute()
        return candidate.is_absolute() and not self._looks_like_windows_absolute_path(configured)

    def _looks_like_windows_absolute_path(self, configured: str) -> bool:
        return bool(self._WINDOWS_ABSOLUTE_PATH.match(configured))

    def _map_windows_workspace_path(self, configured: str) -> Path | None:
        normalized = str(configured or "").replace("\\", "/").strip()
        lowered = normalized.lower()
        markers = ("workspace/projects/projects/", "workspace/projects/", "workspace/")
        for marker in markers:
            index = lowered.find(marker)
            if index < 0:
                continue
            relative = normalized[index + len(marker) :].strip("/")
            if not relative:
                return self.workspace_root
            candidate = (self.workspace_root / Path(relative)).resolve()
            if self._is_within(candidate, self.workspace_root):
                return candidate
        return None

    def _is_within(self, candidate: Path, root: Path) -> bool:
        try:
            return candidate.resolve().is_relative_to(root.resolve())
        except AttributeError:  # pragma: no cover
            return str(candidate.resolve()).startswith(str(root.resolve()))
