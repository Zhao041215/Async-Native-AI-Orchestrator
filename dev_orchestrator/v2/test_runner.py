from __future__ import annotations

import subprocess
from pathlib import Path

from dev_orchestrator.v2.git_runtime import truncate_text


def detect_test_commands(project_root: Path) -> list[dict]:
    root = Path(project_root)
    commands: list[dict] = []
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or (root / "tests").exists():
        if (root / "tests").exists():
            commands.append({"command": "python -m unittest discover -s tests", "cwd": ".", "kind": "python-unittest"})
        else:
            commands.append({"command": "python -m unittest discover", "cwd": ".", "kind": "python-unittest"})
    if (root / "package.json").exists():
        commands.append({"command": "npm test -- --runInBand", "cwd": ".", "kind": "node"})
    return commands


class TestRunner:
    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, project_root: Path, *, repair_round: int = 0) -> list[dict]:
        commands = detect_test_commands(project_root)
        if not commands:
            return [
                {
                    "id": "",
                    "command": "",
                    "cwd": ".",
                    "kind": "none",
                    "status": "failed",
                    "exit_code": 127,
                    "stdout": "",
                    "stderr": "No test command detected.",
                    "repair_round": repair_round,
                }
            ]
        results: list[dict] = []
        for command in commands:
            completed = subprocess.run(
                command["command"],
                cwd=Path(project_root) / command.get("cwd", "."),
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            results.append(
                {
                    "id": "",
                    "command": command["command"],
                    "cwd": command.get("cwd", "."),
                    "kind": command.get("kind", "custom"),
                    "status": "passed" if completed.returncode == 0 else "failed",
                    "exit_code": completed.returncode,
                    "stdout": truncate_text(completed.stdout, 20000),
                    "stderr": truncate_text(completed.stderr, 12000),
                    "repair_round": repair_round,
                }
            )
        return results
