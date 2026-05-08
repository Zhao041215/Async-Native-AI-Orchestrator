from __future__ import annotations

from typing import Any


def list_stack_packs() -> list[dict[str, Any]]:
    return []


def stack_pack_policy() -> dict[str, Any]:
    return {
        "schema_version": "6.0",
        "mode": "ai_agent_native",
        "policy": "Technology hints may be passed to architect_agent, but stack packs do not generate directories or code.",
    }
