"""Tests for quality gate phase."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import MockScheduler


def _make_store(packages=None, artifacts=None):
    store = MagicMock()
    store.list_work_packages = AsyncMock(return_value=packages or [])
    store.list_artifacts = AsyncMock(return_value=artifacts or [])
    store.acquire_ai_slot = AsyncMock(return_value=MagicMock(id="slot-1"))
    store.release_ai_slot = AsyncMock()
    store.record_agent_run = AsyncMock()
    store.record_provider_failure = AsyncMock()
    store.record_provider_success = AsyncMock()
    return store


def _make_artifacts():
    artifacts = MagicMock()
    artifacts.write = AsyncMock(return_value="/tmp/quality-report.json")
    return artifacts


def _make_run(metadata=None):
    return {
        "id": "run-1",
        "metadata": metadata or {
            "scale_profile": {"name": "small"},
            "requirements": {"summary": "test"},
            "architecture": {"modules": []},
        },
    }


def test_ai_gate_reads_ok_false_from_response():
    """_ai_gate must propagate ok=False from AI response, not hardcode True."""
    from dev_orchestrator.v8.phases.quality import QualityPhase

    async def _run():
        failing_response = {"ok": False, "summary": "quality failed", "gates": []}
        scheduler = MockScheduler(response=failing_response)
        store = _make_store(packages=[{"id": "pkg-1", "status": "completed"}])
        artifacts = _make_artifacts()

        phase = QualityPhase(scheduler=scheduler, store=store, artifacts=artifacts, runtime=None)

        job = {"id": "job-1", "job_type": "quality"}
        run = _make_run()
        project = {"id": "proj-1", "name": "test"}

        result = await phase.execute(job, run, project, tenant_id="t1")

        gates = result["quality_report"]["gates"]
        ai_gate = next((g for g in gates if g["name"] == "ai_quality_gate"), None)
        assert ai_gate is not None
        assert ai_gate["ok"] is False, "ai_quality_gate must reflect ok=False from AI response"

    asyncio.run(_run())


def test_ai_gate_passes_when_ok_true():
    """_ai_gate must pass when AI returns ok=True."""
    from dev_orchestrator.v8.phases.quality import QualityPhase

    async def _run():
        passing_response = {"ok": True, "summary": "all good", "gates": []}
        scheduler = MockScheduler(response=passing_response)
        store = _make_store(packages=[{"id": "pkg-1", "status": "completed"}])
        artifacts = _make_artifacts()

        phase = QualityPhase(scheduler=scheduler, store=store, artifacts=artifacts, runtime=None)

        job = {"id": "job-2", "job_type": "quality"}
        run = _make_run()
        project = {"id": "proj-2", "name": "test"}

        result = await phase.execute(job, run, project, tenant_id="t1")

        gates = result["quality_report"]["gates"]
        ai_gate = next((g for g in gates if g["name"] == "ai_quality_gate"), None)
        assert ai_gate is not None
        assert ai_gate["ok"] is True

    asyncio.run(_run())


def test_quality_no_packages_returns_pass():
    """With no packages, quality phase must return ok=True without calling AI."""
    from dev_orchestrator.v8.phases.quality import QualityPhase

    async def _run():
        scheduler = MockScheduler(response={"ok": False})
        store = _make_store(packages=[])
        artifacts = _make_artifacts()

        phase = QualityPhase(scheduler=scheduler, store=store, artifacts=artifacts, runtime=None)

        job = {"id": "job-3", "job_type": "quality"}
        run = _make_run()
        project = {"id": "proj-3", "name": "test"}

        result = await phase.execute(job, run, project, tenant_id="t1")

        assert result["quality_report"]["ok"] is True
        assert len(scheduler.calls) == 0, "AI must not be called when there are no packages"

    asyncio.run(_run())
