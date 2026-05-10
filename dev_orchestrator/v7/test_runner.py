"""V7 Test Runner - async subprocess for validation commands."""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from dev_orchestrator.v7.observability import get_logger

log = get_logger(__name__)

SAFE_ENV_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE")
FORBIDDEN_PATTERNS = (r"\brm\s+-rf\b", r"\bgit\s+reset\s+--hard\b", r"\bdocker\s+system\s+prune\b", r"\bdel\s+/s\b", r"\bformat\b")


async def run_validation_commands(project_root: Path, commands: list[str], timeout_seconds: int = 120, output_limit: int = 20_000) -> dict[str, Any]:
    project_root = project_root.resolve()
    results: list[dict[str, Any]] = []
    env = {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_KEYS}

    for cmd in commands:
        cmd = str(cmd).strip()
        if not cmd:
            continue
        if not _command_safe(cmd):
            results.append({"command": cmd, "status": "blocked", "exit_code": None, "stdout": "", "stderr": "blocked: forbidden pattern", "elapsed_ms": 0})
            continue
        try:
            proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(project_root), env=env)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            results.append({
                "command": cmd, "status": "passed" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace")[-output_limit:],
                "stderr": stderr.decode("utf-8", errors="replace")[-output_limit:],
            })
        except asyncio.TimeoutError:
            results.append({"command": cmd, "status": "timeout", "exit_code": None, "stdout": "", "stderr": f"timeout after {timeout_seconds}s", "elapsed_ms": timeout_seconds * 1000})
        except OSError as exc:
            results.append({"command": cmd, "status": "error", "exit_code": None, "stdout": "", "stderr": str(exc), "elapsed_ms": 0})

    passed = sum(1 for r in results if r["status"] == "passed")
    return {"ok": all(r["status"] == "passed" for r in results), "passed": passed, "total": len(results), "results": results}


def _command_safe(cmd: str) -> bool:
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return False
    return True
