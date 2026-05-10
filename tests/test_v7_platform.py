"""Tests for V7 platform integration - pipeline, worker, api."""
from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from dev_orchestrator.v7.models import (
    AISchedulerLimits, Job, JobType, PackageRole, Project, Run, RunStatus,
    RunMetadata, ScaleProfileData, new_id,
)
from dev_orchestrator.v7.store import InMemoryStore
from dev_orchestrator.v7.artifacts import ArtifactWriter
from dev_orchestrator.v7.runtime import FileRuntime
from dev_orchestrator.v7.scheduler import SkipAIScheduler
from dev_orchestrator.v7.pipeline import PipelineOrchestrator
from dev_orchestrator.v7.profiles import resolve_scale_profile


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestPipeline:
    def _make_pipeline(self, tmp_path: Path):
        store = InMemoryStore()
        scheduler = SkipAIScheduler()
        runtime = FileRuntime(tmp_path)
        artifacts = ArtifactWriter(tmp_path / "artifacts")
        return PipelineOrchestrator(store, scheduler, runtime, artifacts)

    def test_create_project(self, event_loop, tmp_path):
        async def _test():
            pipeline = self._make_pipeline(tmp_path)
            await pipeline.store.bootstrap()
            project = await pipeline.create_project(
                tenant_id="t1", name="test", title="Test",
                description="A test project", target_scale="small", project_path=str(tmp_path),
            )
            assert project.name == "test"
            fetched = await pipeline.store.get_project(project.id)
            assert fetched is not None
        event_loop.run_until_complete(_test())

    def test_create_run(self, event_loop, tmp_path):
        async def _test():
            pipeline = self._make_pipeline(tmp_path)
            await pipeline.store.bootstrap()
            project = await pipeline.create_project(
                tenant_id="t1", name="test", title="Test",
                description="desc", target_scale="small", project_path=str(tmp_path),
            )
            run = await pipeline.create_run("t1", project.id, "Build a todo app")
            assert run.status == RunStatus.running
            # Check that a requirements job was enqueued
            jobs = await pipeline.store.list_jobs(run_id=run.id)
            assert len(jobs) == 1
            assert jobs[0].job_type == JobType.requirements_analysis
        event_loop.run_until_complete(_test())

    def test_execute_requirements_job(self, event_loop, tmp_path):
        async def _test():
            pipeline = self._make_pipeline(tmp_path)
            await pipeline.store.bootstrap()
            project = await pipeline.create_project(
                tenant_id="t1", name="test", title="Test",
                description="desc", target_scale="small", project_path=str(tmp_path),
            )
            run = await pipeline.create_run("t1", project.id, "Build a todo app")
            jobs = await pipeline.store.list_jobs(run_id=run.id)
            assert len(jobs) == 1
            job = jobs[0]
            # Execute - SkipAIScheduler will return ok=False
            result = await pipeline.execute_job(job)
            # SkipAIScheduler returns "ai_skipped" which means blocked
            assert result.get("status") in ("blocked", "ok", "NO_GO")
        event_loop.run_until_complete(_test())


class TestWorker:
    def test_worker_creation(self):
        from dev_orchestrator.v7.worker import AsyncWorker
        store = InMemoryStore()
        scheduler = SkipAIScheduler()
        runtime = FileRuntime(Path("/tmp"))
        artifacts = ArtifactWriter(Path("/tmp/artifacts"))
        pipeline = PipelineOrchestrator(store, scheduler, runtime, artifacts)
        worker = AsyncWorker(pipeline, store, PackageRole.backend, "t1")
        assert worker.role == PackageRole.backend
        assert "backend" in worker.worker_id


class TestAPI:
    def test_build_app(self, tmp_path):
        from dev_orchestrator.v7.api import build_v7_app
        store = InMemoryStore()
        scheduler = SkipAIScheduler()
        runtime = FileRuntime(tmp_path)
        artifacts = ArtifactWriter(tmp_path / "artifacts")
        pipeline = PipelineOrchestrator(store, scheduler, runtime, artifacts)
        app = build_v7_app(pipeline, store)
        assert app.title == "Dev Orchestrator V7"
