"""V7 Requirements Analysis Phase."""
from __future__ import annotations

import json
from typing import Any

from dev_orchestrator.v7.models import AITaskBudget
from dev_orchestrator.v7.observability import get_logger

log = get_logger(__name__)

REQUIREMENTS_SYSTEM_PROMPT = (
    "You are requirements_agent in a multi-agent delivery system. Return strict JSON only. "
    "Analyze the user's project request. Do not write code. Return status GO or NO_GO, "
    "summary, goals, users, constraints, acceptance_criteria, missing_information, risks, and expected_terms."
)


class RequirementsPhase:
    def __init__(self, *, scheduler: Any, store: Any, artifacts: Any) -> None:
        self._scheduler = scheduler
        self._store = store
        self._artifacts = artifacts

    async def execute(self, job: dict[str, Any], run: dict[str, Any], project: dict[str, Any], *, tenant_id: str, heartbeat: Any | None = None) -> dict[str, Any]:
        metadata = dict(run.get("metadata") or {})
        requirements_text = (
            (job.get("payload") or {}).get("requirements_text")
            or metadata.get("requirements_text")
            or project.get("description", "")
        )
        scale_profile = metadata.get("scale_profile") or {}
        budget = self._build_budget(scale_profile)

        result = await self._scheduler.call(
            run_id=run["id"], role="requirements", job_id=job["id"],
            task_kind="requirements_analysis", system_prompt=REQUIREMENTS_SYSTEM_PROMPT,
            user_payload={
                "project": {"name": project.get("name", ""), "title": project.get("title", ""), "description": project.get("description", "")},
                "requirements_text": _compact_text(requirements_text, budget.max_input_chars),
            },
            budget=budget, store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat,
        )

        if not result.ok:
            log.warning("requirements_ai_call_failed", run_id=run["id"], error=result.error, error_kind=result.error_kind)
            return {"status": "blocked", "error": result.error, "error_kind": result.error_kind, "retryable": result.retryable}

        analysis = _json_or_empty(result.raw_response)
        await self._artifacts.write(project["id"], run["id"], job["id"], "requirements_analysis", "requirements-analysis.json", analysis)

        if str(analysis.get("status", "GO")).upper() == "NO_GO":
            log.info("requirements_no_go", run_id=run["id"])
            return {"status": "NO_GO", "requirements_analysis": analysis}

        log.info("requirements_completed", run_id=run["id"], goals_count=len(analysis.get("goals", [])))
        return {"status": "ok", "requirements_analysis": analysis}

    def next_job(self, run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        if result.get("status") == "NO_GO":
            return None
        return {"job_type": "architecture_design", "role": "architect", "run_id": run["id"], "resume_key": f"run:{run['id']}:architecture_design", "payload": {}}

    @staticmethod
    def _build_budget(scale_profile: dict[str, Any]) -> AITaskBudget:
        return AITaskBudget(
            task_kind="requirements_analysis",
            max_input_chars=int(scale_profile.get("context_budget_chars", 36000)),
            max_output_tokens=8000, timeout_seconds=120,
            reasoning_effort="high", retry_attempts=int(scale_profile.get("ai_retry_attempts", 3)),
        )


def _compact_text(value: str, limit: int = 9000) -> str:
    normalized = "\n".join(line.rstrip() for line in str(value or "").splitlines())
    if len(normalized) <= limit:
        return normalized
    head = normalized[: int(limit * 0.72)].rstrip()
    tail = normalized[-int(limit * 0.18):].lstrip()
    return f"{head}\n\n[...truncated for bounded AI agent task...]\n\n{tail}"


def _json_or_empty(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_text": text}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
