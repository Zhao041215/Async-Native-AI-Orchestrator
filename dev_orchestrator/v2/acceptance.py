from __future__ import annotations

import uuid

from dev_orchestrator.config import AppConfig
from dev_orchestrator.healthcheck import build_acceptance_check
from dev_orchestrator.v2.service import V2Orchestrator


def run_v2_acceptance_check(config: AppConfig) -> dict:
    original_mock_mode = config.llm.use_mock
    config.llm.use_mock = True
    service = V2Orchestrator(config=config)
    scenarios = [
        {
            "name": "acceptance-v2-hr",
            "title": "Hosted HR AI Platform",
            "description": (
                "Must support resume parsing, HR policy Q&A RAG, attrition prediction, "
                "tenant isolation, SSO, audit logs, and zh-CN frontend labels."
            ),
            "expect": "GO",
        },
        {
            "name": "acceptance-v2-api",
            "title": "Hosted API Control Plane",
            "description": (
                "Must provide tenant-scoped API projects, audit logs, approval records, "
                "worker job lifecycle tracking, contract tests, and Docker deployment boundaries."
            ),
            "expect": "GO",
        },
        {
            "name": "acceptance-v2-blocked",
            "title": "Blocked Empty Contract",
            "description": "",
            "expect": "NO_GO",
        },
    ]
    results: list[dict] = []
    try:
        for scenario in scenarios:
            project = service.create_project(
                name=f"{scenario['name']}-{uuid.uuid4().hex[:6]}",
                title=scenario["title"],
                description=scenario["description"],
            )
            result = service.run_project_sync(project["id"])
            candidate = result.get("release_candidate", {})
            decision = candidate.get("decision")
            runs = service.get_project(project["id"]).get("runs", [])
            worker_jobs = service.list_worker_jobs(project.get("tenant_id"))
            audit_events = service.list_audit_events(project.get("tenant_id"))
            ok = (
                decision == scenario["expect"]
                and bool(runs)
                and bool(worker_jobs)
                and bool(audit_events)
                and (decision == "NO_GO" or bool(result.get("quality")))
            )
            results.append(
                {
                    "scenario": scenario["name"],
                    "project_id": project["id"],
                    "run_id": result.get("run", {}).get("id", ""),
                    "decision": decision,
                    "status": result.get("run", {}).get("status", ""),
                    "worker_jobs": len(worker_jobs),
                    "audit_events": len(audit_events),
                    "ok": ok,
                }
            )
    finally:
        config.llm.use_mock = original_mock_mode
    return build_acceptance_check(results)
