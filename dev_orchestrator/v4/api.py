from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dev_orchestrator.config import AppConfig
from dev_orchestrator.v4.llm_policy import list_ai_task_budgets
from dev_orchestrator.v4.models import DEFAULT_TENANT, new_id
from dev_orchestrator.v4.service import V4Orchestrator
from dev_orchestrator.v4.stack_packs import list_stack_packs
from dev_orchestrator.v4.system_check import build_v4_system_check
from dev_orchestrator.v4.worker import WorkerStatusRegistry


class ProjectCreate(BaseModel):
    name: str = ""
    title: str = ""
    description: str = ""
    target_scale: str = "small"
    stack_pack: str = "auto"
    deployment_mode: str = ""
    api_only: bool = False
    effective_loc_target: int = 1000
    unattended_mode: str = "off"
    project_path: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class RequirementUpdate(BaseModel):
    requirements_text: str


class RunCreate(BaseModel):
    requirements_text: str = ""


class ProjectBatchDelete(BaseModel):
    project_ids: list[str] = Field(default_factory=list)


def build_app(service: V4Orchestrator, config: AppConfig) -> FastAPI:
    app = FastAPI(title="Dev Orchestrator V4", version="4.0")
    static_root = config.root_dir / "dev_orchestrator" / "static"
    if static_root.exists():
        app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

    @app.get("/")
    def index():
        index_path = static_root / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {"service": "dev-orchestrator-v4", "api": "/api/v4/health"}

    @app.get("/api/v4/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "kernel": "v4", "storage": "postgres", "tenant": service.tenant_id}

    @app.get("/api/v4/system-check")
    def system_check() -> dict[str, Any]:
        return build_v4_system_check(config.root_dir, config.to_dict(), strict_db=False)

    @app.get("/api/v4/stack-packs")
    def stack_packs() -> dict[str, Any]:
        return {"items": list_stack_packs()}

    @app.get("/api/v4/ai-policy")
    def ai_policy() -> dict[str, Any]:
        return {
            "policy": "bounded-ai-task-contract",
            "principles": [
                "AI calls are package-scoped, never whole-project-scoped.",
                "Every AI job has explicit input, output, timeout, reasoning, and retry budgets.",
                "Timeouts produce durable evidence and repair actions instead of blocking the run forever.",
            ],
            "budgets": list_ai_task_budgets(),
        }

    @app.post("/api/v4/projects")
    def create_project(payload: ProjectCreate, request: Request) -> dict[str, Any]:
        tenant_id = request.headers.get(config.identity.tenant_header) or service.tenant_id
        data = payload.model_dump()
        config_payload = dict(data.pop("config") or {})
        for key in ("target_scale", "stack_pack", "deployment_mode", "api_only", "effective_loc_target", "unattended_mode"):
            config_payload[key] = data.pop(key)
        data["config"] = config_payload
        project = service.create_project(data, tenant_id=tenant_id)
        return {"project": project}

    @app.get("/api/v4/projects")
    def list_projects(request: Request) -> dict[str, Any]:
        tenant_id = request.headers.get(config.identity.tenant_header) or service.tenant_id
        return {"items": service.list_projects(tenant_id)}

    @app.get("/api/v4/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        project = service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        return {"project": project}

    @app.delete("/api/v4/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, Any]:
        project = service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        return {"ok": True, "result": service.store.delete_project(project_id)}

    @app.post("/api/v4/projects/batch-delete")
    def batch_delete_projects(payload: ProjectBatchDelete) -> dict[str, Any]:
        if not payload.project_ids:
            raise HTTPException(status_code=400, detail="project_ids is required")
        return {"ok": True, "result": service.store.delete_projects(payload.project_ids)}

    @app.post("/api/v4/projects/{project_id}/requirements")
    def update_requirements(project_id: str, payload: RequirementUpdate) -> dict[str, Any]:
        project = service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        service.store.add_event(project["tenant_id"], project_id, None, "requirements_updated", {"requirements_text": payload.requirements_text})
        return {"ok": True, "project_id": project_id}

    @app.get("/api/v4/projects/{project_id}/stack-decision")
    def stack_decision(project_id: str) -> dict[str, Any]:
        project = service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        from dev_orchestrator.v4.stack_packs import decide_stack_pack

        config_payload = project.get("config") or {}
        decision = decide_stack_pack(
            project.get("description") or project.get("title") or project.get("name"),
            config_payload.get("stack_pack", "auto"),
            config_payload.get("deployment_mode", ""),
            config_payload.get("api_only"),
        )
        return {"project_id": project_id, "decision": decision}

    @app.post("/api/v4/projects/{project_id}/runs")
    def create_run(project_id: str, payload: RunCreate) -> dict[str, Any]:
        project = service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        run = service.create_run(project_id, payload.requirements_text)
        return {"run": run}

    @app.get("/api/v4/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = service.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return {"run": run}

    @app.get("/api/v4/runs/{run_id}/continuation")
    def continuation(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"continuation": service.continuation(run_id)}

    @app.get("/api/v4/runs/{run_id}/jobs")
    def jobs(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"items": service.list_jobs(run_id)}

    @app.get("/api/v4/runs/{run_id}/ai-calls")
    def ai_calls(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"items": [artifact for artifact in service.list_artifacts(run_id) if artifact["kind"] == "agent_run"]}

    @app.get("/api/v4/runs/{run_id}/waves")
    def waves(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"items": service.store.list_waves(run_id)}

    @app.get("/api/v4/runs/{run_id}/packages")
    def packages(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"items": service.store.list_work_packages(run_id)}

    @app.get("/api/v4/runs/{run_id}/wave-report")
    def wave_report(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"items": [artifact for artifact in service.list_artifacts(run_id) if artifact["kind"] == "wave_report"]}

    @app.get("/api/v4/runs/{run_id}/quality-report")
    def quality_report(run_id: str) -> dict[str, Any]:
        return {"report": _latest_payload(service.list_artifacts(run_id), "quality_report")}

    @app.get("/api/v4/runs/{run_id}/context-index")
    def context_index(run_id: str) -> dict[str, Any]:
        return {"snapshot": _latest_payload(service.list_artifacts(run_id), "context_snapshot")}

    @app.get("/api/v4/runs/{run_id}/repair-history")
    def repair_history(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"items": [artifact for artifact in service.list_artifacts(run_id) if artifact["kind"] == "repair_report"]}

    @app.get("/api/v4/runs/{run_id}/artifacts")
    def artifacts(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"items": service.list_artifacts(run_id)}

    @app.get("/api/v4/runs/{run_id}/release-structure")
    def release_structure(run_id: str) -> dict[str, Any]:
        return {"report": _latest_payload(service.list_artifacts(run_id), "release_structure_report")}

    @app.get("/api/v4/runs/{run_id}/deploy-guide")
    def deploy_guide(run_id: str) -> dict[str, Any]:
        return {"guide": _latest_payload(service.list_artifacts(run_id), "deploy_guide")}

    @app.get("/api/v4/runs/{run_id}/browser-smoke")
    def browser_smoke(run_id: str) -> dict[str, Any]:
        return {"report": _latest_payload(service.list_artifacts(run_id), "browser_smoke_report")}

    @app.post("/api/v4/runs/{run_id}/pause")
    def pause(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"run": service.pause_run(run_id)}

    @app.post("/api/v4/runs/{run_id}/resume")
    def resume(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"run": service.resume_run(run_id)}

    @app.post("/api/v4/runs/{run_id}/requeue-blocked")
    def requeue_blocked(run_id: str) -> dict[str, Any]:
        run = service.get_run(run_id) or _missing_run()
        continuation = dict(run.get("continuation") or {})
        continuation.update({"failure_reason": "", "next_action": "claim_pending_jobs"})
        return {"run": service.store.update_run(run_id, status="queued", continuation=continuation)}

    @app.post("/api/v4/runs/{run_id}/repair")
    def repair(run_id: str) -> dict[str, Any]:
        run = service.get_run(run_id) or _missing_run()
        project = service.get_project(run["project_id"])
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        return {"job": service._enqueue_repair_job(project, run, "manual_repair", {"source": "api"})}

    @app.post("/api/v4/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"run": service.cancel_run(run_id)}

    @app.get("/api/v4/workers")
    def workers(limit: int = 12) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        return {"items": WorkerStatusRegistry(service.workspace_root).list(limit=limit), "limit": limit}

    @app.get("/api/v4/dead-letter")
    def dead_letter() -> dict[str, Any]:
        return {"items": service.store.list_jobs(status="dead_letter")}

    @app.post("/api/v4/dead-letter/{job_id}/requeue")
    def requeue_dead_letter(job_id: str) -> dict[str, Any]:
        jobs = [job for job in service.store.list_jobs(status="dead_letter") if job["id"] == job_id]
        if not jobs:
            raise HTTPException(status_code=404, detail="dead-letter job not found")
        job = jobs[0]
        requeued = service.store.enqueue_job(
            job["tenant_id"],
            {
                **job,
                "id": new_id(),
                "status": "queued",
                "attempts": 0,
                "worker_id": "",
                "lease_until": None,
                "heartbeat_at": None,
                "last_error": "",
                "resume_key": f"{job['resume_key']}:manual:{new_id()}",
            },
        )
        if job.get("run_id"):
            run = service.get_run(job["run_id"])
            if run:
                continuation = dict(run.get("continuation") or {})
                continuation.update({"failure_reason": "", "next_action": "claim_pending_jobs"})
                service.store.update_run(job["run_id"], status="queued", continuation=continuation)
        return {"ok": True, "job_id": job_id, "requeued_job_id": requeued["id"]}

    @app.post("/api/v4/release-candidates/{candidate_id}/apply")
    def apply(candidate_id: str) -> dict[str, Any]:
        return {"job": service.enqueue_apply(candidate_id)}

    @app.post("/api/v4/release-candidates/{candidate_id}/rollback")
    def rollback(candidate_id: str) -> dict[str, Any]:
        return {"job": service.enqueue_rollback(candidate_id)}

    return app


def _missing_run() -> None:
    raise HTTPException(status_code=404, detail="run not found")


def _latest_payload(artifacts: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [artifact for artifact in artifacts if artifact["kind"] == kind]
    if not matches:
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    return matches[-1].get("payload") or {}


def run_api_server(service: V4Orchestrator, config: AppConfig, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(build_app(service, config), host=host, port=port)
