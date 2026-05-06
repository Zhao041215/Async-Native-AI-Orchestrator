from __future__ import annotations

import json

from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient


def parse_contract_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {"ok": True, "payload": parsed}
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict):
                return {"ok": True, "payload": parsed}
        except json.JSONDecodeError:
            pass
    return {"ok": False, "payload": {}}


def run_llm_contract_check(client: OpenAICompatibleClient) -> dict:
    system_prompt = (
        "Reply strictly as JSON with keys done, summary, artifacts, tool_calls. "
        "Do not include markdown fences or commentary."
    )
    messages = [
        {
            "role": "user",
            "content": (
                "Return a JSON object with done=true, summary='contract-check', "
                "artifacts=['docs/contract-check.md'], tool_calls=[]."
            ),
        }
    ]
    try:
        raw = client.chat(system_prompt, messages)
    except LLMError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "raw_preview": "",
            "json_parse_ok": False,
            "contract_shape_ok": False,
        }

    parsed = parse_contract_json(raw)
    payload = parsed.get("payload", {})
    contract_shape_ok = (
        isinstance(payload.get("done"), bool)
        and isinstance(payload.get("summary"), str)
        and isinstance(payload.get("artifacts"), list)
        and isinstance(payload.get("tool_calls"), list)
    )
    return {
        "ok": parsed.get("ok", False) and contract_shape_ok,
        "error": "" if parsed.get("ok", False) and contract_shape_ok else "Model output did not satisfy orchestrator JSON contract.",
        "raw_preview": raw[:1200],
        "json_parse_ok": parsed.get("ok", False),
        "contract_shape_ok": contract_shape_ok,
        "payload_preview": payload if parsed.get("ok", False) else {},
    }
