"""V8 In-Memory Store — full AbstractStore implementation for local development.

No database required. All state is lost on process restart.
Use this for local development / testing. Production should use PostgresStore.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from dev_orchestrator.v8.models import (
    AISlot,
    AgentRun,
    Artifact,
    ErrorClassification,
    Event,
    Job,
    JobStatus,
    PackageRole,
    Project,
    ProjectConfig,
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
from dev_orchestrator.v8.observability import get_logger

log = get_logger("store_memory")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryStore:
    """Thread-safe (asyncio) in-memory store for V8. Suitable for local dev / testing."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._projects: dict[str, Project] = {}
        self._runs: dict[str, Run] = {}
        self._jobs: dict[str, Job] = {}
        self._waves: dict[str, Wave] = {}
        self._work_packages: dict[str, WorkPackage] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._events: dict[str, Event] = {}
        self._agent_runs: dict[str, AgentRun] = {}
        self._ai_slots: dict[str, AISlot] = {}
        self._provider_states: dict[str, ProviderState] = {}
        self._event_seq = 0

    # ── Lifecycle ────────────────────────────────────────────────────

    async def bootstrap(self) -> None:
        log.info("memory_store.ready")

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    # ── Projects ─────────────────────────────────────────────────────

    async def create_project(self, tenant_id: str, project: Project) -> Project:
        async with self._lock:
            self._projects[project.id] = deepcopy(project)
            return deepcopy(project)

    async def get_project(self, project_id: str) -> Project | None:
        return deepcopy(self._projects.get(project_id))

    async def list_projects(self, tenant_id: str) -> list[Project]:
        return [deepcopy(p) for p in self._projects.values() if p.tenant_id == tenant_id]

    async def delete_project(self, project_id: str) -> None:
        async with self._lock:
            self._projects.pop(project_id, None)

    # ── Runs ─────────────────────────────────────────────────────────

    async def create_run(self, tenant_id: str, project_id: str, metadata: RunMetadata) -> Run:
        run = Run(
            id=new_id(), tenant_id=tenant_id, project_id=project_id,
            status=RunStatus.queued, checkpoint="run_created",
            metadata=metadata,
            created_at=_now(), updated_at=_now(),
        )
        async with self._lock:
            self._runs[run.id] = deepcopy(run)
        return deepcopy(run)

    async def get_run(self, run_id: str) -> Run | None:
        return deepcopy(self._runs.get(run_id))

    async def update_run(self, run_id: str, **fields: object) -> Run:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(f"run {run_id} not found")
            run_dict = run.model_dump()
            for k, v in fields.items():
                if k == "status" and isinstance(v, str):
                    v = RunStatus(v)
                if k == "metadata" and isinstance(v, dict):
                    v = RunMetadata.model_validate(v)
                run_dict[k] = v
            run_dict["updated_at"] = _now()
            updated = Run.model_validate(run_dict)
            self._runs[run_id] = updated
            return deepcopy(updated)

    async def list_runs(self, project_id: str) -> list[Run]:
        return [deepcopy(r) for r in self._runs.values() if r.project_id == project_id]

    # ── Jobs ─────────────────────────────────────────────────────────

    async def enqueue_job(self, tenant_id: str, job: Job) -> Job:
        async with self._lock:
            if job.resume_key:
                existing = next(
                    (j for j in self._jobs.values() if j.resume_key == job.resume_key
                     and j.status not in {JobStatus.completed, JobStatus.dead_letter, JobStatus.cancelled}),
                    None,
                )
                if existing:
                    return deepcopy(existing)
            if job.id not in self._jobs:
                job = job.model_copy(update={"status": JobStatus.queued, "created_at": _now(), "updated_at": _now()})
            self._jobs[job.id] = deepcopy(job)
            return deepcopy(job)

    async def claim_job(self, tenant_id: str, role: PackageRole, worker_id: str, lease_seconds: int) -> Job | None:
        async with self._lock:
            now = _now()
            for job in self._jobs.values():
                if (job.tenant_id == tenant_id and job.role == role
                        and job.status == JobStatus.queued):
                    job.status = JobStatus.leased
                    job.worker_id = worker_id
                    job.lease_until = now + timedelta(seconds=lease_seconds)
                    job.heartbeat_at = now
                    job.updated_at = now
                    return deepcopy(job)
        return None

    async def start_job(self, job_id: str, worker_id: str, lease_seconds: int) -> Job | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.worker_id != worker_id:
                return None
            now = _now()
            job.status = JobStatus.leased
            job.lease_until = now + timedelta(seconds=lease_seconds)
            job.updated_at = now
            return deepcopy(job)

    async def finish_job(self, job_id: str, worker_id: str, result: dict) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job {job_id} not found")
            job.status = JobStatus.completed
            job.result = result
            job.updated_at = _now()
            return deepcopy(job)

    async def fail_job(self, job_id: str, worker_id: str, error: str, retryable: bool) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job {job_id} not found")
            job.attempts = (job.attempts or 0) + 1
            if retryable and job.attempts < (job.max_attempts or 3):
                job.status = JobStatus.queued
                job.worker_id = ""
                job.lease_until = None
            else:
                job.status = JobStatus.dead_letter
            job.last_error = error
            job.updated_at = _now()
            return deepcopy(job)

    async def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job and job.worker_id == worker_id:
                now = _now()
                job.lease_until = now + timedelta(seconds=lease_seconds)
                job.heartbeat_at = now
                job.updated_at = now

    async def list_jobs(self, run_id: str | None = None, status: JobStatus | None = None) -> list[Job]:
        jobs = list(self._jobs.values())
        if run_id is not None:
            jobs = [j for j in jobs if j.run_id == run_id]
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        return [deepcopy(j) for j in jobs]

    async def count_jobs(self, run_id: str, status: JobStatus) -> int:
        return sum(1 for j in self._jobs.values() if j.run_id == run_id and j.status == status)

    async def requeue_expired_jobs(self, tenant_id: str) -> int:
        async with self._lock:
            now = _now()
            count = 0
            for job in self._jobs.values():
                if (job.tenant_id == tenant_id and job.status == JobStatus.leased
                        and job.lease_until and job.lease_until < now):
                    job.status = JobStatus.queued
                    job.worker_id = ""
                    job.lease_until = None
                    job.updated_at = now
                    count += 1
            return count

    # ── AI Slots ─────────────────────────────────────────────────────

    async def acquire_ai_slot(
        self, *, tenant_id: str, run_id: str, provider: str,
        agent_run_id: str, task_kind: str, provider_limit: int,
        run_limit: int, wait_seconds: int, lease_seconds: int,
    ) -> AISlot:
        now = _now()
        slot = AISlot(
            id=new_id(), tenant_id=tenant_id, run_id=run_id,
            provider=provider, agent_run_id=agent_run_id, task_kind=task_kind,
            status="active",
            acquired_at=now,
            lease_until=now + timedelta(seconds=lease_seconds),
        )
        async with self._lock:
            self._ai_slots[slot.id] = deepcopy(slot)
        return deepcopy(slot)

    async def release_ai_slot(self, slot_id: str) -> None:
        async with self._lock:
            slot = self._ai_slots.get(slot_id)
            if slot:
                slot.status = "released"  # type: ignore[assignment]
                slot.released_at = _now()

    async def list_active_ai_slots(self, run_id: str | None = None) -> list[AISlot]:
        slots = [s for s in self._ai_slots.values() if s.status == "active"]
        if run_id is not None:
            slots = [s for s in slots if s.run_id == run_id]
        return [deepcopy(s) for s in slots]

    # ── Artifacts ────────────────────────────────────────────────────

    async def add_artifact(self, tenant_id: str, project_id: str, artifact: Artifact) -> Artifact:
        async with self._lock:
            self._artifacts[artifact.id] = deepcopy(artifact)
            return deepcopy(artifact)

    async def list_artifacts(self, run_id: str, kind: str | None = None) -> list[Artifact]:
        arts = [a for a in self._artifacts.values() if a.run_id == run_id]
        if kind is not None:
            arts = [a for a in arts if a.kind == kind]
        return [deepcopy(a) for a in arts]

    async def get_artifact(self, run_id: str, kind: str, key: str) -> Artifact | None:
        for a in self._artifacts.values():
            if a.run_id == run_id and a.kind == kind and a.key == key:
                return deepcopy(a)
        return None

    # ── Events ───────────────────────────────────────────────────────

    async def add_event(self, event: Event) -> Event:
        async with self._lock:
            self._event_seq += 1
            event = event.model_copy(update={"sequence": self._event_seq})
            self._events[event.id] = deepcopy(event)
            return deepcopy(event)

    async def list_events(self, run_id: str, event_type: str | None = None) -> list[Event]:
        evts = [e for e in self._events.values() if e.run_id == run_id]
        if event_type is not None:
            evts = [e for e in evts if e.event_type == event_type]
        return [deepcopy(e) for e in sorted(evts, key=lambda e: e.sequence)]

    # ── Waves ────────────────────────────────────────────────────────

    async def upsert_wave(self, run_id: str, wave: Wave) -> Wave:
        async with self._lock:
            self._waves[wave.id] = deepcopy(wave)
            return deepcopy(wave)

    async def list_waves(self, run_id: str) -> list[Wave]:
        return [deepcopy(w) for w in self._waves.values() if w.run_id == run_id]

    # ── Work Packages ────────────────────────────────────────────────

    async def upsert_work_package(self, run_id: str, wave_id: str, package: WorkPackage) -> WorkPackage:
        async with self._lock:
            self._work_packages[package.id] = deepcopy(package)
            return deepcopy(package)

    async def get_work_package(self, package_id: str) -> WorkPackage | None:
        return deepcopy(self._work_packages.get(package_id))

    async def update_work_package(self, package_id: str, **fields: object) -> WorkPackage:
        async with self._lock:
            pkg = self._work_packages.get(package_id)
            if pkg is None:
                raise KeyError(f"work_package {package_id} not found")
            pkg_dict = pkg.model_dump()
            for k, v in fields.items():
                if k == "status" and isinstance(v, str):
                    v = JobStatus(v)
                pkg_dict[k] = v
            updated = WorkPackage.model_validate(pkg_dict)
            self._work_packages[package_id] = updated
            return deepcopy(updated)

    async def list_work_packages(self, run_id: str) -> list[WorkPackage]:
        return [deepcopy(p) for p in self._work_packages.values() if p.run_id == run_id]

    # ── Agent Runs ───────────────────────────────────────────────────

    async def record_agent_run(self, agent_run: AgentRun) -> AgentRun:
        async with self._lock:
            self._agent_runs[agent_run.id] = deepcopy(agent_run)
            return deepcopy(agent_run)

    async def list_agent_runs(self, run_id: str) -> list[AgentRun]:
        return [deepcopy(ar) for ar in self._agent_runs.values() if ar.run_id == run_id]

    async def list_contract_violations(self, run_id: str) -> list[AgentRun]:
        return [deepcopy(ar) for ar in self._agent_runs.values()
                if ar.run_id == run_id and ar.contract_ok is False]

    # ── Provider Health ──────────────────────────────────────────────

    async def record_provider_success(self, provider: str) -> ProviderState:
        async with self._lock:
            state = self._provider_states.get(provider) or ProviderState(provider=provider)
            state.success_count = (state.success_count or 0) + 1
            state.consecutive_failures = 0
            state.health_status = ProviderHealthStatus.healthy
            state.last_used_at = _now()
            self._provider_states[provider] = state
            return deepcopy(state)

    async def record_provider_failure(
        self, provider: str, error: str, classification: ErrorClassification,
    ) -> ProviderState:
        async with self._lock:
            state = self._provider_states.get(provider) or ProviderState(provider=provider)
            state.failure_count = (state.failure_count or 0) + 1
            state.consecutive_failures = (state.consecutive_failures or 0) + 1
            state.last_error = error
            state.last_used_at = _now()
            if state.consecutive_failures >= 5:
                state.health_status = ProviderHealthStatus.degraded
            self._provider_states[provider] = state
            return deepcopy(state)

    async def provider_health_snapshot(self) -> dict[str, ProviderState]:
        return {k: deepcopy(v) for k, v in self._provider_states.items()}
