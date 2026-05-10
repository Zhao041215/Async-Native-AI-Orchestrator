"""V7 Architecture Design Phase - concurrent sub-phases via asyncio.gather."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from dev_orchestrator.v7.models import AITaskBudget
from dev_orchestrator.v7.observability import get_logger

log = get_logger(__name__)

SEED_PROMPT = "You are architecture_seed_agent. Return strict JSON only. Given the project requirements, produce a concise architecture seed with: architecture_summary, technology_choices (list of {name, category, version, rationale}), design_principles, primary_risks, scale_notes."
SURFACE_PROMPT = "You are architecture_surface_agent. Return strict JSON only. Produce a concise, project-specific architecture surface. Return architecture_summary, technology_choices, design_principles, primary_risks, scale_notes."
LAYOUT_PROMPT = (
    "You are architecture_layout_agent. Return strict JSON only. "
    "Design a MINIMAL project directory layout. Be CONCISE - list only essential directories (max 15). "
    "Return: {\"source_root\": \"src\", \"delivery_root\": \"dist\", \"entrypoints\": [{\"name\": \"main\", \"path\": \"src/main.py\", \"type\": \"entry\"}], "
    "\"directories\": [{\"path\": \"src\", \"purpose\": \"source\"}], \"validation_commands\": [\"npm test\"]}. "
    "Keep total response under 2000 chars. Do NOT include files, only directories."
)
CONTRACTS_PROMPT = "You are architecture_contracts_agent. Return strict JSON only. Define module_boundaries, integration_contracts, and implementation_notes. Keep compact and project-specific."

# Metadata keys for caching sub-phase results across retries
_SEED_KEY = "_cached_architecture_seed"
_SURFACE_KEY = "_cached_architecture_surface"
_LAYOUT_KEY = "_cached_architecture_layout"
_CONTRACTS_KEY = "_cached_architecture_contracts"


class ArchitecturePhase:
    def __init__(self, *, scheduler: Any, store: Any, artifacts: Any, runtime: Any) -> None:
        self._scheduler = scheduler
        self._store = store
        self._artifacts = artifacts
        self._runtime = runtime

    async def execute(self, job: dict[str, Any], run: dict[str, Any], project: dict[str, Any], *, tenant_id: str, heartbeat: Any | None = None) -> dict[str, Any]:
        metadata = dict(run.get("metadata") or {})
        scale_profile = metadata.get("scale_profile") or {}
        budget = self._build_budget(scale_profile)

        if metadata.get("architecture_design") and run.get("checkpoint") == "architecture_completed":
            return {"status": "completed", "project_layout": metadata.get("project_layout", {})}

        # Each sub-phase checks cache first (persisted from prior retry)
        seed = metadata.get(_SEED_KEY) or await self._call_ai(project, run, metadata, "architecture_seed", SEED_PROMPT, budget, tenant_id, job, heartbeat)
        if not seed:
            return {"status": "blocked", "error": "architecture_seed_failed"}
        await self._cache_result(run["id"], _SEED_KEY, seed)

        # Layout uses smaller budget for faster response (avoid API disconnect)
        layout_budget = AITaskBudget(
            task_kind="architecture_layout",
            max_input_chars=budget.max_input_chars,
            max_output_tokens=3000,
            timeout_seconds=120,
            reasoning_effort="medium",
            retry_attempts=budget.retry_attempts,
        )
        surface_task = self._cached_or_call(metadata.get(_SURFACE_KEY), project, run, metadata, "architecture_surface", SURFACE_PROMPT, budget, tenant_id, job, heartbeat)
        layout_task = self._cached_or_call(metadata.get(_LAYOUT_KEY), project, run, metadata, "architecture_layout", LAYOUT_PROMPT, layout_budget, tenant_id, job, heartbeat)
        surface, layout = await asyncio.gather(surface_task, layout_task, return_exceptions=True)

        if isinstance(surface, Exception) or not surface:
            return {"status": "blocked", "error": f"surface_failed: {surface}"}
        if isinstance(layout, Exception) or not layout:
            # Generate a default layout from seed/surface instead of blocking
            log.warning("layout_failed_using_default", run_id=run["id"], error=str(layout) if layout else "None")
            layout = self._default_layout(seed, surface)
        await self._cache_result(run["id"], _SURFACE_KEY, surface)
        await self._cache_result(run["id"], _LAYOUT_KEY, layout)

        contracts = metadata.get(_CONTRACTS_KEY) or await self._call_ai(project, run, metadata, "architecture_contracts", CONTRACTS_PROMPT, budget, tenant_id, job, heartbeat)
        if not contracts:
            return {"status": "blocked", "error": "architecture_contracts_failed"}
        await self._cache_result(run["id"], _CONTRACTS_KEY, contracts)

        design = self._merge_design(seed, surface, layout, contracts)
        layout_normalized = self._normalize_layout(design)
        project_root = self._runtime.project_root(project)
        self._runtime.layout_roots(project_root, layout_normalized)
        design["project_layout"] = layout_normalized

        await self._artifacts.write(project["id"], run["id"], job["id"], "architecture_design", "architecture-design.json", design)
        await self._artifacts.write(project["id"], run["id"], job["id"], "project_layout", "project-layout.json", layout_normalized)

        log.info("architecture_completed", run_id=run["id"])
        return {"status": "ok", "architecture_design": design, "project_layout": layout_normalized, "seed": seed, "surface": surface, "layout": layout, "contracts": contracts}

    def next_job(self, run: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        if result.get("status") != "ok":
            return None
        return {"job_type": "package_planning", "role": "planner", "run_id": run["id"], "resume_key": f"run:{run['id']}:package_planning", "payload": {}}

    async def _cached_or_call(self, cached: Any, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], task_kind: str, system_prompt: str, budget: AITaskBudget, tenant_id: str, job: dict[str, Any], heartbeat: Any | None) -> dict[str, Any] | None:
        """Return cached result if available, otherwise call AI."""
        if cached:
            return cached
        return await self._call_ai(project, run, metadata, task_kind, system_prompt, budget, tenant_id, job, heartbeat)

    async def _cache_result(self, run_id: str, key: str, value: dict[str, Any]) -> None:
        """Persist a sub-phase result to run metadata for retry resilience."""
        try:
            run = await self._store.get_run(run_id)
            if run:
                metadata = dict(run.get("metadata") or {})
                metadata[key] = value
                await self._store.update_run(run_id, metadata=metadata)
        except Exception as exc:
            log.debug("cache_result_failed", run_id=run_id, key=key, error=str(exc))

    async def _call_ai(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], task_kind: str, system_prompt: str, budget: AITaskBudget, tenant_id: str, job: dict[str, Any], heartbeat: Any | None) -> dict[str, Any] | None:
        result = await self._scheduler.call(
            run_id=run["id"], role="architect", job_id=job["id"],
            task_kind=task_kind, system_prompt=system_prompt,
            user_payload={"project": _pp(project), "requirements": metadata.get("requirements_analysis", {}), "seed": metadata.get(_SEED_KEY, {})},
            budget=budget, store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat,
        )
        if not result.ok:
            return None
        output = _json_or_empty(result.raw_response)
        await self._artifacts.write(project["id"], run["id"], job["id"], task_kind, f"{task_kind}.json", output)
        return output

    @staticmethod
    def _merge_design(seed: dict[str, Any], surface: dict[str, Any], layout: dict[str, Any], contracts: dict[str, Any]) -> dict[str, Any]:
        return {
            "architecture_summary": surface.get("architecture_summary") or seed.get("architecture_summary", ""),
            "technology_choices": surface.get("technology_choices") or seed.get("technology_choices", []),
            "design_principles": surface.get("design_principles") or seed.get("design_principles", []),
            "primary_risks": surface.get("primary_risks") or seed.get("primary_risks", []),
            "project_layout": layout.get("project_layout") or layout,
            "validation_commands": layout.get("validation_commands", []),
            "module_boundaries": contracts.get("module_boundaries", []),
            "integration_contracts": contracts.get("integration_contracts", []),
        }

    @staticmethod
    def _normalize_layout(design: dict[str, Any]) -> dict[str, Any]:
        layout = design.get("project_layout") or {}
        return {"source_root": layout.get("source_root", "src"), "delivery_root": layout.get("delivery_root", "delivery"), "entrypoints": layout.get("entrypoints", []), "directories": layout.get("directories", []), "validation_commands": layout.get("validation_commands", [])}

    @staticmethod
    def _default_layout(seed: dict[str, Any], surface: dict[str, Any]) -> dict[str, Any]:
        """Generate a minimal default layout when the layout sub-phase fails."""
        tech_choices = surface.get("technology_choices") or seed.get("technology_choices") or []
        # Detect stack from technology choices
        tech_names = " ".join(str(t.get("name", "")).lower() for t in tech_choices if isinstance(t, dict))
        if "react" in tech_names or "vue" in tech_names or "angular" in tech_names:
            return {
                "project_layout": {
                    "source_root": "src",
                    "delivery_root": "dist",
                    "entrypoints": [{"name": "main", "path": "src/index.tsx", "type": "entry"}],
                    "directories": [
                        {"path": "src", "purpose": "source"},
                        {"path": "src/components", "purpose": "UI components"},
                        {"path": "src/pages", "purpose": "page components"},
                        {"path": "src/services", "purpose": "API services"},
                        {"path": "public", "purpose": "static assets"},
                    ],
                    "validation_commands": ["npm run build"],
                }
            }
        # Default: Python/backend layout
        return {
            "project_layout": {
                "source_root": "src",
                "delivery_root": "dist",
                "entrypoints": [{"name": "main", "path": "src/main.py", "type": "entry"}],
                "directories": [
                    {"path": "src", "purpose": "source"},
                    {"path": "src/models", "purpose": "data models"},
                    {"path": "src/routes", "purpose": "API routes"},
                    {"path": "src/services", "purpose": "business logic"},
                    {"path": "tests", "purpose": "tests"},
                ],
                "validation_commands": ["python -m pytest"],
            }
        }

    @staticmethod
    def _build_budget(scale_profile: dict[str, Any]) -> AITaskBudget:
        return AITaskBudget(task_kind="architecture_design", max_input_chars=int(scale_profile.get("context_budget_chars", 36000)), max_output_tokens=8000, timeout_seconds=180, reasoning_effort="high", retry_attempts=int(scale_profile.get("ai_retry_attempts", 3)))


def _pp(project: dict[str, Any]) -> dict[str, Any]:
    return {"name": project.get("name", ""), "title": project.get("title", ""), "description": project.get("description", "")}


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
