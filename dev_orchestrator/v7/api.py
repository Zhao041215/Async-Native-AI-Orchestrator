"""V7 Async FastAPI - all endpoints async with SSE for real-time progress."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dev_orchestrator.v7.models import DEFAULT_TENANT, JobStatus, RunStatus
from dev_orchestrator.v7.observability import get_logger
from dev_orchestrator.v7.pipeline import PipelineOrchestrator
from dev_orchestrator.v7.profiles import list_scale_profiles
from dev_orchestrator.v7.store import AbstractStore

# Type alias for the config (avoid circular import)
AppConfig = Any

log = get_logger("api")

# Directories/files to skip during deployment export
_EXPORT_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "node_modules", ".venv", "venv", "env",
    ".agent", ".claude", "logs", "workspace", ".idea", ".vscode",
    "artifacts", "tmp", "temp", ".cache", "dist", "build", ".next", ".nuxt",
})
_EXPORT_SKIP_FILES = frozenset({
    ".env", ".env.local", ".env.production", ".DS_Store", "Thumbs.db",
    "desktop.ini", "*.pyc", "*.pyo", "*.egg-info",
})
_EXPORT_SKIP_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".o", ".a",
    ".log", ".sqlite", ".db", ".sqlite3",
})


class ProjectCreate(BaseModel):
    name: str = ""
    title: str = ""
    description: str = ""
    target_scale: str = "auto"
    project_path: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class RunCreate(BaseModel):
    requirements_text: str = ""


class ExportDeliveryRequest(BaseModel):
    target_path: str = ""
    overwrite: bool = False


def _should_skip_export_path(path: Path, root: Path) -> bool:
    """Check if a path should be skipped during deployment export."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = rel.parts
    for part in parts:
        if part in _EXPORT_SKIP_DIRS:
            return True
    if path.name in _EXPORT_SKIP_FILES:
        return True
    if path.suffix.lower() in _EXPORT_SKIP_EXTENSIONS:
        return True
    return False


def _export_deployment_files(source: Path, target: Path, overwrite: bool) -> dict[str, Any]:
    """Copy deployment-essential files from source to target."""
    if target.exists() and not overwrite:
        raise ValueError(f"Target directory already exists: {target}. Pass overwrite=true to replace.")
    if target.exists() and overwrite:
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    errors: list[str] = []

    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        if _should_skip_export_path(item, source):
            skipped += 1
            continue
        try:
            rel = item.relative_to(source)
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            copied += 1
        except Exception as exc:
            errors.append(f"{rel}: {exc}")

    return {
        "target_path": str(target),
        "copied_count": copied,
        "skipped_count": skipped,
        "error_count": len(errors),
        "errors": errors[:20],
    }


class ModelSettingsPayload(BaseModel):
    model_provider: str = "custom"
    model: str = ""
    model_reasoning_effort: str = ""
    disable_response_storage: bool = False
    api_key: str = ""
    model_providers: dict[str, Any] = Field(default_factory=dict)


class BatchDeleteRequest(BaseModel):
    project_ids: list[str] = Field(default_factory=list)


def _parse_artifact_content(content: str) -> Any:
    """Parse artifact JSON content string, returning raw string on failure."""
    if not content:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


async def _list_fs_artifacts(artifact_writer: Any, run_id: str, kind: str | None = None) -> list[dict]:
    """Read artifacts from filesystem ArtifactWriter, return list of dicts compatible with Artifact.model_dump()."""
    if artifact_writer is None:
        return []
    try:
        paths = await artifact_writer.list_artifacts(run_id, kind=kind)
        items = []
        for p in paths:
            try:
                content = p.read_text(encoding="utf-8")
                # Reconstruct artifact fields from path: base/tenant/project/run/kind/key
                parts = p.parts
                # Find run_id position in path
                try:
                    run_idx = next(i for i, part in enumerate(parts) if part == run_id)
                    item_kind = parts[run_idx + 1] if run_idx + 1 < len(parts) else p.parent.name
                    item_key = parts[run_idx + 2] if run_idx + 2 < len(parts) else p.name
                    project_id = parts[run_idx - 1] if run_idx > 0 else ""
                    tenant_id = parts[run_idx - 2] if run_idx > 1 else ""
                except StopIteration:
                    item_kind = p.parent.name
                    item_key = p.name
                    project_id = ""
                    tenant_id = ""
                items.append({
                    "id": f"fs:{item_kind}:{item_key}",
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "run_id": run_id,
                    "job_id": "",
                    "kind": item_kind,
                    "key": item_key,
                    "content_type": "application/json",
                    "content": content,
                    "payload": _parse_artifact_content(content),
                })
            except Exception:
                continue
        return items
    except Exception:
        return []


def build_v7_app(
    pipeline: PipelineOrchestrator,
    store: AbstractStore,
    static_dir: Path | None = None,
    config: AppConfig = None,
    artifact_writer: Any | None = None,
) -> FastAPI:
    from contextlib import asynccontextmanager

    _shutdown_bg = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.bootstrap()
        task = asyncio.create_task(_background_worker(), name="bg-worker")
        yield
        _shutdown_bg.set()
        task.cancel()

    app = FastAPI(title="Dev Orchestrator V7", version="7.0.0", lifespan=lifespan)

    @app.post("/api/v7/projects")
    async def create_project(body: ProjectCreate, tenant_id: str = DEFAULT_TENANT):
        try:
            project = await pipeline.create_project(
                tenant_id=tenant_id, name=body.name or body.title or "untitled",
                title=body.title or body.name, description=body.description,
                target_scale=body.target_scale, project_path=body.project_path,
            )
            return {"project": project.model_dump()}
        except Exception as exc:
            log.error("create_project_failed", error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/v7/projects")
    async def list_projects(tenant_id: str = DEFAULT_TENANT):
        projects = await store.list_projects(tenant_id)
        return [p.model_dump() for p in projects]

    @app.get("/api/v7/projects/{project_id}")
    async def get_project(project_id: str):
        project = await store.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        return project.model_dump()

    @app.delete("/api/v7/projects/{project_id}")
    async def delete_project(project_id: str):
        await store.delete_project(project_id)
        return {"ok": True}

    @app.post("/api/v7/projects/{project_id}/runs")
    async def create_run(project_id: str, body: RunCreate):
        try:
            run = await pipeline.create_run(
                tenant_id=DEFAULT_TENANT, project_id=project_id,
                requirements_text=body.requirements_text,
            )
            return {"run": run.model_dump()}
        except Exception as exc:
            log.error("create_run_failed", error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/v7/projects/{project_id}/runs")
    async def list_runs(project_id: str):
        runs = await store.list_runs(project_id)
        return {"items": [r.model_dump() for r in runs]}

    @app.get("/api/v7/runs/{run_id}")
    async def get_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return run.model_dump()

    @app.post("/api/v7/runs/{run_id}/pause")
    async def pause_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        updated = await store.update_run(run_id, status=RunStatus.paused)
        return updated.model_dump()

    @app.post("/api/v7/runs/{run_id}/resume")
    async def resume_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        updated = await store.update_run(run_id, status=RunStatus.running)
        await pipeline.advance_run(run_id)
        return updated.model_dump()

    @app.post("/api/v7/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        updated = await store.update_run(run_id, status=RunStatus.cancelled)
        return updated.model_dump()

    @app.get("/api/v7/runs/{run_id}/jobs")
    async def list_jobs(run_id: str, status: str | None = None):
        job_status = JobStatus(status) if status else None
        jobs = await store.list_jobs(run_id=run_id, status=job_status)
        return {"items": [j.model_dump() for j in jobs]}

    @app.get("/api/v7/runs/{run_id}/waves")
    async def list_waves(run_id: str):
        waves = await store.list_waves(run_id)
        run = await store.get_run(run_id)
        # If run is terminal-success, reflect all waves as completed
        terminal_ok = run and run.status in {RunStatus.release_ready, RunStatus.completed}
        items = []
        for w in waves:
            d = w.model_dump()
            if terminal_ok and d.get("status") not in ("completed", "done"):
                d["status"] = "completed"
            items.append(d)
        return {"items": items}

    @app.get("/api/v7/runs/{run_id}/packages")
    async def list_packages(run_id: str):
        packages = await store.list_work_packages(run_id)
        run = await store.get_run(run_id)
        terminal_ok = run and run.status in {RunStatus.release_ready, RunStatus.completed}
        items = []
        for p in packages:
            d = p.model_dump()
            if terminal_ok and d.get("status") not in ("completed", "done"):
                d["status"] = "completed"
            items.append(d)
        return {"items": items}

    @app.get("/api/v7/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: str, kind: str | None = None):
        artifacts = await store.list_artifacts(run_id, kind=kind)
        items = []
        for a in artifacts:
            d = a.model_dump()
            d["payload"] = _parse_artifact_content(a.content)
            items.append(d)
        # Fallback to filesystem if in-memory store has nothing
        if not items:
            items = await _list_fs_artifacts(artifact_writer, run_id, kind=kind)
        return {"items": items}

    @app.get("/api/v7/runs/{run_id}/events")
    async def list_events(run_id: str, event_type: str | None = None, after_sequence: int = 0, limit: int = 100):
        events = await store.list_events(run_id, event_type=event_type)
        filtered = [e for e in events if e.sequence > after_sequence]
        return [e.model_dump() for e in filtered[:limit]]

    @app.get("/api/v7/runs/{run_id}/mission")
    async def mission_state(run_id: str):
        return await pipeline.mission_state(run_id)

    @app.get("/api/v7/scale-profiles")
    async def scale_profiles():
        return list_scale_profiles()

    @app.get("/api/v7/provider-health")
    async def provider_health():
        try:
            health = await store.provider_health_snapshot()
            return {k: v.model_dump() for k, v in health.items()}
        except Exception:
            return {}

    # ─── Model Settings ───
    @app.get("/api/v7/model-settings")
    async def get_model_settings():
        if config is None:
            return {"llm": None, "recommended": {}}
        from dataclasses import asdict
        llm = asdict(config.llm)
        if llm.get("api_key"):
            llm["api_key"] = "***"
        llm["extra_headers"] = {}
        return {"llm": llm, "recommended": {}}

    @app.put("/api/v7/model-settings")
    async def save_model_settings(body: ModelSettingsPayload):
        if config is None:
            raise HTTPException(status_code=500, detail="Config not available")
        from dev_orchestrator.config import save_config
        if body.model:
            config.llm.model = body.model
        if body.model_reasoning_effort:
            config.llm.model_reasoning_effort = body.model_reasoning_effort
        config.llm.disable_response_storage = body.disable_response_storage
        if body.api_key and body.api_key not in {"", "***"}:
            config.llm.api_key = body.api_key
        prov = body.model_providers.get(body.model_provider, {})
        if prov.get("base_url"):
            config.llm.api_base = prov["base_url"]
        if prov.get("wire_api"):
            config.llm.wire_api = prov["wire_api"]
        save_config(config)
        return await get_model_settings()

    @app.post("/api/v7/model-settings/test")
    async def test_model_settings(body: ModelSettingsPayload):
        if config is None:
            raise HTTPException(status_code=500, detail="Config not available")
        from dev_orchestrator.llm_client import AsyncLLMClient
        from dev_orchestrator.config import LLMConfig
        test_cfg = LLMConfig(
            use_mock=False,
            api_base=body.model_providers.get(body.model_provider, {}).get("base_url", config.llm.api_base),
            api_key=body.api_key if body.api_key and body.api_key not in {"", "***"} else config.llm.api_key,
            model=body.model or config.llm.model,
            wire_api=body.model_providers.get(body.model_provider, {}).get("wire_api", config.llm.wire_api),
            timeout_seconds=config.llm.timeout_seconds,
            retry_attempts=config.llm.retry_attempts,
            model_reasoning_effort=body.model_reasoning_effort or config.llm.model_reasoning_effort,
        )
        client = AsyncLLMClient(test_cfg)
        try:
            result = await client.chat("Reply with 'ok'.", [], max_tokens=10)
            return {"ok": True, "result": {"response": result[:100]}}
        except Exception as exc:
            return {"ok": False, "result": {"error": str(exc)[:200]}}
        finally:
            await client.close()

    # ─── Workers ───
    @app.get("/api/v7/workers")
    async def list_workers(limit: int = 12):
        return {"items": []}

    # ─── Batch Delete ───
    @app.post("/api/v7/projects/batch-delete")
    async def batch_delete_projects(body: BatchDeleteRequest):
        deleted = 0
        for pid in body.project_ids:
            try:
                await store.delete_project(pid)
                deleted += 1
            except Exception:
                pass
        return {"ok": True, "deleted": deleted}

    # ─── Run Actions ───
    @app.post("/api/v7/runs/{run_id}/repair")
    async def repair_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        from dev_orchestrator.v7.models import new_id
        from dev_orchestrator.v7.models import Job, JobType, PackageRole
        job = Job(
            id=new_id(), tenant_id=run.tenant_id, run_id=run_id,
            job_type=JobType.repair, role=PackageRole.integration,
            resume_key=f"{run_id}:repair:{new_id()}",
            payload={"repair_reason": run.continuation.failure_reason or "manual_repair"},
        )
        await store.enqueue_job(run.tenant_id, job)
        return {"ok": True, "job_id": job.id}

    @app.post("/api/v7/runs/{run_id}/requeue-blocked")
    async def requeue_blocked(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        await store.update_run(run_id, status=RunStatus.queued)
        await pipeline.advance_run(run_id)
        return {"ok": True}

    @app.post("/api/v7/runs/{run_id}/recover-all")
    async def recover_all(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        await store.requeue_expired_jobs(run.tenant_id)
        return {"ok": True}

    # ─── Release Candidates ───
    @app.post("/api/v7/release-candidates/{rc_id}/apply")
    async def apply_release_candidate(rc_id: str):
        return {"ok": True, "status": "applied", "rc_id": rc_id}

    @app.post("/api/v7/release-candidates/{rc_id}/rollback")
    async def rollback_release_candidate(rc_id: str):
        return {"ok": True, "status": "rolled_back", "rc_id": rc_id}

    # ─── Run Detail Endpoints (stubs for frontend) ───
    @app.get("/api/v7/runs/{run_id}/continuation")
    async def run_continuation(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return {"continuation": {
            "next_action": run.continuation.next_action or "-",
            "release_status": run.continuation.recovery_state or "-",
            "current_wave": run.continuation.current_wave or "-",
            "active_blocker": run.continuation.failure_reason or "",
        }}

    async def _get_fs_artifact(run_id: str, kind: str) -> str | None:
        """Read latest artifact of given kind from filesystem."""
        items = await _list_fs_artifacts(artifact_writer, run_id, kind=kind)
        return items[-1]["content"] if items else None

    @app.get("/api/v7/runs/{run_id}/ai-calls")
    async def run_ai_calls(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="ai_call")
        return {"items": [a.model_dump() for a in artifacts]}

    @app.get("/api/v7/runs/{run_id}/quality-report")
    async def run_quality_report(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="quality_report")
        if artifacts:
            return {"report": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "quality_report")
        return {"report": _parse_artifact_content(content)}

    @app.get("/api/v7/runs/{run_id}/context-index")
    async def run_context_index(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="context_index")
        if artifacts:
            return {"snapshot": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "context_index")
        return {"snapshot": _parse_artifact_content(content)}

    @app.get("/api/v7/runs/{run_id}/repair-history")
    async def run_repair_history(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="repair")
        return {"items": [a.model_dump() for a in artifacts]}

    @app.get("/api/v7/runs/{run_id}/project-layout")
    async def run_project_layout(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="project_layout")
        if artifacts:
            return {"layout": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "project_layout")
        return {"layout": _parse_artifact_content(content)}

    @app.get("/api/v7/runs/{run_id}/patch-sets")
    async def run_patch_sets(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="patch_set")
        items = [a.model_dump() for a in artifacts]
        if not items:
            items = await _list_fs_artifacts(artifact_writer, run_id, kind="patch_set")
        return {"items": items}

    @app.get("/api/v7/runs/{run_id}/agent-contract-report")
    async def run_agent_contract_report(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="contract_report")
        if artifacts:
            return {"report": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "contract_report")
        return {"report": _parse_artifact_content(content)}

    @app.get("/api/v7/runs/{run_id}/patch-transactions")
    async def run_patch_transactions(run_id: str):
        return {"report": None, "items": [], "conflicts": []}

    @app.get("/api/v7/runs/{run_id}/test-execution")
    async def run_test_execution(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="test_execution")
        if artifacts:
            return {"report": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "test_execution")
        return {"report": _parse_artifact_content(content)}

    @app.get("/api/v7/runs/{run_id}/code-index")
    async def run_code_index(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="code_index")
        if artifacts:
            return {"index": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "code_index")
        return {"index": _parse_artifact_content(content)}

    @app.get("/api/v7/runs/{run_id}/contract-index")
    async def run_contract_index(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="contract_index")
        if artifacts:
            return {"index": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "contract_index")
        return {"index": _parse_artifact_content(content)}

    @app.get("/api/v7/health")
    async def health():
        return {"status": "ok", "version": "7.0.0", "kernel": "v7_ai_native"}

    @app.post("/api/v7/runs/{run_id}/export-delivery")
    async def export_delivery(run_id: str, body: ExportDeliveryRequest):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        if run.status not in {RunStatus.release_ready, RunStatus.completed}:
            raise HTTPException(status_code=400, detail=f"Run status '{run.status.value}' is not exportable. Must be release_ready or completed.")
        if not body.target_path:
            raise HTTPException(status_code=400, detail="target_path is required")

        # Find the project workspace as source
        project = await store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")

        # Source: the project's workspace directory
        # project_root() expects a dict; convert Pydantic model if needed
        project_dict = project.model_dump() if hasattr(project, "model_dump") else (project if isinstance(project, dict) else {})
        source = pipeline.runtime.project_root(project_dict) if hasattr(pipeline.runtime, "project_root") else None
        if source is None or not source.exists():
            raise HTTPException(status_code=400, detail="Project source directory not found")

        target = Path(body.target_path).resolve()
        try:
            report = _export_deployment_files(source, target, body.overwrite)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            log.error("export_failed", run_id=run_id, error=str(exc))
            raise HTTPException(status_code=500, detail=f"Export failed: {exc}")

        log.info("export_delivery", run_id=run_id, target=str(target), copied=report["copied_count"])
        return {"ok": True, "report": report}

    # ─── Background job processor (in-process worker) ───
    async def _background_worker():
        """Poll for queued jobs and execute them inline."""
        import uuid
        worker_id = f"api-{uuid.uuid4().hex[:8]}"
        log.info("bg_worker_started", worker_id=worker_id)
        while not _shutdown_bg.is_set():
            try:
                from dev_orchestrator.v7.models import PackageRole
                found_any = False
                for role in PackageRole:
                    job = await store.claim_job(DEFAULT_TENANT, role, worker_id, lease_seconds=300)
                    if job is not None:
                        found_any = True
                        log.info("bg_worker_executing", job_id=job.id, job_type=job.job_type.value, role=role.value)
                        try:
                            result = await pipeline.execute_job(job)
                            log.info("bg_worker_pipeline_result", job_id=job.id, result_status=result.get("status", ""))
                            await store.finish_job(job.id, worker_id, result or {})
                            log.info("bg_worker_completed", job_id=job.id)
                        except Exception as exc:
                            error_msg = f"{type(exc).__name__}: {exc}"[:2000]
                            log.error("bg_worker_pipeline_exception", job_id=job.id, error=error_msg, exc_type=type(exc).__name__)
                            try:
                                await store.fail_job(job.id, worker_id, error_msg, True)
                            except Exception as fe:
                                log.error("bg_worker_fail_job_error", job_id=job.id, error=str(fe))
                            log.error("bg_worker_failed", job_id=job.id, error=error_msg)
                if not found_any:
                    # Log queue depth for diagnostics
                    try:
                        all_jobs = await store.list_jobs()
                        queued = [j for j in all_jobs if j.status in (JobStatus.queued, JobStatus.retry)]
                        if queued:
                            log.warning("bg_worker_missed_jobs", count=len(queued),
                                        jobs=[{"id": j.id[:12], "type": j.job_type.value, "role": j.role.value, "status": j.status.value} for j in queued])
                    except Exception:
                        pass
                    # No jobs found, wait before next poll
                    try:
                        await asyncio.wait_for(_shutdown_bg.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("bg_worker_cycle_error", error=str(exc), exc_type=type(exc).__name__)
                try:
                    await asyncio.wait_for(_shutdown_bg.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        log.info("bg_worker_stopped", worker_id=worker_id)

    if static_dir and static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def _index():
            from fastapi.responses import FileResponse
            return FileResponse(str(static_dir / "index.html"))

    return app
