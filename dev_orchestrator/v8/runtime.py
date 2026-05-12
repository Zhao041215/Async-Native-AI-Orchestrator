"""Async file manifest runtime for the V7 orchestrator.

Applies AI-generated file patches (create / replace / delete) to a project
directory with strict path validation and sandboxing.
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
from pathlib import Path

from dev_orchestrator.v8.models import FORBIDDEN_RELEASE_NAMES, new_id, sha256_bytes
from dev_orchestrator.v8.observability import get_logger

log = get_logger(__name__)

# Directories that are never touched by the runtime.
_BLOCKED_DIR_NAMES: frozenset[str] = frozenset({".git", ".v6", "__pycache__"})

# Patterns that indicate dangerous shell operations.
_DANGEROUS_PATTERNS: list[str] = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs.",
    "dd if=",
    "> /",
    ">> /",
]


class FileRuntime:
    """Applies file manifests and runs sandboxed shell commands."""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()

    def for_run(self, project_id: str, run_id: str) -> "FileRuntime":
        """Return a new FileRuntime scoped to a specific project/run directory.

        Files are written to <base>/<project_id>/<run_id>/ so that each run
        has its own isolated workspace and projects never overwrite each other.
        """
        scoped_root = self._root / project_id / run_id
        scoped_root.mkdir(parents=True, exist_ok=True)
        return FileRuntime(scoped_root)

    def project_root(self, project: dict | None = None) -> Path:
        """Return the project root directory."""
        if project and project.get("project_path"):
            return Path(project["project_path"]).resolve()
        return self._root

    def layout_roots(self, root: Path, layout: dict) -> None:
        """Create project directory structure from a layout spec."""
        for d in layout.get("directories") or []:
            # Handle both string paths and dict entries {"path": "...", "purpose": "..."}
            if isinstance(d, dict):
                path_str = d.get("path", "")
            else:
                path_str = str(d)
            if path_str:
                target = root / path_str
                target.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def _validate_path(
        self,
        path_str: str,
        allowed_paths: list[str],
        forbidden_paths: list[str],
    ) -> str | None:
        """Return an error message if *path_str* is invalid, else ``None``."""
        if not path_str:
            return "empty path"

        # Reject absolute paths.
        if os.path.isabs(path_str):
            return f"absolute path not allowed: {path_str}"

        # Reject path traversal.
        normalised = os.path.normpath(path_str)
        if ".." in normalised.split(os.sep):
            return f"path traversal not allowed: {path_str}"

        # Reject blocked directory names anywhere in the path.
        parts = Path(normalised).parts
        for part in parts:
            if part in _BLOCKED_DIR_NAMES:
                return f"blocked directory name '{part}' in path: {path_str}"
            if part in FORBIDDEN_RELEASE_NAMES:
                return f"forbidden release name '{part}' in path: {path_str}"

        # Must match at least one allowed_paths pattern (if any are given).
        if allowed_paths:
            matched = any(
                fnmatch.fnmatch(normalised, pat) for pat in allowed_paths
            )
            if not matched:
                return f"path does not match any allowed pattern: {path_str}"

        # Must not match any forbidden_paths pattern.
        for pat in forbidden_paths:
            if fnmatch.fnmatch(normalised, pat):
                return f"path matches forbidden pattern '{pat}': {path_str}"

        # Target must resolve inside project root.
        target = (self._root / normalised).resolve()
        try:
            target.relative_to(self._root)
        except ValueError:
            return f"resolved path escapes project root: {path_str}"

        return None

    # ------------------------------------------------------------------
    # File manifest application
    # ------------------------------------------------------------------

    async def apply_file_manifest(
        self,
        files: list[dict],
        allowed_paths: list[str],
        forbidden_paths: list[str],
    ) -> dict:
        """Apply a list of file operations described by dicts.

        Each dict must contain ``path``, ``action`` (create|replace|delete),
        and optionally ``content`` (required for create/replace).

        Returns ``{"applied": int, "errors": list[str], "files": list[str]}``.
        """
        applied = 0
        errors: list[str] = []
        touched: list[str] = []

        for entry in files:
            path_str: str = entry.get("path", "")
            action: str = entry.get("action", "")
            content: str = entry.get("content", "")

            # Validate action.  "upsert" is an alias for idempotent create-or-overwrite.
            if action not in ("create", "replace", "delete", "upsert"):
                errors.append(f"unknown action '{action}' for {path_str}")
                continue

            # Validate path.
            err = self._validate_path(path_str, allowed_paths, forbidden_paths)
            if err:
                errors.append(err)
                continue

            target = (self._root / os.path.normpath(path_str)).resolve()

            try:
                if action == "delete":
                    if target.is_file():
                        target.unlink()
                        applied += 1
                        touched.append(path_str)
                    elif target.exists():
                        errors.append(f"cannot delete non-file: {path_str}")
                    else:
                        errors.append(f"file not found for delete: {path_str}")

                elif action == "replace":
                    if not target.exists():
                        errors.append(f"file not found for replace: {path_str}")
                        continue
                    target.write_text(content, encoding="utf-8")
                    applied += 1
                    touched.append(path_str)

                elif action in ("create", "upsert"):
                    # Idempotent: create if absent, overwrite if present.
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    applied += 1
                    touched.append(path_str)

            except OSError as exc:
                errors.append(f"I/O error for {path_str}: {exc}")

        log.info(
            "file_manifest_applied",
            applied=applied,
            errors=len(errors),
            files_count=len(touched),
        )
        return {"applied": applied, "errors": errors, "files": touched}

    # ------------------------------------------------------------------
    # Shell command execution
    # ------------------------------------------------------------------

    async def apply_commands(
        self,
        commands: list[str],
        cwd: Path | None = None,
        timeout: int = 120,
    ) -> dict:
        """Run shell commands sequentially inside the project sandbox.

        Returns ``{"results": list[{command, returncode, stdout, stderr}]}``.
        """
        work_dir = cwd or self._root
        results: list[dict] = []

        for cmd in commands:
            # Block obviously dangerous commands.
            lower = cmd.lower().strip()
            if any(pat in lower for pat in _DANGEROUS_PATTERNS):
                log.warning("blocked_dangerous_command", command=cmd)
                results.append(
                    {
                        "command": cmd,
                        "returncode": -1,
                        "stdout": "",
                        "stderr": "blocked: dangerous command detected",
                    }
                )
                continue

            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(work_dir),
                )
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                results.append(
                    {
                        "command": cmd,
                        "returncode": proc.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
            except NotImplementedError:
                # SelectorEventLoop on Windows does not support subprocesses.
                log.warning("subprocess_not_supported_on_platform", command=cmd)
                results.append(
                    {
                        "command": cmd,
                        "returncode": -1,
                        "stdout": "",
                        "stderr": "subprocess_not_supported_on_this_platform",
                    }
                )
                continue
            except asyncio.TimeoutError:
                log.warning("command_timeout", command=cmd, timeout=timeout)
                results.append(
                    {
                        "command": cmd,
                        "returncode": -1,
                        "stdout": "",
                        "stderr": f"timed out after {timeout}s",
                    }
                )
                # Ensure the timed-out process is cleaned up.
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            except OSError as exc:
                log.error("command_exec_error", command=cmd, error=str(exc))
                results.append(
                    {
                        "command": cmd,
                        "returncode": -1,
                        "stdout": "",
                        "stderr": str(exc),
                    }
                )

        log.info("commands_applied", total=len(commands), errors=sum(
            1 for r in results if r["returncode"] != 0
        ))
        return {"results": results}
