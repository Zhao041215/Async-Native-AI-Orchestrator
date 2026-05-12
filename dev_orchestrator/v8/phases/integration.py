"""V8 Integration & Code Review Phase."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dev_orchestrator.v8.models import AITaskBudget
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.skill_loader import load_skills_for_role
from dev_orchestrator.v8.utils import json_or_empty

log = get_logger(__name__)

INTEGRATION_PROMPT = (
    "You are integration_agent. Return strict JSON only. "
    "Review the merged project state including actual generated files. "
    "Return ok, summary, issues (list of {severity, file, line, message}), suggestions, "
    "integration_conflicts (list of {file, packages, description})."
)
CODE_REVIEW_PROMPT = (
    "You are code_review_agent. Return strict JSON only. "
    "Review the generated code for correctness, security, and consistency. "
    "Return ok, summary, issues (list of {severity, file, line, message}), suggestions."
)


def _with_skill(role: str, base: str) -> str:
    skill = load_skills_for_role(role)
    return f"{skill}\n\n{base}" if skill else base


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
        base_prompt = CODE_REVIEW_PROMPT if is_review else INTEGRATION_PROMPT
        system_prompt = _with_skill("review", base_prompt)
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

        # Read actual generated file content from patch_set artifacts
        file_samples = await self._collect_file_samples(run["id"], budget.max_input_chars // 3)

        result = await self._scheduler.call(
            run_id=run["id"], role=role, job_id=job["id"],
            task_kind=task_kind, system_prompt=system_prompt,
            user_payload={
                "project": {"name": project.get("name", "")},
                "requirements": metadata.get("requirements") or metadata.get("requirements_analysis") or {},
                "architecture": metadata.get("architecture") or metadata.get("architecture_design") or {},
                "package_summaries": [
                    {
                        "package_key": p.get("package_key", ""),
                        "role": p.get("role", ""),
                        "domain": p.get("domain", ""),
                        "allowed_paths": p.get("allowed_paths") or [],
                    }
                    for p in completed
                ],
                "generated_files": file_samples,
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

    async def _collect_file_samples(self, run_id: str, max_chars: int) -> list[dict[str, Any]]:
        """Extract file path + first 500 chars from patch_set artifact content (PG-safe)."""
        samples: list[dict[str, Any]] = []
        total = 0
        try:
            artifacts = await self._store.list_artifacts(run_id, kind="patch_set")
            for a in artifacts:
                if total >= max_chars:
                    break
                # PG store: content is the JSON blob; filesystem store: fall back to path
                raw = a.get("content") or ""
                if not raw:
                    path_str = a.get("path", "")
                    if path_str:
                        try:
                            raw = Path(path_str).read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            continue
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    for f in (parsed.get("files") or []):
                        if total >= max_chars:
                            break
                        file_path = f.get("path", "")
                        file_content = str(f.get("content", ""))[:500]
                        samples.append({"path": file_path, "preview": file_content})
                        total += len(file_content) + len(file_path)
                except Exception:
                    continue
        except Exception:
            pass
        return samples

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
            timeout_seconds=180,
            reasoning_effort="high",
            retry_attempts=int(sp.get("ai_retry_attempts", 3)),
        )
