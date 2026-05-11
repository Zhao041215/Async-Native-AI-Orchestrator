"""V8 Implementation Phase — code generation for work packages.

Bug 2 fix: layered_memory and cross_package_context are now included in
user_payload so the AI actually receives the full mission context.
"""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v8.memory import build_layered_memory, memory_for_package
from dev_orchestrator.v8.models import AITaskBudget
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.skill_loader import load_skills_for_role
from dev_orchestrator.v8.utils import json_or_empty

log = get_logger(__name__)

CODE_GEN_PROMPT = (
    "You are {role}_agent. Return strict JSON only. "
    "Be VERY CONCISE. Generate minimal, working code. "
    "Return: {{\"agent\": str, \"status\": \"ok\", \"summary\": str, "
    "\"files\": [{{\"path\": str, \"action\": \"create\", \"content\": str}}], "
    "\"commands\": [str]}}. "
    "Keep total response under 4000 chars. Each file under 80 lines. Max 3 files."
)
TEST_GEN_PROMPT = (
    "You are qa_agent. Return strict JSON only. Be CONCISE. "
    "Return: {{\"agent\": str, \"status\": \"ok\", \"summary\": str, "
    "\"files\": [{{\"path\": str, \"action\": \"create\", \"content\": str}}]}}. "
    "Keep total response under 5000 chars."
)
SECURITY_PROMPT = (
    "You are security_agent. Return strict JSON only. Be CONCISE. "
    "Return: {{\"ok\": bool, \"summary\": str, \"findings\": [{{\"severity\": str, \"description\": str}}]}}. "
    "Keep response under 2000 chars."
)

ROLE_TASK_MAP = {
    "qa": "test_generation",
    "security": "security_review",
    "docs": "code_generation",
    "release": "release_notes",
}


class ImplementationPhase:
    def __init__(self, *, scheduler: Any, store: Any, artifacts: Any, runtime: Any) -> None:
        self._scheduler = scheduler
        self._store = store
        self._artifacts = artifacts
        self._runtime = runtime

    async def execute(
        self,
        job: dict[str, Any],
        run: dict[str, Any],
        project: dict[str, Any],
        *,
        tenant_id: str,
        heartbeat: Any | None = None,
    ) -> dict[str, Any]:
        package_id = job.get("work_package_id") or ""
        package = await self._store.get_work_package(package_id)
        if not package:
            return {"status": "blocked", "error": "work_package_not_found"}

        role = package.get("role", "backend")
        allowed_paths = package.get("allowed_paths") or []
        forbidden_paths = package.get("forbidden_paths") or []
        objective = package.get("objective", "")
        expected_outputs = package.get("expected_outputs") or []
        metadata = self._get_metadata(run)
        scale_profile = metadata.get("scale_profile") or {}
        budget = self._build_budget(scale_profile, role)
        task_kind = ROLE_TASK_MAP.get(role, "code_generation")
        system_prompt = self._prompt_for(role)

        cross_context = await self._build_cross_package_context(run["id"], package)
        pkg_memory = await self._build_package_memory(run, project, metadata, package)

        result = await self._scheduler.call(
            run_id=run["id"], role=role, job_id=job["id"],
            task_kind=task_kind, system_prompt=system_prompt,
            user_payload={
                "project": {"name": project.get("name", ""), "description": project.get("description", "")},
                "package": {
                    "package_key": package.get("package_key", ""),
                    "role": role,
                    "allowed_paths": allowed_paths,
                    "objective": objective,
                    "expected_outputs": expected_outputs,
                },
                "requirements_summary": (
                    metadata.get("requirements") or metadata.get("requirements_analysis") or {}
                ).get("summary", ""),
                "architecture_summary": (
                    metadata.get("architecture") or metadata.get("architecture_design") or {}
                ).get("architecture_summary", ""),
                # Bug 2 fix: layered memory and cross-package context were computed but never sent
                "layered_memory": pkg_memory,
                "cross_package_context": cross_context,
            },
            budget=budget, store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat,
        )

        if not result.ok:
            log.warning("implementation_ai_failed", run_id=run["id"],
                        package_key=package.get("package_key"), error=result.error)
            return {
                "status": "blocked",
                "error": result.error,
                "error_kind": result.error_kind,
                "retryable": result.retryable,
            }

        output = json_or_empty(result.raw_response)
        apply_result = await self._runtime.apply_file_manifest(
            files=output.get("files") or [],
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
        )

        commands = output.get("commands") or []
        cmd_results = []
        if commands:
            cmd_results = (await self._runtime.apply_commands(commands)).get("results") or []

        await self._artifacts.write(
            project["id"], run["id"], job["id"],
            "patch_set", f"{package.get('package_key', 'pkg')}.json",
            {
                "package_key": package.get("package_key", ""),
                "role": role,
                "agent_output": output,
                "apply_result": apply_result,
            },
        )

        changed_files = apply_result.get("files") or []
        log.info("implementation_completed", run_id=run["id"],
                 package_key=package.get("package_key"), applied=apply_result.get("applied", 0))
        return {
            "status": "ok",
            "package_key": package.get("package_key", ""),
            "role": role,
            "task_kind": task_kind,
            "changed_files": changed_files,
            "apply_result": apply_result,
            "command_results": cmd_results,
        }

    def next_job(self, run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        return None

    @staticmethod
    def _prompt_for(role: str) -> str:
        if role == "qa":
            base = TEST_GEN_PROMPT
        elif role == "security":
            base = SECURITY_PROMPT
        else:
            base = CODE_GEN_PROMPT.format(role=role)
        skill_content = load_skills_for_role(role)
        if skill_content:
            return f"{skill_content}\n\n{base}"
        return base

    @staticmethod
    def _build_budget(sp: dict[str, Any], role: str) -> AITaskBudget:
        timeout = 90 if role in ("backend", "frontend", "db") else 60
        return AITaskBudget(
            task_kind="code_generation",
            max_input_chars=int(sp.get("context_budget_chars", 36000)),
            max_output_tokens=2000,
            timeout_seconds=timeout,
            reasoning_effort="low",
            retry_attempts=int(sp.get("ai_retry_attempts", 3)),
        )

    @staticmethod
    def _get_metadata(run: Any) -> dict[str, Any]:
        if hasattr(run, "metadata"):
            m = run.metadata
            return m.model_dump() if hasattr(m, "model_dump") else dict(m)
        return dict(run.get("metadata") or {})

    async def _build_cross_package_context(
        self, run_id: str, current_package: dict[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            all_packages = await self._store.list_work_packages(run_id)
        except Exception:
            return []

        current_key = current_package.get("package_key", "")
        current_wave = current_package.get("wave_key", "")
        context = []
        for pkg in all_packages:
            pkg_key = pkg.get("package_key", "")
            if pkg_key == current_key or pkg.get("wave_key", "") != current_wave:
                continue
            if pkg.get("status") not in ("completed", "done"):
                continue
            context.append({
                "package_key": pkg_key,
                "role": pkg.get("role", ""),
                "domain": pkg.get("domain", ""),
                "objective": pkg.get("objective", ""),
                "allowed_paths": pkg.get("allowed_paths") or [],
            })
        return context

    async def _build_package_memory(
        self, run: Any, project: dict[str, Any],
        metadata: dict[str, Any], package: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            artifacts = await self._store.list_artifacts(run["id"])
            artifact_dicts = [
                a.model_dump() if hasattr(a, "model_dump") else a for a in artifacts
            ]
        except Exception:
            artifact_dicts = []

        memory = build_layered_memory(
            project=project,
            requirements=metadata.get("requirements") or metadata.get("requirements_analysis") or {},
            architecture=metadata.get("architecture") or metadata.get("architecture_design") or {},
            package_plan=metadata.get("package_dag") or {},
            context_snapshot={},
            change_artifacts=artifact_dicts,
        )
        return memory_for_package(memory, package)
