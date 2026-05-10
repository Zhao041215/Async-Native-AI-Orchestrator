"""V8 Requirements Analysis Phase."""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v8.models import AITaskBudget
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.utils import compact_text, json_or_empty

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

    async def execute(
        self,
        job: dict[str, Any],
        run: dict[str, Any],
        project: dict[str, Any],
        *,
        tenant_id: str,
        heartbeat: Any | None = None,
    ) -> dict[str, Any]:
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
            task_kind="requirements",  # V8: canonical task_kind (contract registered)
            system_prompt=REQUIREMENTS_SYSTEM_PROMPT,
            user_payload={
                "project": {
                    "name": project.get("name", ""),
                    "title": project.get("title", ""),
                    "description": project.get("description", ""),
                },
                "requirements_text": compact_text(requirements_text, budget.max_input_chars),
            },
            budget=budget, store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat,
        )

        if not result.ok:
            log.warning("requirements_ai_call_failed", run_id=run["id"],
                        error=result.error, error_kind=result.error_kind)
            return {
                "status": "blocked",
                "error": result.error,
                "error_kind": result.error_kind,
                "retryable": result.retryable,
            }

        analysis = json_or_empty(result.raw_response)
        await self._artifacts.write(
            project["id"], run["id"], job["id"],
            "requirements", "requirements-analysis.json", analysis,
        )

        if str(analysis.get("status", "GO")).upper() == "NO_GO":
            log.info("requirements_no_go", run_id=run["id"])
            return {"status": "NO_GO", "requirements": analysis}

        log.info("requirements_completed", run_id=run["id"],
                 goals_count=len(analysis.get("goals", [])))
        return {"status": "ok", "requirements": analysis}

    def next_job(self, run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        if result.get("status") == "NO_GO":
            return None
        return {
            "job_type": "architecture_design",
            "role": "architect",
            "run_id": run["id"],
            "resume_key": f"run:{run['id']}:architecture_design",
            "payload": {},
        }

    @staticmethod
    def _build_budget(scale_profile: dict[str, Any]) -> AITaskBudget:
        return AITaskBudget(
            task_kind="requirements",
            max_input_chars=int(scale_profile.get("context_budget_chars", 36000)),
            max_output_tokens=8000,
            timeout_seconds=120,
            reasoning_effort="high",
            retry_attempts=int(scale_profile.get("ai_retry_attempts", 3)),
        )
