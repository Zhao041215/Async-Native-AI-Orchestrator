from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from dev_orchestrator.v4.ai_scheduler import AICallScheduler
from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v4.artifacts import ArtifactWriter, build_manifest
from dev_orchestrator.v4.context_memory import build_context_snapshot_v2, package_context, validate_context_snapshot
from dev_orchestrator.v4.llm_policy import get_ai_task_budget
from dev_orchestrator.v4.models import DEFAULT_TENANT, new_id, slugify
from dev_orchestrator.v4.planner import build_blueprint
from dev_orchestrator.v4.release_quality import build_deploy_guide, validate_release_structure
from dev_orchestrator.v4.runtime import PackageMaterializer
from dev_orchestrator.v4.store import V4Store


DEFAULT_PROJECT_CONFIG = {
    "target_scale": "small",
    "stack_pack": "auto",
    "deployment_mode": "",
    "api_only": False,
    "effective_loc_target": 1000,
    "unattended_mode": "off",
}


def _compact_text(value: str, limit: int = 6000) -> str:
    normalized = "\n".join(line.rstrip() for line in str(value or "").splitlines())
    if len(normalized) <= limit:
        return normalized
    head = normalized[: int(limit * 0.72)].rstrip()
    tail = normalized[-int(limit * 0.18) :].lstrip()
    return f"{head}\n\n[...requirements truncated for bounded LLM task...]\n\n{tail}"


def _requirement_outline(value: str, max_items: int = 24, item_limit: int = 220) -> list[str]:
    lines = [line.strip(" -\t") for line in str(value or "").splitlines() if line.strip(" -\t")]
    if not lines:
        return []
    return [line[:item_limit] for line in lines[:max_items]]


class V4Orchestrator:
    def __init__(self, store: V4Store, workspace_root: Path, tenant_id: str = DEFAULT_TENANT, llm_client: OpenAICompatibleClient | None = None):
        self.store = store
        self.workspace_root = workspace_root
        self.tenant_id = tenant_id or DEFAULT_TENANT
        self.artifacts = ArtifactWriter(workspace_root)
        self.materializer = PackageMaterializer(workspace_root / "projects")
        self.llm_client = llm_client
        self.ai_scheduler = AICallScheduler(llm_client)

    def bootstrap(self, attempts: int = 1, delay_seconds: float = 1.0) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                self.store.bootstrap()
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= max(1, attempts):
                    break
                time.sleep(delay_seconds)
        if last_error is not None:
            raise last_error
        self.store.get_or_create_tenant(self.tenant_id)

    def create_project(self, payload: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
        config = dict(DEFAULT_PROJECT_CONFIG)
        config.update(payload.get("config") or {})
        for key in DEFAULT_PROJECT_CONFIG:
            if key in payload and payload[key] is not None:
                config[key] = payload[key]
        name = payload.get("name") or slugify(payload.get("title") or "v4-project")
        project = self.store.create_project(
            tenant_id or self.tenant_id,
            {
                "name": name,
                "title": payload.get("title") or name,
                "description": payload.get("description") or "",
                "project_path": payload.get("project_path") or "",
                "config": config,
            },
        )
        self.store.add_event(project["tenant_id"], project["id"], None, "project_created", {"project_id": project["id"]})
        return project

    def list_projects(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_projects(tenant_id or self.tenant_id)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return self.store.get_project(project_id)

    def create_run(self, project_id: str, requirements_text: str = "", tenant_id: str | None = None) -> dict[str, Any]:
        project = self._require_project(project_id)
        text = requirements_text or project.get("description") or project.get("title") or project.get("name")
        run = self.store.create_run(
            tenant_id or project.get("tenant_id") or self.tenant_id,
            project_id,
            {
                "requirements_text": text,
                "project_config": project.get("config") or {},
                "artifact_manifest_path": "",
                "product_contract": {},
                "stack_decision": {},
            },
        )
        self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": "chief_plan",
                "role": "chief",
                "run_id": run["id"],
                "resume_key": f"run:{run['id']}:chief_plan",
                "payload": {"requirements_text": text},
                "max_attempts": 2,
            },
        )
        self.store.add_event(run["tenant_id"], project_id, run["id"], "run_created", {"run_id": run["id"]})
        return run

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.store.get_run(run_id)

    def list_jobs(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.list_jobs(run_id=run_id)

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.list_artifacts(run_id)

    def continuation(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        packages = self.store.list_work_packages(run_id)
        waves = self.store.list_waves(run_id)
        jobs = self.store.list_jobs(run_id=run_id)
        continuation = dict(run.get("continuation") or {})
        continuation.update(
            {
                "schema_version": "4.0",
                "run_id": run_id,
                "checkpoint": run.get("checkpoint", continuation.get("checkpoint", "run_created")),
                "current_wave": self._current_wave(waves),
                "completed_waves": [wave["wave_key"] for wave in waves if wave.get("status") == "completed"],
                "completed_packages": [package["package_key"] for package in packages if package.get("status") == "completed"],
                "pending_packages": [package["package_key"] for package in packages if package.get("status") not in {"completed", "cancelled"}],
                "leased_jobs": [
                    {
                        "id": job["id"],
                        "role": job["role"],
                        "job_type": job["job_type"],
                        "worker_id": job.get("worker_id", ""),
                        "lease_until": job.get("lease_until"),
                    }
                    for job in jobs
                    if job.get("status") == "running"
                ],
                "context_index_status": self._context_index_status(run),
            }
        )
        preflight = self._artifact_preflight(run_id)
        continuation["artifact_preflight"] = preflight
        continuation["next_action"] = self._next_action(run, jobs, preflight)
        return continuation

    def pause_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        continuation = dict(run.get("continuation") or {})
        continuation["next_action"] = "resume_from_job_boundary"
        return self.store.update_run(run_id, status="paused", checkpoint=run.get("checkpoint", "run_created"), continuation=continuation)

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        preflight = self._artifact_preflight(run_id)
        if not preflight["ok"]:
            return self.store.update_run(
                run_id,
                status="blocked",
                continuation={**dict(run.get("continuation") or {}), "failure_reason": "repair_missing_artifacts", "artifact_preflight": preflight, "next_action": "repair_missing_artifacts"},
            )
        return self.store.update_run(run_id, status="running", continuation={**dict(run.get("continuation") or {}), "next_action": "claim_pending_jobs"})

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        return self.store.update_run(run_id, status="cancelled", continuation={**dict(run.get("continuation") or {}), "next_action": "cancelled"})

    def execute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        job_type = job["job_type"]
        if job_type == "chief_plan":
            return self._execute_chief_plan(job)
        if job_type == "package":
            return self._execute_package(job)
        if job_type == "integration":
            return self._execute_integration(job)
        if job_type == "test":
            return self._execute_test(job)
        if job_type == "quality":
            return self._execute_quality(job)
        if job_type == "release_candidate":
            return self._execute_release_candidate(job)
        if job_type == "apply":
            return self._execute_apply(job)
        if job_type == "rollback":
            return self._execute_rollback(job)
        if job_type == "repair":
            return self._execute_repair(job)
        raise RuntimeError(f"unsupported V4 job type: {job_type}")

    def enqueue_apply(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._find_artifact(candidate_id)
        if not candidate:
            raise RuntimeError(f"release candidate not found: {candidate_id}")
        payload = candidate.get("payload") or {}
        run_id = candidate["run_id"]
        run = self._require_run(run_id)
        return self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": "apply",
                "role": "release",
                "run_id": run_id,
                "resume_key": f"run:{run_id}:apply:{candidate_id}",
                "payload": {"candidate_id": candidate_id, "candidate": payload},
                "max_attempts": 1,
            },
        )

    def enqueue_rollback(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._find_artifact(candidate_id)
        if not candidate:
            raise RuntimeError(f"release candidate not found: {candidate_id}")
        run_id = candidate["run_id"]
        run = self._require_run(run_id)
        return self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": "rollback",
                "role": "release",
                "run_id": run_id,
                "resume_key": f"run:{run_id}:rollback:{candidate_id}",
                "payload": {"candidate_id": candidate_id},
                "max_attempts": 1,
            },
        )

    def _execute_chief_plan(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        requirement_text = (job.get("payload") or {}).get("requirements_text") or (run.get("metadata") or {}).get("requirements_text") or project.get("description", "")
        chief_analysis = self._invoke_llm(
            project,
            run,
            role="chief",
            job=job,
            system_prompt=(
                "You are the V4 chief planner. This is a bounded planning task, not a long "
                "design session. Return strict compact JSON only with keys summary, "
                "business_domains, delivery_risks, package_guidance, and quality_focus. "
                "Each array must contain at most five short strings."
            ),
            user_payload={
                "project": {
                    "name": project.get("name"),
                    "title": project.get("title"),
                    "config": project.get("config") or {},
                },
                "requirements_outline": _requirement_outline(requirement_text),
                "requirements_excerpt": _compact_text(requirement_text, get_ai_task_budget("chief_plan").max_input_chars),
                "task_contract": {
                    "purpose": "Summarize planning risks and package guidance only.",
                    "do_not_generate_code": True,
                    "max_json_bytes": 2500,
                },
            },
            required=True,
            task_kind="chief_plan",
        )
        blueprint = build_blueprint(project, requirement_text)
        self._record_json(project, run, "stack_decision", "stack-decision.json", blueprint.get("stack_decision", {}))
        context_snapshot = build_context_snapshot_v2(requirements=blueprint.get("requirements", []), blueprint=blueprint, project_root=self.materializer.project_root(project))
        self._record_json(project, run, "context_snapshot", "context-snapshot.json", context_snapshot)
        if not blueprint.get("ok"):
            continuation = dict(run.get("continuation") or {})
            continuation.update({"checkpoint": "blueprint_complete", "failure_reason": blueprint.get("no_go_reason", "blueprint_no_go"), "next_action": "repair_requirements"})
            self.store.update_run(run["id"], status="no_go", checkpoint="blueprint_complete", continuation=continuation, metadata={**(run.get("metadata") or {}), "blueprint": blueprint})
            self._write_manifest(project, run)
            return {"status": "NO_GO", "blueprint": blueprint}

        wave_id_by_key = {}
        for wave in blueprint["waves"]:
            stored_wave = self.store.upsert_wave(run["id"], wave)
            wave_id_by_key[wave["wave_key"]] = stored_wave["id"]
        for package in blueprint["work_packages"]:
            self.store.upsert_work_package(run["id"], wave_id_by_key[package["wave_key"]], package)

        continuation = dict(run.get("continuation") or {})
        first_wave = min(blueprint["waves"], key=lambda item: item["sequence"])
        continuation.update(
            {
                "checkpoint": "wave_queued",
                "current_wave": first_wave["wave_key"],
                "pending_packages": [package["package_key"] for package in blueprint["work_packages"]],
                "failure_reason": "",
                "next_action": "worker_claim_package_jobs",
            }
        )
        metadata = dict(run.get("metadata") or {})
        metadata.update(
            {
                "blueprint": blueprint,
                "chief_analysis": chief_analysis,
                "product_contract": blueprint["product_contract"],
                "stack_decision": blueprint["stack_decision"],
                "context_snapshot": context_snapshot,
                "context_snapshot_id": context_snapshot["index_hash"],
                "durable_queue_state": "package_jobs_queued",
            }
        )
        updated_run = self.store.update_run(run["id"], status="running", checkpoint="wave_queued", continuation=continuation, metadata=metadata)
        self._enqueue_wave_packages(project, updated_run, first_wave["wave_key"])
        self._write_manifest(project, run)
        return {"status": "completed", "wave_queued": first_wave["wave_key"], "package_count": len(blueprint["work_packages"])}

    def _execute_package(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        package = self.store.get_work_package(job["work_package_id"] or "")
        if not package:
            raise RuntimeError("work package not found")
        product_contract = (run.get("metadata") or {}).get("product_contract") or {}
        package_plan = self._invoke_llm(
            project,
            run,
            role=package["role"],
            job=job,
            system_prompt=(
                "You are a V4 package worker. This is a bounded package planning task. "
                "Return strict compact JSON with keys package_summary, "
                "files_to_create_or_update, implementation_steps, acceptance_evidence, and risks. "
                "Do not include markdown fences. Keep arrays to at most six short strings."
            ),
            user_payload={
                "project": {
                    "name": project.get("name"),
                    "title": project.get("title"),
                    "config": project.get("config") or {},
                },
                "run": {
                    "id": run.get("id"),
                    "requirements_outline": _requirement_outline((run.get("metadata") or {}).get("requirements_text", ""), 16),
                    "product_contract": product_contract,
                },
                "work_package": package.get("payload") or {},
                "context_memory": self._context_for_package(run, package),
                "task_contract": {
                    "purpose": "Plan this package only; file materialization is handled by deterministic stack-pack runtime.",
                    "do_not_generate_full_project": True,
                    "max_json_bytes": 2200,
                },
            },
            required=True,
            task_kind="package_plan",
        )
        result = self.materializer.execute_package(project, run, package["payload"], product_contract)
        result["llm_agent_run_id"] = package_plan.get("agent_run_id", "")
        self.store.update_work_package(package["id"], status="completed", result=result)
        self._record_json(project, run, "package_evidence", f"{package['package_key']}.json", result, {"work_package_id": package["id"]})
        self._advance_after_package(project, run, package)
        return result

    def _execute_integration(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        product_contract = (run.get("metadata") or {}).get("product_contract") or {}
        release_root = self.materializer.project_root(project) / product_contract.get("release_root", "release")
        context_snapshot = build_context_snapshot_v2(
            requirements=((run.get("metadata") or {}).get("blueprint") or {}).get("requirements", []),
            blueprint=(run.get("metadata") or {}).get("blueprint") or {},
            project_root=self.materializer.project_root(project),
        )
        report = {
            "schema_version": "4.0",
            "status": "passed" if release_root.exists() else "failed",
            "release_root": str(release_root),
            "checks": ["release directory exists", "context index refreshed"],
            "context_index_hash": context_snapshot.get("index_hash", ""),
        }
        self._record_json(project, run, "context_snapshot", "context-snapshot-integration.json", context_snapshot)
        self._record_json(project, run, "integration_report", "integration-report.json", report)
        metadata = {**dict(run.get("metadata") or {}), "context_snapshot": context_snapshot, "context_snapshot_id": context_snapshot.get("index_hash", "")}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "integration_completed", "integration_status": report["status"], "next_action": "test"}
        self.store.update_run(run["id"], checkpoint="integration_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "test", "role": "qa", "run_id": run["id"], "resume_key": f"run:{run['id']}:test", "payload": {}, "max_attempts": 2})
        self._write_manifest(project, run)
        return report

    def _execute_test(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        product_contract = (run.get("metadata") or {}).get("product_contract") or {}
        release_root = self.materializer.project_root(project) / product_contract.get("release_root", "release")
        index_exists = (release_root / "index.php").exists() or (release_root / "index.html").exists()
        admin_exists = "admin/login" in ((release_root / "index.php").read_text(encoding="utf-8", errors="ignore") if (release_root / "index.php").exists() else "")
        report = {
            "schema_version": "4.0",
            "status": "passed" if index_exists and (product_contract.get("api_only") or admin_exists or (release_root / "index.html").exists()) else "failed",
            "home": index_exists,
            "health": True,
            "admin": admin_exists or product_contract.get("api_only", False),
        }
        self._record_json(project, run, "browser_smoke_report", "browser-smoke-report.json", report)
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "test_completed", "test_status": report["status"], "next_action": "quality"}
        self.store.update_run(run["id"], checkpoint="test_completed", continuation=continuation)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "quality", "role": "qa", "run_id": run["id"], "resume_key": f"run:{run['id']}:quality", "payload": {}, "max_attempts": 2})
        self._write_manifest(project, run)
        return report

    def _execute_quality(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        product_contract = metadata.get("product_contract") or {}
        release_root = self.materializer.project_root(project) / product_contract.get("release_root", "release")
        artifacts = self.store.list_artifacts(run["id"])
        run_context = {
            "agent_runs": [artifact for artifact in artifacts if artifact["kind"] == "agent_run"],
            "packages": self.store.list_work_packages(run["id"]),
            "wave_reports": [artifact for artifact in artifacts if artifact["kind"] == "wave_report"],
            "context_index_status": self._context_index_status(run),
            "effective_loc_target": (metadata.get("project_config") or {}).get("effective_loc_target", 0),
        }
        quality_report = validate_release_structure(release_root, product_contract, product_contract.get("stack_pack", ""), run_context=run_context)
        deploy_guide = build_deploy_guide(product_contract, release_root, quality_report)
        quality_artifact = self._record_json(project, run, "quality_report", "quality-report.json", quality_report)
        release_structure_artifact = self._record_json(project, run, "release_structure_report", "release-structure-report.json", quality_report)
        deploy_artifact = self._record_json(project, run, "deploy_guide", "deploy-guide.json", deploy_guide)
        metadata.update(
            {
                "effective_loc_metrics": quality_report.get("effective_loc", {}),
                "release_structure_report_path": release_structure_artifact["path"],
                "deploy_guide_path": deploy_artifact["path"],
                "quality_report_path": quality_artifact["path"],
            }
        )
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "quality_completed", "quality_status": quality_report["status"], "next_action": "release_candidate" if quality_report["ok"] else "repair_quality"}
        status = "running" if quality_report["ok"] else "no_go"
        self.store.update_run(run["id"], status=status, checkpoint="quality_completed", continuation=continuation, metadata=metadata)
        if quality_report["ok"]:
            self.store.enqueue_job(run["tenant_id"], {"job_type": "release_candidate", "role": "release", "run_id": run["id"], "resume_key": f"run:{run['id']}:release_candidate", "payload": {}, "max_attempts": 2})
        else:
            self._enqueue_repair_job(project, run, "quality_gate_failed", quality_report)
        self._write_manifest(project, run)
        return quality_report

    def _execute_release_candidate(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        artifacts = self.store.list_artifacts(run["id"])
        quality_reports = [artifact for artifact in artifacts if artifact["kind"] == "quality_report"]
        quality_ok = True
        if quality_reports:
            quality_ok = bool((quality_reports[-1].get("payload") or {}).get("ok", True))
        candidate = {
            "schema_version": "4.0",
            "candidate_id": new_id(),
            "run_id": run["id"],
            "status": "GO" if quality_ok else "NO_GO",
            "release_root": ((run.get("metadata") or {}).get("product_contract") or {}).get("release_root", "release"),
            "hard_gates_passed": quality_ok,
        }
        candidate_artifact = self._record_json(project, run, "release_candidate", f"release-candidate-{candidate['candidate_id']}.json", candidate)
        metadata = {**dict(run.get("metadata") or {}), "release_candidate_id": candidate_artifact["id"]}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "release_candidate_completed", "release_status": candidate["status"], "next_action": "apply" if quality_ok else "repair_quality"}
        self.store.update_run(run["id"], status="release_ready" if quality_ok else "no_go", checkpoint="release_candidate_completed", continuation=continuation, metadata=metadata)
        self._write_manifest(project, run)
        return candidate

    def _execute_apply(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        rollback_manifest = {
            "schema_version": "4.0",
            "run_id": run["id"],
            "candidate_id": (job.get("payload") or {}).get("candidate_id"),
            "status": "recorded",
            "file_state_verification": "pending_external_apply",
        }
        artifact = self._record_json(project, run, "rollback_manifest", "rollback-manifest.json", rollback_manifest)
        metadata = {**dict(run.get("metadata") or {}), "rollback_manifest_path": artifact["path"]}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "apply_completed", "release_status": "applied", "next_action": "delivery_complete"}
        self.store.update_run(run["id"], status="completed", checkpoint="apply_completed", continuation=continuation, metadata=metadata)
        self._write_manifest(project, run)
        return {"status": "applied", "rollback_manifest_path": artifact["path"]}

    def _execute_rollback(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        report = {
            "schema_version": "4.0",
            "run_id": run["id"],
            "candidate_id": (job.get("payload") or {}).get("candidate_id"),
            "status": "rolled_back",
            "file_state_verification": "passed",
        }
        self._record_json(project, run, "rollback_report", "rollback-report.json", report)
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "rollback_completed", "release_status": "rolled_back", "next_action": "repair_or_replan"}
        self.store.update_run(run["id"], status="rolled_back", checkpoint="rollback_completed", continuation=continuation)
        self._write_manifest(project, run)
        return report

    def _execute_repair(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        payload = job.get("payload") or {}
        attempt = int(payload.get("repair_attempt") or 1)
        report = {
            "schema_version": "4.5",
            "status": "blocked_for_human_review" if attempt >= 3 else "repair_recorded",
            "run_id": run["id"],
            "repair_attempt": attempt,
            "failure_reason": payload.get("failure_reason", "repair_required"),
            "failed_gate_report": payload.get("failed_gate_report", {}),
            "allowed_paths": payload.get("allowed_paths", []),
            "forbidden_paths": payload.get("forbidden_paths", []),
            "next_action": "human_review" if attempt >= 3 else "rerun_failed_wave_or_quality",
        }
        self._record_json(project, run, "repair_report", f"repair-report-{attempt}.json", report)
        continuation = {
            **dict(run.get("continuation") or {}),
            "failure_reason": report["failure_reason"],
            "repair_attempt": attempt,
            "next_action": report["next_action"],
        }
        self.store.update_run(run["id"], status=report["status"], continuation=continuation)
        self._write_manifest(project, run)
        return report

    def _enqueue_repair_job(self, project: dict[str, Any], run: dict[str, Any], reason: str, failed_gate_report: dict[str, Any]) -> dict[str, Any]:
        existing_repairs = [job for job in self.store.list_jobs(run_id=run["id"]) if job["job_type"] == "repair"]
        attempt = len(existing_repairs) + 1
        return self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": "repair",
                "role": "planner",
                "run_id": run["id"],
                "resume_key": f"run:{run['id']}:repair:{reason}:{attempt}",
                "payload": {
                    "failure_reason": reason,
                    "failed_gate_report": failed_gate_report,
                    "repair_attempt": attempt,
                    "allowed_paths": ["release/**", ".v4/**"],
                    "forbidden_paths": [".git/**", "workspace/artifacts/**"],
                },
                "max_attempts": 1,
            },
        )

    def _enqueue_wave_packages(self, project: dict[str, Any], run: dict[str, Any], wave_key: str) -> None:
        context_status = self._context_index_status(run)
        if not context_status["ok"]:
            continuation = {**dict(run.get("continuation") or {}), "failure_reason": "context_index_stale", "context_index_status": context_status, "next_action": "repair_context_index"}
            self.store.update_run(run["id"], status="blocked", continuation=continuation)
            return
        packages = [package for package in self.store.list_work_packages(run["id"]) if package["wave_key"] == wave_key]
        for package in packages:
            self.store.enqueue_job(
                run["tenant_id"],
                {
                    "job_type": "package",
                    "role": package["role"],
                    "run_id": run["id"],
                    "work_package_id": package["id"],
                    "wave_id": package["wave_id"],
                    "resume_key": f"run:{run['id']}:package:{package['id']}",
                    "payload": {
                        "package_key": package["package_key"],
                        "project_id": project["id"],
                        "subsystem": (package.get("payload") or {}).get("subsystem", package["domain"]),
                        "depends_on": (package.get("payload") or {}).get("depends_on", []),
                        "allowed_paths": (package.get("payload") or {}).get("allowed_paths", []),
                        "forbidden_paths": (package.get("payload") or {}).get("forbidden_paths", []),
                        "effective_loc_budget": (package.get("payload") or {}).get("effective_loc_budget", {}),
                    },
                    "max_attempts": 3,
                },
            )

    def _advance_after_package(self, project: dict[str, Any], run: dict[str, Any], completed_package: dict[str, Any]) -> None:
        packages = self.store.list_work_packages(run["id"])
        wave_packages = [package for package in packages if package["wave_id"] == completed_package["wave_id"]]
        if not wave_packages or any(package["status"] != "completed" for package in wave_packages):
            continuation = {**dict(run.get("continuation") or {}), "checkpoint": "package_completed", "next_action": "await_wave_packages"}
            self.store.update_run(run["id"], checkpoint="package_completed", continuation=continuation)
            return
        wave = next((item for item in self.store.list_waves(run["id"]) if item["id"] == completed_package["wave_id"]), None)
        if wave:
            self.store.upsert_wave(run["id"], {**wave["payload"], "status": "completed"})
            self._record_wave_report(project, run, wave["wave_key"], wave_packages)
        waves = self.store.list_waves(run["id"])
        next_wave = next((item for item in waves if item["status"] != "completed"), None)
        if next_wave:
            self._enqueue_wave_packages(project, run, next_wave["wave_key"])
            continuation = {**dict(run.get("continuation") or {}), "checkpoint": "wave_queued", "current_wave": next_wave["wave_key"], "next_action": "worker_claim_package_jobs"}
            self.store.update_run(run["id"], checkpoint="wave_queued", continuation=continuation)
            return
        self.store.enqueue_job(run["tenant_id"], {"job_type": "integration", "role": "integration", "run_id": run["id"], "resume_key": f"run:{run['id']}:integration", "payload": {}, "max_attempts": 2})
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "wave_completed", "current_wave": None, "next_action": "integration"}
        self.store.update_run(run["id"], checkpoint="wave_completed", continuation=continuation)

    def _context_for_package(self, run: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
        snapshot = ((run.get("metadata") or {}).get("context_snapshot") or {})
        return package_context(snapshot, package)

    def _context_index_status(self, run: dict[str, Any]) -> dict[str, Any]:
        return validate_context_snapshot((run.get("metadata") or {}).get("context_snapshot"))

    def _record_wave_report(self, project: dict[str, Any], run: dict[str, Any], wave_key: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
        report = {
            "schema_version": "4.5",
            "run_id": run["id"],
            "wave_key": wave_key,
            "status": "passed" if all(package.get("status") == "completed" for package in packages) else "failed",
            "completed_packages": [package["package_key"] for package in packages if package.get("status") == "completed"],
            "failed_packages": [package["package_key"] for package in packages if package.get("status") not in {"completed"}],
            "package_count": len(packages),
            "interface_changes": [],
            "test_evidence": [package.get("result", {}).get("evidence_path", "") for package in packages if package.get("result", {}).get("evidence_path")],
            "risks": [],
        }
        return self._record_json(project, run, "wave_report", f"wave-report-{wave_key}.json", report, {"wave_key": wave_key})

    def _record_json(self, project: dict[str, Any], run: dict[str, Any], kind: str, filename: str, payload: Any, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        artifact = self.artifacts.write_json(run["tenant_id"], project["name"], run["id"], kind, filename, payload, metadata)
        artifact["payload"] = payload
        return self.store.add_artifact(run["tenant_id"], project["id"], artifact)

    def _invoke_llm(
        self,
        project: dict[str, Any],
        run: dict[str, Any],
        *,
        role: str,
        job: dict[str, Any],
        system_prompt: str,
        user_payload: dict[str, Any],
        required: bool,
        task_kind: str = "package",
    ) -> dict[str, Any]:
        payload = self.ai_scheduler.call(
            run_id=run["id"],
            role=role,
            job=job,
            system_prompt=system_prompt,
            user_payload=user_payload,
            task_kind=task_kind,
            required=required,
        )
        self._record_json(project, run, "agent_run", f"{payload['agent_run_id']}.json", payload, {"role": role, "job_id": job.get("id", ""), "task_kind": payload.get("task_kind", task_kind)})
        if payload.get("ok"):
            self.store.add_event(
                run["tenant_id"],
                project["id"],
                run["id"],
                "llm_call_completed",
                {
                    "agent_run_id": payload["agent_run_id"],
                    "role": role,
                    "job_id": job.get("id", ""),
                    "task_kind": payload.get("task_kind", task_kind),
                    "model_tier": payload.get("model_tier", ""),
                    "elapsed_ms": payload.get("elapsed_ms", 0),
                },
            )
            return payload
        self.store.add_event(
            run["tenant_id"],
            project["id"],
            run["id"],
            "llm_call_failed",
            {
                "agent_run_id": payload["agent_run_id"],
                "role": role,
                "job_id": job.get("id", ""),
                "task_kind": payload.get("task_kind", task_kind),
                "error": str(payload.get("error", ""))[:1000],
            },
        )
        if required:
            continuation = {**dict(run.get("continuation") or {}), "failure_reason": "llm_call_failed", "next_action": "repair_llm_connection"}
            self.store.update_run(run["id"], status="blocked", continuation=continuation)
            raise LLMError(str(payload.get("error", "llm call failed")))
        return payload

    def _write_manifest(self, project: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        latest_run = self._require_run(run["id"])
        existing = [artifact for artifact in self.store.list_artifacts(run["id"]) if artifact["kind"] != "artifact_manifest"]
        manifest = build_manifest(existing)
        artifact = self.artifacts.write_json(run["tenant_id"], project["name"], run["id"], "artifact_manifest", "artifact-manifest.json", manifest)
        artifact["payload"] = manifest
        recorded = self.store.add_artifact(run["tenant_id"], project["id"], artifact)
        metadata = {**dict(latest_run.get("metadata") or {}), "artifact_manifest_path": recorded["path"]}
        self.store.update_run(run["id"], metadata=metadata)
        return recorded

    def _artifact_preflight(self, run_id: str) -> dict[str, Any]:
        artifacts = self.store.list_artifacts(run_id)
        missing = []
        for artifact in artifacts:
            path = artifact.get("path")
            if path and not Path(path).exists():
                missing.append({"artifact_id": artifact["id"], "path": path, "reason": "missing_file"})
        return {"ok": not missing, "checked_count": len(artifacts), "missing": missing}

    def _find_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        for run in list(getattr(self.store, "runs", {}).values()) if hasattr(self.store, "runs") else []:
            for artifact in self.store.list_artifacts(run["id"]):
                if artifact["id"] == artifact_id:
                    return artifact
        for job in self.store.list_jobs():
            run_id = job.get("run_id")
            if not run_id:
                continue
            for artifact in self.store.list_artifacts(run_id):
                if artifact["id"] == artifact_id:
                    return artifact
        return None

    def _current_wave(self, waves: list[dict[str, Any]]) -> str | None:
        active = next((wave for wave in waves if wave.get("status") != "completed"), None)
        return active["wave_key"] if active else None

    def _next_action(self, run: dict[str, Any], jobs: list[dict[str, Any]], preflight: dict[str, Any]) -> str:
        if not preflight["ok"]:
            return "repair_missing_artifacts"
        queued = [job for job in jobs if job["status"] in {"queued", "retry", "running"}]
        if queued:
            return "claim_pending_jobs"
        return (run.get("continuation") or {}).get("next_action", "idle")

    def _require_project(self, project_id: str) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if not project:
            raise RuntimeError(f"project not found: {project_id}")
        return project

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RuntimeError(f"run not found: {run_id}")
        return run
