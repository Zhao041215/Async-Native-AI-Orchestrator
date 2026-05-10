"""V8 Async Worker — polls for jobs, claims with lease, executes, heartbeats.

Provides ``AsyncWorker`` (single-role job consumer) and ``WorkerSupervisor``
(multi-role orchestrator with graceful shutdown).
"""
from __future__ import annotations

import asyncio
import os
import signal
import uuid
from typing import Any

from dev_orchestrator.v8.models import Job, JobStatus, PackageRole, new_id
from dev_orchestrator.v8.observability import get_logger, set_job_id, set_run_id
from dev_orchestrator.v8.pipeline import PipelineOrchestrator
from dev_orchestrator.v8.scheduler import AbstractStore

log = get_logger("worker")


class AsyncWorker:
    """Async job consumer that polls a single role, claims jobs, and executes them.

    Heartbeats are issued as an ``asyncio.Task`` (not a thread) so they share
    the event loop cooperatively.
    """

    def __init__(
        self,
        pipeline: PipelineOrchestrator,
        store: AbstractStore,
        role: PackageRole,
        tenant_id: str,
        poll_seconds: float = 2.0,
        lease_seconds: int = 300,
    ) -> None:
        self.worker_id = f"{role.value}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.pipeline = pipeline
        self.store = store
        self.role = role
        self.tenant_id = tenant_id
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self._shutdown = asyncio.Event()
        self._log = get_logger(f"worker.{role.value}")
        self._heartbeat_task: asyncio.Task | None = None

    async def run_forever(self, max_jobs: int | None = None) -> None:
        executed = 0
        self._log.info("worker.started", worker_id=self.worker_id, role=self.role.value)

        while not self._shutdown.is_set():
            try:
                await self.store.requeue_expired_jobs(self.tenant_id)
            except Exception as exc:
                self._log.debug("requeue_expired.error", error=str(exc))

            job = await self._try_claim()
            if job is None:
                try:
                    await asyncio.wait_for(self._shutdown.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
                continue

            await self._execute(job)
            executed += 1

            if max_jobs is not None and executed >= max_jobs:
                self._log.info("worker.max_jobs_reached", count=executed)
                break

        self._log.info("worker.stopped", worker_id=self.worker_id, jobs_executed=executed)

    def shutdown(self) -> None:
        self._log.info("worker.shutdown_requested", worker_id=self.worker_id)
        self._shutdown.set()

    async def _try_claim(self) -> Job | None:
        try:
            job = await self.store.claim_job(self.tenant_id, self.role, self.worker_id, self.lease_seconds)
            if job is not None:
                self._log.info("worker.job_claimed", job_id=job.id,
                               job_type=job.job_type.value, run_id=job.run_id)
            return job
        except Exception as exc:
            self._log.error("worker.claim_error", error=str(exc))
            return None

    async def _execute(self, job: Job) -> None:
        run_token = set_run_id(job.run_id)
        job_token = set_job_id(job.id)

        try:
            started = await self.store.start_job(job.id, self.worker_id, self.lease_seconds)
            if started is None:
                self._log.warning("worker.start_failed", job_id=job.id)
                return

            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(job.id), name=f"heartbeat-{job.id}",
            )

            try:
                result = await self.pipeline.execute_job(job)
            finally:
                self._cancel_heartbeat()

            await self.store.finish_job(job.id, self.worker_id, result or {})
            self._log.info("worker.job_completed", job_id=job.id, job_type=job.job_type.value)

        except Exception as exc:
            self._cancel_heartbeat()
            error_msg = f"{type(exc).__name__}: {exc}"[:2000]
            retryable = not isinstance(exc, (ValueError, TypeError))
            try:
                await self.store.fail_job(job.id, self.worker_id, error_msg, retryable)
            except Exception as fail_exc:
                self._log.error("worker.fail_job_error", job_id=job.id, error=str(fail_exc))
            self._log.error("worker.job_failed", job_id=job.id, error=error_msg, retryable=retryable)
        finally:
            _reset_context(run_token, job_token)

    async def _heartbeat_loop(self, job_id: str) -> None:
        interval = max(10.0, self.lease_seconds / 4.0)
        while not self._shutdown.is_set():
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self.store.heartbeat_job(job_id, self.worker_id, self.lease_seconds)
                self._log.debug("worker.heartbeat", job_id=job_id)
            except Exception as exc:
                self._log.warning("worker.heartbeat_error", job_id=job_id, error=str(exc))

    def _cancel_heartbeat(self) -> None:
        task = self._heartbeat_task
        if task is not None and not task.done():
            task.cancel()
        self._heartbeat_task = None


def _reset_context(run_token: Any, job_token: Any) -> None:
    from dev_orchestrator.v8.observability import _run_id, _job_id
    _run_id.reset(run_token)
    _job_id.reset(job_token)


# ---------------------------------------------------------------------------
# Worker Supervisor
# ---------------------------------------------------------------------------

class WorkerSupervisor:
    """Manages a pool of ``AsyncWorker`` instances across multiple roles.

    Handles signal-based graceful shutdown (SIGINT / SIGTERM) and coordinates
    all workers through an ``asyncio.TaskGroup``.
    """

    def __init__(
        self,
        pipeline: PipelineOrchestrator,
        store: AbstractStore,
        tenant_id: str,
        role_concurrency: dict[str, int],
        poll_seconds: float = 2.0,
        lease_seconds: int = 300,
    ) -> None:
        self.pipeline = pipeline
        self.store = store
        self.tenant_id = tenant_id
        self.role_concurrency = role_concurrency
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self._workers: list[AsyncWorker] = []
        self._tasks: list[asyncio.Task] = []
        self._log = get_logger("supervisor")
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        self._install_signal_handlers()
        self._create_workers()
        self._log.info("supervisor.starting", total_workers=len(self._workers),
                       roles=list(self.role_concurrency.keys()))
        try:
            async with asyncio.TaskGroup() as tg:
                for worker in self._workers:
                    task = tg.create_task(worker.run_forever(), name=f"worker-{worker.worker_id}")
                    self._tasks.append(task)
        except* Exception as exc_group:
            for exc in exc_group.exceptions:
                self._log.error("supervisor.worker_crash", error=str(exc))
        finally:
            self._log.info("supervisor.all_workers_stopped")

    def shutdown(self) -> None:
        self._log.info("supervisor.shutdown_requested", worker_count=len(self._workers))
        self._shutdown_event.set()
        for worker in self._workers:
            worker.shutdown()

    def _create_workers(self) -> None:
        for role_name, count in self.role_concurrency.items():
            try:
                role = PackageRole(role_name)
            except ValueError:
                self._log.warning("supervisor.unknown_role", role=role_name)
                continue
            for _ in range(max(1, count)):
                self._workers.append(AsyncWorker(
                    pipeline=self.pipeline, store=self.store, role=role,
                    tenant_id=self.tenant_id, poll_seconds=self.poll_seconds,
                    lease_seconds=self.lease_seconds,
                ))

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._on_signal, sig)
            except NotImplementedError:
                self._log.debug("supervisor.signal_not_supported", signal=sig.name)

    def _on_signal(self, sig: signal.Signals) -> None:
        self._log.info("supervisor.signal_received", signal=sig.name)
        self.shutdown()
