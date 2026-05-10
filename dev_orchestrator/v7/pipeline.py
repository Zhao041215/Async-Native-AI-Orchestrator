"""V7 Pipeline Orchestrator - central coordinator replacing the 3085-line God Object.

Dispatches jobs to phase handlers, manages run lifecycle (create / advance / resume),
and assembles mission state for the frontend.  All heavy lifting is delegated to the
seven phase classes; this module only owns orchestration and state-machine transitions.
"""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v7.context import build_context_snapshot, package_context
from dev_orchestrator.v7.kernel import build_checkpoint_resume_plan, build_state_transition_event
from dev_orchestrator.v7.memory import build_layered_memory, memory_for_package
from dev_orchestrator.v7.mission import build_mission_state
from dev_orchestrator.v7.models import (
    ContinuationState,
    Event,
    Job,
    JobStatus,
    JobType,
    PackageRole,
    Project,
    ProjectConfig,
    Run,
    RunMetadata,
    RunStatus,
    ScaleProfileData,
    Wave,
    WorkPackage,
    new_id,
    stable_json,
    utc_now,
)
from dev_orchestrator.v7.observability import get_logger
from dev_orchestrator.v7.profiles import resolve_scale_profile, scale_job_attempts
from dev_orchestrator.v7.role_aliases import canonical_worker_role
from dev_orchestrator.v7.runtime import FileRuntime
from dev_orchestrator.v7.scheduler import AsyncAIScheduler
from dev_orchestrator.v7.store import AbstractStore

log = get_logger("pipeline")


# ---------------------------------------------------------------------------
# Thin adapters so phase handlers get the dict-based interface they expect
# ---------------------------------------------------------------------------

class _StoreAdapter:
    """Wraps AbstractStore + ArtifactWriter to provide dict-based helpers."""

    def __init__(self, store: AbstractStore, artifacts: Any) -> None:
        self._store = store
        self._artifacts = artifacts

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    async def write(self, project_id: str, run_id: str, job_id: str, kind: str, key: str, payload: dict, **kw: Any) -> None:
        await self._artifacts.write_json(project_id, run_id, job_id, kind, key, payload, **kw)

    async def upsert_wave(self, run_id: str, wave_data: dict) -> dict:
        wave = Wave(
            id=wave_data.get("id") or new_id(),
            run_id=run_id,
            wave_key=wave_data.get("wave_key", ""),
            sequence=int(wave_data.get("sequence", 0)),
            status=wave_data.get("status", "queued"),
        )
        stored = await self._store.upsert_wave(run_id, wave)
        return stored.model_dump() if hasattr(stored, "model_dump") else stored

    async def upsert_work_package(self, run_id: str, wave_id: str, pkg_data: dict) -> dict:
        pkg = WorkPackage(
            id=pkg_data.get("id") or new_id(),
            run_id=run_id,
            wave_id=wave_id,
            wave_key=pkg_data.get("wave_key", ""),
            package_key=pkg_data.get("package_key", ""),
            role=PackageRole(pkg_data.get("role", "backend")),
            domain=pkg_data.get("domain", ""),
            allowed_paths=pkg_data.get("allowed_paths", []),
            forbidden_paths=pkg_data.get("forbidden_paths", []),
            depends_on=pkg_data.get("depends_on", []),
            objective=pkg_data.get("objective", ""),
            expected_outputs=pkg_data.get("expected_outputs", []),
            acceptance_gates=pkg_data.get("acceptance_gates", []),
        )
        stored = await self._store.upsert_work_package(run_id, wave_id, pkg)
        return stored.model_dump() if hasattr(stored, "model_dump") else stored

    async def get_work_package(self, package_id: str) -> dict | None:
        pkg = await self._store.get_work_package(package_id)
        return pkg.model_dump() if pkg else None

    async def list_work_packages(self, run_id: str) -> list[dict]:
        return [p.model_dump() for p in await self._store.list_work_packages(run_id)]

    async def list_artifacts(self, run_id: str) -> list[dict]:
        return [a.model_dump() for a in await self._store.list_artifacts(run_id)]


# ---------------------------------------------------------------------------
# Phase dispatch table
# ---------------------------------------------------------------------------

def _build_phase_dispatch(
    adapted_store: _StoreAdapter,
    scheduler: AsyncAIScheduler,
    runtime: FileRuntime,
    artifacts: Any,
) -> dict[JobType, Any]:
    """Lazily import and instantiate all seven phase classes."""
    from dev_orchestrator.v7.phases.architecture import ArchitecturePhase
    from dev_orchestrator.v7.phases.implementation import ImplementationPhase
    from dev_orchestrator.v7.phases.integration import IntegrationPhase
    from dev_orchestrator.v7.phases.planning import PlanningPhase
    from dev_orchestrator.v7.phases.quality import QualityPhase
    from dev_orchestrator.v7.phases.release import ReleasePhase
    from dev_orchestrator.v7.phases.requirements import RequirementsPhase

    return {
        JobType.requirements_analysis: RequirementsPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.architecture_design: ArchitecturePhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
        JobType.package_planning: PlanningPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.code_generation: ImplementationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
        JobType.test_generation: ImplementationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
        JobType.security_review: ImplementationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
        JobType.integration: IntegrationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.code_review: IntegrationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.quality: QualityPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
        JobType.release_notes: ReleasePhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.release_candidate: ReleasePhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.repair: ImplementationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
    }


# ---------------------------------------------------------------------------
# Checkpoint -> next job-type mapping  (mirrors kernel.CHECKPOINT_TO_ACTION)
# ---------------------------------------------------------------------------

_CHECKPOINT_NEXT_JOB: dict[str, JobType | None] = {
    "run_created": JobType.requirements_analysis,
    "requirements_completed": JobType.architecture_design,
    "architecture_completed": JobType.package_planning,
    "package_planning_completed": None,  # wave execution is handled separately
    "wave_queued": None,
    "package_completed": None,
    "wave_completed": JobType.integration,
    "integration_completed": JobType.code_review,
    "review_completed": JobType.quality,
    "quality_completed": JobType.release_notes,
    "release_notes_completed": JobType.release_candidate,
    "release_candidate_completed": None,  # terminal: release_ready
}

_TERMINAL_STATUSES = frozenset({
    RunStatus.completed, RunStatus.cancelled,
    RunStatus.release_ready, RunStatus.rolled_back,
})


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

class PipelineOrchestrator:
    """Central coordinator for the V7 build pipeline.

    Responsibilities (and nothing more):
    * Dispatch a ``Job`` to the correct ``PhaseHandler``.
    * Create projects and runs.
    * Advance a run to the next phase after a job completes.
    * Assemble mission state for the frontend.
    """

    def __init__(
        self,
        store: AbstractStore,
        scheduler: AsyncAIScheduler,
        runtime: FileRuntime,
        artifacts: Any,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self.runtime = runtime
        self.artifacts = artifacts
        self.config: dict[str, Any] = config or {}
        self.log = get_logger("pipeline")
        self._adapted_store = _StoreAdapter(store, artifacts)
        self._phases = _build_phase_dispatch(
            self._adapted_store, scheduler, runtime, artifacts,
        )

    # ------------------------------------------------------------------
    # Job dispatch
    # ------------------------------------------------------------------

    async def execute_job(self, job: Job) -> dict[str, Any]:
        """Dispatch *job* to the appropriate phase handler.

        Raises ``ValueError`` if the job type has no registered handler.
        """
        handler = self._phases.get(job.job_type)
        if handler is None:
            raise ValueError(f"unsupported job type: {job.job_type}")
        self.log.info(
            "pipeline.dispatch",
            job_type=job.job_type.value,
            role=job.role.value,
            job_id=job.id,
        )

        # Fetch run and project context for the phase handler
        run = await self.store.get_run(job.run_id)
        if not run:
            raise ValueError(f"run not found: {job.run_id}")
        project = await self.store.get_project(run.project_id)
        if not project:
            raise ValueError(f"project not found: {run.project_id}")

        # Convert to dicts for phase handler compatibility
        job_dict = job.model_dump()
        run_dict = run.model_dump()
        project_dict = project.model_dump()
        tenant_id = job.tenant_id

        result = await handler.execute(job_dict, run_dict, project_dict, tenant_id=tenant_id)

        # After phase execution, advance the run
        await self._advance_after_job(job, result)
        return result

    async def _advance_after_job(self, job: Job, result: dict[str, Any]) -> None:
        """Advance run state after a job completes."""
        status = result.get("status", "")
        checkpoint_map = {
            JobType.requirements_analysis: "requirements_completed",
            JobType.architecture_design: "architecture_completed",
            JobType.package_planning: "package_planning_completed",
            JobType.integration: "integration_completed",
            JobType.code_review: "review_completed",
            JobType.quality: "quality_completed",
            JobType.release_notes: "release_notes_completed",
            JobType.release_candidate: "release_candidate_completed",
        }
        new_checkpoint = checkpoint_map.get(job.job_type)
        if new_checkpoint and status == "ok":
            await self._update_run_state(job.run_id, checkpoint=new_checkpoint)

        # Retry failed phases if attempts remain
        if status not in ("ok", "completed", ""):
            if job.attempts < job.max_attempts:
                self.log.info("pipeline.phase_retry", run_id=job.run_id, job_type=job.job_type.value,
                              status=status, attempts=job.attempts, max_attempts=job.max_attempts)
                run = await self.store.get_run(job.run_id)
                if run:
                    retry_job = await self._enqueue_phase_job(run, job.job_type, is_retry=True)
                    if retry_job:
                        self.log.info("pipeline.retry_enqueued_confirmed", run_id=job.run_id,
                                      retry_job_id=retry_job.id, retry_status=retry_job.status.value,
                                      retry_resume_key=retry_job.resume_key)
                    else:
                        self.log.error("pipeline.retry_enqueue_failed", run_id=job.run_id, job_type=job.job_type.value)
                else:
                    self.log.error("pipeline.retry_run_not_found", run_id=job.run_id)
                return  # skip advance_run — retry job will re-enter this path
            else:
                self.log.warning("pipeline.phase_blocked", run_id=job.run_id, job_type=job.job_type.value,
                                 status=status, error=result.get("error", ""))
                await self._update_run_state(job.run_id, status=RunStatus.blocked)
                return

        await self.advance_run(job.run_id)

    # ------------------------------------------------------------------
    # Project creation
    # ------------------------------------------------------------------

    async def create_project(
        self,
        tenant_id: str,
        name: str,
        title: str,
        description: str,
        target_scale: str,
        project_path: str,
    ) -> Project:
        """Create a new project with the resolved scale profile."""
        profile = resolve_scale_profile({"target_scale": target_scale})
        project = Project(
            tenant_id=tenant_id,
            name=name,
            title=title,
            description=description,
            project_path=project_path,
            config=ProjectConfig(
                name=name,
                title=title,
                description=description,
                project_path=project_path,
                target_scale=target_scale,
                scale_profile=ScaleProfileData(**profile),
            ),
        )
        return await self.store.create_project(tenant_id, project)

    # ------------------------------------------------------------------
    # Run creation
    # ------------------------------------------------------------------

    async def create_run(
        self,
        tenant_id: str,
        project_id: str,
        requirements_text: str,
    ) -> Run:
        """Create a new run and enqueue the first job (requirements analysis)."""
        project = await self.store.get_project(project_id)
        if not project:
            raise ValueError(f"project not found: {project_id}")

        profile = project.config.scale_profile or ScaleProfileData()
        metadata = RunMetadata(
            requirements_text=requirements_text,
            scale_profile=profile,
            project_config=project.config,
        )
        run = await self.store.create_run(tenant_id, project_id, metadata)

        # Emit run_created event
        await self.store.add_event(Event(
            tenant_id=tenant_id,
            run_id=run.id,
            event_type="run_created",
            payload={"project_id": project_id, "scale_profile": profile.model_dump()},
        ))

        # Enqueue the first job
        job = Job(
            tenant_id=tenant_id,
            run_id=run.id,
            job_type=JobType.requirements_analysis,
            role=PackageRole.requirements,
            resume_key=f"{run.id}:requirements_analysis",
            max_attempts=scale_job_attempts(
                "requirements_analysis",
                profile.model_dump(),
            ),
        )
        await self.store.enqueue_job(tenant_id, job)

        # Transition run to running
        await self._update_run_state(run.id, status=RunStatus.running)
        self.log.info("pipeline.run_created", run_id=run.id, project_id=project_id)
        return await self.store.get_run(run.id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Run advancement (state machine)
    # ------------------------------------------------------------------

    async def advance_run(self, run_id: str) -> None:
        """Check run state after a job completes and enqueue the next phase."""
        run = await self.store.get_run(run_id)
        if not run:
            self.log.warning("advance_run.run_not_found", run_id=run_id)
            return

        if run.status in _TERMINAL_STATUSES:
            self.log.debug("advance_run.terminal", run_id=run_id, status=run.status.value)
            return

        checkpoint = run.checkpoint
        self.log.info("advance_run.checkpoint", run_id=run_id, checkpoint=checkpoint)

        # Wave execution path
        if checkpoint in ("package_planning_completed", "wave_queued", "package_completed"):
            await self._advance_wave_execution(run)
            return

        # Release-ready terminal
        if checkpoint == "release_candidate_completed":
            await self._update_run_state(run_id, status=RunStatus.release_ready, checkpoint="release_ready")
            self.log.info("pipeline.release_ready", run_id=run_id)
            return

        # Sequential phases: checkpoint -> next job type
        next_map: dict[str, tuple[JobType, PackageRole]] = {
            "run_created": (JobType.requirements_analysis, PackageRole.requirements),
            "requirements_completed": (JobType.architecture_design, PackageRole.architect),
            "architecture_completed": (JobType.package_planning, PackageRole.planner),
            "wave_completed": (JobType.integration, PackageRole.integration),
            "integration_completed": (JobType.code_review, PackageRole.review),
            "review_completed": (JobType.quality, PackageRole.qa),
            "quality_completed": (JobType.release_notes, PackageRole.release),
            "release_notes_completed": (JobType.release_candidate, PackageRole.release),
        }
        target = next_map.get(checkpoint)
        if target:
            job_type, role = target
            await self._enqueue_phase_job(run, job_type)
        else:
            self.log.warning("advance_run.unhandled_checkpoint", run_id=run_id, checkpoint=checkpoint)

    # ------------------------------------------------------------------
    # Mission state (frontend)
    # ------------------------------------------------------------------

    async def mission_state(self, run_id: str) -> dict[str, Any]:
        """Build comprehensive mission state for the frontend dashboard."""
        run = await self.store.get_run(run_id)
        if not run:
            return {"error": f"run not found: {run_id}"}

        project = await self.store.get_project(run.project_id)
        if not project:
            return {"error": f"project not found: {run.project_id}"}

        jobs = await self.store.list_jobs(run_id=run_id)
        waves = await self.store.list_waves(run_id)
        packages = await self.store.list_work_packages(run_id)
        artifacts = await self.store.list_artifacts(run_id)
        events = await self.store.list_events(run_id)
        provider_health = await self.store.provider_health_snapshot()

        # Build checkpoint resume plan
        resume_plan = build_checkpoint_resume_plan(
            run=run.model_dump(),
            events=[e.model_dump() for e in events],
            jobs=[j.model_dump() for j in jobs],
            waves=[w.model_dump() for w in waves],
            packages=[p.model_dump() for p in packages],
        )

        # Build mission state via the mission module
        state = build_mission_state(
            run=run.model_dump(),
            project=project.model_dump(),
            jobs=[j.model_dump() for j in jobs],
            waves=[w.model_dump() for w in waves],
            packages=[p.model_dump() for p in packages],
            artifacts=[a.model_dump() for a in artifacts],
            provider_health={k: v.model_dump() for k, v in provider_health.items()},
            events=[e.model_dump() for e in events],
        )
        state["resume_plan"] = resume_plan
        return state

    # ------------------------------------------------------------------
    # Run state update helper
    # ------------------------------------------------------------------

    async def _update_run_state(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        checkpoint: str | None = None,
        continuation: ContinuationState | None = None,
    ) -> Run:
        """Update run state fields and emit a state transition event."""
        # Capture previous state for diffing
        previous_run = await self.store.get_run(run_id)
        previous_snapshot = previous_run.model_dump() if previous_run else {}

        fields: dict[str, Any] = {}
        if status is not None:
            fields["status"] = status
        if checkpoint is not None:
            fields["checkpoint"] = checkpoint
        if continuation is not None:
            fields["continuation"] = continuation

        if fields:
            await self.store.update_run(run_id, **fields)

        # Build and emit state transition event
        current_run = await self.store.get_run(run_id)
        current_snapshot = current_run.model_dump() if current_run else {}
        event_payload = build_state_transition_event(previous_snapshot, current_snapshot)
        if event_payload:
            await self.store.add_event(Event(
                tenant_id=current_snapshot.get("tenant_id", ""),
                run_id=run_id,
                event_type="mission_state_changed",
                payload=event_payload,
            ))
        return current_run  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Wave execution helpers
    # ------------------------------------------------------------------

    async def _advance_wave_execution(self, run: Run) -> None:
        """Advance wave-based execution: enqueue jobs for the next ready wave.

        Respects intra-wave depends_on: a package is only enqueued when all
        packages it depends on (within the same wave) have status=completed.
        """
        packages = await self.store.list_work_packages(run.id)
        waves = await self.store.list_waves(run.id)

        if not waves:
            self.log.warning("advance_run.no_waves", run_id=run.id)
            return

        # Build lookup: package_key -> WorkPackage for dependency resolution
        pkg_by_key: dict[str, WorkPackage] = {}
        for p in packages:
            pkg_by_key[p.package_key] = p

        # Find the first wave whose packages are not all completed
        for wave in sorted(waves, key=lambda w: w.sequence):
            wave_packages = [p for p in packages if p.wave_id == wave.id]
            if not wave_packages:
                continue
            all_done = all(p.status == JobStatus.completed for p in wave_packages)
            if all_done:
                continue

            existing_jobs = await self.store.list_jobs(run_id=run.id)
            enqueued_any = False

            # Only enqueue packages whose dependencies are satisfied
            for pkg in wave_packages:
                if pkg.status == JobStatus.completed:
                    continue

                # Check if already queued/running
                already_queued = any(
                    j.work_package_id == pkg.id
                    and j.status in (JobStatus.queued, JobStatus.leased, JobStatus.running, JobStatus.retry)
                    for j in existing_jobs
                )
                if already_queued:
                    continue

                # Check intra-wave dependencies
                deps = pkg.depends_on or []
                deps_satisfied = True
                for dep_key in deps:
                    dep_pkg = pkg_by_key.get(dep_key)
                    if dep_pkg and dep_pkg.wave_id == wave.id and dep_pkg.status != JobStatus.completed:
                        deps_satisfied = False
                        break

                if not deps_satisfied:
                    self.log.debug("pipeline.package_waiting_on_deps",
                                   run_id=run.id, package_key=pkg.package_key,
                                   deps=deps)
                    continue

                job_type = self._package_job_type(pkg.role)
                canonical_role = canonical_worker_role(
                    pkg.role.value, domain=pkg.domain,
                )
                job = Job(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    job_type=job_type,
                    role=PackageRole(canonical_role),
                    work_package_id=pkg.id,
                    wave_id=wave.id,
                    resume_key=f"{run.id}:{pkg.id}:{job_type.value}",
                    max_attempts=scale_job_attempts(
                        job_type.value,
                        run.metadata.scale_profile.model_dump(),
                    ),
                )
                await self.store.enqueue_job(run.tenant_id, job)
                enqueued_any = True
                self.log.info(
                    "pipeline.wave_job_enqueued",
                    run_id=run.id,
                    wave_id=wave.id,
                    package_id=pkg.id,
                    job_type=job_type.value,
                )

            # Mark wave as in-progress if we enqueued anything
            if enqueued_any:
                await self.store.upsert_wave(run.id, Wave(
                    id=wave.id, run_id=run.id,
                    wave_key=wave.wave_key, sequence=wave.sequence,
                    status="running",
                ))
                await self._update_run_state(
                    run.id,
                    checkpoint="wave_queued",
                    continuation=ContinuationState(
                        next_action="wave_execution",
                        checkpoint="wave_queued",
                        current_wave=wave.wave_key,
                    ),
                )
            return  # Only advance one wave at a time

        # All waves complete
        await self._update_run_state(
            run.id,
            checkpoint="wave_completed",
            continuation=ContinuationState(
                next_action="integration",
                checkpoint="wave_completed",
            ),
        )

    @staticmethod
    def _package_job_type(role: PackageRole) -> JobType:
        """Map a package role to the appropriate job type."""
        if role == PackageRole.security:
            return JobType.security_review
        if role in (PackageRole.qa,):
            return JobType.test_generation
        if role == PackageRole.repair:
            return JobType.repair
        return JobType.code_generation

    # ------------------------------------------------------------------
    # Phase job enqueue helper
    # ------------------------------------------------------------------

    async def _enqueue_phase_job(self, run: Run, job_type: JobType, *, is_retry: bool = False) -> Job | None:
        """Create and enqueue a single job for a sequential phase."""
        role = self._phase_default_role(job_type)
        # Use unique resume_key for retries to avoid idempotency conflict with the still-running original
        suffix = f":retry:{new_id()[:8]}" if is_retry else ""
        job = Job(
            tenant_id=run.tenant_id,
            run_id=run.id,
            job_type=job_type,
            role=role,
            resume_key=f"{run.id}:{job_type.value}{suffix}",
            max_attempts=scale_job_attempts(
                job_type.value,
                run.metadata.scale_profile.model_dump(),
            ),
        )
        enqueued = await self.store.enqueue_job(run.tenant_id, job)
        self.log.info(
            "pipeline.phase_job_enqueued",
            run_id=run.id,
            job_type=job_type.value,
            role=role.value,
            job_id=enqueued.id,
            status=enqueued.status.value,
            resume_key=enqueued.resume_key,
        )
        return enqueued

    @staticmethod
    def _phase_default_role(job_type: JobType) -> PackageRole:
        """Return the default PackageRole for a sequential phase job."""
        mapping = {
            JobType.requirements_analysis: PackageRole.requirements,
            JobType.architecture_design: PackageRole.architect,
            JobType.package_planning: PackageRole.planner,
            JobType.integration: PackageRole.integration,
            JobType.code_review: PackageRole.review,
            JobType.quality: PackageRole.qa,
            JobType.release_notes: PackageRole.release,
            JobType.release_candidate: PackageRole.release,
            JobType.repair: PackageRole.repair,
        }
        return mapping.get(job_type, PackageRole.backend)
