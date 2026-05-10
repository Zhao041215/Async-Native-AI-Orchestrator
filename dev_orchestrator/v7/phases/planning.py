"""V7 Package Planning Phase - scope + wave planning."""
from __future__ import annotations

import json
from typing import Any

from dev_orchestrator.v7.models import AITaskBudget
from dev_orchestrator.v7.observability import get_logger

log = get_logger(__name__)

SCOPE_PROMPT = (
    "You are package_scope_planning_agent. Return strict JSON only. "
    "Be CONCISE. Choose 3-6 AI work packages max. "
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

    async def execute(self, job: dict[str, Any], run: dict[str, Any], project: dict[str, Any], *, tenant_id: str, heartbeat: Any | None = None) -> dict[str, Any]:
        metadata = dict(run.get("metadata") or {})
        scale_profile = metadata.get("scale_profile") or {}
        budget = self._build_budget(scale_profile)

        # Use smaller budget for sub-phases to avoid API disconnections
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

        if metadata.get("package_dag") and run.get("checkpoint") == "package_planning_completed":
            return {"status": "completed", "package_count": len((metadata.get("package_dag") or {}).get("packages") or [])}

        layout = metadata.get("project_layout") or (metadata.get("architecture_design") or {}).get("project_layout") or {}
        architecture = metadata.get("architecture_design") or {}

        # Check retry cache before making AI calls
        scope_plan = metadata.get("_cached_scope_plan") or metadata.get("package_scope_plan")
        if not scope_plan:
            scope_plan = await self._call_ai(project, run, metadata, "package_scope", SCOPE_PROMPT, scope_budget, tenant_id, job, heartbeat, {"architecture": architecture, "layout": layout})
            if scope_plan:
                await self._cache_result(run["id"], "_cached_scope_plan", scope_plan)
        if not scope_plan:
            # Fallback: generate a default scope from architecture/layout
            log.warning("scope_failed_using_default", run_id=run["id"])
            scope_plan = self._default_scope_plan(project, architecture, layout)
            await self._cache_result(run["id"], "_cached_scope_plan", scope_plan)

        wave_plan = metadata.get("_cached_wave_plan") or metadata.get("package_wave_plan")
        if not wave_plan:
            wave_plan = await self._call_ai(project, run, metadata, "package_wave", WAVE_PROMPT, wave_budget, tenant_id, job, heartbeat, {"scope_plan": scope_plan, "architecture": architecture, "layout": layout})
            if wave_plan:
                await self._cache_result(run["id"], "_cached_wave_plan", wave_plan)
        if not wave_plan:
            # Fallback: generate a default wave plan from scope
            log.warning("wave_failed_using_default", run_id=run["id"])
            wave_plan = self._default_wave_plan(scope_plan)
            await self._cache_result(run["id"], "_cached_wave_plan", wave_plan)

        plan = self._merge_plan(scope_plan, wave_plan)
        plan = self._normalize_plan(plan)
        plan = self._resolve_path_overlaps(plan)

        await self._artifacts.write(project["id"], run["id"], job["id"], "package_dag", "package-dag.json", plan)

        wave_id_by_key: dict[str, str] = {}
        for wave in plan.get("waves", []):
            stored = await self._store.upsert_wave(run["id"], wave)
            wave_id_by_key[wave["wave_key"]] = stored["id"]
        for pkg in plan.get("packages", []):
            wave_key = pkg.get("wave_key", "")
            wave_id = wave_id_by_key.get(wave_key, "")
            if wave_id:
                await self._store.upsert_work_package(run["id"], wave_id, pkg)

        log.info("package_planning_completed", run_id=run["id"], package_count=len(plan.get("packages", [])))
        return {"status": "ok", "package_dag": plan, "scope_plan": scope_plan, "wave_plan": wave_plan, "package_count": len(plan.get("packages", [])), "wave_count": len(plan.get("waves", []))}

    def next_job(self, run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        return None

    async def _call_ai(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], task_kind: str, system_prompt: str, budget: dict[str, Any], tenant_id: str, job: dict[str, Any], heartbeat: Any | None, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = {"project": _pp(project), "requirements": metadata.get("requirements_analysis", {}), "scale_profile": metadata.get("scale_profile", {})}
        if extra:
            payload.update(extra)
        result = await self._scheduler.call(run_id=run["id"], role="planner", job_id=job["id"], task_kind=task_kind, system_prompt=system_prompt, user_payload=payload, budget=budget, store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat)
        if not result.ok:
            return None
        output = _json_or_empty(result.raw_response)
        await self._artifacts.write(project["id"], run["id"], job["id"], f"{task_kind}_plan", f"{task_kind}.json", output)
        return output

    async def _cache_result(self, run_id: str, key: str, value: dict[str, Any]) -> None:
        """Cache a sub-phase result in run metadata for retry resilience."""
        try:
            run = await self._store.get_run(run_id)
            if run:
                metadata = dict(run.get("metadata") or {})
                metadata[key] = value
                await self._store.update_run(run_id, metadata=metadata)
        except Exception as exc:
            log.debug("cache_result_failed", run_id=run_id, key=key, error=str(exc))

    @staticmethod
    def _merge_plan(scope: dict[str, Any], waves: dict[str, Any]) -> dict[str, Any]:
        packages = scope.get("packages") or []
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
        # Assign orphaned packages (no wave_key) to the first wave
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
        """Detect and resolve overlapping allowed_paths between packages in the same wave.

        When two packages share a common path prefix, narrow each to avoid file conflicts.
        Packages that cannot be disambiguated get a warning in their objective.
        """
        packages = plan.get("packages", [])
        if not packages:
            return plan

        # Group packages by wave
        wave_groups: dict[str, list[dict[str, Any]]] = {}
        for pkg in packages:
            wave_key = pkg.get("wave_key", "")
            wave_groups.setdefault(wave_key, []).append(pkg)

        for wave_key, pkgs in wave_groups.items():
            if len(pkgs) < 2:
                continue
            # Check pairwise overlaps
            for i in range(len(pkgs)):
                for j in range(i + 1, len(pkgs)):
                    a_paths = pkgs[i].get("allowed_paths") or []
                    b_paths = pkgs[j].get("allowed_paths") or []
                    overlaps = _find_path_overlaps(a_paths, b_paths)
                    if overlaps:
                        log.warning("path_overlap_detected",
                                    wave=wave_key,
                                    pkg_a=pkgs[i].get("package_key", ""),
                                    pkg_b=pkgs[j].get("package_key", ""),
                                    overlaps=overlaps[:5])
                        # Add forbidden_paths to each package for the other's overlapping paths
                        a_forbidden = pkgs[i].setdefault("forbidden_paths", [])
                        b_forbidden = pkgs[j].setdefault("forbidden_paths", [])
                        for overlap in overlaps:
                            if overlap not in a_forbidden:
                                a_forbidden.append(overlap)
                            if overlap not in b_forbidden:
                                b_forbidden.append(overlap)
        return plan

    @staticmethod
    def _default_scope_plan(project: dict[str, Any], architecture: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
        """Generate a minimal default scope plan when the AI call fails."""
        name = project.get("name", "project")
        directories = layout.get("directories") or []
        # Detect main source directories
        src_dirs = []
        for d in directories:
            path = d.get("path", "") if isinstance(d, dict) else str(d)
            if path and path not in ("tests", "test", "docs", "dist", "build", "public", "static"):
                src_dirs.append(path)
        if not src_dirs:
            src_dirs = ["src"]

        packages = [
            {
                "package_key": f"{name}_backend",
                "role": "backend",
                "domain": "core",
                "allowed_paths": [f"{d}/**/*.py" for d in src_dirs] + ["*.py", "*.json", "*.toml", "*.yml"],
                "forbidden_paths": ["tests/**"],
                "depends_on": [],
                "objective": f"Implement core backend logic for {name}",
                "expected_outputs": [f"{src_dirs[0]}/main.py"],
                "acceptance_gates": ["python -m pytest tests/ -x"],
            },
            {
                "package_key": f"{name}_tests",
                "role": "qa",
                "domain": "testing",
                "allowed_paths": ["tests/**/*.py", "test_*.py"],
                "forbidden_paths": [],
                "depends_on": [f"{name}_backend"],
                "objective": f"Write tests for {name}",
                "expected_outputs": ["tests/test_main.py"],
                "acceptance_gates": ["python -m pytest tests/ -v"],
            },
        ]
        return {"packages": packages}

    @staticmethod
    def _default_wave_plan(scope_plan: dict[str, Any]) -> dict[str, Any]:
        """Generate a default wave plan from scope packages."""
        packages = scope_plan.get("packages") or []
        if not packages:
            return {"waves": [], "edges": []}
        # Group by dependency: packages with no deps go in wave 0, others in wave 1
        wave_0 = []
        wave_1 = []
        for pkg in packages:
            key = pkg.get("package_key", "")
            deps = pkg.get("depends_on") or []
            if deps:
                wave_1.append(key)
            else:
                wave_0.append(key)
        waves = []
        if wave_0:
            waves.append({"wave_key": "wave_0", "sequence": 0, "packages": wave_0})
        if wave_1:
            waves.append({"wave_key": "wave_1", "sequence": 1, "packages": wave_1})
        edges = []
        for pkg in packages:
            for dep in pkg.get("depends_on") or []:
                edges.append({"from": dep, "to": pkg.get("package_key", "")})
        return {"waves": waves, "edges": edges}

    @staticmethod
    def _build_budget(sp: dict[str, Any]) -> AITaskBudget:
        return AITaskBudget(task_kind="package_planning", max_input_chars=int(sp.get("context_budget_chars", 36000)), max_output_tokens=8000, timeout_seconds=180, reasoning_effort="high", retry_attempts=int(sp.get("ai_retry_attempts", 3)))


def _find_path_overlaps(paths_a: list[str], paths_b: list[str]) -> list[str]:
    """Find common path prefixes between two path lists."""
    overlaps = []
    for a in paths_a:
        a_norm = a.rstrip("/").lower()
        for b in paths_b:
            b_norm = b.rstrip("/").lower()
            # Check if one is a prefix of the other
            if a_norm.startswith(b_norm) or b_norm.startswith(a_norm):
                overlaps.append(a if len(a) <= len(b) else b)
            # Check shared directory prefix
            a_parts = a_norm.split("/")
            b_parts = b_norm.split("/")
            common = []
            for pa, pb in zip(a_parts, b_parts):
                if pa == pb:
                    common.append(pa)
                else:
                    break
            if common and len(common) >= 2:
                overlaps.append("/".join(common))
    return list(set(overlaps))


def _pp(p: dict[str, Any]) -> dict[str, Any]:
    return {"name": p.get("name", ""), "title": p.get("title", ""), "description": p.get("description", "")}


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
