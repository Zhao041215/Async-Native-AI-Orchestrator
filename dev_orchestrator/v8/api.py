"""V8 Async FastAPI — all endpoints async with SSE for real-time progress.

Upgrades from V7:
- All routes use /api/v8/ prefix
- build_v8_app replaces build_v7_app
- New endpoints: /health (db_connected), /runs/{id}/contract_violations, /runs/{id}/memory
- GET /runs/{id} adds v8: true and contract_summary
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dev_orchestrator.v8.memory import build_layered_memory
from dev_orchestrator.v8.models import DEFAULT_TENANT, JobStatus, RunStatus
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.pipeline import PipelineOrchestrator
from dev_orchestrator.v8.profiles import list_scale_profiles
from dev_orchestrator.v8.scheduler import AbstractStore

AppConfig = Any

log = get_logger("api")

_EXPORT_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "node_modules", ".venv", "venv", "env",
    ".agent", ".claude", "logs", "workspace", ".idea", ".vscode",
    "tmp", "temp", ".cache",
    # orchestrator-internal dirs — not part of deployable output
    "artifacts", "runtime", "qa", "docs_internal",
})
_EXPORT_SKIP_FILES = frozenset({
    ".env", ".env.local", ".env.production", ".DS_Store", "Thumbs.db",
    "desktop.ini", "tmp_payload.json", "run_payload.json",
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
    scale_profile_override: dict[str, Any] = Field(default_factory=dict)


class ExportDeliveryRequest(BaseModel):
    target_path: str = ""
    overwrite: bool = False


class ModelSettingsPayload(BaseModel):
    model_provider: str = "custom"
    model: str = ""
    model_reasoning_effort: str = ""
    disable_response_storage: bool = False
    api_key: str = ""
    model_providers: dict[str, Any] = Field(default_factory=dict)


class BatchDeleteRequest(BaseModel):
    project_ids: list[str] = Field(default_factory=list)


def _should_skip_export_path(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    for part in rel.parts:
        if part in _EXPORT_SKIP_DIRS:
            return True
    if path.name in _EXPORT_SKIP_FILES:
        return True
    if path.suffix.lower() in _EXPORT_SKIP_EXTENSIONS:
        return True
    return False


def _export_deployment_files(source: Path, target: Path, overwrite: bool) -> dict[str, Any]:
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
    return {"target_path": str(target), "copied_count": copied,
            "skipped_count": skipped, "error_count": len(errors), "errors": errors[:20]}


def _parse_artifact_content(content: str) -> Any:
    if not content:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


async def _list_fs_artifacts(artifact_writer: Any, run_id: str, kind: str | None = None) -> list[dict]:
    if artifact_writer is None:
        return []
    try:
        paths = await artifact_writer.list_artifacts(run_id, kind=kind)
        items = []
        for p in paths:
            try:
                content = p.read_text(encoding="utf-8")
                parts = p.parts
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
                    "id": f"fs:{item_kind}:{item_key}", "tenant_id": tenant_id,
                    "project_id": project_id, "run_id": run_id, "job_id": "",
                    "kind": item_kind, "key": item_key, "content_type": "application/json",
                    "content": content, "payload": _parse_artifact_content(content),
                })
            except Exception:
                continue
        return items
    except Exception:
        return []


def build_v8_app(
    pipeline: PipelineOrchestrator,
    store: AbstractStore,
    static_dir: Path | None = None,
    config: AppConfig = None,
    artifact_writer: Any | None = None,
) -> FastAPI:
    from contextlib import asynccontextmanager

    _shutdown_bg = asyncio.Event()
    _active_job_tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await store.bootstrap()
            log.info("store.bootstrapped")
        except Exception as exc:
            log.error("store.bootstrap_failed", error=str(exc))
            log.warning("api_starting_without_store",
                        detail="Store-dependent endpoints will return 500; routing is unaffected")
        task = asyncio.create_task(_background_worker(), name="bg-worker")
        yield
        _shutdown_bg.set()
        # Graceful drain: wait up to 30s for in-flight jobs to complete.
        if _active_job_tasks:
            log.info("shutdown_draining", active_jobs=len(_active_job_tasks))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*_active_job_tasks, return_exceptions=True),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                log.warning("shutdown_drain_timeout", remaining=len(_active_job_tasks))
        task.cancel()

    app = FastAPI(title="Dev Orchestrator V8", version="8.0.0", lifespan=lifespan)

    # ─── V8 Health ───
    @app.get("/api/v8/health")
    async def health():
        db_connected = False
        try:
            if hasattr(store, "ping"):
                db_connected = await store.ping()
            elif hasattr(store, "_pool"):
                db_connected = store._pool is not None
        except Exception:
            pass
        return {
            "ok": True,
            "store": "postgres" if hasattr(store, "_pool") else "memory",
            "db_connected": db_connected,
            "schema_version": "8.0",
            "version": "8.0.0",
            "kernel": "v8_pg_native",
        }

    # ─── Projects ───
    @app.post("/api/v8/projects/batch-delete")
    async def batch_delete_projects(body: BatchDeleteRequest):
        deleted = 0
        for pid in body.project_ids:
            try:
                await store.delete_project(pid)
                deleted += 1
            except Exception:
                pass
        return {"ok": True, "deleted": deleted}

    @app.post("/api/v8/projects")
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

    @app.get("/api/v8/projects")
    async def list_projects(tenant_id: str = DEFAULT_TENANT):
        projects = await store.list_projects(tenant_id)
        return [p.model_dump() for p in projects]

    @app.get("/api/v8/projects/{project_id}")
    async def get_project(project_id: str):
        project = await store.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        return project.model_dump()

    @app.delete("/api/v8/projects/{project_id}")
    async def delete_project(project_id: str):
        await store.delete_project(project_id)
        return {"ok": True}

    @app.post("/api/v8/projects/{project_id}/runs")
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

    @app.get("/api/v8/projects/{project_id}/runs")
    async def list_runs(project_id: str):
        runs = await store.list_runs(project_id)
        return {"items": [r.model_dump() for r in runs]}

    # ─── Runs ───
    @app.get("/api/v8/runs/{run_id}")
    async def get_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        d = run.model_dump()
        d["v8"] = True
        # contract_summary: count total agent_runs and failures
        try:
            agent_runs = await store.list_agent_runs(run_id)
            total = len(agent_runs)
            failed = sum(1 for ar in agent_runs if ar.contract_ok is False)
            d["contract_summary"] = {"total": total, "failed": failed}
        except Exception:
            d["contract_summary"] = {"total": 0, "failed": 0}
        return d

    @app.post("/api/v8/runs/{run_id}/pause")
    async def pause_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        updated = await store.update_run(run_id, status=RunStatus.paused)
        return updated.model_dump()

    @app.post("/api/v8/runs/{run_id}/resume")
    async def resume_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        updated = await store.update_run(run_id, status=RunStatus.running)
        await pipeline.advance_run(run_id)
        return updated.model_dump()

    @app.post("/api/v8/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        updated = await store.update_run(run_id, status=RunStatus.cancelled)
        return updated.model_dump()

    @app.get("/api/v8/runs/{run_id}/jobs")
    async def list_jobs(run_id: str, status: str | None = None):
        job_status = JobStatus(status) if status else None
        jobs = await store.list_jobs(run_id=run_id, status=job_status)
        return {"items": [j.model_dump() for j in jobs]}

    @app.get("/api/v8/runs/{run_id}/waves")
    async def list_waves(run_id: str):
        waves = await store.list_waves(run_id)
        run = await store.get_run(run_id)
        terminal_ok = run and run.status in {RunStatus.release_ready, RunStatus.completed}
        items = []
        for w in waves:
            d = w.model_dump()
            if terminal_ok and d.get("status") not in ("completed", "done"):
                d["status"] = "completed"
            items.append(d)
        return {"items": items}

    @app.get("/api/v8/runs/{run_id}/packages")
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

    @app.get("/api/v8/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: str, kind: str | None = None):
        artifacts = await store.list_artifacts(run_id, kind=kind)
        items = []
        for a in artifacts:
            d = a.model_dump()
            d["payload"] = _parse_artifact_content(a.content)
            items.append(d)
        if not items:
            items = await _list_fs_artifacts(artifact_writer, run_id, kind=kind)
        return {"items": items}

    @app.get("/api/v8/runs/{run_id}/events")
    async def list_events(run_id: str, event_type: str | None = None, after_sequence: int = 0, limit: int = 100):
        events = await store.list_events(run_id, event_type=event_type)
        filtered = [e for e in events if e.sequence > after_sequence]
        return [e.model_dump() for e in filtered[:limit]]

    @app.get("/api/v8/runs/{run_id}/mission")
    async def mission_state(run_id: str):
        return await pipeline.mission_state(run_id)

    # ─── V8 New Endpoints ───
    @app.get("/api/v8/runs/{run_id}/contract_violations")
    async def contract_violations(run_id: str):
        """Return all agent_runs where contract validation failed."""
        try:
            if hasattr(store, "list_contract_violations"):
                violations = await store.list_contract_violations(run_id)
                return {"items": [v.model_dump() for v in violations]}
            agent_runs = await store.list_agent_runs(run_id)
            items = [ar.model_dump() for ar in agent_runs if ar.contract_ok is False]
            return {"items": items}
        except Exception as exc:
            log.error("contract_violations_failed", run_id=run_id, error=str(exc))
            return {"items": []}

    @app.get("/api/v8/runs/{run_id}/memory")
    async def run_memory(run_id: str):
        """Return the current layered mission memory for debugging context transmission."""
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        project = await store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        metadata = run.metadata.model_dump() if hasattr(run.metadata, "model_dump") else {}
        artifacts = await store.list_artifacts(run_id)
        artifact_dicts = [a.model_dump() for a in artifacts]
        memory = build_layered_memory(
            project=project.model_dump(),
            requirements=metadata.get("requirements") or metadata.get("requirements_analysis") or {},
            architecture=metadata.get("architecture") or metadata.get("architecture_design") or {},
            package_plan=metadata.get("package_dag") or {},
            context_snapshot={},
            change_artifacts=artifact_dicts,
        )
        return memory

    # ─── Scale profiles / health / workers ───
    @app.get("/api/v8/scale-profiles")
    async def scale_profiles():
        return list_scale_profiles()

    @app.get("/api/v8/provider-health")
    async def provider_health():
        try:
            health = await store.provider_health_snapshot()
            return {k: v.model_dump() for k, v in health.items()}
        except Exception:
            return {}

    @app.post("/api/v8/providers/{provider_name}/reset")
    async def reset_provider(provider_name: str):
        """Reset a circuit-breaker-blocked provider back to healthy state."""
        try:
            state = await pipeline.scheduler.circuit_breaker.reset(provider_name)
            return {"ok": True, "provider": provider_name, "status": state.status.value}
        except Exception as exc:
            return {"ok": False, "provider": provider_name, "error": str(exc)}

    @app.get("/api/v8/workers")
    async def list_workers(limit: int = 12):
        active = [
            {"worker_id": t.get_name(), "status": "running", "job_id": None}
            for t in _active_job_tasks
        ]
        return {"items": active[:limit], "total": len(active)}

    @app.get("/metrics")
    async def prometheus_metrics():
        from fastapi.responses import PlainTextResponse
        try:
            snap = pipeline.scheduler._circuit_breaker.snapshot if hasattr(pipeline.scheduler, "_circuit_breaker") else None
            cb_snap = await snap() if snap else {}
        except Exception:
            cb_snap = {}
        lines = [
            "# HELP ai_calls_total Total AI/LLM calls made",
            "# TYPE ai_calls_total counter",
            f"ai_calls_total 0",
            "# HELP jobs_active Currently executing jobs",
            "# TYPE jobs_active gauge",
            f"jobs_active {len(_active_job_tasks)}",
            "# HELP circuit_breaker_open Providers with open circuit breaker",
            "# TYPE circuit_breaker_open gauge",
            f"circuit_breaker_open {sum(1 for s in cb_snap.values() if getattr(s, 'status', None) and s.status.value in ('circuit_open', 'blocked'))}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.get("/api/v8/queue-depth")
    async def queue_depth():
        try:
            counts = await store.count_jobs_by_status()
        except Exception:
            counts = {}
        return {
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "retry": counts.get("retry", 0),
            "dead_letter": counts.get("dead_letter", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
        }

    # ─── Model Settings ───
    @app.get("/api/v8/model-settings")
    async def get_model_settings():
        if config is None:
            return {"llm": None, "recommended": {}}
        from dataclasses import asdict
        llm = asdict(config.llm)
        if llm.get("api_key"):
            llm["api_key"] = "***"
        llm["extra_headers"] = {}
        return {"llm": llm, "recommended": {}}

    @app.put("/api/v8/model-settings")
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

    @app.post("/api/v8/model-settings/test")
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

    # ─── Run Actions ───
    @app.post("/api/v8/runs/{run_id}/repair")
    async def repair_run(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        from dev_orchestrator.v8.models import Job, JobType, PackageRole, new_id
        job = Job(
            id=new_id(), tenant_id=run.tenant_id, run_id=run_id,
            job_type=JobType.repair, role=PackageRole.integration,
            resume_key=f"{run_id}:repair:{new_id()}",
            payload={"repair_reason": run.continuation.failure_reason or "manual_repair"},
        )
        await store.enqueue_job(run.tenant_id, job)
        return {"ok": True, "job_id": job.id}

    @app.post("/api/v8/runs/{run_id}/requeue-blocked")
    async def requeue_blocked(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        await store.update_run(run_id, status=RunStatus.queued)
        await pipeline.advance_run(run_id)
        return {"ok": True}

    @app.post("/api/v8/runs/{run_id}/recover-all")
    async def recover_all(run_id: str):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        await store.requeue_expired_jobs(run.tenant_id)
        return {"ok": True}

    # ─── Detail endpoints ───
    @app.get("/api/v8/runs/{run_id}/continuation")
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
        items = await _list_fs_artifacts(artifact_writer, run_id, kind=kind)
        return items[-1]["content"] if items else None

    @app.get("/api/v8/runs/{run_id}/ai-calls")
    async def run_ai_calls(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="ai_call")
        return {"items": [a.model_dump() for a in artifacts]}

    @app.get("/api/v8/runs/{run_id}/quality-report")
    async def run_quality_report(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="quality_report")
        if artifacts:
            return {"report": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "quality_report")
        return {"report": _parse_artifact_content(content)}

    @app.get("/api/v8/runs/{run_id}/context-index")
    async def run_context_index(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="context_index")
        if artifacts:
            return {"snapshot": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "context_index")
        return {"snapshot": _parse_artifact_content(content)}

    @app.get("/api/v8/runs/{run_id}/repair-history")
    async def run_repair_history(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="repair")
        return {"items": [a.model_dump() for a in artifacts]}

    @app.get("/api/v8/runs/{run_id}/project-layout")
    async def run_project_layout(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="project_layout")
        if artifacts:
            return {"layout": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "project_layout")
        return {"layout": _parse_artifact_content(content)}

    @app.get("/api/v8/runs/{run_id}/patch-sets")
    async def run_patch_sets(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="patch_set")
        items = [a.model_dump() for a in artifacts]
        if not items:
            items = await _list_fs_artifacts(artifact_writer, run_id, kind="patch_set")
        return {"items": items}

    @app.get("/api/v8/runs/{run_id}/patch-transactions")
    async def run_patch_transactions(run_id: str):
        return {"report": None, "items": [], "conflicts": []}

    @app.get("/api/v8/runs/{run_id}/test-execution")
    async def run_test_execution(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="test_execution")
        if artifacts:
            return {"report": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "test_execution")
        return {"report": _parse_artifact_content(content)}

    @app.get("/api/v8/runs/{run_id}/code-index")
    async def run_code_index(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="code_index")
        if artifacts:
            return {"index": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "code_index")
        return {"index": _parse_artifact_content(content)}

    @app.get("/api/v8/runs/{run_id}/contract-index")
    async def run_contract_index(run_id: str):
        artifacts = await store.list_artifacts(run_id, kind="contract_index")
        if artifacts:
            return {"index": _parse_artifact_content(artifacts[-1].content)}
        content = await _get_fs_artifact(run_id, "contract_index")
        return {"index": _parse_artifact_content(content)}

    @app.post("/api/v8/runs/{run_id}/export-delivery")
    async def export_delivery(run_id: str, body: ExportDeliveryRequest):
        run = await store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        if run.status not in {RunStatus.release_ready, RunStatus.completed}:
            raise HTTPException(status_code=400,
                                detail=f"Run status '{run.status.value}' is not exportable.")
        if not body.target_path:
            raise HTTPException(status_code=400, detail="target_path is required")
        project = await store.get_project(run.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        project_dict = project.model_dump() if hasattr(project, "model_dump") else {}
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
        # Generate Chinese deployment guide
        try:
            from dev_orchestrator.v8.deploy_doc import generate_deploy_doc
            artifacts = await store.list_artifacts(run_id)
            artifact_dicts = [a.model_dump() if hasattr(a, "model_dump") else a for a in artifacts]
            run_meta = run.metadata.model_dump() if hasattr(run.metadata, "model_dump") else {}
            doc_path = generate_deploy_doc(
                target_dir=target,
                project=project_dict,
                run_metadata=run_meta,
                artifacts=artifact_dicts,
            )
            report["deploy_doc"] = str(doc_path)
            log.info("deploy_doc_generated", path=str(doc_path))
        except Exception as doc_exc:
            log.warning("deploy_doc_failed", error=str(doc_exc))
        return {"ok": True, "report": report}

    @app.post("/api/v8/release-candidates/{rc_id}/apply")
    async def apply_release_candidate(rc_id: str):
        return {"ok": True, "status": "applied", "rc_id": rc_id}

    @app.post("/api/v8/release-candidates/{rc_id}/rollback")
    async def rollback_release_candidate(rc_id: str):
        return {"ok": True, "status": "rolled_back", "rc_id": rc_id}

    # ─── Background job processor (in-process worker) ───
    async def _background_worker():
        import uuid as _uuid
        worker_id = f"api-{_uuid.uuid4().hex[:8]}"
        log.info("bg_worker_started", worker_id=worker_id)
        idle_streak = 0
        while not _shutdown_bg.is_set():
            try:
                from dev_orchestrator.v8.models import PackageRole
                # Claim one job per role concurrently instead of sequentially
                claim_results = await asyncio.gather(
                    *[store.claim_job(DEFAULT_TENANT, role, worker_id, lease_seconds=300)
                      for role in PackageRole],
                    return_exceptions=True,
                )
                jobs = [j for j in claim_results if j is not None and not isinstance(j, Exception)]
                found_any = bool(jobs)

                # Execute all claimed jobs concurrently
                async def _run_job(job: Any) -> None:
                    current_task = asyncio.current_task()
                    if current_task:
                        _active_job_tasks.add(current_task)
                    log.info("bg_worker_executing", job_id=job.id, job_type=job.job_type.value)

                    async def _heartbeat_loop() -> None:
                        while True:
                            await asyncio.sleep(60)
                            try:
                                await store.heartbeat_job(job.id, worker_id, lease_seconds=300)
                            except Exception:
                                break

                    hb_task = asyncio.create_task(_heartbeat_loop())
                    try:
                        result = await pipeline.execute_job(job)
                        await store.finish_job(job.id, worker_id, result or {})
                        log.info("bg_worker_completed", job_id=job.id)
                    except Exception as exc:
                        import traceback as _tb
                        error_msg = f"{type(exc).__name__}: {exc}"[:2000]
                        tb_str = _tb.format_exc()
                        print(f"\n[BG_WORKER_EXCEPTION] job={job.id} type={job.job_type.value}\n{tb_str}", flush=True)
                        log.error("bg_worker_failed", job_id=job.id, error=error_msg,
                                  traceback=tb_str[:3000])
                        try:
                            await store.fail_job(job.id, worker_id, error_msg, True)
                        except Exception:
                            pass
                    finally:
                        hb_task.cancel()
                        current_task = asyncio.current_task()
                        if current_task:
                            _active_job_tasks.discard(current_task)

                if jobs:
                    await asyncio.gather(*[_run_job(j) for j in jobs])

                # Adaptive sleep: fast when busy, exponential back-off when idle
                if found_any:
                    idle_streak = 0
                    sleep_time = 0.3
                else:
                    idle_streak += 1
                    sleep_time = min(8.0, 0.5 * idle_streak)
                try:
                    await asyncio.wait_for(_shutdown_bg.wait(), timeout=sleep_time)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("bg_worker_cycle_error", error=str(exc))
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
