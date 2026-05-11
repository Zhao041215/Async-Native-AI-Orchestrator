"""V8 shared utilities — eliminates duplication across phase handlers."""
from __future__ import annotations

import json
from typing import Any


def json_or_empty(raw: Any) -> dict[str, Any]:
    """Parse JSON string to dict; strips markdown code fences if present."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    text = raw.strip()
    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner = lines[1:] if len(lines) > 1 else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"raw_text": raw}
    except (json.JSONDecodeError, ValueError):
        # Try to find first { ... } block as fallback
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
                return parsed if isinstance(parsed, dict) else {"raw_text": raw}
            except (json.JSONDecodeError, ValueError):
                pass
        return {"raw_text": raw}


def project_summary(project: dict[str, Any]) -> dict[str, Any]:
    """Trim a project dict to the minimal fields needed in AI payloads."""
    return {
        "name": project.get("name", ""),
        "title": project.get("title", ""),
        "description": project.get("description", ""),
    }


def compact_text(value: str, limit: int) -> str:
    """Truncate text to limit, appending an ellipsis marker if cut."""
    if len(value) <= limit:
        return value
    return value[:limit] + f"... [truncated from {len(value)} chars]"


def canonical_metadata_key(key: str) -> str:
    """Normalise legacy V7 metadata key aliases to V8 canonical names."""
    _ALIASES = {
        "requirements_analysis": "requirements",
        "architecture_design": "architecture",
        "package_dag": "package_plan",
    }
    return _ALIASES.get(key, key)


def get_metadata_field(metadata: dict[str, Any], *keys: str) -> Any:
    """Read from metadata trying each key in order; returns first truthy hit."""
    for key in keys:
        val = metadata.get(key)
        if val:
            return val
    return None
