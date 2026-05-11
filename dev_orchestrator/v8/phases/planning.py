"""V8 Package Planning Phase — scope + wave planning.

Bug 1 fix: _cache_result now accesses run.metadata.cache (Pydantic attribute)
instead of calling run.get("metadata") which fails on Pydantic v2 models.
"""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v8.models import AITaskBudget
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.utils import json_or_empty, project_summary

log = get_logger(__name__)

SCOPE_PROMPT = (
    "You are package_scope_planning_agent. Return strict JSON only. "
    "Be CONCISE. Choose 3-6 AI work packages max. "
    "Return a JSON object with key \"packages\" (array). "
    "Each package: {\"package_key\": str, \"role\": \"backend|frontend|db|docs|qa\", \"domain\": str, "
    "\"allowed_paths\": [glob], \"objective\": str, \"expected_outputs\": [str]}. "
    "Keep total response under 3000 chars. No prose."
)
WAVE_PROMPT = (
    "You are package_wave_planning_agent. Return strict JSON only. "
    "Assign packages to waves. Return: {\"waves\": [{\"wave_key\": str, \"sequence\": int, \"packages\": [str]}], "
    "\"edges\": [{\"from\": str, \"to\": str}]}. Keep response under 1500 chars."
)


class PlanningPhase:
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

        scope_budget = AITaskBudget(
            task_kind="package_scope",
            max_input_chars=budget.max_input_chars,
            max_output_tokens=3000,
            timeout_seconds=120,
            reasoning_effort="medium",
            retry_attempts=budget.retry_attempts,
        )
        wave_budget = AITaskBudget(
            task_kind="package_wave",
            max_input_chars=budget.max_input_chars,
            max_output_tokens=2000,
            timeout_seconds=90,
            reasoning_effort="medium",
            retry_attempts=budget.retry_attempts,
        )

        existing_dag = metadata.get("package_dag") or {}
        if (
            existing_dag
            and run.get("checkpoint") == "package_planning_completed"
            and len(existing_dag.get("packages") or []) > 0
        ):
            return {
                "status": "completed",
                "package_count": len(existing_dag.get("packages") or []),
            }

        layout = (
            metadata.get("project_layout")
            or (metadata.get("architecture") or metadata.get("architecture_design") or {}).get("project_layout")
            or {}
        )
        architecture = metadata.get("architecture") or metadata.get("architecture_design") or {}

        # Bug 1 fix: read cache from run.metadata.cache
        cache = self._get_cache(run)

        scope_plan = cache.get("_cached_scope_plan") or metadata.get("_cached_scope_plan")
        if not scope_plan:
            scope_plan = await self._call_ai(
                project, run, metadata, "package_scope", SCOPE_PROMPT, scope_budget,
                tenant_id, job, heartbeat, {"architecture": architecture, "layout": layout},
            )
            if scope_plan:
                await self._cache_result(run["id"], "_cached_scope_plan", scope_plan)
        if not scope_plan:
            log.warning("scope_failed_using_default", run_id=run["id"])
            scope_plan = self._default_scope_plan(project, architecture, layout)
            await self._cache_result(run["id"], "_cached_scope_plan", scope_plan)

        # Log scope_plan keys for diagnostics
        scope_keys = list(scope_plan.keys()) if isinstance(scope_plan, dict) else []
        pkg_count_scope = len(scope_plan.get("packages") or scope_plan.get("work_packages") or scope_plan.get("scope_packages") or [])
        log.info("scope_plan_received", run_id=run["id"], keys=scope_keys, pkg_count=pkg_count_scope)

        wave_plan = cache.get("_cached_wave_plan") or metadata.get("_cached_wave_plan")
        if not wave_plan:
            wave_plan = await self._call_ai(
                project, run, metadata, "package_wave", WAVE_PROMPT, wave_budget,
                tenant_id, job, heartbeat, {"scope_plan": scope_plan, "architecture": architecture, "layout": layout},
            )
            if wave_plan:
                await self._cache_result(run["id"], "_cached_wave_plan", wave_plan)
        if not wave_plan:
            log.warning("wave_failed_using_default", run_id=run["id"])
            wave_plan = self._default_wave_plan(scope_plan)
            await self._cache_result(run["id"], "_cached_wave_plan", wave_plan)

        plan = self._merge_plan(scope_plan, wave_plan)
        plan = self._normalize_plan(plan)
        plan = self._resolve_path_overlaps(plan)

        await self._artifacts.write(
            project["id"], run["id"], job["id"], "package_dag", "package-dag.json", plan,
        )

        wave_id_by_key: dict[str, str] = {}
        for wave in plan.get("waves", []):
            stored = await self._store.upsert_wave(run["id"], wave)
            wave_id_by_key[wave["wave_key"]] = stored["id"]
        for pkg in plan.get("packages", []):
            wave_key = pkg.get("wave_key", "")
            wave_id = wave_id_by_key.get(wave_key, "")
            if wave_id:
                await self._store.upsert_work_package(run["id"], wave_id, pkg)

        log.info("package_planning_completed", run_id=run["id"],
                 package_count=len(plan.get("packages", [])))
        return {
            "status": "ok",
            "package_dag": plan,
            "scope_plan": scope_plan,
            "wave_plan": wave_plan,
            "package_count": len(plan.get("packages", [])),
            "wave_count": len(plan.get("waves", [])),
        }

    def next_job(self, run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        return None

    async def _call_ai(
        self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any],
        task_kind: str, system_prompt: str, budget: AITaskBudget,
        tenant_id: str, job: dict[str, Any], heartbeat: Any | None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "project": project_summary(project),
            "requirements": metadata.get("requirements") or metadata.get("requirements_analysis") or {},
            "scale_profile": metadata.get("scale_profile", {}),
        }
        if extra:
            payload.update(extra)
        result = await self._scheduler.call(
            run_id=run["id"], role="planner", job_id=job["id"],
            task_kind=task_kind, system_prompt=system_prompt,
            user_payload=payload, budget=budget,
            store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat,
        )
        if not result.ok:
            return None
        output = json_or_empty(result.raw_response)
        await self._artifacts.write(
            project["id"], run["id"], job["id"], f"{task_kind}_plan", f"{task_kind}.json", output,
        )
        return output

    async def _cache_result(self, run_id: str, key: str, value: dict[str, Any]) -> None:
        """Bug 1 fix: access run.metadata.cache via Pydantic attribute, not run.get()."""
        try:
            run = await self._store.get_run(run_id)
            if run is None:
                return
            if hasattr(run, "metadata"):
                current_cache = dict(run.metadata.cache)
                current_cache[key] = value
                await self._store.update_run(run_id, metadata={
                    **run.metadata.model_dump(),
                    "cache": current_cache,
                })
            else:
                metadata = dict(run.get("metadata") or {})
                metadata[key] = value
                await self._store.update_run(run_id, metadata=metadata)
        except Exception as exc:
            log.debug("cache_result_failed", run_id=run_id, key=key, error=str(exc))

    @staticmethod
    def _get_metadata(run: Any) -> dict[str, Any]:
        if hasattr(run, "metadata"):
            m = run.metadata
            return m.model_dump() if hasattr(m, "model_dump") else dict(m)
        return dict(run.get("metadata") or {})

    @staticmethod
    def _get_cache(run: Any) -> dict[str, Any]:
        """Bug 1 fix: read cache from Pydantic run.metadata.cache attribute."""
        if hasattr(run, "metadata") and hasattr(run.metadata, "cache"):
            return dict(run.metadata.cache)
        metadata = run.get("metadata") or {} if isinstance(run, dict) else {}
        return dict(metadata.get("cache") or {})

    @staticmethod
    def _merge_plan(scope: dict[str, Any], waves: dict[str, Any]) -> dict[str, Any]:
        packages = scope.get("packages") or scope.get("work_packages") or scope.get("scope_packages") or []
        wave_list = waves.get("waves") or []
        assignment = {}
        for w in wave_list:
            for pk in w.get("packages") or []:
                assignment[pk] = w
        for pkg in packages:
            assigned = assignment.get(pkg.get("package_key", ""))
            if assigned:
                pkg["wave_key"] = assigned.get("wave_key", "")
                pkg.setdefault("depends_on", [])
        return {"packages": packages, "waves": wave_list, "edges": waves.get("edges", [])}

    @staticmethod
    def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
        for pkg in plan.get("packages", []):
            pkg.setdefault("package_key", "")
            pkg.setdefault("role", "backend")
            pkg.setdefault("wave_key", "")
            pkg.setdefault("allowed_paths", [])
            pkg.setdefault("forbidden_paths", [])
            pkg.setdefault("depends_on", [])
            pkg.setdefault("objective", "")
            pkg.setdefault("expected_outputs", [])
            pkg.setdefault("acceptance_gates", [])
            pkg.setdefault("domain", "")
        for i, w in enumerate(plan.get("waves", [])):
            w.setdefault("wave_key", f"wave_{i}")
            w.setdefault("sequence", i)
            w.setdefault("packages", [])
        waves = plan.get("waves", [])
        if waves:
            first_wave_key = waves[0].get("wave_key", "wave_0")
            for pkg in plan.get("packages", []):
                if not pkg.get("wave_key"):
                    pkg["wave_key"] = first_wave_key
                    waves[0].setdefault("packages", []).append(pkg.get("package_key", ""))
        return plan

    @staticmethod
    def _resolve_path_overlaps(plan: dict[str, Any]) -> dict[str, Any]:
        packages = plan.get("packages", [])
        if not packages:
            return plan
        wave_groups: dict[str, list[dict[str, Any]]] = {}
        for pkg in packages:
            wave_groups.setdefault(pkg.get("wave_key", ""), []).append(pkg)
        for wave_key, pkgs in wave_groups.items():
            if len(pkgs) < 2:
                continue
            for i in range(len(pkgs)):
                for j in range(i + 1, len(pkgs)):
                    a_paths = pkgs[i].get("allowed_paths") or []
                    b_paths = pkgs[j].get("allowed_paths") or []
                    overlaps = _find_path_overlaps(a_paths, b_paths)
                    if overlaps:
                        log.warning("path_overlap_detected", wave=wave_key,
                                    pkg_a=pkgs[i].get("package_key", ""),
                                    pkg_b=pkgs[j].get("package_key", ""),
                                    overlaps=overlaps[:5])
                        a_forbidden = pkgs[i].setdefault("forbidden_paths", [])
                        b_forbidden = pkgs[j].setdefault("forbidden_paths", [])
                        for overlap in overlaps:
                            if overlap not in a_forbidden:
                                a_forbidden.append(overlap)
                            if overlap not in b_forbidden:
                                b_forbidden.append(overlap)
        return plan

    @staticmethod
    def _default_scope_plan(
        project: dict[str, Any], architecture: dict[str, Any], layout: dict[str, Any],
    ) -> dict[str, Any]:
        name = project.get("name", "project")
        directories = layout.get("directories") or []
        src_dirs = []
        for d in directories:
            path = d.get("path", "") if isinstance(d, dict) else str(d)
            if path and path not in ("tests", "test", "docs", "dist", "build", "public", "static"):
                src_dirs.append(path)
        if not src_dirs:
            src_dirs = ["src"]
        return {
            "packages": [
                {
                    "package_key": f"{name}_backend",
                    "role": "backend", "domain": "core",
                    "allowed_paths": [f"{d}/**/*.py" for d in src_dirs] + ["*.py", "*.json", "*.toml", "*.yml"],
                    "forbidden_paths": ["tests/**"],
                    "depends_on": [],
                    "objective": f"Implement core backend logic for {name}",
                    "expected_outputs": [f"{src_dirs[0]}/main.py"],
                    "acceptance_gates": ["python -m pytest tests/ -x"],
                },
                {
                    "package_key": f"{name}_tests",
                    "role": "qa", "domain": "testing",
                    "allowed_paths": ["tests/**/*.py", "test_*.py"],
                    "forbidden_paths": [],
                    "depends_on": [f"{name}_backend"],
                    "objective": f"Write tests for {name}",
                    "expected_outputs": ["tests/test_main.py"],
                    "acceptance_gates": ["python -m pytest tests/ -v"],
                },
            ]
        }

    @staticmethod
    def _default_wave_plan(scope_plan: dict[str, Any]) -> dict[str, Any]:
        packages = scope_plan.get("packages") or []
        if not packages:
            return {"waves": [], "edges": []}
        wave_0, wave_1 = [], []
        for pkg in packages:
            key = pkg.get("package_key", "")
            (wave_1 if pkg.get("depends_on") else wave_0).append(key)
        waves = []
        if wave_0:
            waves.append({"wave_key": "wave_0", "sequence": 0, "packages": wave_0})
        if wave_1:
            waves.append({"wave_key": "wave_1", "sequence": 1, "packages": wave_1})
        edges = [
            {"from": dep, "to": pkg.get("package_key", "")}
            for pkg in packages
            for dep in (pkg.get("depends_on") or [])
        ]
        return {"waves": waves, "edges": edges}

    @staticmethod
    def _build_budget(sp: dict[str, Any]) -> AITaskBudget:
        return AITaskBudget(
            task_kind="package_planning",
            max_input_chars=int(sp.get("context_budget_chars", 36000)),
            max_output_tokens=8000,
            timeout_seconds=180,
            reasoning_effort="high",
            retry_attempts=int(sp.get("ai_retry_attempts", 3)),
        )


def _find_path_overlaps(paths_a: list[str], paths_b: list[str]) -> list[str]:
    overlaps = []
    for a in paths_a:
        a_norm = a.rstrip("/").lower()
        for b in paths_b:
            b_norm = b.rstrip("/").lower()
            # Skip glob patterns in startswith check to avoid false positives.
            if "*" not in a_norm and "*" not in b_norm:
                if a_norm.startswith(b_norm) or b_norm.startswith(a_norm):
                    overlaps.append(a if len(a) <= len(b) else b)
            # Only count non-glob path segments as common prefix.
            a_parts = [p for p in a_norm.split("/") if "*" not in p]
            b_parts = [p for p in b_norm.split("/") if "*" not in p]
            common = [pa for pa, pb in zip(a_parts, b_parts) if pa == pb]
            if common and len(common) >= 2:
                overlaps.append("/".join(common))
    return list(set(overlaps))
