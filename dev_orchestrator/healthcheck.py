from __future__ import annotations

from pathlib import Path
from typing import Any

from dev_orchestrator.v4.system_check import build_v4_system_check


def build_system_check(root_dir: Path, config: dict[str, Any], v2_storage: object | None = None) -> dict[str, Any]:
    return build_v4_system_check(root_dir, config, strict_db=False)


def build_acceptance_check(results: list[dict]) -> dict:
    failures = [item for item in results if not item.get("ok", False)]
    return {
        "ok": len(failures) == 0,
        "scenario_count": len(results),
        "failed_count": len(failures),
        "results": results,
    }

