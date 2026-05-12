"""V8 Pipeline Orchestrator — central coordinator with Bug 5 fix.

Bug 5 fix: _advance_after_job now treats only "ok" and "completed" as success;
an empty-string status is no longer silently bypassed.
"""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v8.context import build_context_snapshot, package_context
from dev_orchestrator.v8.kernel import build_checkpoint_resume_plan, build_state_transition_event
from dev_orchestrator.v8.memory import build_layered_memory, memory_for_package
from dev_orchestrator.v8.mission import build_mission_state
from dev_orchestrator.v8.models import (
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
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.profiles import resolve_scale_profile, scale_job_attempts
from dev_orchestrator.v8.role_aliases import canonical_worker_role
from dev_orchestrator.v8.runtime import FileRuntime
from dev_orchestrator.v8.scale_inference import (
    choose_larger_scale, infer_scale_from_requirements, infer_scale_from_architecture,
    infer_scale_from_package_plan, merge_inference_history,
)
from dev_orchestrator.v8.scheduler import AsyncAIScheduler
from dev_orchestrator.v8.scheduler import AbstractStore

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

    async def list_artifacts(self, run_id: str, kind: str | None = None) -> list[dict]:
        results = [a.model_dump() for a in await self._store.list_artifacts(run_id, kind=kind)]
        if not results and self._artifacts:
            try:
                fs_artifacts = await self._artifacts.list_artifacts(run_id, kind=kind)
                results = [{"kind": str(p.parent.name), "name": p.name, "path": str(p)} for p in fs_artifacts]
            except Exception:
                pass
        return results

    async def get_run(self, run_id: str) -> Any:
        return await self._store.get_run(run_id)


# ---------------------------------------------------------------------------
# Phase dispatch table
# ---------------------------------------------------------------------------

def _build_phase_dispatch(
    adapted_store: _StoreAdapter,
    scheduler: AsyncAIScheduler,
    runtime: FileRuntime,
    artifacts: Any,
) -> dict[JobType, Any]:
    from dev_orchestrator.v8.phases.architecture import ArchitecturePhase
    from dev_orchestrator.v8.phases.implementation import ImplementationPhase
    from dev_orchestrator.v8.phases.integration import IntegrationPhase
    from dev_orchestrator.v8.phases.planning import PlanningPhase
    from dev_orchestrator.v8.phases.quality import QualityPhase
    from dev_orchestrator.v8.phases.release import ReleasePhase
    from dev_orchestrator.v8.phases.requirements import RequirementsPhase

    impl = ImplementationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime)
    return {
        JobType.requirements_analysis: RequirementsPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.architecture_design: ArchitecturePhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
        JobType.package_planning: PlanningPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.code_generation: impl,
        JobType.test_generation: impl,
        JobType.security_review: impl,
        JobType.integration: IntegrationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.code_review: IntegrationPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.quality: QualityPhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store, runtime=runtime),
        JobType.release_notes: ReleasePhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.release_candidate: ReleasePhase(scheduler=scheduler, store=adapted_store, artifacts=adapted_store),
        JobType.repair: impl,
    }


# ---------------------------------------------------------------------------
# Checkpoint -> next job-type mapping
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = frozenset({
    RunStatus.completed, RunStatus.cancelled,
    RunStatus.release_ready, RunStatus.rolled_back,
})


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

class PipelineOrchestrator:
    """Central coordinator for the V8 build pipeline."""

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
        self._phases = _build_phase_dispatch(self._adapted_store, scheduler, runtime, artifacts)

    # ------------------------------------------------------------------
    # Job dispatch
    # ------------------------------------------------------------------

    async def execute_job(self, job: Job) -> dict[str, Any]:
        handler = self._phases.get(job.job_type)
        if handler is None:
            raise ValueError(f"unsupported job type: {job.job_type}")
        self.log.info("pipeline.dispatch", job_type=job.job_type.value, role=job.role.value, job_id=job.id)

        run = await self.store.get_run(job.run_id)
        if not run:
            raise ValueError(f"run not found: {job.run_id}")
        project = await self.store.get_project(run.project_id)
        if not project:
            raise ValueError(f"project not found: {run.project_id}")

        # Pass Pydantic models directly — phase handlers use _get_metadata() helper
        job_dict = job.model_dump()
        run_dict = run.model_dump()
        project_dict = project.model_dump()
        tenant_id = job.tenant_id

        result = await handler.execute(job_dict, run_dict, project_dict, tenant_id=tenant_id)
        await self._advance_after_job(job, result)
        return result

    async def _advance_after_job(self, job: Job, result: dict[str, Any]) -> None:
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

        if status == "ok":
            await self._persist_phase_result(job, result)

        if job.work_package_id and status == "ok" and job.job_type in (
            JobType.code_generation, JobType.test_generation, JobType.security_review,
        ):
            try:
                await self.store.update_work_package(job.work_package_id, status=JobStatus.completed)
                self.log.info("pipeline.package_completed", run_id=job.run_id, package_id=job.work_package_id)
            except Exception as exc:
                self.log.warning("pipeline.package_status_update_failed",
                                 run_id=job.run_id, package_id=job.work_package_id, error=str(exc))

        # Bug 5 fix: "" is no longer treated as success — only "ok" and "completed" advance
        if status not in ("ok", "completed"):
            error_kind = result.get("error_kind", "")
            if error_kind == "circuit_open":
                self.log.warning("pipeline.phase_circuit_blocked", run_id=job.run_id,
                                 job_type=job.job_type.value, error=result.get("error", ""))
                await self._update_run_state(job.run_id, status=RunStatus.blocked)
                return

            run = await self.store.get_run(job.run_id)
            if not run:
                self.log.error("pipeline.retry_run_not_found", run_id=job.run_id)
                return

            metadata = run.metadata
            retry_counts = dict(metadata.retry_counts) if metadata.retry_counts else {}
            phase_key = job.job_type.value
            current_retries = retry_counts.get(phase_key, 0)
            max_retries = job.max_attempts - 1

            if current_retries < max_retries:
                retry_counts[phase_key] = current_retries + 1
                new_metadata = metadata.model_copy(update={"retry_counts": retry_counts})
                await self.store.update_run(job.run_id, metadata=new_metadata)
                self.log.info("pipeline.phase_retry", run_id=job.run_id, job_type=job.job_type.value,
                              status=status, retry=current_retries + 1, max_retries=max_retries)
                retry_job = await self._enqueue_phase_job(run, job.job_type, is_retry=True)
                if retry_job:
                    self.log.info("pipeline.retry_enqueued_confirmed", run_id=job.run_id,
                                  retry_job_id=retry_job.id)
                else:
                    self.log.error("pipeline.retry_enqueue_failed", run_id=job.run_id, job_type=job.job_type.value)
                return
            else:
                self.log.warning("pipeline.phase_blocked", run_id=job.run_id, job_type=job.job_type.value,
                                 status=status, error=result.get("error", ""),
                                 retries=current_retries, max_retries=max_retries)
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
        profile = resolve_scale_profile({"target_scale": target_scale})
        project = Project(
            tenant_id=tenant_id,
            name=name,
            title=title,
            description=description,
            project_path=project_path,
            config=ProjectConfig(
                name=name, title=title, description=description,
                project_path=project_path, target_scale=target_scale,
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

        await self.store.add_event(Event(
            tenant_id=tenant_id,
            run_id=run.id,
            event_type="run_created",
            payload={"project_id": project_id, "scale_profile": profile.model_dump()},
        ))

        job = Job(
            tenant_id=tenant_id,
            run_id=run.id,
            job_type=JobType.requirements_analysis,
            role=PackageRole.requirements,
            resume_key=f"{run.id}:requirements_analysis",
            max_attempts=scale_job_attempts("requirements_analysis", profile.model_dump()),
        )
        await self.store.enqueue_job(tenant_id, job)
        await self._update_run_state(run.id, status=RunStatus.running)
        self.log.info("pipeline.run_created", run_id=run.id, project_id=project_id)
        return await self.store.get_run(run.id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Run advancement (state machine)
    # ------------------------------------------------------------------

    async def advance_run(self, run_id: str) -> None:
        run = await self.store.get_run(run_id)
        if not run:
            self.log.warning("advance_run.run_not_found", run_id=run_id)
            return
        if run.status in _TERMINAL_STATUSES:
            self.log.debug("advance_run.terminal", run_id=run_id, status=run.status.value)
            return

        checkpoint = run.checkpoint
        self.log.info("advance_run.checkpoint", run_id=run_id, checkpoint=checkpoint)

        while checkpoint in ("package_planning_completed", "wave_queued", "package_completed"):
            await self._advance_wave_execution(run)
            run = await self.store.get_run(run_id)
            if not run or run.checkpoint == checkpoint:
                return
            checkpoint = run.checkpoint
            self.log.info("advance_run.checkpoint_updated", run_id=run_id, checkpoint=checkpoint)

        if checkpoint == "release_candidate_completed":
            await self._update_run_state(run_id, status=RunStatus.release_ready, checkpoint="release_ready")
            await self._complete_pending_waves(run_id)
            self.log.info("pipeline.release_ready", run_id=run_id)
            return

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
            job_type, _ = target
            await self._enqueue_phase_job(run, job_type)
        else:
            self.log.warning("advance_run.unhandled_checkpoint", run_id=run_id, checkpoint=checkpoint)

    # ------------------------------------------------------------------
    # Mission state (frontend)
    # ------------------------------------------------------------------

    async def mission_state(self, run_id: str) -> dict[str, Any]:
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

        resume_plan = build_checkpoint_resume_plan(
            run=run.model_dump(),
            events=[e.model_dump() for e in events],
            jobs=[j.model_dump() for j in jobs],
            waves=[w.model_dump() for w in waves],
            packages=[p.model_dump() for p in packages],
        )
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
    # Phase result persistence
    # ------------------------------------------------------------------

    async def _complete_pending_waves(self, run_id: str) -> None:
        try:
            waves = await self.store.list_waves(run_id)
            for wave in waves:
                if wave.status not in ("completed", "done"):
                    await self.store.upsert_wave(run_id, Wave(
                        id=wave.id, run_id=run_id,
                        wave_key=wave.wave_key, sequence=wave.sequence, status="completed",
                    ))
            if waves:
                self.log.info("pipeline.waves_completed", run_id=run_id, count=len(waves))
        except Exception as exc:
            self.log.warning("pipeline.complete_pending_waves_failed", run_id=run_id, error=str(exc))

    async def _persist_phase_result(self, job: Job, result: dict[str, Any]) -> None:
        # V8: canonical metadata keys match what phase handlers return
        _PHASE_METADATA_KEYS = {
            JobType.requirements_analysis: "requirements",
            JobType.architecture_design: "architecture",
            JobType.package_planning: "package_plan",
        }
        meta_key = _PHASE_METADATA_KEYS.get(job.job_type)
        if not meta_key:
            return
        # Try canonical key first (V8 phases), then V7 compat variants
        data = (
            result.get(meta_key)
            or result.get(f"{meta_key}_analysis")
            or result.get(f"{meta_key}_design")
            or result.get("package_dag")
        )
        if data is None:
            return
        try:
            run = await self.store.get_run(job.run_id)
            if not run:
                return
            metadata = run.metadata.model_copy(update={meta_key: data})

            # Scale inference: auto-upgrade profile based on phase output
            if run.metadata.scale_profile and run.metadata.scale_profile.name in ("", "auto", "medium"):
                inference: dict[str, Any] | None = None
                if job.job_type == JobType.requirements_analysis:
                    inference = infer_scale_from_requirements(
                        data, run.metadata.requirements_text or ""
                    )
                elif job.job_type == JobType.architecture_design:
                    inference = infer_scale_from_architecture(
                        data, result.get("project_layout") or {}
                    )
                elif job.job_type == JobType.package_planning:
                    inference = infer_scale_from_package_plan(data)

                if inference:
                    inferred = inference.get("selected_scale", "")
                    current = run.metadata.scale_profile.name
                    upgraded = choose_larger_scale(current, inferred)
                    if upgraded != current:
                        new_profile = ScaleProfileData(**resolve_scale_profile({"target_scale": upgraded}))
                        history = merge_inference_history(
                            getattr(run.metadata, "scale_inference_history", None), inference
                        )
                        metadata = metadata.model_copy(update={
                            "scale_profile": new_profile,
                            "scale_inference_history": history,
                        })
                        self.log.info("pipeline.scale_upgraded", run_id=job.run_id,
                                      from_scale=current, to_scale=upgraded,
                                      stage=inference.get("stage", ""))

            await self.store.update_run(job.run_id, metadata=metadata)
            self.log.info("pipeline.phase_result_persisted", run_id=job.run_id,
                          job_type=job.job_type.value, meta_key=meta_key)
        except Exception as exc:
            self.log.warning("pipeline.persist_phase_result_failed", run_id=job.run_id,
                             job_type=job.job_type.value, error=str(exc))

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
        packages = await self.store.list_work_packages(run.id)
        waves = await self.store.list_waves(run.id)

        if not waves:
            self.log.warning("advance_run.no_waves", run_id=run.id)
            await self._update_run_state(
                run.id,
                checkpoint="wave_completed",
                continuation=ContinuationState(next_action="integration", checkpoint="wave_completed"),
            )
            return

        pkg_by_key: dict[str, WorkPackage] = {p.package_key: p for p in packages}

        for wave in sorted(waves, key=lambda w: w.sequence):
            wave_packages = [p for p in packages if p.wave_id == wave.id]
            if not wave_packages:
                continue
            if all(p.status == JobStatus.completed for p in wave_packages):
                continue

            existing_jobs = await self.store.list_jobs(run_id=run.id)
            enqueued_any = False

            for pkg in wave_packages:
                if pkg.status == JobStatus.completed:
                    continue
                already_queued = any(
                    j.work_package_id == pkg.id
                    and j.status in (JobStatus.queued, JobStatus.leased, JobStatus.running, JobStatus.retry)
                    for j in existing_jobs
                )
                if already_queued:
                    continue

                deps_satisfied = all(
                    pkg_by_key.get(dep_key) is None
                    or pkg_by_key[dep_key].wave_id != wave.id
                    or pkg_by_key[dep_key].status == JobStatus.completed
                    for dep_key in (pkg.depends_on or [])
                )
                if not deps_satisfied:
                    self.log.debug("pipeline.package_waiting_on_deps",
                                   run_id=run.id, package_key=pkg.package_key, deps=pkg.depends_on)
                    continue

                job_type = self._package_job_type(pkg.role)
                canonical_role = canonical_worker_role(pkg.role.value, domain=pkg.domain)
                job = Job(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    job_type=job_type,
                    role=PackageRole(canonical_role),
                    work_package_id=pkg.id,
                    wave_id=wave.id,
                    resume_key=f"{run.id}:{pkg.id}:{job_type.value}",
                    max_attempts=scale_job_attempts(job_type.value, run.metadata.scale_profile.model_dump()),
                )
                await self.store.enqueue_job(run.tenant_id, job)
                enqueued_any = True
                self.log.info("pipeline.wave_job_enqueued", run_id=run.id, wave_id=wave.id,
                              package_id=pkg.id, job_type=job_type.value)

            if enqueued_any:
                await self.store.upsert_wave(run.id, Wave(
                    id=wave.id, run_id=run.id,
                    wave_key=wave.wave_key, sequence=wave.sequence, status="running",
                ))
                await self._update_run_state(
                    run.id,
                    checkpoint="wave_queued",
                    continuation=ContinuationState(
                        next_action="wave_execution", checkpoint="wave_queued",
                        current_wave=wave.wave_key,
                    ),
                )
            return

        await self._update_run_state(
            run.id,
            checkpoint="wave_completed",
            continuation=ContinuationState(next_action="integration", checkpoint="wave_completed"),
        )

    @staticmethod
    def _package_job_type(role: PackageRole) -> JobType:
        if role == PackageRole.security:
            return JobType.security_review
        if role == PackageRole.qa:
            return JobType.test_generation
        if role == PackageRole.repair:
            return JobType.repair
        return JobType.code_generation

    async def _enqueue_phase_job(self, run: Run, job_type: JobType, *, is_retry: bool = False) -> Job | None:
        role = self._phase_default_role(job_type)
        suffix = f":retry:{new_id()[:8]}" if is_retry else ""
        job = Job(
            tenant_id=run.tenant_id,
            run_id=run.id,
            job_type=job_type,
            role=role,
            resume_key=f"{run.id}:{job_type.value}{suffix}",
            max_attempts=scale_job_attempts(job_type.value, run.metadata.scale_profile.model_dump()),
        )
        enqueued = await self.store.enqueue_job(run.tenant_id, job)
        self.log.info("pipeline.phase_job_enqueued", run_id=run.id, job_type=job_type.value,
                      role=role.value, job_id=enqueued.id)
        return enqueued

    @staticmethod
    def _phase_default_role(job_type: JobType) -> PackageRole:
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
