from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v5.agent_contracts import agent_contract_schema, validate_agent_contract
from dev_orchestrator.v5.ai_scheduler import AICallScheduler
from dev_orchestrator.v5.artifacts import ArtifactWriter, build_manifest
from dev_orchestrator.v5.code_indexer import build_code_index
from dev_orchestrator.v5.contract_store import build_contract_index
from dev_orchestrator.v5.context_memory import build_context_snapshot_v2, package_context, validate_context_snapshot
from dev_orchestrator.v5.llm_policy import get_ai_task_budget
from dev_orchestrator.v5.models import DEFAULT_TENANT, new_id, sha256_file, slugify
from dev_orchestrator.v5.patch_runtime import TransactionalPatchRuntime
from dev_orchestrator.v5.release_quality import build_deploy_guide, validate_ai_native_project
from dev_orchestrator.v5.runtime import AgentFileRuntime, PatchValidationError
from dev_orchestrator.v5.store import V5Store
from dev_orchestrator.v5.test_runner import run_validation_commands


DEFAULT_PROJECT_CONFIG = {
    "target_scale": "small",
    "stack_pack": "auto",
    "deployment_mode": "",
    "api_only": False,
    "effective_loc_target": 1000,
    "unattended_mode": "off",
}

PACKAGE_ROLE_TO_TASK = {
    "qa": "test_generation",
    "security": "security_review",
    "docs": "code_generation",
    "release": "release_notes",
}


class AgentContractViolationError(LLMError):
    retryable = False


class ProjectPathError(RuntimeError):
    pass


class DeliveryExportError(RuntimeError):
    pass


EXPORT_EXCLUDED_DIRS = {
    ".agent",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".v5",
    "__pycache__",
    "artifacts",
    "logs",
    "node_modules",
    "reports",
}

EXPORT_EXCLUDED_FILES = {".coverage", ".DS_Store"}


def _compact_text(value: str, limit: int = 9000) -> str:
    normalized = "\n".join(line.rstrip() for line in str(value or "").splitlines())
    if len(normalized) <= limit:
        return normalized
    head = normalized[: int(limit * 0.72)].rstrip()
    tail = normalized[-int(limit * 0.18) :].lstrip()
    return f"{head}\n\n[...truncated for bounded AI agent task...]\n\n{tail}"


def _json_or_empty(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_text": text}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class V5Orchestrator:
    def __init__(self, store: V5Store, workspace_root: Path, tenant_id: str = DEFAULT_TENANT, llm_client: OpenAICompatibleClient | None = None):
        self.store = store
        self.workspace_root = workspace_root
        self.tenant_id = tenant_id or DEFAULT_TENANT
        self.artifacts = ArtifactWriter(workspace_root)
        self.runtime = AgentFileRuntime(workspace_root)
        self.patch_runtime = TransactionalPatchRuntime(self.runtime)
        self.materializer = self.runtime
        self.llm_client = llm_client
        self.ai_scheduler = AICallScheduler(llm_client)

    def update_llm_client(self, llm_client: OpenAICompatibleClient | None) -> None:
        self.llm_client = llm_client
        self.ai_scheduler.llm_client = llm_client

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
        name = payload.get("name") or slugify(payload.get("title") or "v5-ai-agent-project")
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
        path_status = self.runtime.project_path_status(project)
        if not path_status["ok"]:
            self.store.delete_project(project["id"])
            raise ProjectPathError(path_status["reason"])
        self.store.add_event(project["tenant_id"], project["id"], None, "project_created", {"project_id": project["id"]})
        return self._project_snapshot(project)

    def list_projects(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        return [self._project_snapshot(project) for project in self.store.list_projects(tenant_id or self.tenant_id)]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        project = self.store.get_project(project_id)
        return self._project_snapshot(project) if project else None

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        return self.store.list_runs(project_id)

    def create_run(self, project_id: str, requirements_text: str = "", tenant_id: str | None = None) -> dict[str, Any]:
        project = self._require_project(project_id)
        text = requirements_text or project.get("description") or project.get("title") or project.get("name")
        path_status = self.runtime.project_path_status(project)
        if not path_status["ok"]:
            raise ProjectPathError(path_status["reason"])
        self.runtime.reset_project_root(project)
        run = self.store.create_run(
            tenant_id or project.get("tenant_id") or self.tenant_id,
            project_id,
            {
                "requirements_text": text,
                "project_config": project.get("config") or {},
                "project_root": str(self.runtime.project_root(project)),
                "project_layout": {},
                "package_dag": {},
            },
        )
        self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": "requirements_analysis",
                "role": "requirements",
                "run_id": run["id"],
                "resume_key": f"run:{run['id']}:requirements_analysis",
                "payload": {"requirements_text": text},
                "max_attempts": 2,
            },
        )
        self.store.add_event(run["tenant_id"], project_id, run["id"], "run_created", {"run_id": run["id"]})
        return run

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.store.get_run(run_id)
        if not run:
            return None
        return self._project_run_snapshot(run)

    def list_jobs(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.list_jobs(run_id=run_id)

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.list_artifacts(run_id)

    def continuation(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        return self._live_continuation(run)

    def pause_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        return self.store.update_run(run_id, status="paused", checkpoint=run.get("checkpoint", "run_created"), continuation={**dict(run.get("continuation") or {}), "next_action": "resume_from_job_boundary"})

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        preflight = self._artifact_preflight(run_id)
        if not preflight["ok"]:
            return self.store.update_run(run_id, status="blocked", continuation={**dict(run.get("continuation") or {}), "failure_reason": "repair_missing_artifacts", "artifact_preflight": preflight, "next_action": "repair_missing_artifacts"})
        return self.store.update_run(run_id, status="running", continuation={**dict(run.get("continuation") or {}), "next_action": "claim_pending_jobs"})

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        return self.store.update_run(run_id, status="cancelled", continuation={**dict(run.get("continuation") or {}), "next_action": "cancelled"})

    def export_delivery(self, run_id: str, target_path: str, overwrite: bool = False) -> dict[str, Any]:
        run = self._require_run(run_id)
        project = self._require_project(run["project_id"])
        source_root = self.runtime.project_root(project)
        if not source_root.exists() or not source_root.is_dir():
            raise DeliveryExportError(f"project root not found: {source_root}")
        target_root = self._resolve_export_target(target_path, project)
        if target_root == source_root or self.runtime._is_within(target_root, source_root):
            raise DeliveryExportError("export target must be outside the internal project workspace")
        if target_root.exists():
            if any(target_root.iterdir()) and not overwrite:
                raise DeliveryExportError("export target already contains files; enable overwrite to replace it")
            if overwrite:
                shutil.rmtree(target_root)
        target_root.mkdir(parents=True, exist_ok=True)

        copied: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for path in sorted(source_root.rglob("*")):
            relative = path.relative_to(source_root)
            normalized = str(relative).replace("\\", "/")
            if self._should_skip_export_path(relative):
                skipped.append({"path": normalized, "reason": "excluded_internal_or_cache_path"})
                continue
            target = (target_root / relative).resolve()
            if not self.runtime._is_within(target, target_root):
                skipped.append({"path": normalized, "reason": "target_escape"})
                continue
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append({"path": normalized, "size": target.stat().st_size, "sha256": sha256_file(target)})

        report = {
            "schema_version": "5.0",
            "run_id": run_id,
            "project_id": project["id"],
            "source_project_root": str(source_root),
            "target_path": str(target_root),
            "overwrite": overwrite,
            "copied_count": len(copied),
            "skipped_count": len(skipped),
            "copied_files": copied,
            "skipped": skipped[:200],
            "ok": True,
        }
        artifact = self._record_json(project, run, "delivery_export", "delivery-export-report.json", report)
        latest_run = self._require_run(run_id)
        self.store.update_run(run_id, metadata={**dict(latest_run.get("metadata") or {}), "delivery_export_path": artifact["path"], "delivery_export_target": str(target_root)})
        return report

    def execute_job(self, job: dict[str, Any]) -> dict[str, Any]:
        job_type = job["job_type"]
        if job_type == "requirements_analysis":
            return self._execute_requirements_analysis(job)
        if job_type == "architecture_design":
            return self._execute_architecture_design(job)
        if job_type == "package_planning":
            return self._execute_package_planning(job)
        if job_type in {"code_generation", "test_generation", "security_review"}:
            return self._execute_agent_package(job)
        if job_type == "integration":
            return self._execute_integration(job)
        if job_type == "code_review":
            return self._execute_code_review(job)
        if job_type == "quality":
            return self._execute_quality(job)
        if job_type == "release_notes":
            return self._execute_release_notes(job)
        if job_type == "release_candidate":
            return self._execute_release_candidate(job)
        if job_type == "apply":
            return self._execute_apply(job)
        if job_type == "rollback":
            return self._execute_rollback(job)
        if job_type == "repair":
            return self._execute_repair(job)
        raise RuntimeError(f"unsupported V5 job type: {job_type}")

    def enqueue_apply(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._find_artifact(candidate_id)
        if not candidate:
            raise RuntimeError(f"release candidate not found: {candidate_id}")
        run = self._require_run(candidate["run_id"])
        return self.store.enqueue_job(run["tenant_id"], {"job_type": "apply", "role": "release", "run_id": run["id"], "resume_key": f"run:{run['id']}:apply:{candidate_id}", "payload": {"candidate_id": candidate_id}, "max_attempts": 1})

    def enqueue_rollback(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._find_artifact(candidate_id)
        if not candidate:
            raise RuntimeError(f"release candidate not found: {candidate_id}")
        run = self._require_run(candidate["run_id"])
        return self.store.enqueue_job(run["tenant_id"], {"job_type": "rollback", "role": "release", "run_id": run["id"], "resume_key": f"run:{run['id']}:rollback:{candidate_id}", "payload": {"candidate_id": candidate_id}, "max_attempts": 1})

    def _execute_requirements_analysis(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        requirements_text = (job.get("payload") or {}).get("requirements_text") or (run.get("metadata") or {}).get("requirements_text") or project.get("description", "")
        analysis = self._invoke_json_agent(
            project,
            run,
            role="requirements",
            job=job,
            task_kind="requirements_analysis",
            system_prompt=(
                "You are requirements_agent in a multi-agent delivery system. Return strict JSON only. "
                "Analyze the user's project request. Do not write code. Return status GO or NO_GO, "
                "summary, goals, users, constraints, acceptance_criteria, missing_information, risks, and expected_terms."
            ),
            user_payload={"project": self._project_prompt(project), "requirements_text": _compact_text(requirements_text, get_ai_task_budget("requirements_analysis").max_input_chars)},
            required=True,
        )
        self._record_json(project, run, "requirements_analysis", "requirements-analysis.json", analysis)
        if str(analysis.get("status", "GO")).upper() == "NO_GO":
            continuation = {**dict(run.get("continuation") or {}), "checkpoint": "requirements_completed", "failure_reason": "requirements_need_clarification", "next_action": "clarify_requirements"}
            self.store.update_run(run["id"], status="no_go", checkpoint="requirements_completed", continuation=continuation, metadata={**dict(run.get("metadata") or {}), "requirements_analysis": analysis})
            self._write_manifest(project, run)
            return {"status": "NO_GO", "requirements_analysis": analysis}
        metadata = {**dict(run.get("metadata") or {}), "requirements_analysis": analysis}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "requirements_completed", "next_action": "architecture_design"}
        self.store.update_run(run["id"], status="running", checkpoint="requirements_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "architecture_design", "role": "architect", "run_id": run["id"], "resume_key": f"run:{run['id']}:architecture_design", "payload": {}, "max_attempts": 2})
        self._write_manifest(project, run)
        return {"status": "completed", "next": "architecture_design"}

    def _execute_architecture_design(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        design = self._invoke_json_agent(
            project,
            run,
            role="architect",
            job=job,
            task_kind="architecture_design",
            system_prompt=(
                "You are architect_agent. Return strict JSON only. You must design the project directory layout. "
                "Do not use a fixed template. Return architecture_summary, technology_choices, project_layout, module_boundaries, integration_contracts. "
                "project_layout must contain source_root, delivery_root, entrypoints, directories, validation_commands."
            ),
            user_payload={"project": self._project_prompt(project), "requirements_analysis": metadata.get("requirements_analysis", {}), "stack_constraints": (project.get("config") or {})},
            required=True,
        )
        layout = self._normalize_layout(design)
        project_root = self.runtime.project_root(project)
        self.runtime.layout_roots(project_root, layout)
        design["project_layout"] = layout
        self._record_json(project, run, "architecture_design", "architecture-design.json", design)
        self._record_json(project, run, "project_layout", "project-layout.json", layout)
        metadata.update({"architecture_design": design, "project_layout": layout, "project_root": str(project_root)})
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "architecture_completed", "next_action": "package_planning"}
        self.store.update_run(run["id"], status="running", checkpoint="architecture_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "package_planning", "role": "planner", "run_id": run["id"], "resume_key": f"run:{run['id']}:package_planning", "payload": {}, "max_attempts": 2})
        self._write_manifest(project, run)
        return {"status": "completed", "project_layout": layout}

    def _execute_package_planning(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        plan = self._invoke_json_agent(
            project,
            run,
            role="planner",
            job=job,
            task_kind="package_planning",
            system_prompt=(
                "You are planner_agent. Return strict JSON only. Build a DAG of AI-agent work packages. "
                "Return waves and packages. Each package needs package_key, role, domain, subsystem, wave_key, depends_on, allowed_paths, forbidden_paths, objective, requirements_mapping, expected_outputs. "
                "Use roles db, backend, frontend, security, qa, docs, release when useful. Paths must be relative to the AI-generated layout."
            ),
            user_payload={
                "project": self._project_prompt(project),
                "requirements_analysis": metadata.get("requirements_analysis", {}),
                "architecture_design": metadata.get("architecture_design", {}),
            },
            required=True,
        )
        plan = self._normalize_package_plan(plan, metadata.get("project_layout") or {})
        self._record_json(project, run, "package_dag", "package-dag.json", plan)
        wave_id_by_key = {}
        for wave in plan["waves"]:
            stored_wave = self.store.upsert_wave(run["id"], wave)
            wave_id_by_key[wave["wave_key"]] = stored_wave["id"]
        for package in plan["packages"]:
            self.store.upsert_work_package(run["id"], wave_id_by_key[package["wave_key"]], package)
        code_index, contract_index = self._refresh_indexes(project, {**run, "metadata": {**metadata, "package_dag": plan}})
        context_snapshot = build_context_snapshot_v2(requirements=metadata.get("requirements_analysis", {}), architecture=metadata.get("architecture_design", {}), package_plan=plan, project_root=self.runtime.project_root(project), code_index=code_index, contract_index=contract_index)
        self._record_json(project, run, "context_snapshot", "context-snapshot.json", context_snapshot)
        metadata.update(
            {
                "package_dag": plan,
                "context_snapshot": context_snapshot,
                "context_snapshot_id": context_snapshot["index_hash"],
                "code_index": code_index,
                "contract_index": contract_index,
                "code_index_hash": code_index.get("index_hash", ""),
                "contract_index_hash": contract_index.get("index_hash", ""),
            }
        )
        first_wave = min(plan["waves"], key=lambda item: item["sequence"])
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "package_planning_completed", "current_wave": first_wave["wave_key"], "pending_packages": [package["package_key"] for package in plan["packages"]], "next_action": "worker_claim_package_jobs"}
        updated_run = self.store.update_run(run["id"], status="running", checkpoint="package_planning_completed", continuation=continuation, metadata=metadata)
        self._enqueue_wave_packages(project, updated_run, first_wave["wave_key"])
        self._write_manifest(project, run)
        return {"status": "completed", "package_count": len(plan["packages"])}

    def _execute_agent_package(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        package = self.store.get_work_package(job["work_package_id"] or "")
        if not package:
            raise RuntimeError("work package not found")
        payload = package.get("payload") or {}
        task_kind = job["job_type"]
        if task_kind == "security_review":
            task_kind = "security_review"
        elif package["role"] == "qa":
            task_kind = "test_generation"
        else:
            task_kind = "code_generation"
        output = self._invoke_json_agent(
            project,
            run,
            role=package["role"],
            job=job,
            task_kind=task_kind,
            system_prompt=(
                f"You are {package['role']}_agent. Return strict JSON only using the file-manifest patch protocol. "
                "Return agent, status, summary, files, commands, evidence, risks. "
                "files must be AI-produced content with path, action create|replace|delete, and content. "
                "Do not return markdown fences. Do not rely on hidden templates."
            ),
            user_payload={
                "project": self._project_prompt(project),
                "context": self._context_for_package(run, package),
                "package": payload,
                "current_project_files": self.runtime.list_project_files(project),
                "output_protocol": {"files": [{"path": "relative/path", "action": "create|replace|delete", "content": "full file content"}]},
            },
            required=True,
        )
        apply_result = self.patch_runtime.apply_file_manifest_transaction(
            run_id=run["id"],
            project=project,
            layout=((run.get("metadata") or {}).get("project_layout") or {}),
            agent_output=output,
            allowed_paths=payload.get("allowed_paths") or [],
            forbidden_paths=payload.get("forbidden_paths") or [],
        )
        if not apply_result.get("ok"):
            self._record_json(project, run, "integration_conflict", f"conflict-{package['package_key']}.json", apply_result, {"work_package_id": package["id"]})
            self.store.enqueue_job(run["tenant_id"], {"job_type": "integration", "role": "integration", "run_id": run["id"], "resume_key": f"run:{run['id']}:integration:conflict:{package['id']}:{new_id()}", "payload": {"conflicts": apply_result.get("conflicts", [])}, "max_attempts": 2})
            raise RuntimeError(f"agent patch conflict: {apply_result.get('conflicts')}")
        patch_set = self._record_json(
            project,
            run,
            "patch_set",
            f"{package['package_key']}.json",
            {
                "schema_version": "5.0",
                "package_key": package["package_key"],
                "role": package["role"],
                "agent_output": output,
                **apply_result,
            },
            {"work_package_id": package["id"], "role": package["role"]},
        )
        self._record_json(project, run, "patch_transaction", f"{apply_result['transaction_id']}.json", apply_result, {"work_package_id": package["id"], "role": package["role"]})
        self._write_patch_transaction_report(project, run)
        if task_kind == "test_generation":
            self._record_json(
                project,
                run,
                "test_report",
                f"test-report-{package['package_key']}.json",
                {
                    "schema_version": "5.0",
                    "ok": str(output.get("status", "GO")).upper() != "NO_GO",
                    "status": output.get("status", "GO"),
                    "package_key": package["package_key"],
                    "changed_files": apply_result.get("changed_files", []),
                    "commands": output.get("commands", []),
                    "evidence": output.get("evidence", []),
                },
                {"work_package_id": package["id"]},
            )
        if task_kind == "security_review":
            self._record_json(
                project,
                run,
                "security_report",
                f"security-report-{package['package_key']}.json",
                {
                    "schema_version": "5.0",
                    "ok": str(output.get("status", "GO")).upper() != "NO_GO",
                    "status": output.get("status", "GO"),
                    "package_key": package["package_key"],
                    "changed_files": apply_result.get("changed_files", []),
                    "evidence": output.get("evidence", []),
                    "risks": output.get("risks", []),
                },
                {"work_package_id": package["id"]},
            )
        result = {
            "schema_version": "5.0",
            "project_root": apply_result["project_root"],
            "package_key": package["package_key"],
            "role": package["role"],
            "requirements_mapping": payload.get("requirements_mapping") or payload.get("requirements") or [],
            "patch_set_id": patch_set["id"],
            "patch_manifest": {"changed_files": apply_result.get("changed_files", []), "boundary": {"allowed_paths": payload.get("allowed_paths", []), "forbidden_paths": payload.get("forbidden_paths", [])}},
            "evidence": output.get("evidence", []),
            "risks": output.get("risks", []),
        }
        self.store.update_work_package(package["id"], status="completed", result=result)
        self._record_json(project, run, "package_evidence", f"{package['package_key']}.json", result, {"work_package_id": package["id"]})
        self._advance_after_package(project, run, package)
        return result

    def _execute_integration(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        output = self._invoke_json_agent(
            project,
            run,
            role="integration",
            job=job,
            task_kind="integration_merge",
            system_prompt=(
                "You are integration_agent. Return strict JSON only. Inspect package outputs and current files. "
                "If glue code or conflict resolution is needed, return file-manifest patches. If no changes are needed, return files as an empty list and status GO."
            ),
            user_payload={
                "project": self._project_prompt(project),
                "layout": metadata.get("project_layout", {}),
                "packages": self.store.list_work_packages(run["id"]),
                "current_project_files": self.runtime.list_project_files(project),
                "contract_index": metadata.get("contract_index", {}),
                "conflict_evidence": [artifact.get("payload", {}) for artifact in self.store.list_artifacts(run["id"]) if artifact["kind"] == "integration_conflict"],
            },
            required=True,
        )
        changed_files: list[str] = []
        if output.get("files"):
            apply_result = self.patch_runtime.apply_file_manifest_transaction(run_id=run["id"], project=project, layout=metadata.get("project_layout", {}), agent_output=output, allowed_paths=["**"], forbidden_paths=[".git/**", ".v5/**"], replace_conflicts=True, conflict_sources=[artifact.get("payload", {}) for artifact in self.store.list_artifacts(run["id"]) if artifact["kind"] == "integration_conflict"])
            changed_files = apply_result.get("changed_files", [])
            self._record_json(project, run, "patch_set", "integration.json", {"schema_version": "5.0", "role": "integration", "agent_output": output, **apply_result}, {"role": "integration"})
            self._record_json(project, run, "patch_transaction", f"{apply_result['transaction_id']}.json", apply_result, {"role": "integration"})
            self._write_patch_transaction_report(project, run)
        report = {"schema_version": "5.0", "ok": True, "status": "passed", "changed_files": changed_files, "summary": output.get("summary", "")}
        self._record_json(project, run, "integration_report", "integration-report.json", report)
        code_index, contract_index = self._refresh_indexes(project, run)
        metadata = dict((self._require_run(run["id"]).get("metadata") or metadata))
        context_snapshot = build_context_snapshot_v2(requirements=metadata.get("requirements_analysis", {}), architecture=metadata.get("architecture_design", {}), package_plan=metadata.get("package_dag", {}), project_root=self.runtime.project_root(project), code_index=code_index, contract_index=contract_index)
        metadata.update({"context_snapshot": context_snapshot, "context_snapshot_id": context_snapshot["index_hash"]})
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "integration_completed", "integration_status": "passed", "next_action": "code_review"}
        self.store.update_run(run["id"], status="running", checkpoint="integration_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "code_review", "role": "review", "run_id": run["id"], "resume_key": f"run:{run['id']}:code_review", "payload": {}, "max_attempts": 2})
        self._write_manifest(project, run)
        return report

    def _execute_code_review(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        output = self._invoke_json_agent(
            project,
            run,
            role="review",
            job=job,
            task_kind="code_review",
            system_prompt="You are review_agent. Return strict JSON only with ok, status, findings, required_fixes, requirement_coverage, security_notes.",
            user_payload={"project": self._project_prompt(project), "files": self._file_summaries(project), "packages": self.store.list_work_packages(run["id"]), "requirements": (run.get("metadata") or {}).get("requirements_analysis", {}), "contract_index": (run.get("metadata") or {}).get("contract_index", {})},
            required=True,
        )
        output.setdefault("ok", str(output.get("status", "GO")).upper() != "NO_GO")
        self._record_json(project, run, "code_review_report", "code-review-report.json", output)
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "review_completed", "review_status": "passed" if output.get("ok") else "failed", "next_action": "quality" if output.get("ok") else "repair_review"}
        self.store.update_run(run["id"], status="running" if output.get("ok") else "no_go", checkpoint="review_completed", continuation=continuation)
        if output.get("ok"):
            self.store.enqueue_job(run["tenant_id"], {"job_type": "quality", "role": "qa", "run_id": run["id"], "resume_key": f"run:{run['id']}:quality", "payload": {}, "max_attempts": 2})
        else:
            self._enqueue_repair_job(project, run, "code_review_failed", output)
        self._write_manifest(project, run)
        return output

    def _execute_quality(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        artifacts = self.store.list_artifacts(run["id"])
        test_execution_report = self._execute_validation_commands(project, run, metadata, artifacts)
        artifacts = self.store.list_artifacts(run["id"])
        code_index, contract_index = self._refresh_indexes(project, self._require_run(run["id"]))
        latest_run = self._require_run(run["id"])
        metadata = dict(latest_run.get("metadata") or metadata)
        context_snapshot = build_context_snapshot_v2(requirements=metadata.get("requirements_analysis", {}), architecture=metadata.get("architecture_design", {}), package_plan=metadata.get("package_dag", {}), project_root=self.runtime.project_root(project), code_index=code_index, contract_index=contract_index)
        metadata.update({"context_snapshot": context_snapshot, "context_snapshot_id": context_snapshot["index_hash"]})
        run_context = {
            "agent_runs": [artifact for artifact in artifacts if artifact["kind"] == "agent_run"],
            "patch_sets": [artifact for artifact in artifacts if artifact["kind"] == "patch_set"],
            "packages": self.store.list_work_packages(run["id"]),
            "test_reports": [artifact for artifact in artifacts if artifact["kind"] == "test_report"],
            "test_execution_reports": [artifact for artifact in artifacts if artifact["kind"] == "test_execution_report"],
            "test_execution_report": test_execution_report,
            "code_reviews": [artifact for artifact in artifacts if artifact["kind"] == "code_review_report"],
            "release_notes": [artifact for artifact in artifacts if artifact["kind"] == "release_notes"],
            "agent_contract_reports": [artifact for artifact in artifacts if artifact["kind"] == "agent_contract_report"],
            "patch_transactions": [artifact for artifact in artifacts if artifact["kind"] == "patch_transaction"],
            "code_index": code_index,
            "contract_index": contract_index,
            "requirements_text": metadata.get("requirements_text", ""),
            "effective_loc_target": (metadata.get("project_config") or {}).get("effective_loc_target", 0),
        }
        quality_report = validate_ai_native_project(project_root=self.runtime.project_root(project), layout=metadata.get("project_layout", {}), run_context=run_context)
        quality_artifact = self._record_json(project, run, "quality_report", "quality-report.json", quality_report)
        if quality_report.get("template_leak_report"):
            self._record_json(project, run, "template_leak_report", "template-leak-report.json", quality_report.get("template_leak_report"))
        metadata.update({"quality_report_path": quality_artifact["path"], "effective_loc_metrics": quality_report.get("effective_loc", {}), "test_execution_report": test_execution_report})
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "quality_completed", "quality_status": quality_report["status"], "next_action": "release_notes" if quality_report["ok"] else "repair_quality"}
        self.store.update_run(run["id"], status="running" if quality_report["ok"] else "no_go", checkpoint="quality_completed", continuation=continuation, metadata=metadata)
        if quality_report["ok"]:
            self.store.enqueue_job(run["tenant_id"], {"job_type": "release_notes", "role": "release", "run_id": run["id"], "resume_key": f"run:{run['id']}:release_notes", "payload": {}, "max_attempts": 2})
        else:
            self._enqueue_repair_job(project, run, "quality_gate_failed", quality_report)
        self._write_manifest(project, run)
        return quality_report

    def _execute_release_notes(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        output = self._invoke_json_agent(
            project,
            run,
            role="release",
            job=job,
            task_kind="release_notes",
            system_prompt="You are release_agent. Return strict JSON only with release_summary, deploy_steps, validation_steps, rollback, changed_files.",
            user_payload={"project": self._project_prompt(project), "layout": metadata.get("project_layout", {}), "quality": self._latest_artifact_payload(run["id"], "quality_report"), "files": self.runtime.list_project_files(project)},
            required=True,
        )
        release_artifact = self._record_json(project, run, "release_notes", "release-notes.json", output)
        deploy_guide = build_deploy_guide(metadata.get("project_layout", {}), output, self._latest_artifact_payload(run["id"], "quality_report"))
        deploy_artifact = self._record_json(project, run, "deploy_guide", "deploy-guide.json", deploy_guide)
        metadata.update({"release_notes_path": release_artifact["path"], "deploy_guide_path": deploy_artifact["path"]})
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "release_notes_completed", "release_status": "notes_ready", "next_action": "release_candidate"}
        self.store.update_run(run["id"], status="running", checkpoint="release_notes_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "release_candidate", "role": "release", "run_id": run["id"], "resume_key": f"run:{run['id']}:release_candidate", "payload": {}, "max_attempts": 2})
        self._write_manifest(project, run)
        return output

    def _execute_release_candidate(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        quality = self._latest_artifact_payload(run["id"], "quality_report")
        candidate = {"schema_version": "5.0", "candidate_id": new_id(), "run_id": run["id"], "status": "GO" if quality.get("ok") else "NO_GO", "project_root": str(self.runtime.project_root(project)), "delivery_root": ((run.get("metadata") or {}).get("project_layout") or {}).get("delivery_root", ""), "hard_gates_passed": bool(quality.get("ok"))}
        artifact = self._record_json(project, run, "release_candidate", f"release-candidate-{candidate['candidate_id']}.json", candidate)
        metadata = {**dict(run.get("metadata") or {}), "release_candidate_id": artifact["id"]}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "release_candidate_completed", "release_status": candidate["status"], "next_action": "apply" if quality.get("ok") else "repair_quality"}
        self.store.update_run(run["id"], status="release_ready" if quality.get("ok") else "no_go", checkpoint="release_candidate_completed", continuation=continuation, metadata=metadata)
        self._write_manifest(project, run)
        return candidate

    def _execute_apply(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        rollback_manifest = {"schema_version": "5.0", "run_id": run["id"], "candidate_id": (job.get("payload") or {}).get("candidate_id"), "status": "recorded", "project_root": str(self.runtime.project_root(project))}
        artifact = self._record_json(project, run, "rollback_manifest", "rollback-manifest.json", rollback_manifest)
        metadata = {**dict(run.get("metadata") or {}), "rollback_manifest_path": artifact["path"]}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "apply_completed", "release_status": "applied", "next_action": "delivery_complete"}
        self.store.update_run(run["id"], status="completed", checkpoint="apply_completed", continuation=continuation, metadata=metadata)
        self._write_manifest(project, run)
        return {"status": "applied", "rollback_manifest_path": artifact["path"]}

    def _execute_rollback(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        report = {"schema_version": "5.0", "run_id": run["id"], "candidate_id": (job.get("payload") or {}).get("candidate_id"), "status": "rolled_back"}
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
        if attempt > 3:
            report = {"schema_version": "5.0", "ok": False, "status": "NO_GO", "repair_attempt": attempt, "next_action": "human_review", "failure_reason": payload.get("failure_reason", "repair_required")}
            self._record_json(project, run, "repair_report", f"repair-report-{attempt}.json", report)
            self.store.update_run(run["id"], status="no_go", continuation={**dict(run.get("continuation") or {}), "repair_attempt": attempt, "next_action": "human_review"})
            self._write_manifest(project, run)
            return report
        output = self._invoke_json_agent(
            project,
            run,
            role="repair",
            job=job,
            task_kind="failure_analysis",
            system_prompt="You are repair_agent. Return strict JSON only. Produce file-manifest patches that fix the failed gate without changing unrelated files.",
            user_payload={"failure": payload, "layout": (run.get("metadata") or {}).get("project_layout", {}), "files": self._file_summaries(project), "artifacts": self._artifact_summaries(run["id"]), "contract_index": (run.get("metadata") or {}).get("contract_index", {})},
            required=True,
        )
        changed_files: list[str] = []
        if output.get("files"):
            apply_result = self.patch_runtime.apply_file_manifest_transaction(run_id=run["id"], project=project, layout=((run.get("metadata") or {}).get("project_layout") or {}), agent_output=output, allowed_paths=["**"], forbidden_paths=[".git/**", ".v5/**"], replace_conflicts=True)
            changed_files = apply_result.get("changed_files", [])
            self._record_json(project, run, "patch_set", f"repair-{attempt}.json", {"schema_version": "5.0", "role": "repair", "agent_output": output, **apply_result}, {"role": "repair", "repair_attempt": attempt})
            self._record_json(project, run, "patch_transaction", f"{apply_result['transaction_id']}.json", apply_result, {"role": "repair", "repair_attempt": attempt})
            self._write_patch_transaction_report(project, run)
        report = {"schema_version": "5.0", "ok": True, "status": "repair_applied", "repair_attempt": attempt, "changed_files": changed_files, "next_action": "code_review"}
        self._record_json(project, run, "repair_report", f"repair-report-{attempt}.json", report)
        self.store.update_run(run["id"], status="running", continuation={**dict(run.get("continuation") or {}), "repair_attempt": attempt, "next_action": "code_review"})
        self.store.enqueue_job(run["tenant_id"], {"job_type": "code_review", "role": "review", "run_id": run["id"], "resume_key": f"run:{run['id']}:code_review:repair:{attempt}", "payload": {}, "max_attempts": 2})
        self._write_manifest(project, run)
        return report

    def _enqueue_wave_packages(self, project: dict[str, Any], run: dict[str, Any], wave_key: str) -> None:
        context_status = self._context_index_status(run)
        if not context_status["ok"]:
            self.store.update_run(run["id"], status="blocked", continuation={**dict(run.get("continuation") or {}), "failure_reason": "context_index_stale", "context_index_status": context_status, "next_action": "repair_context_index"})
            return
        packages = [package for package in self.store.list_work_packages(run["id"]) if package["wave_key"] == wave_key]
        completed_keys = {package["package_key"] for package in self.store.list_work_packages(run["id"]) if package.get("status") == "completed"}
        for package in packages:
            payload = package.get("payload") or {}
            depends_on = payload.get("depends_on") or []
            if any(dep not in completed_keys for dep in depends_on):
                continue
            job_type = self._job_type_for_package(payload)
            self.store.enqueue_job(
                run["tenant_id"],
                {
                    "job_type": job_type,
                    "role": package["role"],
                    "run_id": run["id"],
                    "work_package_id": package["id"],
                    "wave_id": package["wave_id"],
                    "resume_key": f"run:{run['id']}:package:{package['id']}:{job_type}",
                    "payload": {"package_key": package["package_key"], "project_id": project["id"], "subsystem": payload.get("subsystem", package["domain"]), "depends_on": depends_on, "allowed_paths": payload.get("allowed_paths", []), "forbidden_paths": payload.get("forbidden_paths", [])},
                    "max_attempts": 3,
                },
            )

    def _advance_after_package(self, project: dict[str, Any], run: dict[str, Any], completed_package: dict[str, Any]) -> None:
        packages = self.store.list_work_packages(run["id"])
        wave_packages = [package for package in packages if package["wave_id"] == completed_package["wave_id"]]
        if any(package["status"] != "completed" for package in wave_packages):
            self._enqueue_wave_packages(project, run, completed_package["wave_key"])
            self.store.update_run(run["id"], checkpoint="package_completed", continuation={**dict(run.get("continuation") or {}), "checkpoint": "package_completed", "next_action": "await_wave_packages"})
            return
        wave = next((item for item in self.store.list_waves(run["id"]) if item["id"] == completed_package["wave_id"]), None)
        if wave:
            self.store.upsert_wave(run["id"], {**wave["payload"], "status": "completed"})
            self._record_wave_report(project, run, wave["wave_key"], wave_packages)
        waves = self.store.list_waves(run["id"])
        next_wave = next((item for item in waves if item["status"] != "completed"), None)
        if next_wave:
            self._enqueue_wave_packages(project, run, next_wave["wave_key"])
            self.store.update_run(run["id"], checkpoint="wave_queued", continuation={**dict(run.get("continuation") or {}), "checkpoint": "wave_queued", "current_wave": next_wave["wave_key"], "next_action": "worker_claim_package_jobs"})
            return
        self.store.enqueue_job(run["tenant_id"], {"job_type": "integration", "role": "integration", "run_id": run["id"], "resume_key": f"run:{run['id']}:integration", "payload": {}, "max_attempts": 2})
        self.store.update_run(run["id"], checkpoint="wave_completed", continuation={**dict(run.get("continuation") or {}), "checkpoint": "wave_completed", "current_wave": None, "next_action": "integration"})

    def _enqueue_repair_job(self, project: dict[str, Any], run: dict[str, Any], reason: str, failed_gate_report: dict[str, Any]) -> dict[str, Any]:
        existing_repairs = [job for job in self.store.list_jobs(run_id=run["id"]) if job["job_type"] == "repair"]
        attempt = len(existing_repairs) + 1
        return self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": "repair",
                "role": "repair",
                "run_id": run["id"],
                "resume_key": f"run:{run['id']}:repair:{reason}:{attempt}",
                "payload": {"failure_reason": reason, "failed_gate_report": failed_gate_report, "repair_attempt": attempt},
                "max_attempts": 1,
            },
        )

    def _invoke_json_agent(self, project: dict[str, Any], run: dict[str, Any], *, role: str, job: dict[str, Any], system_prompt: str, user_payload: dict[str, Any], required: bool, task_kind: str) -> dict[str, Any]:
        retry_payload = dict(user_payload)
        retry_prompt = system_prompt
        last_error = "agent contract validation failed"
        for attempt in (1, 2):
            payload = self.ai_scheduler.call(run_id=run["id"], role=role, job=job, system_prompt=retry_prompt, user_payload=retry_payload, task_kind=task_kind, required=required)
            payload["parsed_response"] = _json_or_empty(payload.get("raw_response", {}))
            payload["contract_attempt"] = attempt
            if not payload.get("ok"):
                self._record_json(project, run, "agent_run", f"{payload['agent_run_id']}.json", payload, {"role": role, "job_id": job.get("id", ""), "task_kind": payload.get("task_kind", task_kind)})
                self.store.add_event(run["tenant_id"], project["id"], run["id"], "llm_call_failed", {"agent_run_id": payload["agent_run_id"], "role": role, "job_id": job.get("id", ""), "task_kind": payload.get("task_kind", task_kind), "error": str(payload.get("error", ""))[:1000]})
                if required:
                    self.store.update_run(run["id"], status="blocked", continuation={**dict(run.get("continuation") or {}), "failure_reason": "llm_call_failed", "next_action": "repair_llm_connection"})
                    raise LLMError(str(payload.get("error", "llm call failed")))
                return dict(payload["parsed_response"])

            validation = validate_agent_contract(task_kind, dict(payload["parsed_response"]))
            payload["contract_validation"] = self._contract_validation_summary(validation)
            if validation["ok"]:
                payload["parsed_response"] = dict(validation["data"])
            self._record_json(project, run, "agent_run", f"{payload['agent_run_id']}.json", payload, {"role": role, "job_id": job.get("id", ""), "task_kind": payload.get("task_kind", task_kind), "contract_attempt": attempt})

            if validation["ok"]:
                self.store.add_event(run["tenant_id"], project["id"], run["id"], "llm_call_completed", {"agent_run_id": payload["agent_run_id"], "role": role, "job_id": job.get("id", ""), "task_kind": payload.get("task_kind", task_kind), "model_tier": payload.get("model_tier", ""), "elapsed_ms": payload.get("elapsed_ms", 0), "contract_schema": validation["schema_name"], "contract_attempt": attempt})
                self._write_agent_contract_report(project, run)
                parsed = dict(validation["data"])
                parsed.setdefault("agent_run_id", payload["agent_run_id"])
                return parsed

            last_error = f"agent contract validation failed for {task_kind}: {validation['errors']}"
            self._record_agent_contract_violation(project, run, role, job, task_kind, attempt, payload, validation)
            self.store.add_event(run["tenant_id"], project["id"], run["id"], "agent_contract_violation", {"agent_run_id": payload["agent_run_id"], "role": role, "job_id": job.get("id", ""), "task_kind": task_kind, "attempt": attempt, "schema_name": validation["schema_name"], "errors": validation["errors"]})
            self._write_agent_contract_report(project, run)
            if attempt == 1:
                retry_payload = {
                    **dict(user_payload),
                    "agent_contract_retry": {
                        "previous_attempt": attempt,
                        "schema_name": validation["schema_name"],
                        "schema_errors": validation["errors"],
                        "required_json_schema": validation.get("schema") or agent_contract_schema(task_kind),
                        "instruction": "Return corrected strict JSON only. Do not include markdown fences or explanatory text.",
                    },
                }
                retry_prompt = (
                    f"{system_prompt}\n\n"
                    "The previous response failed the required JSON contract. Return corrected strict JSON only. "
                    f"Schema errors: {json.dumps(validation['errors'], ensure_ascii=True)}"
                )

        blocked_run = self.store.update_run(run["id"], status="blocked", continuation={**dict(run.get("continuation") or {}), "failure_reason": "agent_contract_violation", "next_action": "repair_agent_contract", "contract_error": last_error[:2000]})
        self._write_agent_contract_report(project, blocked_run)
        raise AgentContractViolationError(last_error)

    def _contract_validation_summary(self, validation: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": bool(validation.get("ok")),
            "schema_name": validation.get("schema_name", ""),
            "errors": validation.get("errors", []),
        }

    def _record_agent_contract_violation(self, project: dict[str, Any], run: dict[str, Any], role: str, job: dict[str, Any], task_kind: str, attempt: int, payload: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
        violation = {
            "schema_version": "5.0",
            "run_id": run["id"],
            "job_id": job.get("id", ""),
            "role": role,
            "task_kind": task_kind,
            "attempt": attempt,
            "agent_run_id": payload.get("agent_run_id", ""),
            "schema_name": validation.get("schema_name", ""),
            "errors": validation.get("errors", []),
            "parsed_response": payload.get("parsed_response", {}),
            "raw_response_preview": str(payload.get("raw_response", ""))[:4000],
            "schema": validation.get("schema") or agent_contract_schema(task_kind),
        }
        return self._record_json(project, run, "agent_contract_violation", f"{payload.get('agent_run_id', new_id())}.json", violation, {"role": role, "job_id": job.get("id", ""), "task_kind": task_kind, "attempt": attempt})

    def _write_agent_contract_report(self, project: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        artifacts = self.store.list_artifacts(run["id"])
        validations = []
        for artifact in artifacts:
            if artifact["kind"] != "agent_run":
                continue
            payload = artifact.get("payload") or {}
            validation = payload.get("contract_validation") or {}
            if not validation:
                continue
            validations.append(
                {
                    "agent_run_id": payload.get("agent_run_id", artifact["id"]),
                    "role": payload.get("role", ""),
                    "job_id": payload.get("job_id", ""),
                    "task_kind": payload.get("task_kind", ""),
                    "attempt": payload.get("contract_attempt", 1),
                    "ok": bool(validation.get("ok")),
                    "schema_name": validation.get("schema_name", ""),
                    "errors": validation.get("errors", []),
                }
            )
        violations = [artifact.get("payload") or {} for artifact in artifacts if artifact["kind"] == "agent_contract_violation"]
        blocked = bool((run.get("continuation") or {}).get("next_action") == "repair_agent_contract")
        latest_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in validations:
            latest_by_key[(item["role"], item["job_id"], item["task_kind"])] = item
        report_ok = not blocked and all(item["ok"] for item in latest_by_key.values())
        report = {
            "schema_version": "5.0",
            "run_id": run["id"],
            "ok": report_ok,
            "status": "blocked" if blocked else ("passed" if not violations else "passed_with_retries"),
            "validation_count": len(validations),
            "violation_count": len(violations),
            "validations": validations,
            "violations": violations,
        }
        return self._record_json(project, run, "agent_contract_report", "agent-contract-report.json", report)

    def _project_run_snapshot(self, run: dict[str, Any]) -> dict[str, Any]:
        projected = dict(run)
        projected["continuation"] = self._live_continuation(run)
        return projected

    def _live_continuation(self, run: dict[str, Any]) -> dict[str, Any]:
        packages = self.store.list_work_packages(run["id"])
        waves = self.store.list_waves(run["id"])
        jobs = self.store.list_jobs(run_id=run["id"])
        continuation = dict(run.get("continuation") or {})
        continuation.update(
            {
                "schema_version": "5.0",
                "run_id": run["id"],
                "checkpoint": run.get("checkpoint", continuation.get("checkpoint", "run_created")),
                "current_wave": self._current_wave(waves),
                "completed_waves": [wave["wave_key"] for wave in waves if wave.get("status") == "completed"],
                "completed_packages": [package["package_key"] for package in packages if package.get("status") == "completed"],
                "pending_packages": [package["package_key"] for package in packages if package.get("status") not in {"completed", "cancelled"}],
                "leased_jobs": [{"id": job["id"], "role": job["role"], "job_type": job["job_type"], "worker_id": job.get("worker_id", ""), "lease_until": job.get("lease_until")} for job in jobs if job.get("status") == "running"],
                "context_index_status": self._context_index_status(run),
                "artifact_preflight": self._artifact_preflight(run["id"]),
            }
        )
        continuation["next_action"] = self._next_action(run, jobs, continuation["artifact_preflight"])
        return continuation

    def _next_action(self, run: dict[str, Any], jobs: list[dict[str, Any]], preflight: dict[str, Any]) -> str:
        if run.get("status") in {"completed", "cancelled", "release_ready", "no_go", "blocked"}:
            return (run.get("continuation") or {}).get("next_action", run.get("status"))
        if not preflight.get("ok", True):
            return "repair_missing_artifacts"
        queued = [job for job in jobs if job["status"] in {"queued", "retry", "leased", "running"}]
        if queued:
            return "claim_pending_jobs"
        return (run.get("continuation") or {}).get("next_action", "idle")

    def _context_for_package(self, run: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
        return package_context(((run.get("metadata") or {}).get("context_snapshot") or {}), package)

    def _context_index_status(self, run: dict[str, Any]) -> dict[str, Any]:
        checkpoint = run.get("checkpoint", "")
        if checkpoint in {"run_created", "requirements_completed", "architecture_completed"}:
            return {"ok": True, "missing": [], "schema_version": "5.0", "not_required_yet": True}
        return validate_context_snapshot((run.get("metadata") or {}).get("context_snapshot"))

    def _record_wave_report(self, project: dict[str, Any], run: dict[str, Any], wave_key: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
        report = {"schema_version": "5.0", "run_id": run["id"], "wave_key": wave_key, "status": "passed" if all(package.get("status") == "completed" for package in packages) else "failed", "completed_packages": [package["package_key"] for package in packages if package.get("status") == "completed"], "package_count": len(packages), "risks": []}
        return self._record_json(project, run, "wave_report", f"wave-report-{wave_key}.json", report, {"wave_key": wave_key})

    def _record_json(self, project: dict[str, Any], run: dict[str, Any], kind: str, filename: str, payload: Any, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        artifact = self.artifacts.write_json(run["tenant_id"], project["name"], run["id"], kind, filename, payload, metadata)
        artifact["payload"] = payload
        return self.store.add_artifact(run["tenant_id"], project["id"], artifact)

    def _write_manifest(self, project: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        latest_run = self._require_run(run["id"])
        existing = [artifact for artifact in self.store.list_artifacts(run["id"]) if artifact["kind"] != "artifact_manifest"]
        manifest = build_manifest(existing)
        artifact = self.artifacts.write_json(run["tenant_id"], project["name"], run["id"], "artifact_manifest", "artifact-manifest.json", manifest)
        artifact["payload"] = manifest
        recorded = self.store.add_artifact(run["tenant_id"], project["id"], artifact)
        self.store.update_run(run["id"], metadata={**dict(latest_run.get("metadata") or {}), "artifact_manifest_path": recorded["path"]})
        return recorded

    def _artifact_preflight(self, run_id: str) -> dict[str, Any]:
        artifacts = self.store.list_artifacts(run_id)
        missing = [{"artifact_id": artifact["id"], "path": artifact.get("path"), "reason": "missing_file"} for artifact in artifacts if artifact.get("path") and not Path(artifact["path"]).exists()]
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

    def _latest_artifact_payload(self, run_id: str, kind: str) -> dict[str, Any]:
        matches = [artifact for artifact in self.store.list_artifacts(run_id) if artifact["kind"] == kind]
        return (matches[-1].get("payload") if matches else {}) or {}

    def _refresh_indexes(self, project: dict[str, Any], run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = dict(run.get("metadata") or {})
        artifacts = self.store.list_artifacts(run["id"])
        code_index = build_code_index(self.runtime.project_root(project))
        code_artifact = self._record_json(project, run, "code_index", "code-index.json", code_index)
        contract_index = build_contract_index(
            architecture=metadata.get("architecture_design", {}),
            package_plan=metadata.get("package_dag", {}),
            patch_sets=[artifact for artifact in artifacts if artifact["kind"] == "patch_set"],
            code_index=code_index,
            code_review=self._latest_payload_or_empty(artifacts, "code_review_report"),
        )
        contract_artifact = self._record_json(project, run, "contract_index", "contract-index.json", contract_index)
        latest_run = self._require_run(run["id"])
        self.store.update_run(
            run["id"],
            metadata={
                **dict(latest_run.get("metadata") or {}),
                "code_index": code_index,
                "contract_index": contract_index,
                "code_index_hash": code_index.get("index_hash", ""),
                "contract_index_hash": contract_index.get("index_hash", ""),
                "code_index_path": code_artifact["path"],
                "contract_index_path": contract_artifact["path"],
            },
        )
        return code_index, contract_index

    def _latest_payload_or_empty(self, artifacts: list[dict[str, Any]], kind: str) -> dict[str, Any]:
        matches = [artifact for artifact in artifacts if artifact["kind"] == kind]
        return (matches[-1].get("payload") if matches else {}) or {}

    def _write_patch_transaction_report(self, project: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        transactions = [artifact.get("payload") or {} for artifact in self.store.list_artifacts(run["id"]) if artifact["kind"] == "patch_transaction"]
        report = {
            "schema_version": "5.0",
            "run_id": run["id"],
            "ok": all(item.get("ok", False) for item in transactions) if transactions else True,
            "transaction_count": len(transactions),
            "changed_file_count": sum(len(item.get("changed_files") or []) for item in transactions),
            "conflict_count": sum(len(item.get("conflicts") or []) for item in transactions),
            "transactions": transactions,
        }
        return self._record_json(project, run, "patch_transaction_report", "patch-transactions.json", report)

    def _execute_validation_commands(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
        layout = metadata.get("project_layout") or {}
        layout_commands = [str(item).strip() for item in layout.get("validation_commands") or [] if str(item).strip()]
        qa_reports = [artifact.get("payload") or {} for artifact in artifacts if artifact["kind"] == "test_report"]
        qa_commands = [str(command).strip() for report in qa_reports for command in report.get("commands") or [] if str(command).strip()]
        commands = layout_commands or qa_commands
        source = "project_layout" if layout_commands else ("qa_agent" if qa_commands else "none")
        execution = run_validation_commands(self.runtime.project_root(project), commands)
        report = {
            **execution,
            "run_id": run["id"],
            "source": source,
            "commands": commands,
            "coverage": {
                "layout_validation_command_count": len(layout_commands),
                "qa_command_count": len(qa_commands),
                "selected_command_count": len(commands),
            },
        }
        self._record_json(project, run, "test_execution_report", "test-execution-report.json", report)
        return report

    def _current_wave(self, waves: list[dict[str, Any]]) -> str | None:
        pending = [wave for wave in waves if wave.get("status") != "completed"]
        if not pending:
            return None
        return sorted(pending, key=lambda item: item["sequence"])[0]["wave_key"]

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

    def _project_prompt(self, project: dict[str, Any]) -> dict[str, Any]:
        return {"id": project.get("id"), "name": project.get("name"), "title": project.get("title"), "description": project.get("description"), "config": project.get("config") or {}, "project_path": project.get("project_path", "")}

    def _project_snapshot(self, project: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(project)
        snapshot["project_path_status"] = self.runtime.project_path_status(project)
        snapshot["resolved_project_root"] = snapshot["project_path_status"].get("project_root", "")
        return snapshot

    def _resolve_export_target(self, target_path: str, project: dict[str, Any]) -> Path:
        configured = str(target_path or "").strip()
        if not configured:
            configured = project.get("name") or project.get("title") or project["id"]
        if self.runtime._can_use_configured_project_path(configured):
            return Path(configured).expanduser().resolve()
        normalized = configured.replace("\\", "/")
        lowered = normalized.lower()
        marker = "/ai_agent/"
        if self.runtime._looks_like_windows_absolute_path(configured) and marker in lowered:
            suffix = normalized[lowered.index(marker) + len(marker) :].strip("/")
            candidate = (self.workspace_root.parent / suffix).resolve()
            return candidate
        if normalized.lower().startswith("/app/"):
            return Path(normalized).resolve()
        if self.runtime._looks_like_windows_absolute_path(configured):
            raise DeliveryExportError("Windows export paths must be under the mounted AI_Agent folder, or use a relative export path")
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.workspace_root.parent / "exports" / candidate).resolve()

    def _should_skip_export_path(self, relative: Path) -> bool:
        parts = set(relative.parts)
        name = relative.name
        if parts & EXPORT_EXCLUDED_DIRS:
            return True
        if name in EXPORT_EXCLUDED_FILES:
            return True
        if name.endswith((".pyc", ".pyo", ".log", ".tmp")):
            return True
        return False

    def _normalize_layout(self, design: dict[str, Any]) -> dict[str, Any]:
        layout = design.get("project_layout") or design.get("layout") or {}
        if not isinstance(layout, dict):
            layout = {}
        source_root = str(layout.get("source_root") or "src").strip()
        delivery_root = str(layout.get("delivery_root") or source_root).strip()
        directories = layout.get("directories") or [{"path": source_root}, {"path": delivery_root}]
        entrypoints = layout.get("entrypoints") or []
        validation_commands = layout.get("validation_commands") or []
        return {"schema_version": "5.0", "source_root": source_root, "delivery_root": delivery_root, "entrypoints": entrypoints, "directories": directories, "validation_commands": validation_commands}

    def _normalize_package_plan(self, plan: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
        waves = plan.get("waves") if isinstance(plan.get("waves"), list) else []
        packages = plan.get("packages") if isinstance(plan.get("packages"), list) else []
        if not waves:
            waves = [{"wave_key": "WAVE-001", "sequence": 1, "status": "queued"}]
        normalized_waves = []
        for index, wave in enumerate(waves, start=1):
            normalized_waves.append({"id": wave.get("id") or new_id(), "wave_key": wave.get("wave_key") or f"WAVE-{index:03d}", "sequence": int(wave.get("sequence") or index), "status": wave.get("status", "queued"), "summary": wave.get("summary", "")})
        if not packages:
            source_root = layout.get("source_root") or "src"
            packages = [
                {"package_key": "PKG-BACKEND", "role": "backend", "domain": "backend", "subsystem": "backend", "wave_key": normalized_waves[0]["wave_key"], "depends_on": [], "allowed_paths": [f"{source_root}/**"], "objective": "Implement core backend files."},
                {"package_key": "PKG-QA", "role": "qa", "domain": "qa", "subsystem": "tests", "wave_key": normalized_waves[0]["wave_key"], "depends_on": [], "allowed_paths": ["tests/**"], "objective": "Implement AI-generated tests."},
            ]
        wave_keys = {wave["wave_key"] for wave in normalized_waves}
        normalized_packages = []
        for index, package in enumerate(packages, start=1):
            role = str(package.get("role") or package.get("agent") or "backend").lower()
            if role == "database":
                role = "db"
            wave_key = package.get("wave_key") if package.get("wave_key") in wave_keys else normalized_waves[0]["wave_key"]
            allowed_paths = package.get("allowed_paths") or [f"{layout.get('source_root') or 'src'}/**"]
            normalized_packages.append(
                {
                    "id": package.get("id") or new_id(),
                    "package_key": package.get("package_key") or f"PKG-{index:03d}",
                    "role": role,
                    "agent": package.get("agent") or f"{role}_agent",
                    "domain": package.get("domain") or role,
                    "subsystem": package.get("subsystem") or role,
                    "wave_key": wave_key,
                    "depends_on": package.get("depends_on") or [],
                    "allowed_paths": allowed_paths,
                    "forbidden_paths": package.get("forbidden_paths") or [".git/**", ".v5/**"],
                    "objective": package.get("objective") or package.get("summary") or f"Implement {role} package.",
                    "requirements_mapping": package.get("requirements_mapping") or package.get("requirements") or [],
                    "expected_outputs": package.get("expected_outputs") or [],
                    "acceptance_gates": package.get("acceptance_gates") or [],
                    "status": package.get("status", "queued"),
                }
            )
        return {"schema_version": "5.0", "waves": normalized_waves, "packages": normalized_packages}

    def _job_type_for_package(self, package: dict[str, Any]) -> str:
        role = package.get("role", "")
        if role == "qa":
            return "test_generation"
        if role == "security":
            return "security_review"
        return "code_generation"

    def _file_summaries(self, project: dict[str, Any]) -> list[dict[str, Any]]:
        project_root = self.runtime.project_root(project)
        summaries = []
        for relative in self.runtime.list_project_files(project, limit=160):
            path = project_root / relative
            text = ""
            if path.suffix.lower() in {".php", ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".sql", ".html", ".md", ".json"}:
                text = path.read_text(encoding="utf-8", errors="ignore")[:1200]
            summaries.append({"path": relative, "preview": text})
        return summaries

    def _artifact_summaries(self, run_id: str) -> list[dict[str, Any]]:
        return [{"id": artifact["id"], "kind": artifact["kind"], "payload": artifact.get("payload", {})} for artifact in self.store.list_artifacts(run_id)[-20:]]
