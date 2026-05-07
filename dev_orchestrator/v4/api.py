from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dev_orchestrator.config import AppConfig
from dev_orchestrator.v4.models import DEFAULT_TENANT
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

    @app.post("/api/v4/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict[str, Any]:
        service.get_run(run_id) or _missing_run()
        return {"run": service.cancel_run(run_id)}

    @app.get("/api/v4/workers")
    def workers() -> dict[str, Any]:
        return {"items": WorkerStatusRegistry(service.workspace_root).list()}

    @app.get("/api/v4/dead-letter")
    def dead_letter() -> dict[str, Any]:
        return {"items": service.store.list_jobs(status="dead_letter")}

    @app.post("/api/v4/dead-letter/{job_id}/requeue")
    def requeue_dead_letter(job_id: str) -> dict[str, Any]:
        jobs = [job for job in service.store.list_jobs(status="dead_letter") if job["id"] == job_id]
        if not jobs:
            raise HTTPException(status_code=404, detail="dead-letter job not found")
        job = jobs[0]
        service.store.enqueue_job(job["tenant_id"], {**job, "status": "queued", "resume_key": f"{job['resume_key']}:manual:{job_id}"})
        return {"ok": True, "job_id": job_id}

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
