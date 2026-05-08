from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


SAFE_ENV_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE")
FORBIDDEN_COMMAND_PATTERNS = (
    r"\brm\s+-rf\b",
    r"\bgit\s+reset\b",
    r"\bgit\s+checkout\b",
    r"\bdocker\s+system\s+prune\b",
    r"\bdel\s+/s\b",
    r"\brmdir\s+/s\b",
    r"\bformat\b",
)


def run_validation_commands(project_root: Path, commands: list[str], timeout_seconds: int = 120, output_limit: int = 20_000) -> dict[str, Any]:
    project_root = project_root.resolve()
    results = []
    safety_failures = []
    for command in [str(item).strip() for item in commands if str(item).strip()]:
        safety = command_safety(command)
        if not safety["ok"]:
            safety_failures.append({"command": command, "reason": safety["reason"]})
            results.append({"command": command, "status": "blocked", "exit_code": None, "stdout": "", "stderr": safety["reason"], "elapsed_ms": 0})
            continue
        started = time.time()
        try:
            completed = subprocess.run(
                command,
                cwd=str(project_root),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env={key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV_KEYS},
            )
            elapsed_ms = round((time.time() - started) * 1000)
            results.append(
                {
                    "command": command,
                    "status": "passed" if completed.returncode == 0 else "failed",
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[-output_limit:],
                    "stderr": completed.stderr[-output_limit:],
                    "elapsed_ms": elapsed_ms,
                }
            )
        except subprocess.TimeoutExpired as exc:
            results.append({"command": command, "status": "timeout", "exit_code": None, "stdout": (exc.stdout or "")[-output_limit:] if isinstance(exc.stdout, str) else "", "stderr": (exc.stderr or "")[-output_limit:] if isinstance(exc.stderr, str) else "", "elapsed_ms": round((time.time() - started) * 1000)})
    executed = [item for item in results if item["status"] not in {"blocked"}]
    ok = bool(commands) and not safety_failures and all(item["status"] == "passed" for item in executed) and len(executed) == len(commands)
    return {
        "schema_version": "5.0",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "command_count": len(commands),
        "executed_count": len(executed),
        "safety_failures": safety_failures,
        "results": results,
    }


def command_safety(command: str) -> dict[str, Any]:
    lowered = command.lower()
    for pattern in FORBIDDEN_COMMAND_PATTERNS:
        if re.search(pattern, lowered):
            return {"ok": False, "reason": f"forbidden command pattern: {pattern}"}
    if ".." in command.replace("\\", "/"):
        return {"ok": False, "reason": "path traversal marker is not allowed in validation command"}
    if re.search(r"(^|[\s'\"=])[A-Za-z]:[\\/]", command) or re.search(r"(^|[\s'\"=])\\\\", command):
        return {"ok": False, "reason": "absolute paths are not allowed in validation command"}
    if re.search(r"(^|[\s'\"=])/[A-Za-z0-9_.\-/]+", command):
        return {"ok": False, "reason": "absolute paths are not allowed in validation command"}
    if ">" in command or "<" in command:
        return {"ok": False, "reason": "shell redirection is not allowed in validation command"}
    return {"ok": True, "reason": ""}
