from __future__ import annotations

from typing import Any


def planner_removed_notice() -> dict[str, Any]:
    return {
        "schema_version": "5.0",
        "mode": "ai_agent_native",
        "message": "Static blueprint planning has been removed. package_planning is produced by planner_agent at runtime.",
    }
