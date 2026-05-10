"""V8 Integration & Code Review Phase."""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v8.models import AITaskBudget
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.utils import json_or_empty

log = get_logger(__name__)

INTEGRATION_PROMPT = "You are integration_agent. Return strict JSON only. Review merged project state. Return ok, summary, issues (list of {severity, file, line, message}), suggestions."
CODE_REVIEW_PROMPT = "You are code_review_agent. Return strict JSON only. Return ok, summary, issues (list of {severity, file, line, message}), suggestions."


class IntegrationPhase:
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
        metadata = self._get_metadata(run)
        scale_profile = metadata.get("scale_profile") or {}
        budget = self._build_budget(scale_profile)

        is_review = job.get("job_type") == "code_review"
        system_prompt = CODE_REVIEW_PROMPT if is_review else INTEGRATION_PROMPT
        task_kind = "code_review" if is_review else "integration"
        role = "review" if is_review else "integration"

        packages = await self._store.list_work_packages(run["id"])
        completed = [p for p in packages if p.get("status") == "completed"]

        if not packages:
            log.info("integration_no_packages", run_id=run["id"], is_review=is_review)
            return {
                "status": "ok", "ok": True,
                "output": {"ok": True, "summary": "No packages to review" if is_review else "No packages to integrate"},
                "critical_issues": [],
            }

        result = await self._scheduler.call(
            run_id=run["id"], role=role, job_id=job["id"],
            task_kind=task_kind, system_prompt=system_prompt,
            user_payload={
                "project": {"name": project.get("name", "")},
                "requirements": metadata.get("requirements") or metadata.get("requirements_analysis") or {},
                "architecture": metadata.get("architecture") or metadata.get("architecture_design") or {},
                "package_summaries": [
                    {"package_key": p.get("package_key", ""), "role": p.get("role", "")}
                    for p in completed
                ],
            },
            budget=budget, store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat,
        )

        if not result.ok:
            return {
                "status": "blocked",
                "error": result.error,
                "error_kind": result.error_kind,
                "retryable": result.retryable,
            }

        output = json_or_empty(result.raw_response)
        await self._artifacts.write(
            project["id"], run["id"], job["id"],
            f"{task_kind}_report", f"{task_kind}.json", output,
        )
        critical = [
            i for i in (output.get("issues") or [])
            if isinstance(i, dict) and i.get("severity") == "critical"
        ]
        ok = len(critical) == 0
        return {
            "status": "ok" if ok else "blocked",
            "ok": ok,
            "output": output,
            "critical_issues": critical,
        }

    def next_job(self, run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        if result.get("status") != "ok":
            return None
        return {
            "job_type": "quality",
            "role": "qa",
            "run_id": run["id"],
            "resume_key": f"run:{run['id']}:quality",
            "payload": {},
        }

    @staticmethod
    def _get_metadata(run: Any) -> dict[str, Any]:
        if hasattr(run, "metadata"):
            m = run.metadata
            return m.model_dump() if hasattr(m, "model_dump") else dict(m)
        return dict(run.get("metadata") or {})

    @staticmethod
    def _build_budget(sp: dict[str, Any]) -> AITaskBudget:
        return AITaskBudget(
            task_kind="integration",
            max_input_chars=int(sp.get("context_budget_chars", 36000)),
            max_output_tokens=8000,
            timeout_seconds=120,
            reasoning_effort="high",
            retry_attempts=int(sp.get("ai_retry_attempts", 3)),
        )
