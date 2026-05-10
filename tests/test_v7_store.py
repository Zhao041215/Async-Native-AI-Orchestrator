"""Tests for V7 InMemoryStore."""
from __future__ import annotations

import asyncio
import pytest

from dev_orchestrator.v7.models import (
    Event, Job, JobStatus, JobType, PackageRole, Project, Run, RunStatus,
    RunMetadata, ScaleProfileData, Wave, WorkPackage,
)
from dev_orchestrator.v7.store import InMemoryStore


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestInMemoryStore:
    def test_bootstrap(self, store, event_loop):
        event_loop.run_until_complete(store.bootstrap())

    def test_create_project(self, store, event_loop):
        async def _test():
            await store.bootstrap()
            p = Project(name="test", title="Test")
            created = await store.create_project("tenant1", p)
            assert created.name == "test"
            fetched = await store.get_project(created.id)
            assert fetched is not None
            assert fetched.name == "test"
        event_loop.run_until_complete(_test())

    def test_create_run(self, store, event_loop):
        async def _test():
            await store.bootstrap()
            p = Project(name="test", title="Test")
            p = await store.create_project("t1", p)
            r = await store.create_run("t1", p.id, RunMetadata(requirements_text="test"))
            assert r.status == RunStatus.queued
            fetched = await store.get_run(r.id)
            assert fetched is not None
        event_loop.run_until_complete(_test())

    def test_enqueue_and_claim_job(self, store, event_loop):
        async def _test():
            await store.bootstrap()
            p = Project(name="test", title="Test")
            p = await store.create_project("t1", p)
            r = await store.create_run("t1", p.id, RunMetadata())
            j = Job(tenant_id="t1", run_id=r.id, job_type=JobType.requirements_analysis, role=PackageRole.requirements)
            await store.enqueue_job("t1", j)
            claimed = await store.claim_job("t1", PackageRole.requirements, "worker-1", 300)
            assert claimed is not None
            assert claimed.status == JobStatus.leased
        event_loop.run_until_complete(_test())

    def test_upsert_wave(self, store, event_loop):
        async def _test():
            await store.bootstrap()
            w = Wave(run_id="r1", wave_key="wave_0", sequence=0)
            result = await store.upsert_wave("r1", w)
            assert result.wave_key == "wave_0"
            waves = await store.list_waves("r1")
            assert len(waves) == 1
        event_loop.run_until_complete(_test())

    def test_add_event(self, store, event_loop):
        async def _test():
            await store.bootstrap()
            e = Event(tenant_id="t1", run_id="r1", event_type="test_event")
            await store.add_event(e)
            events = await store.list_events("r1")
            assert len(events) == 1
            assert events[0].event_type == "test_event"
        event_loop.run_until_complete(_test())
