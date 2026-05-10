"""V7 Async Data Store - Protocol definition and in-memory implementation."""
from __future__ import annotations

import asyncio
import copy
import time as _time
from datetime import timedelta
from typing import Protocol, runtime_checkable

from dev_orchestrator.v7.observability import get_logger
from dev_orchestrator.v7.models import (
    AISlot,
    AgentRun,
    Artifact,
    ErrorClassification,
    Event,
    Job,
    JobStatus,
    PackageRole,
    Project,
    ProviderHealthStatus,
    ProviderState,
    Run,
    RunMetadata,
    RunStatus,
    Wave,
    WorkPackage,
    new_id,
    utc_now,
)


class StoreError(RuntimeError):
    """Raised when a store operation fails."""


# ---------------------------------------------------------------------------
# Abstract Store Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class AbstractStore(Protocol):
    """Async protocol that every V7 store backend must implement."""

    async def bootstrap(self) -> None: ...

    # -- Projects -----------------------------------------------------------
    async def create_project(self, tenant_id: str, project: Project) -> Project: ...
    async def get_project(self, project_id: str) -> Project | None: ...
    async def list_projects(self, tenant_id: str) -> list[Project]: ...
    async def delete_project(self, project_id: str) -> None: ...

    # -- Runs ---------------------------------------------------------------
    async def create_run(self, tenant_id: str, project_id: str, metadata: RunMetadata) -> Run: ...
    async def get_run(self, run_id: str) -> Run | None: ...
    async def update_run(self, run_id: str, **fields: object) -> Run: ...
    async def list_runs(self, project_id: str) -> list[Run]: ...

    # -- Jobs ---------------------------------------------------------------
    async def enqueue_job(self, tenant_id: str, job: Job) -> Job: ...
    async def claim_job(self, tenant_id: str, role: PackageRole, worker_id: str, lease_seconds: int) -> Job | None: ...
    async def start_job(self, job_id: str, worker_id: str, lease_seconds: int) -> Job | None: ...
    async def finish_job(self, job_id: str, worker_id: str, result: dict) -> Job: ...
    async def fail_job(self, job_id: str, worker_id: str, error: str, retryable: bool) -> Job: ...
    async def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> None: ...
    async def list_jobs(self, run_id: str | None = None, status: JobStatus | None = None) -> list[Job]: ...
    async def count_jobs(self, run_id: str, status: JobStatus) -> int: ...
    async def requeue_expired_jobs(self, tenant_id: str) -> int: ...

    # -- AI Slots -----------------------------------------------------------
    async def acquire_ai_slot(
        self,
        tenant_id: str,
        run_id: str,
        provider: str,
        agent_run_id: str,
        task_kind: str,
        provider_limit: int,
        run_limit: int,
        wait_seconds: int,
        lease_seconds: int,
    ) -> AISlot: ...
    async def release_ai_slot(self, slot_id: str) -> None: ...
    async def list_active_ai_slots(self, run_id: str | None = None) -> list[AISlot]: ...

    # -- Artifacts ----------------------------------------------------------
    async def add_artifact(self, tenant_id: str, project_id: str, artifact: Artifact) -> Artifact: ...
    async def list_artifacts(self, run_id: str, kind: str | None = None) -> list[Artifact]: ...
    async def get_artifact(self, run_id: str, kind: str, key: str) -> Artifact | None: ...

    # -- Events -------------------------------------------------------------
    async def add_event(self, event: Event) -> Event: ...
    async def list_events(self, run_id: str, event_type: str | None = None) -> list[Event]: ...

    # -- Waves & Work Packages ---------------------------------------------
    async def upsert_wave(self, run_id: str, wave: Wave) -> Wave: ...
    async def list_waves(self, run_id: str) -> list[Wave]: ...
    async def upsert_work_package(self, run_id: str, wave_id: str, package: WorkPackage) -> WorkPackage: ...
    async def get_work_package(self, package_id: str) -> WorkPackage | None: ...
    async def update_work_package(self, package_id: str, **fields: object) -> WorkPackage: ...
    async def list_work_packages(self, run_id: str) -> list[WorkPackage]: ...

    # -- Agent Runs ---------------------------------------------------------
    async def record_agent_run(self, agent_run: AgentRun) -> AgentRun: ...
    async def list_agent_runs(self, run_id: str) -> list[AgentRun]: ...

    # -- Provider Health ----------------------------------------------------
    async def record_provider_success(self, provider: str) -> ProviderState: ...
    async def record_provider_failure(self, provider: str, error: str, classification: ErrorClassification) -> ProviderState: ...
    async def provider_health_snapshot(self) -> dict[str, ProviderState]: ...


# ---------------------------------------------------------------------------
# In-Memory Store Implementation
# ---------------------------------------------------------------------------

_store_log = get_logger("store")


class InMemoryStore:
    """Thread-safe in-memory store backed by asyncio.Lock.

    Every public method acquires the lock before touching the internal dicts,
    ensuring safe concurrent access from multiple coroutines.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # Entity storage -- keyed by entity id
        self._projects: dict[str, Project] = {}
        self._runs: dict[str, Run] = {}
        self._jobs: dict[str, Job] = {}
        self._waves: dict[str, Wave] = {}
        self._work_packages: dict[str, WorkPackage] = {}
        self._ai_slots: dict[str, AISlot] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._events: dict[str, Event] = {}
        self._agent_runs: dict[str, AgentRun] = {}
        self._provider_states: dict[str, ProviderState] = {}

    # -- Helpers ------------------------------------------------------------

    def _copy[T](self, obj: T) -> T:
        """Deep-copy a Pydantic model to prevent mutation of stored state."""
        return copy.deepcopy(obj)

    # -- Bootstrap ----------------------------------------------------------

    async def bootstrap(self) -> None:
        """No-op for the in-memory store; exists for protocol parity."""
        async with self._lock:
            pass

    # -- Projects -----------------------------------------------------------

    async def create_project(self, tenant_id: str, project: Project) -> Project:
        async with self._lock:
            stored = self._copy(project)
            stored.tenant_id = tenant_id
            stored.updated_at = utc_now()
            self._projects[stored.id] = stored
            return self._copy(stored)

    async def get_project(self, project_id: str) -> Project | None:
        async with self._lock:
            project = self._projects.get(project_id)
            return self._copy(project) if project else None

    async def list_projects(self, tenant_id: str) -> list[Project]:
        async with self._lock:
            return [
                self._copy(p)
                for p in sorted(self._projects.values(), key=lambda x: x.created_at, reverse=True)
                if p.tenant_id == tenant_id
            ]

    async def delete_project(self, project_id: str) -> None:
        async with self._lock:
            self._projects.pop(project_id, None)
            # Cascade: remove runs, jobs, waves, packages, artifacts, events, agent_runs
            run_ids = [r.id for r in self._runs.values() if r.project_id == project_id]
            for rid in run_ids:
                self._runs.pop(rid, None)
                self._jobs = {k: v for k, v in self._jobs.items() if v.run_id != rid}
                self._waves = {k: v for k, v in self._waves.items() if v.run_id != rid}
                self._work_packages = {k: v for k, v in self._work_packages.items() if v.run_id != rid}
                self._ai_slots = {k: v for k, v in self._ai_slots.items() if v.run_id != rid}
                self._artifacts = {k: v for k, v in self._artifacts.items() if v.run_id != rid}
                self._events = {k: v for k, v in self._events.items() if v.run_id != rid}
                self._agent_runs = {k: v for k, v in self._agent_runs.items() if v.run_id != rid}

    # -- Runs ---------------------------------------------------------------

    async def create_run(self, tenant_id: str, project_id: str, metadata: RunMetadata) -> Run:
        async with self._lock:
            run = Run(
                tenant_id=tenant_id,
                project_id=project_id,
                status=RunStatus.queued,
                metadata=metadata,
            )
            self._runs[run.id] = self._copy(run)
            return self._copy(run)

    async def get_run(self, run_id: str) -> Run | None:
        async with self._lock:
            run = self._runs.get(run_id)
            return self._copy(run) if run else None

    async def update_run(self, run_id: str, **fields: object) -> Run:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise StoreError(f"run not found: {run_id}")
            for key, value in fields.items():
                if hasattr(run, key):
                    setattr(run, key, value)
            run.updated_at = utc_now()
            self._runs[run_id] = run
            return self._copy(run)

    async def list_runs(self, project_id: str) -> list[Run]:
        async with self._lock:
            return [
                self._copy(r)
                for r in sorted(self._runs.values(), key=lambda x: x.created_at, reverse=True)
                if r.project_id == project_id
            ]

    # -- Jobs ---------------------------------------------------------------

    async def enqueue_job(self, tenant_id: str, job: Job) -> Job:
        async with self._lock:
            # Idempotency: if a non-terminal job with the same resume_key exists, return it
            for existing in self._jobs.values():
                if existing.resume_key == job.resume_key and existing.status not in (JobStatus.completed, JobStatus.dead_letter, JobStatus.cancelled):
                    _store_log.info("enqueue_job_idempotent", resume_key=job.resume_key,
                                    existing_id=existing.id[:12], existing_status=existing.status.value)
                    return self._copy(existing)
            stored = self._copy(job)
            stored.tenant_id = tenant_id
            stored.status = JobStatus.queued
            stored.updated_at = utc_now()
            self._jobs[stored.id] = stored
            _store_log.info("enqueue_job_created", job_id=stored.id[:12], job_type=stored.job_type.value,
                            role=stored.role.value, resume_key=stored.resume_key,
                            total_jobs=len(self._jobs))
            return self._copy(stored)

    async def claim_job(self, tenant_id: str, role: PackageRole, worker_id: str, lease_seconds: int) -> Job | None:
        """Claim the oldest queued/retry job matching tenant+role (FIFO)."""
        async with self._lock:
            now = utc_now()
            lease_until = now + timedelta(seconds=lease_seconds)
            candidates = sorted(
                [
                    j
                    for j in self._jobs.values()
                    if j.tenant_id == tenant_id
                    and j.role == role
                    and j.status in (JobStatus.queued, JobStatus.retry)
                ],
                key=lambda j: j.created_at,
            )
            if not candidates:
                return None
            job = candidates[0]
            _store_log.info("claim_job_found", job_id=job.id[:12], job_type=job.job_type.value,
                            role=role.value, status=job.status.value, resume_key=job.resume_key)
            job.status = JobStatus.leased
            job.attempts += 1
            job.worker_id = worker_id
            job.lease_until = lease_until
            job.heartbeat_at = now
            job.updated_at = now
            return self._copy(job)

    async def start_job(self, job_id: str, worker_id: str, lease_seconds: int) -> Job | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.worker_id != worker_id or job.status != JobStatus.leased:
                return None
            now = utc_now()
            job.status = JobStatus.running
            job.heartbeat_at = now
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.updated_at = now
            return self._copy(job)

    async def finish_job(self, job_id: str, worker_id: str, result: dict) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise StoreError(f"job not found: {job_id}")
            if job.worker_id != worker_id:
                raise StoreError("worker does not own job")
            now = utc_now()
            job.status = JobStatus.completed
            job.result = result
            job.lease_until = None
            job.heartbeat_at = now
            job.updated_at = now
            return self._copy(job)

    async def fail_job(self, job_id: str, worker_id: str, error: str, retryable: bool) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise StoreError(f"job not found: {job_id}")
            if job.worker_id != worker_id:
                raise StoreError("worker does not own job")
            now = utc_now()
            if retryable and job.attempts < job.max_attempts:
                job.status = JobStatus.retry
            else:
                job.status = JobStatus.dead_letter
            job.worker_id = ""
            job.lease_until = None
            job.last_error = error[:2000]
            job.heartbeat_at = now
            job.updated_at = now
            return self._copy(job)

    async def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.worker_id != worker_id:
                return
            if job.status not in (JobStatus.leased, JobStatus.running):
                return
            now = utc_now()
            job.heartbeat_at = now
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.updated_at = now

    async def list_jobs(self, run_id: str | None = None, status: JobStatus | None = None) -> list[Job]:
        async with self._lock:
            jobs = list(self._jobs.values())
            if run_id is not None:
                jobs = [j for j in jobs if j.run_id == run_id]
            if status is not None:
                jobs = [j for j in jobs if j.status == status]
            return [self._copy(j) for j in sorted(jobs, key=lambda j: j.created_at)]

    async def count_jobs(self, run_id: str, status: JobStatus) -> int:
        async with self._lock:
            return sum(
                1
                for j in self._jobs.values()
                if j.run_id == run_id and j.status == status
            )

    async def requeue_expired_jobs(self, tenant_id: str) -> int:
        """Move expired leased/running jobs back to retry or dead_letter."""
        async with self._lock:
            now = utc_now()
            count = 0
            for job in self._jobs.values():
                if job.tenant_id != tenant_id:
                    continue
                if job.status not in (JobStatus.leased, JobStatus.running):
                    continue
                if job.lease_until is None or job.lease_until >= now:
                    continue
                if job.attempts < job.max_attempts:
                    job.status = JobStatus.retry
                else:
                    job.status = JobStatus.dead_letter
                job.worker_id = ""
                job.lease_until = None
                job.updated_at = now
                count += 1
            return count

    # -- AI Slots -----------------------------------------------------------

    async def acquire_ai_slot(
        self,
        tenant_id: str,
        run_id: str,
        provider: str,
        agent_run_id: str,
        task_kind: str,
        provider_limit: int,
        run_limit: int,
        wait_seconds: int,
        lease_seconds: int,
    ) -> AISlot:
        """Acquire an AI concurrency slot, waiting up to *wait_seconds*.

        Uses ``asyncio.sleep(0.5)`` between attempts so that other coroutines
        can release slots while we wait.
        """
        provider_limit = max(1, provider_limit)
        run_limit = max(1, run_limit)
        lease_sec = max(30, lease_seconds)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0, wait_seconds)

        while True:
            async with self._lock:
                now = utc_now()

                # Expire stale slots
                for slot in self._ai_slots.values():
                    if slot.status == "active" and slot.lease_until < now:
                        slot.status = "expired"
                        slot.released_at = now

                provider_active = sum(
                    1
                    for s in self._ai_slots.values()
                    if s.provider == provider and s.status == "active"
                )
                run_active = sum(
                    1
                    for s in self._ai_slots.values()
                    if s.run_id == run_id and s.status == "active"
                )

                if provider_active < provider_limit and run_active < run_limit:
                    slot = AISlot(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        provider=provider,
                        agent_run_id=agent_run_id,
                        task_kind=task_kind,
                        status="active",
                        acquired_at=now,
                        lease_until=now + timedelta(seconds=lease_sec),
                    )
                    self._ai_slots[slot.id] = slot
                    return self._copy(slot)

            # Check deadline before sleeping
            if loop.time() >= deadline:
                raise StoreError("AI concurrency slot wait timed out")

            await asyncio.sleep(0.5)

    async def release_ai_slot(self, slot_id: str) -> None:
        async with self._lock:
            slot = self._ai_slots.get(slot_id)
            if slot is None:
                return
            slot.status = "released"
            slot.released_at = utc_now()

    async def list_active_ai_slots(self, run_id: str | None = None) -> list[AISlot]:
        async with self._lock:
            slots = [
                s for s in self._ai_slots.values() if s.status == "active"
            ]
            if run_id is not None:
                slots = [s for s in slots if s.run_id == run_id]
            return [self._copy(s) for s in slots]

    # -- Artifacts ----------------------------------------------------------

    async def add_artifact(self, tenant_id: str, project_id: str, artifact: Artifact) -> Artifact:
        async with self._lock:
            stored = self._copy(artifact)
            stored.tenant_id = tenant_id
            stored.project_id = project_id
            stored.created_at = utc_now()
            self._artifacts[stored.id] = stored
            return self._copy(stored)

    async def list_artifacts(self, run_id: str, kind: str | None = None) -> list[Artifact]:
        async with self._lock:
            artifacts = [a for a in self._artifacts.values() if a.run_id == run_id]
            if kind is not None:
                artifacts = [a for a in artifacts if a.kind == kind]
            return [self._copy(a) for a in sorted(artifacts, key=lambda a: a.created_at)]

    async def get_artifact(self, run_id: str, kind: str, key: str) -> Artifact | None:
        async with self._lock:
            for a in self._artifacts.values():
                if a.run_id == run_id and a.kind == kind and a.key == key:
                    return self._copy(a)
            return None

    # -- Events -------------------------------------------------------------

    async def add_event(self, event: Event) -> Event:
        async with self._lock:
            stored = self._copy(event)
            stored.created_at = utc_now()
            self._events[stored.id] = stored
            return self._copy(stored)

    async def list_events(self, run_id: str, event_type: str | None = None) -> list[Event]:
        async with self._lock:
            events = [e for e in self._events.values() if e.run_id == run_id]
            if event_type is not None:
                events = [e for e in events if e.event_type == event_type]
            return [self._copy(e) for e in sorted(events, key=lambda e: e.created_at)]

    # -- Waves & Work Packages ---------------------------------------------

    async def upsert_wave(self, run_id: str, wave: Wave) -> Wave:
        async with self._lock:
            stored = self._copy(wave)
            stored.run_id = run_id
            self._waves[stored.id] = stored
            return self._copy(stored)

    async def list_waves(self, run_id: str) -> list[Wave]:
        async with self._lock:
            return [
                self._copy(w)
                for w in sorted(self._waves.values(), key=lambda w: w.sequence)
                if w.run_id == run_id
            ]

    async def upsert_work_package(self, run_id: str, wave_id: str, package: WorkPackage) -> WorkPackage:
        async with self._lock:
            stored = self._copy(package)
            stored.run_id = run_id
            stored.wave_id = wave_id
            self._work_packages[stored.id] = stored
            return self._copy(stored)

    async def get_work_package(self, package_id: str) -> WorkPackage | None:
        async with self._lock:
            pkg = self._work_packages.get(package_id)
            return self._copy(pkg) if pkg else None

    async def update_work_package(self, package_id: str, **fields: object) -> WorkPackage:
        async with self._lock:
            pkg = self._work_packages.get(package_id)
            if pkg is None:
                raise StoreError(f"work package not found: {package_id}")
            for key, value in fields.items():
                if hasattr(pkg, key):
                    setattr(pkg, key, value)
            self._work_packages[package_id] = pkg
            return self._copy(pkg)

    async def list_work_packages(self, run_id: str) -> list[WorkPackage]:
        async with self._lock:
            return [
                self._copy(p)
                for p in self._work_packages.values()
                if p.run_id == run_id
            ]

    # -- Agent Runs ---------------------------------------------------------

    async def record_agent_run(self, agent_run: AgentRun) -> AgentRun:
        async with self._lock:
            stored = self._copy(agent_run)
            stored.created_at = utc_now()
            self._agent_runs[stored.id] = stored
            return self._copy(stored)

    async def list_agent_runs(self, run_id: str) -> list[AgentRun]:
        async with self._lock:
            return [
                self._copy(ar)
                for ar in sorted(self._agent_runs.values(), key=lambda a: a.created_at)
                if ar.run_id == run_id
            ]

    # -- Provider Health ----------------------------------------------------

    async def record_provider_success(self, provider: str) -> ProviderState:
        async with self._lock:
            provider = provider or "unknown-provider"
            existing = self._provider_states.get(provider)
            now_epoch = _time.time()
            state = ProviderState(
                provider=provider,
                status=ProviderHealthStatus.healthy,
                success_count=(existing.success_count if existing else 0) + 1,
                failure_count=(existing.failure_count if existing else 0),
                failure_streak=0,
                last_error_kind="",
                last_error=existing.last_error if existing else "",
                last_event_at=now_epoch,
                retry_after_seconds=0,
                circuit_open_until=0.0,
            )
            self._provider_states[provider] = state
            return self._copy(state)

    async def record_provider_failure(
        self,
        provider: str,
        error: str,
        classification: ErrorClassification,
    ) -> ProviderState:
        async with self._lock:
            provider = provider or "unknown-provider"
            existing = self._provider_states.get(provider)
            now_epoch = _time.time()
            failure_streak = (existing.failure_streak if existing else 0) + 1
            retry_after = int(classification.backoff_seconds)
            retryable = classification.retryable

            if retryable and failure_streak >= 5:
                status = ProviderHealthStatus.circuit_open
            elif retryable:
                status = ProviderHealthStatus.degraded
            else:
                status = ProviderHealthStatus.blocked

            state = ProviderState(
                provider=provider,
                status=status,
                success_count=(existing.success_count if existing else 0),
                failure_count=(existing.failure_count if existing else 0) + 1,
                failure_streak=failure_streak,
                last_error_kind=classification.error_kind or "provider_unknown",
                last_error=(error or "")[:1000],
                last_event_at=now_epoch,
                retry_after_seconds=retry_after,
                circuit_open_until=(now_epoch + retry_after) if retryable and retry_after else 0.0,
            )
            self._provider_states[provider] = state
            return self._copy(state)

    async def provider_health_snapshot(self) -> dict[str, ProviderState]:
        async with self._lock:
            return {key: self._copy(state) for key, state in self._provider_states.items()}
