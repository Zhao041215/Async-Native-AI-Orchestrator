from __future__ import annotations

import shutil
import subprocess
import threading
import hashlib
from dataclasses import dataclass
from pathlib import Path

from dev_orchestrator.v2.models import slugify


class GitRuntimeError(RuntimeError):
    pass


@dataclass
class GitCommandResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def truncate_text(value: object, limit: int = 16000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[truncated]"


class GitRuntime:
    def __init__(self, project_root: Path, worktree_root: Path, timeout_seconds: int = 120) -> None:
        self.project_root = Path(project_root).resolve()
        self.worktree_root = Path(worktree_root).resolve()
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()

    def git(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> GitCommandResult:
        working_dir = Path(cwd or self.project_root).resolve()
        with self._lock:
            completed = subprocess.run(
                ["git", *args],
                cwd=working_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds or self.timeout_seconds,
            )
        result = GitCommandResult(
            code=completed.returncode,
            stdout=truncate_text(completed.stdout),
            stderr=truncate_text(completed.stderr),
        )
        if check and not result.ok:
            command = "git " + " ".join(args)
            raise GitRuntimeError(f"{command} failed with code {result.code}: {result.stderr or result.stdout}")
        return result

    def ensure_repository(self) -> dict:
        self.project_root.mkdir(parents=True, exist_ok=True)
        if not (self.project_root / ".git").exists():
            init_result = self.git(["init", "-b", "main"], check=False)
            if not init_result.ok:
                self.git(["init"])
                self.git(["branch", "-M", "main"], check=False)

        has_head = self.git(["rev-parse", "--verify", "HEAD"], check=False)
        if not has_head.ok:
            self.git(["add", "-A"])
            self.git(
                [
                    "-c",
                    "user.name=V2 Chief",
                    "-c",
                    "user.email=v2-chief@local",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "v2 baseline",
                ]
            )

        base_sha = self.git(["rev-parse", "HEAD"]).stdout.strip()
        dirty = self.git(["status", "--porcelain"], check=False).stdout.strip().splitlines()
        return {
            "ok": True,
            "base_sha": base_sha,
            "dirty_paths": dirty,
            "project_root": str(self.project_root),
        }

    def create_worktree(self, run_id: str, purpose: str, identifier: str, attempt: int, base_sha: str) -> dict:
        safe_identifier = slugify(identifier)[:10] or "worktree"
        safe_purpose = slugify(purpose)[:6] or "agent"
        digest = hashlib.sha1(f"{run_id}:{purpose}:{identifier}:{attempt}".encode("utf-8")).hexdigest()[:8]
        path = (self.worktree_root / run_id[:6] / f"{safe_purpose}-{safe_identifier}-{attempt}-{digest}").resolve()
        self._assert_inside(path, self.worktree_root)
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        branch = f"v2/{run_id[:6]}/{safe_purpose}-{digest}-{attempt}"
        existing = self.git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
        if existing.ok:
            branch = f"{branch}-{digest}"
        self.git(["worktree", "add", "-f", "-b", branch, str(path), base_sha])
        return {"path": str(path), "branch": branch, "base_sha": base_sha}

    def remove_worktree(self, path: Path) -> GitCommandResult:
        target = Path(path).resolve()
        self._assert_inside(target, self.worktree_root)
        result = self.git(["worktree", "remove", "--force", str(target)], check=False)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return result

    def mark_untracked_for_diff(self, worktree_path: Path) -> None:
        self.git(["add", "-N", "."], cwd=worktree_path, check=False)

    def _diff_pathspec(self) -> list[str]:
        return [
            ":(exclude).agent/v2/runs",
            ":(exclude)**/__pycache__/**",
            ":(exclude)**/*.pyc",
            ":(exclude).pytest_cache/**",
            ":(exclude)node_modules/**",
        ]

    def changed_files(self, worktree_path: Path, base_sha: str) -> list[str]:
        self.mark_untracked_for_diff(worktree_path)
        output = self.git(["diff", "--name-only", base_sha, "--", *self._diff_pathspec()], cwd=worktree_path).stdout
        return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]

    def diff_stat(self, worktree_path: Path, base_sha: str) -> str:
        self.mark_untracked_for_diff(worktree_path)
        return self.git(["diff", "--stat", base_sha, "--", *self._diff_pathspec()], cwd=worktree_path, check=False).stdout.strip()

    def write_patch(self, worktree_path: Path, base_sha: str, patch_path: Path) -> dict:
        self.mark_untracked_for_diff(worktree_path)
        diff = self.git(["diff", "--binary", base_sha, "--", *self._diff_pathspec()], cwd=worktree_path).stdout
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff, encoding="utf-8")
        files = self.changed_files(worktree_path, base_sha)
        return {
            "patch_path": str(patch_path),
            "files_changed": files,
            "diff_summary": self.diff_stat(worktree_path, base_sha),
            "empty": not bool(diff.strip()),
        }

    def apply_patch(self, worktree_path: Path, patch_path: Path, *, reverse: bool = False) -> dict:
        mode = ["-R"] if reverse else []
        selected_check = None
        selected_args = None
        for args in (
            ["apply", *mode, "--check", str(patch_path)],
            ["apply", *mode, "-p0", "--check", str(patch_path)],
        ):
            check = self.git(args, cwd=worktree_path, check=False)
            if check.ok:
                selected_check = check
                selected_args = args
                break
            selected_check = check
        if selected_args is None:
            return {
                "ok": False,
                "status": "conflict",
                "stdout": selected_check.stdout if selected_check else "",
                "stderr": selected_check.stderr if selected_check else "Patch check failed.",
            }
        apply_args = [item for item in selected_args if item != "--check"]
        if "--whitespace=nowarn" not in apply_args:
            apply_args.insert(1, "--whitespace=nowarn")
        applied = self.git(apply_args, cwd=worktree_path, check=False)
        return {
            "ok": applied.ok,
            "status": "applied" if applied.ok else "failed",
            "stdout": applied.stdout,
            "stderr": applied.stderr,
        }

    def commit_all(self, worktree_path: Path, message: str) -> str:
        self.git(["add", "-A"], cwd=worktree_path)
        self.git(
            [
                "-c",
                "user.name=V2 Chief",
                "-c",
                "user.email=v2-chief@local",
                "commit",
                "--allow-empty",
                "-m",
                message,
            ],
            cwd=worktree_path,
        )
        return self.git(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()

    def _assert_inside(self, target: Path, root: Path) -> None:
        target = target.resolve()
        root = root.resolve()
        if target != root and root not in target.parents:
            raise GitRuntimeError(f"Path escapes V2 worktree root: {target}")
