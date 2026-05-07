from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dev_orchestrator.config import AppConfig
from dev_orchestrator.v2.automation import build_project_blueprint, initial_run_metadata, normalize_automation_mode
from dev_orchestrator.v2.autonomy import (
    ENTERPRISE_SAAS_BENCHMARK,
    build_artifact_manifest,
    build_code_index,
    build_context_snapshot_payload,
    measure_effective_loc,
    normalize_benchmark_type,
    normalize_effective_loc_target,
    normalize_target_scale,
    normalize_unattended_mode,
    score_enterprise_saas_benchmark,
    sha256_file,
    write_artifact_file,
    write_json_with_integrity,
)
from dev_orchestrator.v2.chief import build_dag, summarize_chief_plan
from dev_orchestrator.v2.executor import TERMINAL_STATUSES, V2ChiefExecutor
from dev_orchestrator.v2.git_runtime import GitRuntime
from dev_orchestrator.v2.models import Project, ReleaseCandidate, RepairTask, slugify, utc_now
from dev_orchestrator.v2.quality import evaluate_project_quality, write_quality_report
from dev_orchestrator.v2.requirements import parse_requirements
from dev_orchestrator.v2.storage import V2Storage
from dev_orchestrator.v2.test_runner import TestRunner


WORKER_ROLES = {"chief", "planner", "architect", "frontend", "backend", "qa", "security", "integration", "release"}


class V2Orchestrator:
    def __init__(self, config: AppConfig, storage: V2Storage | None = None) -> None:
        self.config = config
        self.storage = storage or V2Storage(config.db_path)
        self._run_threads: dict[str, threading.Thread] = {}
        self.default_tenant = self.storage.get_or_create_tenant(self.config.identity.default_tenant)
        self.default_user = self.storage.get_or_create_user(self.config.identity.default_user, self.default_tenant["id"])

    def default_session(self) -> dict:
        return {
            "tenant": self.default_tenant,
            "user": self.default_user,
            "headers": {
                self.config.identity.tenant_header: self.default_tenant["id"],
                self.config.identity.user_header: self.default_user["id"],
            },
        }

    def resolve_session(self, tenant_id: str | None = None, user_id: str | None = None) -> dict:
        tenant = self.storage.get_or_create_tenant(tenant_id or self.config.identity.default_tenant)
        if user_id:
            try:
                user = self.storage.get_user(user_id)
            except KeyError:
                user = self.storage.get_or_create_user(user_id, tenant["id"])
        else:
            user = self.storage.get_or_create_user(self.config.identity.default_user, tenant["id"])
        if user["tenant_id"] != tenant["id"]:
            raise ValueError("User does not belong to tenant.")
        return {"tenant": tenant, "user": user}

    def _audit(
        self,
        *,
        tenant_id: str,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        message: str,
        payload: dict | None = None,
    ) -> dict:
        return self.storage.add_audit_event(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            message=message,
            payload=payload or {},
        )

    def create_project(
        self,
        *,
        name: str,
        title: str,
        description: str = "",
        project_path: str | None = None,
        automation_mode: str | None = None,
        target_scale: str | None = None,
        benchmark_type: str | None = None,
        unattended_mode: str | None = None,
        effective_loc_target: object = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        session = self.resolve_session(tenant_id, user_id)
        tenant = session["tenant"]
        user = session["user"]
        safe_name = slugify(name or title or "project")
        if project_path:
            root = Path(project_path).expanduser()
            if not root.is_absolute():
                root = self.config.root_dir / root
            project_root = root.resolve()
        else:
            project_root = (self.config.workspace_root / safe_name).resolve()
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / ".agent" / "v2").mkdir(parents=True, exist_ok=True)
        now = utc_now()
        project = Project(
            id=str(uuid.uuid4()),
            name=safe_name,
            title=title.strip() or safe_name,
            description=description.strip(),
            project_path=str(project_root),
            status="draft",
            created_at=now,
            updated_at=now,
        )
        payload = {
            **project.to_dict(),
            "tenant_id": tenant["id"],
            "created_by": user["id"],
            "owner_user_id": user["id"],
            "visibility": "tenant",
            "approval_state": "not_requested",
            "automation_mode": normalize_automation_mode(automation_mode),
            "target_scale": normalize_target_scale(target_scale),
            "benchmark_type": normalize_benchmark_type(benchmark_type),
            "unattended_mode": normalize_unattended_mode(unattended_mode),
            "effective_loc_target": normalize_effective_loc_target(effective_loc_target),
        }
        created = self.storage.create_project(payload)
        self._audit(
            tenant_id=tenant["id"],
            user_id=user["id"],
            action="project.created",
            resource_type="project",
            resource_id=created["id"],
            message="V2 hosted project created.",
            payload={"name": safe_name},
        )
        if description.strip():
            self.ingest_requirements(created["id"], description)
        return self.get_project(created["id"])

    def get_project(self, project_id: str) -> dict:
        project = self.storage.get_project(project_id)
        requirements = self.storage.get_requirement_bundle(project_id)
        runs = self.storage.list_project_runs(project_id)
        candidate = self.storage.latest_release_candidate_for_project(project_id)
        return {
            **project,
            "requirements": requirements,
            "runs": runs,
            "latest_release_candidate": candidate,
            "repairs": self.storage.list_project_repairs(project_id),
            "approvals": self.storage.list_project_approvals(project_id),
            "audit_summary": self._audit_summary(project),
        }

    def _audit_summary(self, project: dict) -> dict:
        events = self.storage.list_audit_events(project.get("tenant_id", self.default_tenant["id"]), limit=200)
        project_events = [item for item in events if item.get("resource_id") == project["id"]]
        approvals = self.storage.list_project_approvals(project["id"])
        return {
            "event_count": len(project_events),
            "approval_count": len(approvals),
            "latest_event": project_events[0] if project_events else None,
        }

    def list_projects(self, tenant_id: str | None = None) -> list[dict]:
        session = self.resolve_session(tenant_id, None)
        return [self.get_project(item["id"]) for item in self.storage.list_projects_for_tenant(session["tenant"]["id"])]

    def ingest_requirements(self, project_id: str, raw_text: str) -> dict:
        project = self.storage.get_project(project_id)
        bundle = parse_requirements(raw_text)
        dag = build_dag(bundle["work_packages"])
        blueprint = build_project_blueprint(
            bundle,
            project.get("automation_mode", "supervised_auto"),
            target_scale=project.get("target_scale", "medium"),
            effective_loc_target=int(project.get("effective_loc_target", 100000) or 100000),
            benchmark_type=project.get("benchmark_type", "enterprise_saas"),
        )
        project_root = Path(project["project_path"])
        agent_root = project_root / ".agent" / "v2"
        agent_root.mkdir(parents=True, exist_ok=True)
        (agent_root / "acceptance-contract.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0.0",
                    "kind": "acceptance-contract",
                    "requirements": bundle["atoms"],
                    "contracts": bundle["contracts"],
                    "coverage": bundle["coverage"],
                    "findings": bundle["findings"],
                },
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        (agent_root / "dag.json").write_text(json.dumps(dag, indent=2, ensure_ascii=True), encoding="utf-8")
        (agent_root / "project-blueprint.json").write_text(json.dumps(blueprint, indent=2, ensure_ascii=True), encoding="utf-8")
        saved = self.storage.save_requirement_bundle(project_id, bundle)
        self.storage.update_project(project_id, description=raw_text, status="contracted")
        return {
            **saved,
            "dag": dag,
            "project_blueprint": blueprint,
            "chief_summary": summarize_chief_plan(bundle),
        }

    def validate_project_blueprint(self, project_id: str) -> dict:
        project = self.storage.get_project(project_id)
        bundle = self.storage.get_requirement_bundle(project_id)
        blueprint = build_project_blueprint(
            bundle,
            project.get("automation_mode", "supervised_auto"),
            target_scale=project.get("target_scale", "medium"),
            effective_loc_target=int(project.get("effective_loc_target", 100000) or 100000),
            benchmark_type=project.get("benchmark_type", "enterprise_saas"),
        )
        return {
            "project_id": project_id,
            "valid": not blueprint.get("blocked", False),
            "blueprint": blueprint,
            "repair_prompts": blueprint.get("actionable_repair_prompts", []),
        }

    def run_project(self, project_id: str) -> dict:
        run = self.start_project_run(project_id)
        return self.wait_for_run(run["id"])

    def start_project_run(self, project_id: str, tenant_id: str | None = None, user_id: str | None = None) -> dict:
        project = self.storage.get_project(project_id)
        session = self.resolve_session(tenant_id or project.get("tenant_id"), user_id or project.get("created_by"))
        tenant = session["tenant"]
        user = session["user"]
        if project.get("tenant_id") != tenant["id"]:
            raise ValueError("Project is outside the current tenant.")
        requirement_bundle = self.storage.get_requirement_bundle(project_id)
        run_metadata = initial_run_metadata(
            requirement_bundle,
            project.get("automation_mode", "supervised_auto"),
            target_scale=project.get("target_scale", "medium"),
            effective_loc_target=int(project.get("effective_loc_target", 100000) or 100000),
            benchmark_type=project.get("benchmark_type", "enterprise_saas"),
        )
        now = utc_now()
        run_id = str(uuid.uuid4())
        worker_job = self.storage.create_worker_job(
            {
                "tenant_id": tenant["id"],
                "run_id": run_id,
                "queue": "v2-runs",
                "status": "queued",
                "payload": {"project_id": project_id, "mode": self.config.runtime.queue_mode},
            }
        )
        durable_job = self.storage.enqueue_durable_job(
            {
                "tenant_id": tenant["id"],
                "project_id": project_id,
                "run_id": run_id,
                "kind": "run",
                "role": "planner",
                "priority": 50,
                "resume_key": f"run:{run_id}:chief",
                "payload": {
                    "project_id": project_id,
                    "worker_job_id": worker_job["id"],
                    "target_scale": project.get("target_scale", "medium"),
                    "unattended_mode": project.get("unattended_mode", "off"),
                },
            }
        )
        run = self.storage.create_run(
            {
                "id": run_id,
                "project_id": project_id,
                "tenant_id": tenant["id"],
                "created_by": user["id"],
                "worker_job_id": worker_job["id"],
                "status": "running",
                "chief_summary": "项目蓝图已生成，Chief run started.",
                **run_metadata,
                "durable_queue_state": {
                    "backend": "sqlite",
                    "state": "queued",
                    "job_id": durable_job["id"],
                    "resume_key": durable_job["resume_key"],
                    "attempts": durable_job["attempts"],
                    "lease_until": "",
                },
                "created_at": now,
                "updated_at": now,
            }
        )
        self.storage.add_event(run_id, "info", "chief", "Chief control plane accepted the run.", {"project_id": project_id})
        self.storage.add_event(
            run_id,
            "info",
            "chief",
            "项目蓝图已生成。",
            run_metadata["project_blueprint"],
        )
        self._audit(
            tenant_id=tenant["id"],
            user_id=user["id"],
            action="run.started",
            resource_type="run",
            resource_id=run_id,
            message="V2 hosted run started.",
            payload={"project_id": project_id, "worker_job_id": worker_job["id"], "durable_job_id": durable_job["id"]},
        )
        if not requirement_bundle.get("atoms"):
            self.storage.add_event(
                run_id,
                "error",
                "product-analyst",
                "No requirement contract exists; run is blocked.",
            )
            result = self._block_missing_contract(project_id, run_id)
            self.storage.update_worker_job(worker_job["id"], status="finished", attempts=1, finished_at=utc_now())
            self.storage.finish_durable_job(durable_job["id"], status="failed", error="missing_requirement_contract")
            self._refresh_run_durable_queue_state(run_id)
            self._finalize_run_artifacts(project_id, run_id, result.get("quality"))
            return self.get_run(run_id)
        if run_metadata["project_blueprint"].get("blocked"):
            self.storage.add_event(
                run_id,
                "error",
                "product-analyst",
                run_metadata["project_blueprint"].get("block_reason") or "项目蓝图不足，运行已阻断。",
                {"repair_prompts": run_metadata["project_blueprint"].get("actionable_repair_prompts", [])},
            )
            result = self._block_blueprint(project_id, run_id, run_metadata["project_blueprint"])
            self.storage.update_worker_job(worker_job["id"], status="finished", attempts=1, finished_at=utc_now())
            self.storage.finish_durable_job(durable_job["id"], status="failed", error="project_blueprint_blocked")
            self._refresh_run_durable_queue_state(run_id)
            self._finalize_run_artifacts(project_id, run_id, result.get("quality"))
            return self.get_run(run_id)
        else:
            self.storage.add_event(
                run_id,
                "info",
                "chief",
                "DAG waves planned.",
                {
                    "work_package_count": len(requirement_bundle.get("work_packages", [])),
                    "must_coverage_percent": requirement_bundle.get("coverage", {}).get("must_coverage_percent", 0),
                },
            )
        if not self.config.llm.use_mock and (not self.config.llm.api_base or not self.config.llm.api_key):
            self.storage.add_event(
                run_id,
                "error",
                "configuration",
                "Real agent execution requires API base and API key when mock mode is disabled.",
            )
            result = self._block_runtime_config(project_id, run_id)
            self.storage.update_worker_job(worker_job["id"], status="finished", attempts=1, finished_at=utc_now())
            self.storage.finish_durable_job(durable_job["id"], status="failed", error="runtime_config_missing")
            self._refresh_run_durable_queue_state(run_id)
            self._finalize_run_artifacts(project_id, run_id, result.get("quality"))
            return self.get_run(run_id)
        self.storage.update_project(project_id, status="running")
        if self.config.runtime.queue_mode == "local-adapter":
            thread = threading.Thread(target=self._execute_run_safe, args=(project_id, run_id), daemon=True)
            self._run_threads[run_id] = thread
            thread.start()
        return self.get_run(run_id)

    def run_project_sync(self, project_id: str, tenant_id: str | None = None, user_id: str | None = None) -> dict:
        run = self.start_project_run(project_id, tenant_id=tenant_id, user_id=user_id)
        return self.wait_for_run(run["id"])

    def wait_for_run(self, run_id: str, timeout_seconds: int = 1800, poll_seconds: float = 0.5) -> dict:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            run = self.storage.get_run(run_id)
            if run["status"] in TERMINAL_STATUSES:
                if run.get("durable_queue_state", {}).get("job_id") and not run.get("artifact_manifest_path"):
                    time.sleep(poll_seconds)
                    continue
                project_id = run["project_id"]
                latest_validation = self.storage.list_validations(run_id)
                return {
                    "run": run,
                    "quality": latest_validation[-1] if latest_validation else {},
                    "repair_tasks": self.storage.list_project_repairs(project_id),
                    "release_candidate": self.storage.latest_release_candidate_for_project(project_id) or {},
                }
            if self.config.runtime.queue_mode != "local-adapter":
                self.run_worker_once(
                    tenant_id=run.get("tenant_id") or self.default_tenant["id"],
                    role="planner",
                    worker_id="sync-driver",
                )
            time.sleep(poll_seconds)
        raise TimeoutError(f"V2 run did not finish within {timeout_seconds} seconds.")

    def _execute_run_safe(self, project_id: str, run_id: str) -> None:
        del project_id
        run = self.storage.get_run(run_id)
        durable_job_id = run.get("durable_queue_state", {}).get("job_id", "")
        if not durable_job_id:
            return
        worker_id = f"local-adapter-{run_id[:8]}"
        job = self.storage.lease_durable_job_by_id(durable_job_id, worker_id, self._lease_until(minutes=30))
        self.execute_durable_job(job, worker_id=worker_id, lease_seconds=1800)

    def _lease_until(self, minutes: int = 5) -> str:
        return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()

    def _lease_until_seconds(self, seconds: int = 300) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))).isoformat()

    def _claim_roles_for_worker(self, role: str) -> list[str]:
        normalized = (role or "planner").strip().lower()
        if normalized in {"all", "*"}:
            return []
        if normalized == "planner":
            return ["planner", "chief"]
        return [normalized]

    def reclaim_expired_durable_jobs(self) -> list[dict]:
        expired = self.storage.requeue_expired_jobs(utc_now())
        for job in expired:
            run_id = job.get("run_id", "")
            if not run_id:
                continue
            try:
                self._refresh_run_durable_queue_state(run_id)
                run = self.storage.get_run(run_id)
                project_id = job.get("project_id") or run["project_id"]
                if job.get("status") == "dead_letter":
                    self.storage.update_run(
                        run_id,
                        status="failed",
                        chief_summary="Durable job lease expired and reached dead-letter.",
                        continuation_state={
                            **run.get("continuation_state", {}),
                            "state": "dead_letter",
                            "next_action": "inspect_dead_letter",
                            "failure_reason": job.get("error", "lease_timeout"),
                        },
                    )
                    self.storage.update_project(project_id, status="failed")
                    self._finalize_run_artifacts(project_id, run_id)
                else:
                    self.storage.update_run(
                        run_id,
                        continuation_state={
                            **run.get("continuation_state", {}),
                            "state": "retry",
                            "next_action": "claim_retry_job",
                            "failure_reason": job.get("error", "lease_timeout"),
                        },
                    )
            except Exception:
                continue
        return expired

    def claim_durable_job(
        self,
        *,
        tenant_id: str | None = None,
        role: str = "planner",
        worker_id: str = "",
        lease_seconds: int = 300,
    ) -> dict:
        session = self.resolve_session(tenant_id, None)
        tenant = session["tenant"]
        expired = self.reclaim_expired_durable_jobs()
        job = self.storage.lease_durable_job(
            tenant["id"],
            worker_id or f"{role}-worker",
            self._lease_until_seconds(lease_seconds),
            kinds=["run"],
            roles=self._claim_roles_for_worker(role),
        )
        if job:
            self._refresh_run_durable_queue_state(job["run_id"])
        return {"job": job, "expired": expired}

    def run_worker_once(
        self,
        *,
        tenant_id: str | None = None,
        role: str = "planner",
        worker_id: str = "",
        lease_seconds: int = 300,
    ) -> dict:
        claimed = self.claim_durable_job(
            tenant_id=tenant_id,
            role=role,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        job = claimed.get("job")
        if not job:
            return {"ok": True, "claimed": False, "expired": claimed.get("expired", [])}
        result = self.execute_durable_job(job, worker_id=worker_id or job.get("worker_id", ""), lease_seconds=lease_seconds)
        return {"ok": result.get("ok", False), "claimed": True, "job": result.get("job", job), "expired": claimed.get("expired", [])}

    def execute_durable_job(self, job: dict, *, worker_id: str = "", lease_seconds: int = 300) -> dict:
        job = self.storage.get_durable_job(job["id"])
        run_id = job.get("run_id", "")
        if job.get("status") != "leased":
            job = self.storage.lease_durable_job_by_id(
                job["id"],
                worker_id or job.get("worker_id") or "durable-worker",
                self._lease_until_seconds(lease_seconds),
            )
        if job.get("status") != "leased":
            return {"ok": False, "job": job, "status": job.get("status", ""), "error": "job_not_leased"}
        worker_id = worker_id or job.get("worker_id") or "durable-worker"
        if job.get("kind") != "run":
            failed = self.storage.fail_durable_job(job["id"], f"Unsupported durable job kind: {job.get('kind')}", retryable=False)
            return {"ok": False, "job": failed, "error": failed.get("error", "")}

        run = self.storage.get_run(run_id)
        project_id = job.get("project_id") or run["project_id"]
        worker_job_id = job.get("payload", {}).get("worker_job_id") or run.get("worker_job_id", "")
        if worker_job_id:
            self.storage.update_worker_job(
                worker_job_id,
                status="running",
                worker_id=worker_id,
                attempts=max(1, int(job.get("attempts", 0) or 0)),
                locked_at=utc_now(),
            )
        self.storage.update_run(
            run_id,
            status="running",
            durable_queue_state={
                **run.get("durable_queue_state", {}),
                "backend": "sqlite",
                "state": "leased",
                "job_id": job["id"],
                "worker_id": worker_id,
                "role": job.get("role", ""),
                "attempts": job.get("attempts", 0),
                "lease_until": job.get("lease_until", ""),
                "heartbeat_at": job.get("heartbeat_at", ""),
            },
        )
        self.storage.add_event(run_id, "info", "worker", "Durable worker claimed run job.", {"job_id": job["id"], "worker_id": worker_id})

        try:
            V2ChiefExecutor(config=self.config, storage=self.storage).execute_run(project_id, run_id)
            completed = self.storage.finish_durable_job(job["id"], status="completed")
            self._refresh_run_durable_queue_state(run_id)
            if worker_job_id:
                self.storage.update_worker_job(worker_job_id, status="finished", finished_at=utc_now())
            artifacts = self._finalize_run_artifacts(project_id, run_id)
            return {"ok": True, "job": completed, "artifacts": artifacts}
        except Exception as exc:
            error = str(exc)
            failed = self.storage.fail_durable_job(job["id"], error, retryable=True)
            self._refresh_run_durable_queue_state(run_id)
            if worker_job_id:
                self.storage.update_worker_job(
                    worker_job_id,
                    status="failed" if failed["status"] in {"failed", "dead_letter"} else "retry",
                    finished_at=utc_now() if failed["status"] in {"failed", "dead_letter"} else "",
                )
            current = self.storage.get_run(run_id)
            continuation = {
                **current.get("continuation_state", {}),
                "failure_reason": error,
                "failed_job_id": job["id"],
            }
            if failed["status"] == "dead_letter":
                continuation.update({"state": "dead_letter", "next_action": "inspect_dead_letter"})
                self.storage.update_run(run_id, status="failed", chief_summary=error, continuation_state=continuation)
                self.storage.update_project(project_id, status="failed")
                self._finalize_run_artifacts(project_id, run_id)
            else:
                continuation.update({"state": "retry", "next_action": "claim_retry_job"})
                self.storage.update_run(
                    run_id,
                    status="running",
                    chief_summary="Durable job failed and was queued for retry.",
                    continuation_state=continuation,
                )
            self.storage.add_event(run_id, "error", "worker", "Durable worker job failed.", {"job_id": job["id"], "status": failed["status"], "error": error})
            return {"ok": False, "job": failed, "error": error}

    def _refresh_run_durable_queue_state(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        job_id = run.get("durable_queue_state", {}).get("job_id", "")
        jobs = self.storage.list_durable_jobs(run_id=run_id)
        job = self.storage.get_durable_job(job_id) if job_id else (jobs[0] if jobs else None)
        counts: dict[str, int] = {}
        for item in jobs:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        state = {
            **run.get("durable_queue_state", {}),
            "backend": "sqlite",
            "job_id": job.get("id", "") if job else "",
            "state": job.get("status", "empty") if job else "empty",
            "worker_id": job.get("worker_id", "") if job else "",
            "attempts": job.get("attempts", 0) if job else 0,
            "lease_until": job.get("lease_until", "") if job else "",
            "heartbeat_at": job.get("heartbeat_at", "") if job else "",
            "counts": counts,
        }
        return self.storage.update_run(run_id, durable_queue_state=state)["durable_queue_state"]

    def _register_artifact(self, *, tenant_id: str, project_id: str, run_id: str, kind: str, name: str, payload: object) -> dict:
        artifact = write_artifact_file(self.config.root_dir, tenant_id, project_id, run_id, kind, name, payload)
        return self.storage.save_artifact(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "kind": artifact["kind"],
                "path": artifact["path"],
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
                "payload": artifact.get("payload", {}),
            }
        )

    def _latest_artifacts_by_path(self, items: list[dict]) -> list[dict]:
        latest: dict[str, dict] = {}
        order: list[str] = []
        for item in items:
            path = str(item.get("path", "")).strip()
            if not path:
                continue
            if path not in latest:
                order.append(path)
            latest[path] = item
        return [latest[path] for path in order]

    def _artifact_source_root(self, project_root: Path, run_id: str) -> Path:
        worktrees = self.storage.list_worktrees(run_id)
        for item in reversed(worktrees):
            if item.get("work_package_id") == "integration" and Path(item.get("path", "")).exists():
                return Path(item["path"])
        return project_root

    def _artifact_integrity_row(self, item: dict) -> dict:
        row = dict(item)
        path = Path(str(row.get("path", "")))
        row["exists"] = path.exists()
        row["actual_size_bytes"] = path.stat().st_size if path.exists() else 0
        row["actual_sha256"] = sha256_file(path) if path.exists() and path.is_file() else ""
        expected_size = int(row.get("size_bytes", 0) or 0)
        expected_sha = str(row.get("sha256", "") or "")
        row["size_ok"] = not expected_size or expected_size == row["actual_size_bytes"]
        row["sha256_ok"] = not expected_sha or expected_sha == row["actual_sha256"]
        row["integrity_ok"] = bool(row["exists"] and row["size_ok"] and row["sha256_ok"])
        return row

    def _artifact_requirement_score(self, item: dict) -> tuple[int, int]:
        score = 0
        if str(item.get("sha256", "") or "").strip():
            score += 2
        if int(item.get("size_bytes", 0) or 0) > 0:
            score += 1
        if item.get("strict_integrity"):
            score += 1
        source = str(item.get("source", ""))
        if source.startswith("artifact_db:"):
            score += 2
        elif source.startswith("artifact_manifest.items"):
            score += 1
        return score, len(source)

    def _read_json_file(self, path: Path) -> tuple[dict, str]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {}, str(exc)
        return payload if isinstance(payload, dict) else {}, ""

    def _manifest_items_for_validation(self, manifest_path: str) -> tuple[list[dict], list[dict]]:
        path = Path(manifest_path)
        if not path.exists():
            return [], []
        payload, error = self._read_json_file(path)
        if error:
            return [], [{"kind": "artifact_manifest", "path": manifest_path, "source": "artifact_manifest.items", "error": error}]
        items = payload.get("items", [])
        if not isinstance(items, list):
            return [], [{"kind": "artifact_manifest", "path": manifest_path, "source": "artifact_manifest.items", "error": "manifest items must be a list"}]
        normalized: list[dict] = []
        manifest_path_text = str(path.resolve())
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_path = str(item.get("path", "")).strip()
            if item_path and str(Path(item_path).resolve()) == manifest_path_text:
                continue
            normalized.append(
            {
                "kind": item.get("kind", "artifact"),
                "path": item_path,
                "source": f"artifact_manifest.items[{index}]",
                "sha256": item.get("sha256", ""),
                "size_bytes": item.get("size_bytes", 0),
                "artifact_id": item.get("id", ""),
                "strict_integrity": True,
            }
            )
        return self._latest_artifacts_by_path(normalized), []

    def _validate_run_recovery_contract(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        candidate = self.storage.latest_release_candidate_for_project(run["project_id"])
        continuation = run.get("continuation_state", {})
        required: list[dict] = []
        db_artifacts = self._latest_artifacts_by_path(self.storage.list_artifacts(run_id=run_id))

        def add_required(kind: str, path: object, source: str) -> None:
            if path is None:
                return
            text = str(path).strip()
            if text:
                required.append({"kind": kind, "path": text, "source": source})

        add_required("artifact_manifest", run.get("artifact_manifest_path", ""), "run.artifact_manifest_path")
        add_required("rollback_manifest", run.get("rollback_manifest_path", ""), "run.rollback_manifest_path")
        add_required("release_manifest", continuation.get("release_manifest_path", ""), "continuation.release_manifest_path")
        add_required("release_patch", continuation.get("release_patch_path", ""), "continuation.release_patch_path")
        if continuation.get("rollback_manifest_path"):
            add_required("rollback_manifest", continuation.get("rollback_manifest_path", ""), "continuation.rollback_manifest_path")
        for path in continuation.get("patch_paths", []) or []:
            add_required("patch", path, "continuation.patch_paths")
        for patch_set in self.storage.list_patch_sets(run_id):
            if patch_set.get("status") in {"captured", "applied", "conflict"}:
                add_required("patch", patch_set.get("patch_path", ""), f"patch_set:{patch_set.get('id', '')}")
        if candidate:
            add_required("release_manifest", candidate.get("manifest_path", ""), "release_candidate.manifest_path")
            release_patch_reference = {
                "kind": "release_patch",
                "path": candidate.get("release_patch_path", ""),
                "source": "release_candidate.release_patch_path",
                "sha256": candidate.get("release_patch_sha256", ""),
                "size_bytes": candidate.get("release_patch_size_bytes", 0),
            }
            manifest_path = Path(candidate.get("manifest_path", ""))
            if manifest_path.exists():
                try:
                    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    release_patch_reference.update(
                        {
                            "path": manifest_payload.get("release_patch_path", release_patch_reference["path"]),
                            "sha256": manifest_payload.get("release_patch_sha256", release_patch_reference["sha256"]),
                            "size_bytes": manifest_payload.get("release_patch_size_bytes", release_patch_reference["size_bytes"]),
                            "source": "release_manifest.release_patch",
                        }
                    )
                except (OSError, json.JSONDecodeError):
                    pass
            if release_patch_reference["path"]:
                required.append(release_patch_reference)

        for artifact in db_artifacts:
            required.append(
                {
                    "kind": artifact.get("kind", "artifact"),
                    "path": artifact.get("path", ""),
                    "source": f"artifact_db:{artifact.get('id', '')}",
                    "sha256": artifact.get("sha256", ""),
                    "size_bytes": artifact.get("size_bytes", 0),
                    "artifact_id": artifact.get("id", ""),
                }
            )

        manifest_items, manifest_errors = [], []
        if run.get("artifact_manifest_path"):
            manifest_items, manifest_errors = self._manifest_items_for_validation(run["artifact_manifest_path"])
            required.extend(manifest_items)
        if run.get("rollback_manifest_path"):
            payload, error = self._read_json_file(Path(run["rollback_manifest_path"]))
            if error:
                manifest_errors.append(
                    {"kind": "rollback_manifest", "path": run["rollback_manifest_path"], "source": "rollback_manifest", "error": error}
                )
            else:
                if payload.get("release_patch_path"):
                    required.append(
                        {
                            "kind": "release_patch",
                            "path": payload.get("release_patch_path", ""),
                            "source": "rollback_manifest.release_patch",
                            "sha256": payload.get("release_patch_sha256", ""),
                            "size_bytes": payload.get("release_patch_size_bytes", 0),
                        }
                    )
                if payload.get("reverse_patch_path"):
                    required.append(
                        {
                            "kind": "reverse_patch",
                            "path": payload.get("reverse_patch_path", ""),
                            "source": "rollback_manifest.reverse_patch",
                            "sha256": payload.get("reverse_patch_sha256", ""),
                            "size_bytes": payload.get("reverse_patch_size_bytes", 0),
                        }
                    )

        chosen: dict[str, dict] = {}
        order: list[str] = []
        for item in required:
            if not str(item.get("path", "")).strip():
                continue
            path = str(item.get("path", ""))
            current = chosen.get(path)
            if current is None:
                chosen[path] = item
                order.append(path)
                continue
            if self._artifact_requirement_score(item) >= self._artifact_requirement_score(current):
                chosen[path] = item
        checked: list[dict] = []
        missing: list[dict] = []
        mismatched: list[dict] = []
        for path in order:
            row = self._artifact_integrity_row(chosen[path])
            checked.append(row)
            if not row["exists"]:
                missing.append(row)
            elif not row["integrity_ok"]:
                mismatched.append(row)
        mismatched.extend(manifest_errors)
        return {
            "ok": not missing and not mismatched,
            "checked": checked,
            "missing": missing,
            "mismatched": mismatched,
            "manifest_item_count": len(manifest_items),
            "artifact_db_count": len(db_artifacts),
        }

    def _block_resume_missing_artifacts(self, run_id: str, validation: dict) -> dict:
        run = self.storage.get_run(run_id)
        reason = "Resume blocked because required artifacts or patch files are missing or failed integrity checks."
        missing_paths = [item["path"] for item in validation.get("missing", [])]
        bad_paths = [item.get("path", "") for item in validation.get("mismatched", [])]
        self.storage.add_event(
            run_id,
            "error",
            "recovery",
            reason,
            {"missing": validation.get("missing", []), "mismatched": validation.get("mismatched", [])},
        )
        repair = RepairTask(
            id=str(uuid.uuid4()),
            project_id=run["project_id"],
            run_id=run_id,
            finding_code="resume_missing_artifact",
            assigned_role="integration",
            status="ready",
            reason=f"{reason} Missing: {', '.join(missing_paths)[:1800]} Corrupt: {', '.join(bad_paths)[:1800]}",
        )
        self.storage.save_repair_task(repair.to_dict())
        updated = self.storage.update_run(
            run_id,
            status="blocked",
            chief_summary=reason,
            continuation_state={
                **run.get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "resume_preflight_failed",
                "state": "blocked",
                "next_action": "repair_missing_artifacts",
                "failure_reason": reason,
                "missing_artifacts": validation.get("missing", []),
                "mismatched_artifacts": validation.get("mismatched", []),
            },
            durable_queue_state={**run.get("durable_queue_state", {}), "state": "blocked"},
        )
        self.storage.cancel_durable_jobs_for_run(run_id, status="cancelled", reason="resume_preflight_failed")
        self.storage.update_project(run["project_id"], status="blocked")
        return updated

    def _finalize_run_artifacts(self, project_id: str, run_id: str, quality: dict | None = None) -> dict:
        project = self.storage.get_project(project_id)
        run = self.storage.get_run(run_id)
        tenant_id = run.get("tenant_id") or project.get("tenant_id", self.default_tenant["id"])
        requirement_bundle = self.storage.get_requirement_bundle(project_id)
        project_root = Path(project["project_path"])
        source_root = self._artifact_source_root(project_root, run_id)
        validations = self.storage.list_validations(run_id)
        latest_quality = quality or (validations[-1] if validations else {})
        run_artifacts: list[dict] = []

        run_artifacts.append(
            self._register_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                kind="continuation",
                name="continuation-state.json",
                payload=self.get_run_continuation(run_id),
            )
        )

        effective_loc = measure_effective_loc(source_root, requirement_bundle)
        target = int(project.get("effective_loc_target", 100000) or 100000)
        effective_loc["target_ready"] = effective_loc.get("effective_loc", 0) >= target
        run_artifacts.append(
            self._register_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                kind="metrics",
                name="effective-loc.json",
                payload=effective_loc,
            )
        )

        code_entries = build_code_index(source_root, tenant_id, project_id, run_id)
        snapshot_payload = build_context_snapshot_payload(project, requirement_bundle, run, code_entries)
        snapshot_artifact = self._register_artifact(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            kind="context",
            name="context-snapshot.json",
            payload=snapshot_payload,
        )
        snapshot = self.storage.save_context_snapshot(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "summary": f"{project.get('name', project_id)} {run.get('status', '')} context snapshot",
                "payload": snapshot_payload,
                "artifact_id": snapshot_artifact["id"],
            }
        )
        for item in code_entries:
            item["snapshot_id"] = snapshot["id"]
        self.storage.save_code_index_entries(code_entries)
        run_artifacts.append(snapshot_artifact)

        benchmark = score_enterprise_saas_benchmark(project, requirement_bundle, effective_loc, latest_quality)
        self.storage.save_benchmark_run(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "run_id": run_id,
                "benchmark_type": project.get("benchmark_type", "enterprise_saas"),
                "status": benchmark["status"],
                "score": benchmark["score"],
                "effective_loc": benchmark["effective_loc"],
                "payload": benchmark,
            }
        )
        run_artifacts.append(
            self._register_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=run_id,
                kind="benchmark",
                name="enterprise-saas-score.json",
                payload=benchmark,
            )
        )

        existing = [
            item
            for item in self.storage.list_artifacts(run_id=run_id)
            if item.get("kind") != "manifest"
        ]
        manifest_payload = build_artifact_manifest(self._latest_artifacts_by_path(existing + run_artifacts))
        manifest_artifact = self._register_artifact(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            kind="manifest",
            name="artifact-manifest.json",
            payload=manifest_payload,
        )
        self.storage.update_run(
            run_id,
            artifact_manifest_path=manifest_artifact["path"],
            context_snapshot_id=snapshot["id"],
            effective_loc_metrics=effective_loc,
            benchmark_score=benchmark["score"],
        )
        self.storage.add_event(
            run_id,
            "info",
            "artifact-store",
            "Run artifact manifest, context snapshot, code index, and benchmark metrics recorded.",
            {"artifact_manifest_path": manifest_artifact["path"], "benchmark_score": benchmark["score"]},
        )
        return {
            "manifest": manifest_artifact,
            "context_snapshot": snapshot,
            "effective_loc_metrics": effective_loc,
            "benchmark": benchmark,
        }

    def _block_missing_contract(self, project_id: str, run_id: str) -> dict:
        quality = {
            "id": f"VAL-{uuid.uuid4().hex[:12]}",
            "run_id": run_id,
            "gate": "v2-contract",
            "status": "failed",
            "score": 0,
            "findings": [
                {
                    "code": "no_requirement_contract",
                    "severity": "critical",
                    "category": "requirement-ingest",
                    "message": "No requirement contract exists; run is blocked.",
                    "evidence": [],
                    "owner": "product-analyst",
                    "repair_role": "product-analyst",
                }
            ],
            "evidence": [],
            "created_at": utc_now(),
            "metrics": {},
            "anti_shit_score": 0,
            "release_candidate_allowed": False,
            "summary": "No requirement contract exists; run is blocked.",
        }
        self.storage.save_validation(run_id, quality)
        candidate = self.storage.save_release_candidate(
            {
                "id": str(uuid.uuid4()),
                "project_id": project_id,
                "run_id": run_id,
                "status": "blocked",
                "decision": "NO_GO",
                "gate_score": 0,
                "blockers": quality["findings"],
                "created_at": utc_now(),
                "quality_gate": {"status": "failed", "score": 0, "anti_shit_score": 0},
                "automation": {
                    "decomposition_score": self.storage.get_run(run_id).get("decomposition_score", 0),
                    "autonomy_level": self.storage.get_run(run_id).get("autonomy_level", 2),
                    "repair_round_count": self.storage.get_run(run_id).get("repair_round_count", 0),
                    "quality_gate_history": [quality],
                    "continuation_state": self.storage.get_run(run_id).get("continuation_state", {}),
                },
                "release_patch_path": "",
                "manifest_path": "",
                "base_sha": "",
                "final_sha": "",
                "changed_files": [],
                "test_evidence_count": 0,
            }
        )
        self.storage.update_run(
            run_id,
            status="blocked",
            chief_summary=quality["summary"],
            quality_gate_history=[quality],
            continuation_state={
                **self.storage.get_run(run_id).get("continuation_state", {}),
                "state": "blocked",
                "next_action": "ingest_requirements",
                "summary": quality["summary"],
            },
        )
        self.storage.update_project(project_id, status="blocked")
        return {
            "run": self.storage.get_run(run_id),
            "quality": quality,
            "repair_tasks": self.storage.list_project_repairs(project_id),
            "release_candidate": candidate,
        }

    def _block_blueprint(self, project_id: str, run_id: str, blueprint: dict) -> dict:
        summary = blueprint.get("block_reason") or "项目蓝图不足，请补充需求后重新运行。"
        findings = [
            {
                "code": item.get("code", "project_blueprint_blocked"),
                "severity": item.get("severity", "high"),
                "category": "project-blueprint",
                "message": item.get("message", summary),
                "evidence": item.get("repair_prompt", ""),
                "owner": "product-analyst",
                "repair_role": "product-analyst",
            }
            for item in blueprint.get("risk_estimate", {}).get("items", [])
        ] or [
            {
                "code": "project_blueprint_blocked",
                "severity": "critical",
                "category": "project-blueprint",
                "message": summary,
                "evidence": [],
                "owner": "product-analyst",
                "repair_role": "product-analyst",
            }
        ]
        quality = {
            "id": f"VAL-{uuid.uuid4().hex[:12]}",
            "run_id": run_id,
            "gate": "project-blueprint",
            "status": "failed",
            "score": int(blueprint.get("decomposition_score", 0)),
            "findings": findings,
            "evidence": blueprint.get("actionable_repair_prompts", []),
            "created_at": utc_now(),
            "metrics": {"project_blueprint": blueprint},
            "anti_shit_score": int(blueprint.get("decomposition_score", 0)),
            "release_candidate_allowed": False,
            "summary": summary,
        }
        self.storage.save_validation(run_id, quality)
        self.storage.update_run(
            run_id,
            status="blocked",
            chief_summary=summary,
            quality_gate_history=[quality],
            continuation_state={
                "state": "blocked",
                "next_action": "repair_requirements",
                "completed_waves": [],
                "pending_waves": [],
                "summary": summary,
            },
        )
        candidate = self.storage.save_release_candidate(
            {
                "id": str(uuid.uuid4()),
                "project_id": project_id,
                "run_id": run_id,
                "status": "blocked",
                "decision": "NO_GO",
                "gate_score": int(blueprint.get("decomposition_score", 0)),
                "blockers": quality["findings"],
                "created_at": utc_now(),
                "quality_gate": {"status": "failed", "score": quality["score"], "anti_shit_score": quality["anti_shit_score"]},
                "automation": {
                    "decomposition_score": blueprint.get("decomposition_score", 0),
                    "autonomy_level": self.storage.get_run(run_id).get("autonomy_level", 2),
                    "repair_round_count": 0,
                    "quality_gate_history": [quality],
                    "continuation_state": self.storage.get_run(run_id).get("continuation_state", {}),
                    "project_blueprint": blueprint,
                },
                "release_patch_path": "",
                "manifest_path": "",
                "base_sha": "",
                "final_sha": "",
                "changed_files": [],
                "test_evidence_count": 0,
            }
        )
        self.storage.update_project(project_id, status="blocked")
        return {
            "run": self.storage.get_run(run_id),
            "quality": quality,
            "repair_tasks": self.storage.list_project_repairs(project_id),
            "release_candidate": candidate,
        }

    def _block_runtime_config(self, project_id: str, run_id: str) -> dict:
        summary = "Real V2 execution requires API base and API key, or mock mode must be enabled for scripted local tests."
        quality = {
            "id": f"VAL-{uuid.uuid4().hex[:12]}",
            "run_id": run_id,
            "gate": "v2-runtime-config",
            "status": "failed",
            "score": 0,
            "findings": [
                {
                    "code": "v2_runtime_config_missing_api_key",
                    "severity": "critical",
                    "category": "configuration",
                    "message": summary,
                    "evidence": ["llm.use_mock=false"],
                    "owner": "chief",
                    "repair_role": "chief",
                }
            ],
            "evidence": [],
            "created_at": utc_now(),
            "metrics": {},
            "anti_shit_score": 0,
            "release_candidate_allowed": False,
            "summary": summary,
        }
        self.storage.save_validation(run_id, quality)
        candidate = self.storage.save_release_candidate(
            {
                "id": str(uuid.uuid4()),
                "project_id": project_id,
                "run_id": run_id,
                "status": "blocked",
                "decision": "NO_GO",
                "gate_score": 0,
                "blockers": quality["findings"],
                "created_at": utc_now(),
                "quality_gate": {"status": "failed", "score": 0, "anti_shit_score": 0},
                "automation": {
                    "decomposition_score": self.storage.get_run(run_id).get("decomposition_score", 0),
                    "autonomy_level": self.storage.get_run(run_id).get("autonomy_level", 2),
                    "repair_round_count": self.storage.get_run(run_id).get("repair_round_count", 0),
                    "quality_gate_history": [quality],
                    "continuation_state": self.storage.get_run(run_id).get("continuation_state", {}),
                },
                "release_patch_path": "",
                "manifest_path": "",
                "base_sha": "",
                "final_sha": "",
                "changed_files": [],
                "test_evidence_count": 0,
            }
        )
        self.storage.update_run(
            run_id,
            status="blocked",
            chief_summary=summary,
            quality_gate_history=[quality],
            continuation_state={
                **self.storage.get_run(run_id).get("continuation_state", {}),
                "state": "blocked",
                "next_action": "configure_model",
                "summary": summary,
            },
        )
        self.storage.update_project(project_id, status="blocked")
        return {
            "run": self.storage.get_run(run_id),
            "quality": quality,
            "repair_tasks": self.storage.list_project_repairs(project_id),
            "release_candidate": candidate,
        }

    def _create_release_candidate(self, project_id: str, run_id: str, quality: dict) -> dict:
        blockers = [
            item
            for item in quality.get("findings", [])
            if item.get("severity") in {"critical", "high"}
        ]
        allowed = quality.get("release_candidate_allowed", False)
        candidate = ReleaseCandidate(
            id=str(uuid.uuid4()),
            project_id=project_id,
            run_id=run_id,
            status="ready" if allowed else "blocked",
            decision="GO" if allowed else "NO_GO",
            gate_score=int(quality.get("score", 0)),
            blockers=[],
        ).to_dict()
        candidate["blockers"] = blockers
        candidate["quality_gate"] = {
            "status": quality.get("status"),
            "score": quality.get("score"),
            "anti_shit_score": quality.get("anti_shit_score"),
        }
        return self.storage.save_release_candidate(candidate)

    def get_events(self, run_id: str) -> list[dict]:
        return self.storage.list_events(run_id)

    def get_run(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        project_id = run["project_id"]
        worker_job = None
        if run.get("worker_job_id"):
            try:
                worker_job = self.storage.get_worker_job(run["worker_job_id"])
            except KeyError:
                worker_job = None
        return {
            **run,
            "agent_runs": self.storage.list_agent_runs(run_id),
            "patch_sets": self.storage.list_patch_sets(run_id),
            "test_runs": self.storage.list_test_runs(run_id),
            "integration_steps": self.storage.list_integration_steps(run_id),
            "release_candidate": self.storage.latest_release_candidate_for_project(project_id),
            "worker_job": worker_job,
            "durable_jobs": self.storage.list_durable_jobs(run_id=run_id),
            "artifacts": self.storage.list_artifacts(run_id=run_id),
        }

    def get_run_continuation(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        project_id = run["project_id"]
        jobs = self.storage.list_durable_jobs(run_id=run_id)
        artifacts = self.storage.list_artifacts(run_id=run_id)
        snapshot = None
        if run.get("context_snapshot_id"):
            try:
                snapshot = self.storage.get_context_snapshot(run["context_snapshot_id"])
            except KeyError:
                snapshot = None
        return {
            "run_id": run_id,
            "project_id": project_id,
            "status": run["status"],
            "continuation_state": run.get("continuation_state", {}),
            "durable_queue_state": run.get("durable_queue_state", {}),
            "durable_jobs": jobs,
            "artifact_manifest_path": run.get("artifact_manifest_path", ""),
            "recovery_contract": self._validate_run_recovery_contract(run_id),
            "context_snapshot_id": run.get("context_snapshot_id", ""),
            "context_snapshot": snapshot,
            "effective_loc_metrics": run.get("effective_loc_metrics", {}),
            "rollback_manifest_path": run.get("rollback_manifest_path", ""),
            "benchmark_score": run.get("benchmark_score", 0),
            "completed_work_packages": [item["work_package_id"] for item in self.storage.list_agent_runs(run_id) if item["status"] == "completed"],
            "patch_sets": self.storage.list_patch_sets(run_id),
            "test_runs": self.storage.list_test_runs(run_id),
            "artifacts": artifacts,
            "next_action": run.get("continuation_state", {}).get("next_action", ""),
        }

    def get_run_artifacts(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        return {
            "run_id": run_id,
            "artifact_manifest_path": run.get("artifact_manifest_path", ""),
            "items": self.storage.list_artifacts(run_id=run_id),
        }

    def pause_run(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return run
        jobs = self.storage.list_durable_jobs(run_id=run_id)
        leased = [item for item in jobs if item.get("status") == "leased"]
        if leased:
            self.storage.add_event(
                run_id,
                "warning",
                "chief",
                "Run pause requested; active job will stop only at a job boundary.",
                {"leased_jobs": [item["id"] for item in leased]},
            )
            return self.storage.update_run(
                run_id,
                chief_summary="Pause requested; waiting for active job boundary.",
                durable_queue_state={**run.get("durable_queue_state", {}), "state": "pause_requested"},
                continuation_state={
                    **run.get("continuation_state", {}),
                    "state": "pause_requested",
                    "next_action": "wait_for_job_boundary",
                    "pause_requested": True,
                },
            )
        self.storage.pause_pending_durable_jobs_for_run(run_id, reason="pause_requested")
        self.storage.add_event(run_id, "warning", "chief", "Run paused at durable job boundary.")
        return self.storage.update_run(
            run_id,
            status="paused",
            chief_summary="Run paused at durable job boundary.",
            durable_queue_state={**run.get("durable_queue_state", {}), "state": "paused"},
            continuation_state={**run.get("continuation_state", {}), "state": "paused", "next_action": "resume", "pause_requested": False},
        )

    def resume_run(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        if run["status"] in {"running"}:
            return self.get_run(run_id)
        if run["status"] in {"completed", "release_candidate_ready"}:
            return self.get_run(run_id)
        project = self.storage.get_project(run["project_id"])
        recovery = self._validate_run_recovery_contract(run_id)
        if not recovery["ok"]:
            return self._block_resume_missing_artifacts(run_id, recovery)
        job = self.storage.enqueue_durable_job(
            {
                "tenant_id": run.get("tenant_id") or project.get("tenant_id", self.default_tenant["id"]),
                "project_id": project["id"],
                "run_id": run_id,
                "kind": "run",
                "role": "planner",
                "priority": 40,
                "resume_key": f"run:{run_id}:chief",
                "status": "queued",
                "payload": {"project_id": project["id"], "resume": True},
            }
        )
        self.storage.add_event(run_id, "info", "chief", "Run resume requested.", {"durable_job_id": job["id"]})
        self.storage.update_run(
            run_id,
            status="running",
            chief_summary="Run resumed from durable state.",
            durable_queue_state={
                **run.get("durable_queue_state", {}),
                "backend": "sqlite",
                "state": "queued",
                "job_id": job["id"],
                "resume_key": job["resume_key"],
                "attempts": job["attempts"],
            },
            continuation_state={**run.get("continuation_state", {}), "state": "resuming", "next_action": "execute_waves"},
        )
        if self.config.runtime.queue_mode == "local-adapter":
            thread = threading.Thread(target=self._execute_run_safe, args=(project["id"], run_id), daemon=True)
            self._run_threads[run_id] = thread
            thread.start()
        return self.get_run(run_id)

    def get_agent_runs(self, run_id: str) -> list[dict]:
        return self.storage.list_agent_runs(run_id)

    def get_patch_sets(self, run_id: str) -> list[dict]:
        return self.storage.list_patch_sets(run_id)

    def get_test_runs(self, run_id: str) -> list[dict]:
        return self.storage.list_test_runs(run_id)

    def get_release_patch(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        candidate = self.storage.latest_release_candidate_for_project(run["project_id"])
        if not candidate or not candidate.get("release_patch_path"):
            return {"run_id": run_id, "available": False, "content": ""}
        path = Path(candidate["release_patch_path"])
        if not path.exists():
            return {"run_id": run_id, "available": False, "content": "", "path": str(path)}
        return {
            "run_id": run_id,
            "available": True,
            "path": str(path),
            "content": path.read_text(encoding="utf-8", errors="ignore")[:80000],
        }

    def cancel_run(self, run_id: str) -> dict:
        run = self.storage.get_run(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return run
        self.storage.cancel_durable_jobs_for_run(run_id, status="cancelled", reason="cancel_requested")
        self.storage.add_event(run_id, "warning", "chief", "Run cancellation requested.")
        updated = self.storage.update_run(
            run_id,
            status="cancelled",
            chief_summary="Run cancellation requested.",
            durable_queue_state={**run.get("durable_queue_state", {}), "state": "cancelled"},
            continuation_state={**run.get("continuation_state", {}), "state": "cancelled", "next_action": "resume"},
        )
        self._finalize_run_artifacts(run["project_id"], run_id)
        return updated

    def get_work_packages(self, project_id: str) -> dict:
        bundle = self.storage.get_requirement_bundle(project_id)
        return {
            "items": bundle.get("work_packages", []),
            "dag": build_dag(bundle.get("work_packages", [])),
        }

    def get_coverage(self, project_id: str) -> dict:
        bundle = self.storage.get_requirement_bundle(project_id)
        return bundle.get("coverage", {})

    def get_quality_gates(self, project_id: str) -> dict:
        runs = self.storage.list_project_runs(project_id)
        latest_run = runs[0] if runs else None
        if not latest_run:
            return {
                "project_id": project_id,
                "latest_run_id": "",
                "items": [],
                "status": "not_run",
            }
        validations = self.storage.list_validations(latest_run["id"])
        return {
            "project_id": project_id,
            "latest_run_id": latest_run["id"],
            "items": validations,
            "status": validations[-1]["status"] if validations else "not_run",
        }

    def run_repair_task(self, repair_id: str) -> dict:
        repair = self.storage.get_repair_task(repair_id)
        if repair["status"] not in {"ready", "failed"}:
            return repair
        self.storage.update_repair_task(repair_id, status="running")
        self.storage.add_event(
            repair["run_id"],
            "info",
            "chief",
            "Repair task dispatched.",
            {"repair_id": repair_id, "assigned_role": repair["assigned_role"], "finding_code": repair["finding_code"]},
        )
        updated = self.storage.update_repair_task(
            repair_id,
            status="blocked",
            reason=repair["reason"] + " | Awaiting implementation agent patch set.",
        )
        self.storage.add_event(
            repair["run_id"],
            "warning",
            repair["assigned_role"],
            "Repair task requires a real patch set; no fallback will be generated.",
            {"repair_id": repair_id},
        )
        return updated

    def get_release_candidate(self, candidate_id: str) -> dict:
        return self.storage.get_release_candidate(candidate_id)

    def _prepare_release_rollback(self, candidate: dict, reason: str = "auto_apply") -> dict:
        project = self.storage.get_project(candidate["project_id"])
        tenant_id = project.get("tenant_id", self.default_tenant["id"])
        project_root = Path(project["project_path"])
        git = GitRuntime(
            project_root=project_root,
            worktree_root=self.config.root_dir / "workspace" / "v2-worktrees" / candidate["project_id"],
            timeout_seconds=self.config.runtime.max_shell_seconds,
        )
        sandbox = git.ensure_repository()
        base_sha = sandbox["base_sha"]
        project_segment = candidate["project_id"][:12]
        run_segment = candidate["run_id"][:12]
        rollback_dir = (
            self.config.root_dir
            / "workspace"
            / "artifacts"
            / tenant_id
            / project_segment
            / run_segment
            / "rollback"
        )
        rollback_dir.mkdir(parents=True, exist_ok=True)
        reverse_patch_path = rollback_dir / f"{candidate['id'][:12]}-reverse.patch"
        release_patch_path = Path(candidate.get("release_patch_path", ""))
        if release_patch_path.exists():
            reverse_patch_path.write_text(release_patch_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        else:
            reverse_patch_path.write_text("", encoding="utf-8")
        release_patch_sha = sha256_file(release_patch_path) if release_patch_path.exists() else ""
        reverse_patch_sha = sha256_file(reverse_patch_path)
        reverse_patch_size = reverse_patch_path.stat().st_size
        release_patch_size = release_patch_path.stat().st_size if release_patch_path.exists() else 0
        manifest = {
            "schema_version": "1.1.0",
            "kind": "v2-release-rollback-manifest",
            "release_candidate_id": candidate["id"],
            "project_id": candidate["project_id"],
            "run_id": candidate["run_id"],
            "base_sha_before_apply": base_sha,
            "reverse_patch_path": str(reverse_patch_path),
            "reverse_patch_sha256": reverse_patch_sha,
            "reverse_patch_size_bytes": reverse_patch_size,
            "reverse_patch_mode": "git_apply_reverse",
            "release_patch_path": candidate.get("release_patch_path", ""),
            "release_patch_sha256": release_patch_sha,
            "release_patch_size_bytes": release_patch_size,
            "test_evidence_count": candidate.get("test_evidence_count", 0),
            "quality_gate": candidate.get("quality_gate", {}),
            "reason": reason,
            "created_at": utc_now(),
        }
        manifest_path = rollback_dir / f"{candidate['id'][:12]}-rollback-manifest.json"
        manifest["manifest_path"] = str(manifest_path)
        integrity = write_json_with_integrity(manifest_path, {**manifest, "sha256": "", "size_bytes": 0})
        manifest = integrity["payload"]
        rollback = self.storage.save_release_rollback(
            {
                "tenant_id": tenant_id,
                "project_id": candidate["project_id"],
                "run_id": candidate["run_id"],
                "release_candidate_id": candidate["id"],
                "status": "prepared",
                "reverse_patch_path": str(reverse_patch_path),
                "manifest_path": str(manifest_path),
                "reason": reason,
                "payload": manifest,
            }
        )
        self.storage.update_run(candidate["run_id"], rollback_manifest_path=str(manifest_path))
        return rollback

    def _verify_project_base_sha(self, project: dict, expected_sha: str) -> dict:
        git = GitRuntime(
            project_root=Path(project["project_path"]),
            worktree_root=self.config.root_dir / "workspace" / "v2-worktrees" / project["id"],
            timeout_seconds=self.config.runtime.max_shell_seconds,
        )
        sandbox = git.ensure_repository()
        dirty = sandbox.get("dirty_paths", [])
        current_sha = sandbox.get("base_sha", "")
        if dirty:
            git.git(["add", "-A"], check=False)
            commit = git.git(
                [
                    "-c",
                    "user.name=V2 Chief",
                    "-c",
                    "user.email=v2-chief@local",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "v2 rollback verification snapshot",
                ],
                check=False,
            )
            if commit.ok:
                current_sha = git.git(["rev-parse", "HEAD"]).stdout.strip()
                dirty = git.git(["status", "--porcelain"], check=False).stdout.strip().splitlines()
        return {
            "ok": (not expected_sha or current_sha == expected_sha) and not dirty,
            "expected_sha": expected_sha,
            "current_sha": current_sha,
            "dirty_paths": dirty,
        }

    def _verify_release_files_reverted(self, project: dict, candidate: dict, rollback: dict) -> dict:
        project_root = Path(project["project_path"])
        release_patch = Path(candidate.get("release_patch_path", ""))
        changed_files = candidate.get("changed_files", []) or []
        forbidden_existing: list[str] = []
        if release_patch.exists():
            try:
                diff_text = release_patch.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                diff_text = ""
            for relative in re.findall(r"^\+\+\+ b/(.+)$", diff_text, flags=re.MULTILINE):
                if relative and relative not in changed_files:
                    changed_files.append(relative)
        for relative in changed_files:
            if not str(relative).strip() or str(relative).startswith(".agent/v2/"):
                continue
            target = (project_root / str(relative)).resolve()
            if project_root not in target.parents and target != project_root:
                continue
            if target.exists():
                forbidden_existing.append(str(relative))
        return {
            "ok": not forbidden_existing,
            "changed_files": changed_files,
            "unexpected_existing_files": forbidden_existing,
            "base_sha_before_apply": rollback.get("payload", {}).get("base_sha_before_apply", ""),
        }

    def _write_delivery_pack(self, candidate: dict, project: dict, rollback: dict, smoke_results: list[dict] | None = None) -> dict:
        run = self.storage.get_run(candidate["run_id"])
        requirement_bundle = self.storage.get_requirement_bundle(candidate["project_id"])
        latest_quality = (self.storage.list_validations(candidate["run_id"]) or [{}])[-1]
        payload = {
            "schema_version": "1.0.0",
            "kind": "v2-delivery-pack",
            "project_id": candidate["project_id"],
            "run_id": candidate["run_id"],
            "release_candidate_id": candidate["id"],
            "generated_at": utc_now(),
            "change_summary": {
                "changed_files": candidate.get("changed_files", []),
                "base_sha": candidate.get("base_sha", ""),
                "final_sha": candidate.get("final_sha", ""),
                "release_patch_path": candidate.get("release_patch_path", ""),
            },
            "requirement_coverage": requirement_bundle.get("coverage", {}),
            "effective_loc": run.get("effective_loc_metrics", {}),
            "test_evidence": {
                "candidate_test_evidence_count": candidate.get("test_evidence_count", 0),
                "smoke": smoke_results or [],
            },
            "quality_gate": latest_quality,
            "risk_summary": candidate.get("blockers", []),
            "rollback_path": {
                "manifest_path": rollback.get("manifest_path", ""),
                "reverse_patch_path": rollback.get("reverse_patch_path", ""),
                "status": rollback.get("status", ""),
            },
            "operations_manual_entry": "docs/operations/manual.md",
        }
        artifact = self._register_artifact(
            tenant_id=project.get("tenant_id", self.default_tenant["id"]),
            project_id=candidate["project_id"],
            run_id=candidate["run_id"],
            kind="delivery",
            name="delivery-pack.json",
            payload=payload,
        )
        self.storage.update_release_candidate(candidate["id"], delivery_pack_path=artifact["path"])
        self.storage.add_event(candidate["run_id"], "info", "release", "Delivery pack recorded.", {"path": artifact["path"]})
        current = self.storage.get_run(candidate["run_id"])
        self.storage.update_run(
            candidate["run_id"],
            continuation_state={
                **current.get("continuation_state", {}),
                "delivery_pack_path": artifact["path"],
            },
        )
        return artifact

    def apply_release_candidate(self, candidate_id: str) -> dict:
        candidate = self.storage.get_release_candidate(candidate_id)
        if candidate.get("decision") != "GO" or candidate.get("status") != "ready":
            raise ValueError("Only ready GO release candidates can be applied.")
        patch_path = Path(candidate.get("release_patch_path", ""))
        if not patch_path.exists():
            raise ValueError("Release patch file is missing.")
        validation = self._validate_run_recovery_contract(candidate["run_id"])
        if not validation.get("ok"):
            raise ValueError("Release candidate artifacts failed recovery preflight.")
        if candidate.get("quality_gate", {}).get("status") != "passed" or candidate.get("test_evidence_count", 0) <= 0:
            raise ValueError("Release candidate lacks hard gate evidence.")
        project = self.storage.get_project(candidate["project_id"])
        git = GitRuntime(
            project_root=Path(project["project_path"]),
            worktree_root=self.config.root_dir / "workspace" / "v2-worktrees" / candidate["project_id"],
            timeout_seconds=self.config.runtime.max_shell_seconds,
        )
        rollback = self.storage.latest_release_rollback_for_candidate(candidate_id) or self._prepare_release_rollback(
            candidate,
            reason="manual_apply",
        )
        result = git.apply_patch(Path(project["project_path"]), patch_path)
        self.storage.add_event(
            candidate["run_id"],
            "info" if result["ok"] else "error",
            "chief",
            "Release candidate apply attempted.",
            {"candidate_id": candidate_id, "result": result},
        )
        if not result["ok"]:
            raise ValueError(result.get("stderr") or "Release patch failed to apply.")
        updated_candidate = self.storage.update_release_candidate(candidate_id, status="applied", applied_at=utc_now())
        rollback = self.storage.update_release_rollback(
            rollback["id"],
            status="prepared",
            payload={**rollback.get("payload", {}), "reverse_patch_recorded_at": utc_now(), "reverse_patch_mode": "git_apply_reverse"},
        )
        self.storage.update_run(candidate["run_id"], rollback_manifest_path=rollback.get("manifest_path", ""))
        self.storage.save_integration_step(
            {
                "run_id": candidate["run_id"],
                "step_type": "apply_release_candidate",
                "status": "applied",
                "work_package_id": "release",
                "patch_set_id": "",
                "message": "Release patch applied to project root.",
                "payload": {"candidate_id": candidate_id, "rollback_id": rollback["id"]},
            }
        )
        current = self.storage.get_run(candidate["run_id"])
        self.storage.update_run(
            candidate["run_id"],
            continuation_state={
                **current.get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "apply_complete",
                "state": "applied",
                "next_action": "post_apply_smoke",
                "rollback_manifest_path": rollback.get("manifest_path", ""),
            },
        )
        self._audit(
            tenant_id=project.get("tenant_id", self.default_tenant["id"]),
            user_id=project.get("created_by", self.default_user["id"]),
            action="release_candidate.applied",
            resource_type="release_candidate",
            resource_id=candidate_id,
            message="Release candidate applied with rollback manifest prepared.",
            payload={"rollback_id": rollback["id"]},
        )
        delivery_pack = self._write_delivery_pack(updated_candidate, project, rollback)
        return {"ok": True, "candidate": self.storage.get_release_candidate(candidate_id), "rollback": rollback, "delivery_pack": delivery_pack}

    def auto_apply_release_candidate(self, candidate_id: str) -> dict:
        candidate = self.storage.get_release_candidate(candidate_id)
        project = self.storage.get_project(candidate["project_id"])
        if project.get("unattended_mode") != "auto_publish_with_rollback":
            raise ValueError("Project unattended_mode must be auto_publish_with_rollback.")
        rollback = self._prepare_release_rollback(candidate, reason="auto_apply")
        try:
            applied = self.apply_release_candidate(candidate_id)
            smoke_results = TestRunner(self.config.runtime.max_shell_seconds).run(Path(project["project_path"]), repair_round=0)
            for result in smoke_results:
                self.storage.save_test_run(candidate["run_id"], {**result, "kind": f"smoke:{result.get('kind', 'custom')}"})
            if not smoke_results or any(item.get("status") != "passed" for item in smoke_results):
                rolled_back = self.rollback_release_candidate(candidate_id, reason="smoke_check_failed")
                return {"ok": False, "candidate": applied["candidate"], "rollback": rolled_back["rollback"], "smoke": smoke_results}
            self.storage.update_release_rollback(rollback["id"], status="not_needed")
            delivery_pack = self._write_delivery_pack(self.storage.get_release_candidate(candidate_id), project, rollback, smoke_results)
            self.storage.add_event(candidate["run_id"], "info", "release", "Auto apply smoke check passed.", {"candidate_id": candidate_id})
            return {"ok": True, "candidate": self.storage.get_release_candidate(candidate_id), "rollback": rollback, "smoke": smoke_results, "delivery_pack": delivery_pack}
        except Exception:
            self.rollback_release_candidate(candidate_id, reason="auto_apply_exception")
            raise

    def rollback_release_candidate(self, candidate_id: str, reason: str = "manual_rollback") -> dict:
        candidate = self.storage.get_release_candidate(candidate_id)
        rollback = self.storage.latest_release_rollback_for_candidate(candidate_id)
        if not rollback:
            raise ValueError("No rollback manifest exists for this release candidate.")
        project = self.storage.get_project(candidate["project_id"])
        patch_path = Path(rollback.get("reverse_patch_path", ""))
        if not patch_path.exists():
            self.storage.update_release_rollback(rollback["id"], status="failed", reason=reason, applied_at=utc_now())
            self.storage.update_release_candidate(candidate_id, status="rollback_failed")
            self.storage.update_run(
                candidate["run_id"],
                status="blocked",
                continuation_state={
                    **self.storage.get_run(candidate["run_id"]).get("continuation_state", {}),
                    "schema_version": "2.2.0",
                    "checkpoint": "rollback_failed",
                    "state": "rollback_failed",
                    "next_action": "manual_recovery",
                    "failure_reason": "Reverse patch file is missing.",
                },
            )
            raise ValueError("Reverse patch file is missing.")
        git = GitRuntime(
            project_root=Path(project["project_path"]),
            worktree_root=self.config.root_dir / "workspace" / "v2-worktrees" / candidate["project_id"],
            timeout_seconds=self.config.runtime.max_shell_seconds,
        )
        if patch_path.stat().st_size > 0:
            result = git.apply_patch(Path(project["project_path"]), patch_path, reverse=True)
            if not result["ok"]:
                self.storage.update_release_rollback(rollback["id"], status="failed", reason=reason, applied_at=utc_now())
                self.storage.update_release_candidate(candidate_id, status="rollback_failed")
                self.storage.update_run(
                    candidate["run_id"],
                    status="blocked",
                    continuation_state={
                        **self.storage.get_run(candidate["run_id"]).get("continuation_state", {}),
                        "schema_version": "2.2.0",
                        "checkpoint": "rollback_failed",
                        "state": "rollback_failed",
                        "next_action": "manual_recovery",
                        "failure_reason": result.get("stderr") or "Rollback patch failed to apply.",
                    },
                )
                raise ValueError(result.get("stderr") or "Rollback patch failed to apply.")
        else:
            result = {"ok": True, "status": "noop", "stdout": "", "stderr": ""}
        verification = self._verify_release_files_reverted(project, candidate, rollback)
        if not verification["ok"]:
            failed = self.storage.update_release_rollback(
                rollback["id"],
                status="failed",
                reason="rollback_verification_failed",
                applied_at=utc_now(),
                payload={**rollback.get("payload", {}), "rollback_result": result, "verification": verification},
            )
            self.storage.update_release_candidate(candidate_id, status="rollback_failed")
            self.storage.update_run(
                candidate["run_id"],
                status="blocked",
                continuation_state={
                    **self.storage.get_run(candidate["run_id"]).get("continuation_state", {}),
                    "schema_version": "2.2.0",
                    "checkpoint": "rollback_failed",
                    "state": "rollback_failed",
                    "next_action": "manual_recovery",
                    "failure_reason": "Rollback verification failed.",
                },
            )
            return {"ok": False, "candidate": self.storage.get_release_candidate(candidate_id), "rollback": failed}
        updated_rollback = self.storage.update_release_rollback(
            rollback["id"],
            status="applied",
            reason=reason,
            applied_at=utc_now(),
            payload={**rollback.get("payload", {}), "rollback_result": result, "rollback_reason": reason, "verification": verification},
        )
        updated_candidate = self.storage.update_release_candidate(candidate_id, status="rolled_back")
        self.storage.update_run(
            candidate["run_id"],
            continuation_state={
                **self.storage.get_run(candidate["run_id"]).get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "rollback_complete",
                "state": "rolled_back",
                "next_action": "repair_or_reapply",
            },
        )
        self.storage.save_integration_step(
            {
                "run_id": candidate["run_id"],
                "step_type": "rollback_release_candidate",
                "status": "applied",
                "work_package_id": "release",
                "patch_set_id": "",
                "message": "Release candidate rollback applied.",
                "payload": {"candidate_id": candidate_id, "rollback_id": rollback["id"], "reason": reason},
            }
        )
        self.storage.add_event(candidate["run_id"], "warning", "release", "Release rollback applied.", {"candidate_id": candidate_id, "reason": reason})
        return {"ok": True, "candidate": updated_candidate, "rollback": updated_rollback}

    def approve(self, project_id: str, approver: str, note: str = "", tenant_id: str | None = None, user_id: str | None = None) -> dict:
        project = self.storage.get_project(project_id)
        session = self.resolve_session(tenant_id or project.get("tenant_id"), user_id or approver)
        tenant = session["tenant"]
        user = session["user"]
        if project.get("tenant_id") != tenant["id"]:
            raise ValueError("Project is outside the current tenant.")
        candidate = self.storage.latest_release_candidate_for_project(project_id)
        if not candidate:
            raise ValueError("No release candidate exists.")
        if candidate.get("status") != "ready" or candidate.get("decision") != "GO":
            self._audit(
                tenant_id=tenant["id"],
                user_id=user["id"],
                action="approval.rejected",
                resource_type="release_candidate",
                resource_id=candidate.get("id", ""),
                message="Blocked or NO_GO release candidate approval was rejected.",
                payload={"project_id": project_id, "decision": candidate.get("decision")},
            )
            raise ValueError("Blocked or NO_GO release candidates cannot be approved.")
        approval = self.storage.save_approval(
            {
                "tenant_id": tenant["id"],
                "project_id": project_id,
                "release_candidate_id": candidate["id"],
                "approver_user_id": user["id"],
                "approver": approver or user["display_name"],
                "decision": "approved",
                "note": note,
            }
        )
        self.storage.update_project(project_id, approval_state="approved")
        self._audit(
            tenant_id=tenant["id"],
            user_id=user["id"],
            action="approval.created",
            resource_type="release_candidate",
            resource_id=candidate["id"],
            message="Release candidate approved.",
            payload={"project_id": project_id, "approval_id": approval["id"]},
        )
        return {"ok": True, "approval": approval, "project": self.get_project(project_id)}

    def current_tenant(self, tenant_id: str | None = None, user_id: str | None = None) -> dict:
        session = self.resolve_session(tenant_id, user_id)
        return {
            "tenant": session["tenant"],
            "user": session["user"],
            "project_count": len(self.storage.list_projects_for_tenant(session["tenant"]["id"])),
        }

    def list_audit_events(self, tenant_id: str | None = None) -> list[dict]:
        session = self.resolve_session(tenant_id, None)
        return self.storage.list_audit_events(session["tenant"]["id"])

    def list_worker_jobs(self, tenant_id: str | None = None) -> list[dict]:
        session = self.resolve_session(tenant_id, None)
        return self.storage.list_worker_jobs(session["tenant"]["id"])

    def list_durable_jobs(self, tenant_id: str | None = None, run_id: str | None = None) -> list[dict]:
        session = self.resolve_session(tenant_id, None)
        return self.storage.list_durable_jobs(tenant_id=session["tenant"]["id"], run_id=run_id)

    def get_worker_status(self, tenant_id: str | None = None) -> dict:
        from dev_orchestrator.v2.worker import DEFAULT_WORKER_ROLES, describe_worker_runtime

        session = self.resolve_session(tenant_id, None)
        tenant = session["tenant"]
        durable_jobs = self.storage.list_durable_jobs(tenant_id=tenant["id"], limit=500)
        worker_jobs = self.storage.list_worker_jobs(tenant["id"])[:500]
        return {
            **describe_worker_runtime(
                self.config.root_dir,
                durable_jobs=durable_jobs,
                worker_jobs=worker_jobs,
                roles=list(DEFAULT_WORKER_ROLES),
            ),
            "tenant_id": tenant["id"],
        }

    def get_enterprise_saas_benchmark(self) -> dict:
        recent = self.storage.list_benchmark_runs(benchmark_type="enterprise_saas")[:50]
        ladder_ids = ["10k", "30k", "60k", "100k"]
        trends = []
        for ladder_id in ladder_ids:
            values = [
                {
                    "run_id": item.get("run_id", ""),
                    "score": item.get("score", 0),
                    "effective_loc": item.get("effective_loc", 0),
                    "ready": any(rung.get("id") == ladder_id and rung.get("ready") for rung in item.get("payload", {}).get("ladder", [])),
                    "created_at": item.get("created_at", ""),
                }
                for item in recent
                if any(rung.get("id") == ladder_id for rung in item.get("payload", {}).get("ladder", []))
            ]
            trends.append(
                {
                    "id": ladder_id,
                    "run_count": len(values),
                    "ready_count": sum(1 for item in values if item["ready"]),
                    "latest": values[0] if values else {},
                }
            )
        return {
            "benchmark": ENTERPRISE_SAAS_BENCHMARK,
            "ladder_trends": trends,
            "recent_runs": recent,
        }
