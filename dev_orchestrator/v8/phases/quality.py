"""V8 Quality Gates Phase."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from dev_orchestrator.v8.models import AITaskBudget
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.utils import json_or_empty

log = get_logger(__name__)


class QualityPhase:
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
        metadata = self._get_metadata(run)
        started = time.monotonic()

        packages = await self._store.list_work_packages(run["id"])
        has_packages = len(packages) > 0
        if not has_packages:
            report = {
                "ok": True, "status": "GO", "gates": [], "critical_failures": 0,
                "major_failures": 0, "total_gates": 0, "elapsed_ms": 0,
                "note": "No packages to evaluate — observation-only pass",
            }
            await self._artifacts.write(
                project["id"], run["id"], job["id"],
                "quality_report", "quality-report.json", report,
            )
            log.info("quality_no_packages_pass", run_id=run["id"])
            return {"status": "ok", "quality_report": report}

        gates = [
            self._gate_scale_profile(metadata),
            self._gate_mission_state(metadata),
            self._gate_dag(metadata, observation_only=not has_packages),
            self._gate_ai_execution(run, observation_only=not has_packages),
        ]
        gate_results = await asyncio.gather(*gates, return_exceptions=True)
        results = []
        for r in gate_results:
            if isinstance(r, Exception):
                results.append({"name": "unknown", "ok": False, "severity": "major", "details": {"error": str(r)}})
            else:
                results.append(r)

        ai_result = await self._ai_gate(run, project, metadata, tenant_id, job, heartbeat)
        if ai_result:
            results.append(ai_result)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        deterministic = [g for g in results if g.get("name") != "ai_quality_gate"]
        critical = sum(1 for g in deterministic if not g.get("ok") and g.get("severity") == "critical")
        major = sum(1 for g in deterministic if not g.get("ok") and g.get("severity") == "major")
        ok = critical == 0 and major == 0

        report = {
            "ok": ok, "status": "GO" if ok else "NO_GO",
            "gates": results, "critical_failures": critical, "major_failures": major,
            "total_gates": len(results), "elapsed_ms": elapsed_ms,
        }
        await self._artifacts.write(
            project["id"], run["id"], job["id"],
            "quality_report", "quality-report.json", report,
        )
        log.info("quality_completed", run_id=run["id"], ok=ok, gates=len(results))
        return {"status": "ok" if ok else "blocked", "quality_report": report}

    async def _ai_gate(
        self, run: dict[str, Any], project: dict[str, Any],
        metadata: dict[str, Any], tenant_id: str, job: dict[str, Any], heartbeat: Any | None,
    ) -> dict[str, Any] | None:
        sp = metadata.get("scale_profile") or {}
        budget = AITaskBudget(
            task_kind="quality", max_input_chars=int(sp.get("context_budget_chars", 36000)),
            max_output_tokens=4000, timeout_seconds=120, reasoning_effort="high", retry_attempts=2,
        )
        result = await self._scheduler.call(
            run_id=run["id"], role="qa", job_id=job["id"], task_kind="quality",
            system_prompt="Return strict JSON with ok, summary, gates (list of {name, ok, severity, details}).",
            user_payload={"project": {"name": project.get("name", "")}},
            budget=budget, store=self._store, tenant_id=tenant_id, heartbeat_callback=heartbeat,
        )
        if not result.ok:
            return {
                "name": "ai_quality_gate", "ok": True, "severity": "info",
                "details": {"error": result.error, "note": "AI gate is informational only"},
            }
        output = json_or_empty(result.raw_response)
        ai_ok = output.get("ok", True)
        return {"name": "ai_quality_gate", "ok": ai_ok, "severity": "info", "details": output}

    @staticmethod
    def _get_metadata(run: Any) -> dict[str, Any]:
        if hasattr(run, "metadata"):
            m = run.metadata
            return m.model_dump() if hasattr(m, "model_dump") else dict(m)
        return dict(run.get("metadata") or {})

    @staticmethod
    async def _gate_scale_profile(metadata: dict[str, Any]) -> dict[str, Any]:
        profile = metadata.get("scale_profile") or {}
        return {"name": "scale_profile_gate", "ok": bool(profile.get("name")), "severity": "critical", "details": {}}

    @staticmethod
    async def _gate_mission_state(metadata: dict[str, Any]) -> dict[str, Any]:
        ok = (
            bool(metadata.get("requirements") or metadata.get("requirements_analysis"))
            and bool(metadata.get("architecture") or metadata.get("architecture_design"))
        )
        return {"name": "mission_state_contract_gate", "ok": ok, "severity": "critical", "details": {}}

    @staticmethod
    async def _gate_dag(metadata: dict[str, Any], observation_only: bool = False) -> dict[str, Any]:
        dag = (
            metadata.get("package_dag")
            or metadata.get("package_plan")
            or (metadata.get("cache") or {}).get("_cached_scope_plan")
            or {}
        )
        ok = len(dag.get("packages") or []) > 0
        severity = "info" if observation_only else "critical"
        return {
            "name": "package_dag_acyclic_gate",
            "ok": ok or observation_only,
            "severity": severity,
            "details": {"observation_only": observation_only},
        }

    async def _gate_ai_execution(self, run: dict[str, Any], observation_only: bool = False) -> dict[str, Any]:
        artifacts = await self._store.list_artifacts(run["id"])
        patch_sets = [a for a in artifacts if a.get("kind") == "patch_set"]
        ok = len(patch_sets) > 0
        severity = "info" if observation_only else "critical"
        return {
            "name": "ai_live_execution_gate",
            "ok": ok or observation_only,
            "severity": severity,
            "details": {"count": len(patch_sets), "observation_only": observation_only},
        }
