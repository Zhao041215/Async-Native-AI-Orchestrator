from __future__ import annotations

from copy import deepcopy
from typing import Any

from dev_orchestrator.v6.llm_policy import AITaskBudget, DEFAULT_PROVIDER_BODY_LIMIT_BYTES
from dev_orchestrator.v6.models import sha256_bytes, stable_json


def estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


def payload_budget_report(
    *,
    system_prompt: str,
    user_payload: dict[str, Any],
    task_budget: AITaskBudget,
    scale_profile: dict[str, Any],
    provider_body_limit_bytes: int | None = None,
    compression_applied: bool = False,
    summary_agent_run_id: str = "",
    trim_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_text = stable_json(user_payload)
    body_text = stable_json({"system": system_prompt, "messages": [{"role": "user", "content": prompt_text}]})
    profile_limit = int(scale_profile.get("context_budget_chars") or task_budget.max_input_chars)
    budget_limit = max(1024, min(int(task_budget.max_input_chars), profile_limit))
    body_limit = int(provider_body_limit_bytes or DEFAULT_PROVIDER_BODY_LIMIT_BYTES)
    prompt_chars = len(prompt_text)
    body_bytes = len(body_text.encode("utf-8"))
    over_context = prompt_chars > budget_limit
    over_body = body_bytes > body_limit
    status = "within_budget"
    if over_context and over_body:
        status = "over_context_and_body_budget"
    elif over_context:
        status = "over_context_budget"
    elif over_body:
        status = "over_body_budget"
    return {
        "schema_version": "6.1",
        "ok": not over_context and not over_body,
        "budget_status": status,
        "payload_chars": prompt_chars,
        "payload_bytes": len(prompt_text.encode("utf-8")),
        "body_bytes": body_bytes,
        "estimated_tokens": estimate_tokens(prompt_text),
        "budget_limit": budget_limit,
        "task_budget_limit": int(task_budget.max_input_chars),
        "scale_context_budget_chars": profile_limit,
        "provider_body_limit_bytes": body_limit,
        "compression_applied": bool(compression_applied),
        "summary_agent_run_id": summary_agent_run_id,
        "payload_hash": sha256_bytes(prompt_text.encode("utf-8")),
        "trim_report": trim_report or {},
    }


def compact_payload_for_budget(payload: dict[str, Any], *, target_chars: int) -> tuple[dict[str, Any], dict[str, Any]]:
    target = max(1024, int(target_chars))
    original_text = stable_json(payload)
    dropped: list[dict[str, Any]] = []
    selected_layers: list[str] = []
    compacted: Any = deepcopy(payload)
    string_limit = 2000
    list_limit = 40
    for _ in range(6):
        dropped.clear()
        selected_layers.clear()
        compacted = _compact_value(
            payload,
            path="$",
            depth=0,
            string_limit=string_limit,
            list_limit=list_limit,
            dropped=dropped,
            selected_layers=selected_layers,
        )
        if len(stable_json(compacted)) <= target:
            break
        string_limit = max(280, int(string_limit * 0.55))
        list_limit = max(4, int(list_limit * 0.55))
    compacted_text = stable_json(compacted)
    return dict(compacted if isinstance(compacted, dict) else {"value": compacted}), {
        "schema_version": "6.1",
        "strategy": "deterministic_relevance_preserving_trim",
        "before_chars": len(original_text),
        "after_chars": len(compacted_text),
        "target_chars": target,
        "selected_layers": sorted(set(selected_layers))[:80],
        "dropped_count": len(dropped),
        "dropped": dropped[:120],
        "payload_hash_before": sha256_bytes(original_text.encode("utf-8")),
        "payload_hash_after": sha256_bytes(compacted_text.encode("utf-8")),
    }


def payload_outline(payload: dict[str, Any]) -> dict[str, Any]:
    return _outline(payload, depth=0)


def _compact_value(
    value: Any,
    *,
    path: str,
    depth: int,
    string_limit: int,
    list_limit: int,
    dropped: list[dict[str, Any]],
    selected_layers: list[str],
) -> Any:
    if isinstance(value, str):
        selected_layers.append(path)
        if len(value) <= string_limit:
            return value
        head = value[: int(string_limit * 0.75)].rstrip()
        tail = value[-int(string_limit * 0.15) :].lstrip()
        dropped.append({"path": path, "reason": "string_truncated", "original_chars": len(value), "kept_chars": len(head) + len(tail)})
        return f"{head}\n\n[...trimmed for AI request budget...]\n\n{tail}"
    if isinstance(value, list):
        keep = max(2, list_limit - depth * 4)
        selected_layers.append(path)
        items = value[:keep]
        if len(value) > keep:
            dropped.append({"path": path, "reason": "list_truncated", "original_items": len(value), "kept_items": len(items)})
        return [
            _compact_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
                string_limit=string_limit,
                list_limit=list_limit,
                dropped=dropped,
                selected_layers=selected_layers,
            )
            for index, item in enumerate(items)
        ]
    if isinstance(value, dict):
        selected_layers.append(path)
        compacted: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str):
            item = value[key]
            child_path = f"{path}.{key}"
            compacted[str(key)] = _compact_value(
                item,
                path=child_path,
                depth=depth + 1,
                string_limit=string_limit,
                list_limit=list_limit,
                dropped=dropped,
                selected_layers=selected_layers,
            )
        return compacted
    return value


def _outline(value: Any, *, depth: int) -> Any:
    if depth >= 4:
        if isinstance(value, dict):
            return {"type": "object", "keys": sorted(str(key) for key in value.keys())[:30]}
        if isinstance(value, list):
            return {"type": "array", "items": len(value)}
        if isinstance(value, str):
            return {"type": "string", "chars": len(value)}
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        return {str(key): _outline(item, depth=depth + 1) for key, item in list(value.items())[:60]}
    if isinstance(value, list):
        return {"type": "array", "items": len(value), "sample": [_outline(item, depth=depth + 1) for item in value[:6]]}
    if isinstance(value, str):
        return {"type": "string", "chars": len(value), "preview": value[:120]}
    return value
