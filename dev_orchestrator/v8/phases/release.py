"""V8 Release Phase — release notes and release candidate."""
from __future__ import annotations

import json
from typing import Any

from dev_orchestrator.v8.models import AITaskBudget
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.utils import json_or_empty

log = get_logger(__name__)

RELEASE_NOTES_PROMPT = (
    "You are release_agent. Return strict JSON only. No markdown outside JSON. "
    "Based on the project architecture, technology stack, and completed packages, produce a detailed release document. "
    "Return: {"
    "\"summary\": str, "
    "\"deploy_steps\": [str] (SPECIFIC shell commands with actual paths, ports, env var names — not generic descriptions), "
    "\"validation_steps\": [str] (SPECIFIC curl/test commands to verify the deployment), "
    "\"rollback_plan\": str, "
    "\"env_vars\": [{\"name\": str, \"description\": str, \"example\": str, \"required\": bool}], "
    "\"ports\": [{\"port\": int, \"service\": str, \"protocol\": \"http|https|tcp\"}], "
    "\"startup_commands\": [str] (ordered list of exact shell commands to start the project from scratch)"
    "}. "
    "Use the actual technology stack from the project — do not use generic placeholders."
)
RELEASE_CANDIDATE_PROMPT = "You are release_candidate_agent. Return strict JSON only. Return ok, summary, checklist (list of {item, status, notes})."


class ReleasePhase:
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

        is_candidate = job.get("job_type") == "release_candidate"
        system_prompt = RELEASE_CANDIDATE_PROMPT if is_candidate else RELEASE_NOTES_PROMPT
        task_kind = "release_candidate" if is_candidate else "release_notes"

        packages = await self._store.list_work_packages(run["id"])
        artifacts = await self._store.list_artifacts(run["id"])
        quality_report = _latest_payload(artifacts, "quality_report")

        result = await self._scheduler.call(
            run_id=run["id"], role="release", job_id=job["id"],
            task_kind=task_kind, system_prompt=system_prompt,
            user_payload={
                "project": {"name": project.get("name", ""), "title": project.get("title", "")},
                "requirements": metadata.get("requirements") or metadata.get("requirements_analysis") or {},
                "architecture": metadata.get("architecture") or metadata.get("architecture_design") or {},
                "quality_report": quality_report,
                "package_count": len(packages),
                "completed": len([p for p in packages if p.get("status") == "completed"]),
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
            task_kind, f"{task_kind}.json", output,
        )
        log.info(f"{task_kind}_completed", run_id=run["id"])
        return {"status": "ok", "output": output}

    @staticmethod
    def _get_metadata(run: Any) -> dict[str, Any]:
        if hasattr(run, "metadata"):
            m = run.metadata
            return m.model_dump() if hasattr(m, "model_dump") else dict(m)
        return dict(run.get("metadata") or {})

    @staticmethod
    def _build_budget(sp: dict[str, Any]) -> AITaskBudget:
        return AITaskBudget(
            task_kind="release_notes",
            max_input_chars=int(sp.get("context_budget_chars", 36000)),
            max_output_tokens=4000,
            timeout_seconds=120,
            reasoning_effort="high",
            retry_attempts=2,
        )


def _latest_payload(artifacts: list[Any], kind: str) -> dict[str, Any]:
    for a in reversed(artifacts):
        a_dict = a if isinstance(a, dict) else (a.model_dump() if hasattr(a, "model_dump") else {})
        if a_dict.get("kind") == kind:
            content = a_dict.get("content", "")
            if isinstance(content, str) and content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(content, dict):
                return content
    return {}
