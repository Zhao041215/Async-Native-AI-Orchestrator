from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v6.ai_payload import compact_payload_for_budget, payload_budget_report, payload_outline
from dev_orchestrator.v6.agent_contracts import agent_contract_schema, validate_agent_contract
from dev_orchestrator.v6.ai_scheduler import AICallScheduler
from dev_orchestrator.v6.artifacts import ArtifactWriter, build_manifest
from dev_orchestrator.v6.code_indexer import build_code_index
from dev_orchestrator.v6.contract_store import build_contract_index
from dev_orchestrator.v6.context_memory import build_context_snapshot, package_context, validate_context_snapshot
from dev_orchestrator.v6.kernel import build_checkpoint_resume_plan, build_mission_graph, build_recovery_trace, build_replay_projection
from dev_orchestrator.v6.llm_policy import ai_policy_payload_limits, get_ai_task_budget, resolve_ai_task_budget
from dev_orchestrator.v6.models import DEFAULT_TENANT, new_id, sha256_file, slugify
from dev_orchestrator.v6.role_aliases import canonical_worker_role, normalize_role_name
from dev_orchestrator.v6.patch_runtime import TransactionalPatchRuntime
from dev_orchestrator.v6.release_quality import build_deploy_guide, validate_ai_native_project
from dev_orchestrator.v6.runtime import AgentFileRuntime, PatchValidationError
from dev_orchestrator.v6.scale_inference import (
    choose_larger_scale,
    infer_initial_scale,
    infer_scale_from_architecture,
    infer_scale_from_package_plan,
    infer_scale_from_requirements,
    merge_inference_history,
)
from dev_orchestrator.v6.store import V6Store
from dev_orchestrator.v6.storage_lifecycle import StorageLifecycleManager, StorageLifecyclePolicy
from dev_orchestrator.v6.test_runner import run_validation_commands
from dev_orchestrator.v6.memory import build_layered_memory, memory_for_package
from dev_orchestrator.v6.mission import build_mission_state
from dev_orchestrator.v6.profiles import resolve_scale_profile, scale_job_attempts


DEFAULT_PROJECT_CONFIG = {
    "target_scale": "auto",
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


class ProviderCallError(LLMError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


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
    ".v6",
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


class V6Orchestrator:
    def __init__(
        self,
        store: V6Store,
        workspace_root: Path,
        tenant_id: str = DEFAULT_TENANT,
        llm_client: OpenAICompatibleClient | None = None,
        logs_root: Path | None = None,
        storage_policy: StorageLifecyclePolicy | None = None,
    ):
        self.store = store
        self.workspace_root = workspace_root.resolve()
        self.logs_root = (logs_root or self.workspace_root.parent.parent / "logs").resolve()
        self.tenant_id = tenant_id or DEFAULT_TENANT
        self.artifacts = ArtifactWriter(self.workspace_root)
        self.runtime = AgentFileRuntime(self.workspace_root)
        self.patch_runtime = TransactionalPatchRuntime(self.runtime)
        self.materializer = self.runtime
        self.llm_client = llm_client
        self.ai_scheduler = AICallScheduler(llm_client)
        self.storage_policy = storage_policy or StorageLifecyclePolicy()
        self.storage_lifecycle = StorageLifecycleManager(self.workspace_root, self.logs_root, self.storage_policy)

    def update_llm_client(self, llm_client: OpenAICompatibleClient | None) -> None:
        self.llm_client = llm_client
        self.ai_scheduler.llm_client = llm_client

    def bootstrap(self, attempts: int = 1, delay_seconds: float = 1.0) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.logs_root.mkdir(parents=True, exist_ok=True)
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

    def _project_scale_profile(self, project: dict[str, Any]) -> dict[str, Any]:
        config = project.get("config") or {}
        inferred = config.get("inferred_target_scale")
        if str(config.get("target_scale") or "").strip().lower() in {"", "auto"} and inferred:
            return resolve_scale_profile({"target_scale": inferred})
        return resolve_scale_profile(config)

    def _run_scale_profile(self, run: dict[str, Any], project: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata = run.get("metadata") or {}
        if metadata.get("scale_profile"):
            return resolve_scale_profile(metadata.get("scale_profile"))
        if project is not None:
            return self._project_scale_profile(project)
        return resolve_scale_profile(metadata.get("project_config") or {})

    def _job_payload(self, run: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {**dict(payload or {}), "scale_profile": self._run_scale_profile(run)}

    def _job_attempts(self, run: dict[str, Any], task_kind: str) -> int:
        return scale_job_attempts(task_kind, self._run_scale_profile(run))

    def _apply_scale_inference(self, run: dict[str, Any], project: dict[str, Any], inference: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = dict(run.get("metadata") or {})
        requested = str(metadata.get("target_scale_requested") or (project.get("config") or {}).get("target_scale") or "auto").strip().lower() or "auto"
        current_profile = self._run_scale_profile(run, project)
        current_scale = current_profile.get("name", "medium")
        inferred_scale = inference.get("selected_scale") or current_scale
        selected_scale = choose_larger_scale(current_scale, inferred_scale) if requested in {"", "auto"} else current_scale
        upgraded = selected_scale != current_scale
        selected_profile = resolve_scale_profile({"target_scale": selected_scale})
        history = merge_inference_history(metadata.get("scale_inference_history"), {**inference, "previous_scale": current_scale, "applied_scale": selected_scale, "upgraded": upgraded})
        metadata.update(
            {
                "scale_profile": selected_profile,
                "scale_inference": {**inference, "previous_scale": current_scale, "applied_scale": selected_scale, "upgraded": upgraded},
                "scale_inference_history": history,
                "inferred_target_scale": selected_scale,
                "scale_auto_upgrade_enabled": requested in {"", "auto"},
                "target_scale_requested": requested,
            }
        )
        if upgraded:
            continuation = {**dict(run.get("continuation") or {}), "scale_profile": selected_profile, "scale_upgraded_from": current_scale, "scale_upgraded_to": selected_scale}
            updated_run = self.store.update_run(run["id"], metadata=metadata, continuation=continuation)
            self.store.add_event(run["tenant_id"], project["id"], run["id"], "scale_profile.upgraded", {"run_id": run["id"], "from": current_scale, "to": selected_scale, "inference": inference})
            return updated_run, metadata
        return {**run, "metadata": metadata}, metadata

    def ai_policy(self) -> dict[str, Any]:
        limits = ai_policy_payload_limits()
        config_limit = int(getattr(getattr(self.llm_client, "config", None), "max_request_body_bytes", 0) or 0)
        if config_limit:
            limits = {**limits, "provider_body_limit_bytes": config_limit}
        return {
            "schema_version": "6.1",
            "policy": "ai-agent-native-file-manifest",
            "payload_limits": limits,
            "compression_policy": {
                "mode": "trim_then_ai_context_summary_then_recover",
                "summary_task_kind": "context_summary",
                "blocking_event": "ai_payload_oversize_blocked",
            },
            "live_ai_policy": {
                "large_profiles_require_live_provider_evidence": True,
                "fake_clients_allowed_for_small_unit_regressions_only": True,
            },
            "parallel_policy": {
                "mode": "profile_driven_durable_ai_slots",
                "provider_health_source": "durable_store",
                "backpressure": "provider_and_run_slot_limits",
            },
        }

    def ai_calls(self, run_id: str) -> list[dict[str, Any]]:
        self._require_run(run_id)
        items = [artifact for artifact in self.store.list_artifacts(run_id) if artifact["kind"] == "agent_run"]
        return [
            {
                **artifact,
                "payload_budget": (artifact.get("payload") or {}).get("payload_budget", {}),
                "payload_chars": (artifact.get("payload") or {}).get("payload_chars", 0),
                "payload_bytes": (artifact.get("payload") or {}).get("payload_bytes", 0),
                "body_bytes": (artifact.get("payload") or {}).get("body_bytes", 0),
                "estimated_tokens": (artifact.get("payload") or {}).get("estimated_tokens", 0),
                "budget_status": (artifact.get("payload") or {}).get("budget_status", ""),
                "compression_applied": (artifact.get("payload") or {}).get("compression_applied", False),
                "summary_agent_run_id": (artifact.get("payload") or {}).get("summary_agent_run_id", ""),
                "budget_limit": (artifact.get("payload") or {}).get("budget_limit", 0),
                "provider_body_limit_bytes": (artifact.get("payload") or {}).get("provider_body_limit_bytes", 0),
                "live_provider": (artifact.get("payload") or {}).get("live_provider", False),
                "model": (artifact.get("payload") or {}).get("model", ""),
                "model_tier": (artifact.get("payload") or {}).get("model_tier", ""),
                "queued_at": (artifact.get("payload") or {}).get("queued_at", 0),
                "started_at": (artifact.get("payload") or {}).get("started_at", 0),
                "finished_at": (artifact.get("payload") or {}).get("finished_at", 0),
                "wait_ms": (artifact.get("payload") or {}).get("wait_ms", 0),
                "slot_id": (artifact.get("payload") or {}).get("slot_id", ""),
                "concurrency_limited": (artifact.get("payload") or {}).get("concurrency_limited", False),
            }
            for artifact in items
        ]

    def context_index(self, run_id: str) -> dict[str, Any]:
        self._require_run(run_id)
        artifacts = self.store.list_artifacts(run_id)
        snapshot = self._latest_payload_or_empty(artifacts, "context_snapshot")
        budget_reports = [artifact.get("payload") or {} for artifact in artifacts if artifact["kind"] == "ai_payload_budget"]
        latest_budget = budget_reports[-1] if budget_reports else {}
        return {
            "snapshot": snapshot,
            "trimming": latest_budget.get("trim_report", {}),
            "budget": latest_budget,
            "compression_applied": bool(latest_budget.get("compression_applied")),
            "selected_layers": (latest_budget.get("trim_report") or {}).get("selected_layers", []),
            "dropped": (latest_budget.get("trim_report") or {}).get("dropped", []),
        }

    def create_project(self, payload: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
        config = dict(DEFAULT_PROJECT_CONFIG)
        config.update(payload.get("config") or {})
        for key in DEFAULT_PROJECT_CONFIG:
            if key in payload and payload[key] is not None:
                config[key] = payload[key]
        requested_scale = str(config.get("target_scale") or "auto").strip().lower() or "auto"
        inference = infer_initial_scale(config, payload.get("description") or "")
        effective_scale = inference["selected_scale"] if requested_scale in {"", "auto"} else requested_scale
        scale_profile = resolve_scale_profile({"target_scale": effective_scale})
        config["target_scale"] = requested_scale
        config["inferred_target_scale"] = effective_scale
        config["scale_inference"] = inference
        config["scale_inference_history"] = [inference]
        config["scale_profile"] = scale_profile
        config["kernel_generation"] = scale_profile.get("kernel_generation", "100k_ai_native")
        config["mission_contract_version"] = scale_profile.get("mission_contract_version", "6.1")
        name = payload.get("name") or slugify(payload.get("title") or "v6-ai-agent-project")
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
        scale_profile = self._project_scale_profile(project)
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
                "scale_profile": scale_profile,
                "target_scale_requested": (project.get("config") or {}).get("target_scale", "auto"),
                "scale_inference": (project.get("config") or {}).get("scale_inference", {}),
                "scale_inference_history": (project.get("config") or {}).get("scale_inference_history", []),
                "kernel_generation": scale_profile.get("kernel_generation", "100k_ai_native"),
                "mission_contract_version": scale_profile.get("mission_contract_version", "6.1"),
            },
        )
        continuation = {
            **dict(run.get("continuation") or {}),
            "schema_version": "6.0",
            "kernel_generation": scale_profile.get("kernel_generation", "100k_ai_native"),
            "mission_contract_version": scale_profile.get("mission_contract_version", "6.1"),
            "scale_profile": scale_profile,
            "recovery_policy": scale_profile.get("recovery_policy", "checkpoint_retry_then_repair"),
        }
        run = self.store.update_run(run["id"], continuation=continuation, metadata={**dict(run.get("metadata") or {}), "scale_profile": scale_profile})
        self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": "requirements_analysis",
                "role": "requirements",
                "run_id": run["id"],
                "resume_key": f"run:{run['id']}:requirements_analysis",
                "payload": {"requirements_text": text, "scale_profile": scale_profile},
                "max_attempts": scale_job_attempts("requirements_analysis", scale_profile),
            },
        )
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

    def storage_report(self) -> dict[str, Any]:
        projects = self.store.list_projects(self.tenant_id)
        runs_by_project = {project["id"]: self.store.list_runs(project["id"]) for project in projects}
        jobs_by_run: dict[str, list[dict[str, Any]]] = {}
        artifacts_by_run: dict[str, list[dict[str, Any]]] = {}
        for runs in runs_by_project.values():
            for run in runs:
                jobs_by_run[run["id"]] = self.store.list_jobs(run_id=run["id"])
                artifacts_by_run[run["id"]] = self.store.list_artifacts(run["id"])
        report = self.storage_lifecycle.build_report(projects, runs_by_project, jobs_by_run, artifacts_by_run)
        report["managed_roots"] = {
            "runtime_root": str(self.workspace_root),
            "logs_root": str(self.logs_root),
            "user_export_policy": "never_delete_user_export_targets",
        }
        return report

    def run_storage(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        project = self._require_project(run["project_id"])
        return self.storage_lifecycle.run_summary(project, run, self.store.list_jobs(run_id=run_id), self.store.list_artifacts(run_id))

    def prune_run_storage(self, run_id: str, dry_run: bool = True, force: bool = False) -> dict[str, Any]:
        run = self._require_run(run_id)
        project = self._require_project(run["project_id"])
        jobs = self.store.list_jobs(run_id=run_id)
        artifacts = self.store.list_artifacts(run_id)
        plan = self.storage_lifecycle.build_run_prune_plan(project, run, jobs, artifacts, force=force)
        self.store.add_event(run["tenant_id"], project["id"], run_id, "storage.gc_planned", {"run_id": run_id, "dry_run": dry_run, "force": force, "plan": plan})
        result = self.storage_lifecycle.apply_delete_plan(plan, dry_run=dry_run) if plan["ok"] else {"schema_version": "6.3", "ok": False, "dry_run": dry_run, "deleted": [], "errors": [], "reclaimed_bytes": 0, "blocked_reasons": plan["blocked_reasons"]}
        latest = self._require_run(run_id)
        lifecycle = dict((latest.get("metadata") or {}).get("storage_lifecycle") or {})
        if result.get("ok") and not dry_run:
            lifecycle.update(
                {
                    "state": "pruned",
                    "pruned_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                    "last_prune": result,
                }
            )
            self.store.update_run(run_id, metadata={**dict(latest.get("metadata") or {}), "storage_lifecycle": lifecycle})
        self.store.add_event(run["tenant_id"], project["id"], run_id, "storage.gc_completed", {"run_id": run_id, "dry_run": dry_run, "result": result})
        return {"plan": plan, "result": result}

    def archive_run_storage(self, run_id: str, include_worktree: bool = False, dry_run: bool = True) -> dict[str, Any]:
        run = self._require_run(run_id)
        project = self._require_project(run["project_id"])
        result = self.storage_lifecycle.archive_run(project, run, self.store.list_artifacts(run_id), include_worktree=include_worktree, dry_run=dry_run)
        self.store.add_event(run["tenant_id"], project["id"], run_id, "storage.archive_planned" if dry_run else "storage.archived", {"run_id": run_id, "result": result})
        if result.get("ok") and not dry_run:
            latest = self._require_run(run_id)
            lifecycle = dict((latest.get("metadata") or {}).get("storage_lifecycle") or {})
            lifecycle.update(
                {
                    "state": "archived",
                    "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                    "archive_path": result.get("archive_path", ""),
                    "archive_file_count": result.get("file_count", 0),
                }
            )
            self.store.update_run(run_id, metadata={**dict(latest.get("metadata") or {}), "storage_lifecycle": lifecycle})
        return result

    def gc_storage(self, dry_run: bool = True, force: bool = False, include_logs: bool = True) -> dict[str, Any]:
        report = self.storage_report()
        run_results = []
        candidates = report.get("runs", []) if force else report.get("cleanup_candidates", [])
        for candidate in candidates:
            run_results.append(self.prune_run_storage(candidate["run_id"], dry_run=dry_run, force=force))
        log_plan = self.storage_lifecycle.old_log_delete_plan() if include_logs else {"schema_version": "6.3", "ok": True, "targets": [], "estimated_reclaim_bytes": 0}
        self.store.add_event(self.tenant_id, None, None, "storage.gc_planned", {"dry_run": dry_run, "force": force, "include_logs": include_logs, "run_candidate_count": len(run_results), "log_plan": log_plan})
        log_result = self.storage_lifecycle.apply_delete_plan(log_plan, dry_run=dry_run)
        result = {
            "schema_version": "6.3",
            "ok": all(item["result"].get("ok") for item in run_results) and log_result.get("ok", False),
            "dry_run": dry_run,
            "force": force,
            "run_results": run_results,
            "log_plan": log_plan,
            "log_result": log_result,
            "estimated_reclaim_bytes": sum(int(item["plan"].get("estimated_reclaim_bytes") or 0) for item in run_results) + int(log_plan.get("estimated_reclaim_bytes") or 0),
            "reclaimed_bytes": sum(int(item["result"].get("reclaimed_bytes") or 0) for item in run_results) + int(log_result.get("reclaimed_bytes") or 0),
        }
        self.store.add_event(self.tenant_id, None, None, "storage.gc_completed", result)
        return result

    def list_events(self, run_id: str, event_type: str | None = None, after_sequence: int | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        events = self.store.list_events(run_id, event_type=event_type)
        if after_sequence is not None:
            events = [event for event in events if int((event.get("metadata") or {}).get("sequence") or 0) > int(after_sequence)]
        events = sorted(events, key=lambda event: int((event.get("metadata") or {}).get("sequence") or 0))
        if limit is not None:
            events = events[: max(1, min(int(limit), 1000))]
        return events

    def replay_projection(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        events = self.store.list_events(run_id=run_id)
        return build_replay_projection(
            run=run,
            events=events,
            jobs=self.store.list_jobs(run_id=run_id),
            waves=self.store.list_waves(run_id=run_id),
            packages=self.store.list_work_packages(run_id=run_id),
        )

    def checkpoint_resume(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        events = self.store.list_events(run_id=run_id)
        return build_checkpoint_resume_plan(
            run=run,
            events=events,
            jobs=self.store.list_jobs(run_id=run_id),
            waves=self.store.list_waves(run_id=run_id),
            packages=self.store.list_work_packages(run_id=run_id),
        )

    def continuation(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        return self._live_continuation(run)

    def provider_health(self) -> dict[str, Any]:
        try:
            return self.store.provider_health_snapshot()
        except Exception:
            return self.ai_scheduler.provider_health.snapshot()

    def ai_slot_snapshot(self, run_id: str | None = None) -> dict[str, Any]:
        try:
            return self.store.ai_slot_snapshot(run_id=run_id)
        except Exception:
            return {"schema_version": "6.2", "active_count": 0, "slot_count": 0, "items": []}

    def mission_state(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        project = self._require_project(run["project_id"])
        events = self.store.list_events(run_id=run_id)
        return build_mission_state(
            run={**run, "continuation": self._live_continuation(run)},
            project=project,
            jobs=self.store.list_jobs(run_id=run_id),
            waves=self.store.list_waves(run_id),
            packages=self.store.list_work_packages(run_id),
            artifacts=self.store.list_artifacts(run_id),
            provider_health=self.provider_health(),
            events=events,
        )

    def mission_graph(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        project = self._require_project(run["project_id"])
        events = self.store.list_events(run_id=run_id)
        return build_mission_graph(
            run={**run, "continuation": self._live_continuation(run)},
            project=project,
            jobs=self.store.list_jobs(run_id=run_id),
            waves=self.store.list_waves(run_id),
            packages=self.store.list_work_packages(run_id),
            artifacts=self.store.list_artifacts(run_id),
            events=events,
        )

    def recovery_trace(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        events = self.store.list_events(run_id=run_id)
        return build_recovery_trace(
            run={**run, "continuation": self._live_continuation(run)},
            jobs=self.store.list_jobs(run_id=run_id),
            events=events,
            provider_health=self.provider_health(),
        )

    def pause_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        return self.store.update_run(run_id, status="paused", checkpoint=run.get("checkpoint", "run_created"), continuation={**dict(run.get("continuation") or {}), "next_action": "resume_from_job_boundary"})

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        preflight = self._artifact_preflight(run_id)
        if not preflight["ok"]:
            return self.store.update_run(run_id, status="blocked", continuation={**dict(run.get("continuation") or {}), "failure_reason": "repair_missing_artifacts", "artifact_preflight": preflight, "next_action": "repair_missing_artifacts"})
        plan = self.checkpoint_resume(run_id)
        updated = self.store.update_run(run_id, status="running", continuation={**dict(run.get("continuation") or {}), "next_action": plan["next_action"], "resume_from": plan["resume_from"], "checkpoint_resume": plan})
        self._enqueue_checkpoint_resume_job(updated, plan)
        return updated

    def recover_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        plan = self.checkpoint_resume(run_id)
        continuation = {
            **dict(run.get("continuation") or {}),
            "failure_reason": "",
            "next_action": plan["next_action"],
            "resume_from": plan["resume_from"],
            "checkpoint_resume": plan,
            "recovery_source": "manual_api",
        }
        recovered = self.store.update_run(run_id, status="running", continuation=continuation)
        self.store.add_event(recovered["tenant_id"], recovered.get("project_id"), run_id, "run.manual_recovery_requested", {"run_id": run_id, "checkpoint_resume": plan})
        self._enqueue_checkpoint_resume_job(recovered, plan)
        return recovered

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        return self.store.update_run(run_id, status="cancelled", continuation={**dict(run.get("continuation") or {}), "next_action": "cancelled"})

    def requeue_dead_letter(self, job_id: str) -> dict[str, Any]:
        jobs = [job for job in self.store.list_jobs(status="dead_letter") if job["id"] == job_id]
        if not jobs:
            raise RuntimeError(f"dead-letter job not found: {job_id}")
        job = jobs[0]
        requeued = self.store.enqueue_job(
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
                "resume_key": f"{job['resume_key']}:requeued:{new_id()}",
            },
        )
        if job.get("run_id"):
            run = self.get_run(job["run_id"])
            if run:
                continuation = dict(run.get("continuation") or {})
                continuation.update({"failure_reason": "", "next_action": "claim_pending_jobs"})
                self.store.update_run(job["run_id"], status="queued", continuation=continuation)
        self.store.add_event(job["tenant_id"], None, job.get("run_id"), "dead_letter.requeued", {"source_job_id": job_id, "requeued_job_id": requeued["id"], "resume_key": requeued.get("resume_key", "")})
        return {"ok": True, "job_id": job_id, "requeued_job_id": requeued["id"]}

    def requeue_expired_leases(self) -> dict[str, Any]:
        count = self.store.requeue_expired_jobs(self.tenant_id)
        self.store.add_event(self.tenant_id, None, None, "lease.requeue_sweep", {"tenant_id": self.tenant_id, "requeued_count": count})
        return {"ok": True, "requeued_count": count}

    def _enqueue_checkpoint_resume_job(self, run: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
        if plan["next_action"] == "worker_claim_pending_jobs":
            return None
        mapping = {
            "requirements_analysis": ("requirements_analysis", "requirements"),
            "architecture_design": ("architecture_design", "architect"),
            "package_planning": ("package_planning", "planner"),
            "integration": ("integration", "integration"),
            "code_review": ("code_review", "review"),
            "quality": ("quality", "qa"),
            "release_notes": ("release_notes", "release"),
            "release_candidate": ("release_candidate", "release"),
        }
        if plan["next_action"] == "worker_claim_package_jobs":
            project = self._require_project(run["project_id"])
            current_wave = (run.get("continuation") or {}).get("current_wave") or self._current_wave(self.store.list_waves(run["id"]))
            if current_wave:
                self._enqueue_wave_packages(project, run, current_wave)
            return None
        target = mapping.get(str(plan.get("next_action") or ""))
        if not target:
            return None
        job_type, role = target
        existing = [job for job in self.store.list_jobs(run_id=run["id"]) if job["job_type"] == job_type and job["status"] in {"queued", "retry", "leased", "running"}]
        if existing:
            return existing[0]
        return self.store.enqueue_job(
            run["tenant_id"],
            {
                "job_type": job_type,
                "role": role,
                "run_id": run["id"],
                "resume_key": f"run:{run['id']}:{job_type}:checkpoint_resume",
                "payload": self._job_payload(run, {"resume_from": plan["resume_from"], "checkpoint_resume": plan}),
                "max_attempts": self._job_attempts(run, job_type),
            },
        )

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
            "schema_version": "6.3",
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
        export_time = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        quality_summary = self._latest_payload_or_empty(self.store.list_artifacts(run_id), "quality_report")
        receipt = {
            "schema_version": "6.3",
            "run_id": run_id,
            "project_id": project["id"],
            "exported_at": export_time,
            "source_project_root": str(source_root),
            "target_path": str(target_root),
            "copied_count": len(copied),
            "copied_bytes": sum(int(item.get("size") or 0) for item in copied),
            "copied_files": copied,
            "quality_status": quality_summary.get("status", ""),
            "quality_ok": quality_summary.get("ok"),
            "retention": {
                "state": "exported",
                "worktree_retention_days": self.storage_policy.exported_worktree_retention_days,
                "user_export_policy": "never_delete_user_export_targets",
            },
        }
        receipt_artifact = self._record_json(project, latest_run, "delivery_export_receipt", "delivery-export-receipt.json", receipt)
        metadata = dict(latest_run.get("metadata") or {})
        lifecycle = dict(metadata.get("storage_lifecycle") or {})
        lifecycle.update(
            {
                "schema_version": "6.3",
                "state": "exported",
                "exported_at": export_time,
                "delivery_export_receipt_id": receipt_artifact["id"],
                "delivery_export_target": str(target_root),
                "worktree_retention_days": self.storage_policy.exported_worktree_retention_days,
                "user_export_policy": "never_delete_user_export_targets",
            }
        )
        metadata.update({"delivery_export_path": artifact["path"], "delivery_export_target": str(target_root), "delivery_exported_at": export_time, "storage_lifecycle": lifecycle})
        self.store.update_run(run_id, metadata=metadata)
        self.store.add_event(run["tenant_id"], project["id"], run_id, "storage.export_recorded", {"run_id": run_id, "target_path": str(target_root), "receipt_artifact_id": receipt_artifact["id"], "copied_count": len(copied)})
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
        raise RuntimeError(f"unsupported V6 job type: {job_type}")

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
            user_payload={"project": self._project_prompt(project), "requirements_text": _compact_text(requirements_text, resolve_ai_task_budget("requirements_analysis", self._run_scale_profile(run, project)).max_input_chars)},
            required=True,
        )
        self._record_json(project, run, "requirements_analysis", "requirements-analysis.json", analysis)
        if str(analysis.get("status", "GO")).upper() == "NO_GO":
            continuation = {**dict(run.get("continuation") or {}), "checkpoint": "requirements_completed", "failure_reason": "requirements_need_clarification", "next_action": "clarify_requirements"}
            self.store.update_run(run["id"], status="no_go", checkpoint="requirements_completed", continuation=continuation, metadata={**dict(run.get("metadata") or {}), "requirements_analysis": analysis})
            self._write_manifest(project, run)
            return {"status": "NO_GO", "requirements_analysis": analysis}
        inference = infer_scale_from_requirements(analysis, requirements_text)
        inferred_run, inferred_metadata = self._apply_scale_inference(run, project, inference)
        metadata = {**inferred_metadata, "requirements_analysis": analysis}
        continuation = {**dict(inferred_run.get("continuation") or {}), "checkpoint": "requirements_completed", "next_action": "architecture_design"}
        updated_run = self.store.update_run(run["id"], status="running", checkpoint="requirements_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "architecture_design", "role": "architect", "run_id": run["id"], "resume_key": f"run:{run['id']}:architecture_design", "payload": self._job_payload(updated_run, {}), "max_attempts": self._job_attempts(updated_run, "architecture_design")})
        self._write_manifest(project, updated_run)
        return {"status": "completed", "next": "architecture_design"}

    def _execute_architecture_design(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        if metadata.get("architecture_design") and run.get("checkpoint") == "architecture_completed":
            layout = self._normalize_layout(metadata.get("architecture_design") or {})
            return {"status": "completed", "project_layout": layout}

        architecture_seed = metadata.get("architecture_seed") or self._build_architecture_seed(project, run, metadata)
        seed_artifact = {"id": metadata.get("architecture_seed_artifact_id", ""), "path": metadata.get("architecture_seed_path", "")}
        if not metadata.get("architecture_seed_path"):
            seed_artifact = self._record_json(project, run, "architecture_seed", "architecture-seed.json", architecture_seed, {"stage": "seed"})
            metadata["architecture_seed_path"] = seed_artifact["path"]
            metadata["architecture_seed_artifact_id"] = seed_artifact["id"]
            seed_continuation = {**dict(run.get("continuation") or {}), "architecture_phase": "seed_completed", "next_action": "architecture_design"}
            run = self.store.update_run(run["id"], status="running", continuation=seed_continuation, metadata=metadata)
        metadata["architecture_seed"] = architecture_seed

        surface = dict(metadata.get("architecture_surface") or {})
        if not surface:
            surface = self._invoke_json_agent(
                project,
                run,
                role="architect",
                job=job,
                task_kind="architecture_surface",
                system_prompt=(
                    "You are architecture_surface_agent. Return strict JSON only. "
                    "Produce a concise, project-specific architecture surface. "
                    "Return architecture_summary, technology_choices, design_principles, primary_risks, scale_notes. "
                    "Avoid generic templates and keep lists short and concrete."
                ),
                user_payload=self._architecture_surface_payload(project, run, metadata, architecture_seed),
                required=True,
            )
            surface_artifact = self._record_json(project, run, "architecture_surface", "architecture-surface.json", surface, {"stage": "surface"})
            self.store.add_event(run["tenant_id"], project["id"], run["id"], "architecture.surface_completed", {"stage": "surface", "artifact_id": surface_artifact["id"], "seed_artifact_id": seed_artifact["id"]})
            metadata["architecture_surface"] = surface
            metadata["architecture_surface_path"] = surface_artifact["path"]
            metadata["architecture_surface_artifact_id"] = surface_artifact["id"]
            surface_continuation = {**dict(run.get("continuation") or {}), "architecture_phase": "surface_completed", "next_action": "architecture_design"}
            run = self.store.update_run(run["id"], status="running", continuation=surface_continuation, metadata=metadata)

        structure = dict(metadata.get("architecture_structure") or {})
        if not structure:
            layout_draft = dict(metadata.get("architecture_layout") or {})
            if not layout_draft:
                layout_draft = self._invoke_json_agent(
                    project,
                    run,
                    role="architect",
                    job=job,
                    task_kind="architecture_layout",
                    system_prompt=(
                        "You are architecture_layout_agent. Return strict JSON only. "
                        "Design only the concrete project layout for this project. "
                        "Return project_layout and validation_commands. "
                        "project_layout must contain source_root, delivery_root, entrypoints, directories, validation_commands. "
                        "Keep the answer compact; do not include module contracts here."
                    ),
                    user_payload=self._architecture_layout_payload(project, run, metadata, architecture_seed, surface),
                    required=True,
                )
                layout_artifact = self._record_json(project, run, "architecture_layout", "architecture-layout.json", layout_draft, {"stage": "layout"})
                self.store.add_event(run["tenant_id"], project["id"], run["id"], "architecture.layout_completed", {"stage": "layout", "artifact_id": layout_artifact["id"], "surface_ready": bool(surface)})
                metadata["architecture_layout"] = layout_draft
                metadata["architecture_layout_path"] = layout_artifact["path"]
                metadata["architecture_layout_artifact_id"] = layout_artifact["id"]
                layout_continuation = {**dict(run.get("continuation") or {}), "architecture_phase": "layout_completed", "next_action": "architecture_design"}
                run = self.store.update_run(run["id"], status="running", continuation=layout_continuation, metadata=metadata)

            contracts_draft = dict(metadata.get("architecture_contracts") or {})
            if not contracts_draft:
                contracts_draft = self._invoke_json_agent(
                    project,
                    run,
                    role="architect",
                    job=job,
                    task_kind="architecture_contracts",
                    system_prompt=(
                        "You are architecture_contracts_agent. Return strict JSON only. "
                        "Using the architecture surface and layout, define only module_boundaries, integration_contracts, and implementation_notes. "
                        "Keep the answer compact and project-specific. Do not repeat directory layout."
                    ),
                    user_payload=self._architecture_contracts_payload(project, run, metadata, architecture_seed, surface, layout_draft),
                    required=True,
                )
                contracts_artifact = self._record_json(project, run, "architecture_contracts", "architecture-contracts.json", contracts_draft, {"stage": "contracts"})
                self.store.add_event(run["tenant_id"], project["id"], run["id"], "architecture.contracts_completed", {"stage": "contracts", "artifact_id": contracts_artifact["id"], "layout_ready": bool(layout_draft)})
                metadata["architecture_contracts"] = contracts_draft
                metadata["architecture_contracts_path"] = contracts_artifact["path"]
                metadata["architecture_contracts_artifact_id"] = contracts_artifact["id"]
                contracts_continuation = {**dict(run.get("continuation") or {}), "architecture_phase": "contracts_completed", "next_action": "architecture_design"}
                run = self.store.update_run(run["id"], status="running", continuation=contracts_continuation, metadata=metadata)

            structure = self._merge_architecture_structure(layout_draft, contracts_draft)
            structure_artifact = self._record_json(project, run, "architecture_structure", "architecture-structure.json", structure, {"stage": "structure_merged"})
            self.store.add_event(run["tenant_id"], project["id"], run["id"], "architecture.structure_completed", {"stage": "structure_merged", "artifact_id": structure_artifact["id"], "surface_ready": bool(surface), "layout_ready": bool(layout_draft), "contracts_ready": bool(contracts_draft)})
            metadata["architecture_structure"] = structure
            metadata["architecture_structure_path"] = structure_artifact["path"]
            metadata["architecture_structure_artifact_id"] = structure_artifact["id"]

        design = self._merge_architecture_design(architecture_seed, surface, structure)
        layout = self._normalize_layout(design)
        project_root = self.runtime.project_root(project)
        self.runtime.layout_roots(project_root, layout)
        design["project_layout"] = layout
        self._record_json(project, run, "architecture_design", "architecture-design.json", design)
        self._record_json(project, run, "project_layout", "project-layout.json", layout)
        inference = infer_scale_from_architecture(design, layout)
        inferred_run, inferred_metadata = self._apply_scale_inference(run, project, inference)
        metadata = {
            **inferred_metadata,
            "architecture_seed": architecture_seed,
            "architecture_seed_path": seed_artifact["path"],
            "architecture_seed_artifact_id": metadata.get("architecture_seed_artifact_id", seed_artifact["id"]),
            "architecture_surface": surface,
            "architecture_surface_path": metadata.get("architecture_surface_path", ""),
            "architecture_surface_artifact_id": metadata.get("architecture_surface_artifact_id", ""),
            "architecture_layout": metadata.get("architecture_layout", {}),
            "architecture_layout_path": metadata.get("architecture_layout_path", ""),
            "architecture_layout_artifact_id": metadata.get("architecture_layout_artifact_id", ""),
            "architecture_contracts": metadata.get("architecture_contracts", {}),
            "architecture_contracts_path": metadata.get("architecture_contracts_path", ""),
            "architecture_contracts_artifact_id": metadata.get("architecture_contracts_artifact_id", ""),
            "architecture_structure": structure,
            "architecture_structure_path": metadata.get("architecture_structure_path", ""),
            "architecture_structure_artifact_id": metadata.get("architecture_structure_artifact_id", ""),
            "architecture_design": design,
            "project_layout": layout,
            "project_root": str(project_root),
            "architecture_phase": "completed",
        }
        continuation = {**dict(inferred_run.get("continuation") or {}), "checkpoint": "architecture_completed", "next_action": "package_planning", "architecture_phase": "completed"}
        updated_run = self.store.update_run(run["id"], status="running", checkpoint="architecture_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "package_planning", "role": "planner", "run_id": run["id"], "resume_key": f"run:{run['id']}:package_planning", "payload": self._job_payload(updated_run, {}), "max_attempts": self._job_attempts(updated_run, "package_planning")})
        self._write_manifest(project, updated_run)
        return {"status": "completed", "project_layout": layout}

    def _execute_package_planning(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        metadata = dict(run.get("metadata") or {})
        if metadata.get("package_dag") and run.get("checkpoint") == "package_planning_completed":
            return {"status": "completed", "package_count": len((metadata.get("package_dag") or {}).get("packages") or [])}

        layout = metadata.get("project_layout") or (metadata.get("architecture_design") or {}).get("project_layout") or {}
        seed_is_current = isinstance(metadata.get("package_planning_seed"), dict) and metadata["package_planning_seed"].get("seed_profile") == "compact_scope_current"
        planning_seed = metadata.get("package_planning_seed") if seed_is_current else self._build_package_planning_seed(project, run, metadata)
        seed_artifact = {"id": metadata.get("package_planning_seed_artifact_id", ""), "path": metadata.get("package_planning_seed_path", "")}
        if not seed_is_current or not metadata.get("package_planning_seed_path"):
            seed_artifact = self._record_json(project, run, "package_planning_seed", "package-planning-seed.json", planning_seed, {"stage": "seed"})
            metadata["package_planning_seed"] = planning_seed
            metadata["package_planning_seed_path"] = seed_artifact["path"]
            metadata["package_planning_seed_artifact_id"] = seed_artifact["id"]
            seed_continuation = {**dict(run.get("continuation") or {}), "package_planning_phase": "seed_completed", "next_action": "package_planning"}
            run = self.store.update_run(run["id"], status="running", continuation=seed_continuation, metadata=metadata)

        scope_plan = dict(metadata.get("package_scope_plan") or {})
        if not scope_plan:
            scope_plan = self._invoke_json_agent(
                project,
                run,
                role="planner",
                job=job,
                task_kind="package_scope_planning",
                system_prompt=(
                    "You are package_scope_planning_agent. Return strict JSON only. "
                    "Choose project-specific AI work packages only; do not assign waves or dependencies. "
                    "Return packages with package_key, role, domain, subsystem, allowed_paths, forbidden_paths, objective, requirements_mapping, expected_outputs, acceptance_gates. "
                    "Keep the package count appropriate to the actual project, not to a template."
                ),
                user_payload=self._package_scope_payload(project, run, metadata, planning_seed),
                required=True,
            )
            scope_artifact = self._record_json(project, run, "package_scope_plan", "package-scope-plan.json", scope_plan, {"stage": "scope"})
            self.store.add_event(run["tenant_id"], project["id"], run["id"], "package_planning.scope_completed", {"stage": "scope", "artifact_id": scope_artifact["id"], "package_count": len(scope_plan.get("packages") or [])})
            metadata["package_scope_plan"] = scope_plan
            metadata["package_scope_plan_path"] = scope_artifact["path"]
            metadata["package_scope_plan_artifact_id"] = scope_artifact["id"]
            scope_continuation = {**dict(run.get("continuation") or {}), "package_planning_phase": "scope_completed", "next_action": "package_planning"}
            run = self.store.update_run(run["id"], status="running", continuation=scope_continuation, metadata=metadata)

        wave_plan = dict(metadata.get("package_wave_plan") or {})
        if not wave_plan:
            wave_plan = self._invoke_json_agent(
                project,
                run,
                role="planner",
                job=job,
                task_kind="package_wave_planning",
                system_prompt=(
                    "You are package_wave_planning_agent. Return strict JSON only. "
                    "Assign the provided package candidates to execution waves and dependency edges. "
                    "Return waves and assignments only. Do not repeat requirements or package descriptions."
                ),
                user_payload=self._package_wave_payload(project, run, metadata, planning_seed, scope_plan),
                required=True,
            )
            wave_artifact = self._record_json(project, run, "package_wave_plan", "package-wave-plan.json", wave_plan, {"stage": "waves"})
            self.store.add_event(run["tenant_id"], project["id"], run["id"], "package_planning.waves_completed", {"stage": "waves", "artifact_id": wave_artifact["id"], "wave_count": len(wave_plan.get("waves") or [])})
            metadata["package_wave_plan"] = wave_plan
            metadata["package_wave_plan_path"] = wave_artifact["path"]
            metadata["package_wave_plan_artifact_id"] = wave_artifact["id"]
            wave_continuation = {**dict(run.get("continuation") or {}), "package_planning_phase": "waves_completed", "next_action": "package_planning"}
            run = self.store.update_run(run["id"], status="running", continuation=wave_continuation, metadata=metadata)

        plan = self._merge_package_plan(scope_plan, wave_plan, layout)
        try:
            plan = self._normalize_package_plan(plan, layout)
        except AgentContractViolationError as exc:
            self.store.update_run(run["id"], status="blocked", continuation={**dict(run.get("continuation") or {}), "failure_reason": "agent_contract_violation", "next_action": "repair_agent_contract", "contract_error": str(exc)[:2000]})
            raise
        inference = infer_scale_from_package_plan(plan)
        inferred_run, inferred_metadata = self._apply_scale_inference(run, project, inference)
        metadata = {**metadata, **inferred_metadata}
        run = inferred_run
        self._record_json(project, run, "package_dag", "package-dag.json", plan)
        wave_id_by_key = {}
        for wave in plan["waves"]:
            stored_wave = self.store.upsert_wave(run["id"], wave)
            wave_id_by_key[wave["wave_key"]] = stored_wave["id"]
        for package in plan["packages"]:
            self.store.upsert_work_package(run["id"], wave_id_by_key[package["wave_key"]], package)
        code_index, contract_index = self._refresh_indexes(project, {**run, "metadata": {**metadata, "package_dag": plan}})
        context_snapshot = build_context_snapshot(requirements=metadata.get("requirements_analysis", {}), architecture=metadata.get("architecture_design", {}), package_plan=plan, project_root=self.runtime.project_root(project), code_index=code_index, contract_index=contract_index)
        self._record_json(project, run, "context_snapshot", "context-snapshot.json", context_snapshot)
        mission_memory = build_layered_memory(
            project=project,
            requirements=metadata.get("requirements_analysis", {}),
            architecture=metadata.get("architecture_design", {}),
            package_plan=plan,
            context_snapshot=context_snapshot,
            code_index=code_index,
            contract_index=contract_index,
            change_artifacts=self.store.list_artifacts(run["id"]),
        )
        self._record_json(project, run, "mission_memory", "mission-memory.json", mission_memory)
        metadata.update(
            {
                "package_planning_seed": planning_seed,
                "package_planning_seed_path": metadata.get("package_planning_seed_path", seed_artifact["path"]),
                "package_planning_seed_artifact_id": metadata.get("package_planning_seed_artifact_id", seed_artifact["id"]),
                "package_scope_plan": scope_plan,
                "package_scope_plan_path": metadata.get("package_scope_plan_path", ""),
                "package_scope_plan_artifact_id": metadata.get("package_scope_plan_artifact_id", ""),
                "package_wave_plan": wave_plan,
                "package_wave_plan_path": metadata.get("package_wave_plan_path", ""),
                "package_wave_plan_artifact_id": metadata.get("package_wave_plan_artifact_id", ""),
                "package_dag": plan,
                "context_snapshot": context_snapshot,
                "context_snapshot_id": context_snapshot["index_hash"],
                "mission_memory": mission_memory,
                "mission_memory_hash": mission_memory["memory_hash"],
                "code_index": code_index,
                "contract_index": contract_index,
                "code_index_hash": code_index.get("index_hash", ""),
                "contract_index_hash": contract_index.get("index_hash", ""),
            }
        )
        first_wave = min(plan["waves"], key=lambda item: item["sequence"])
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "package_planning_completed", "current_wave": first_wave["wave_key"], "pending_packages": [package["package_key"] for package in plan["packages"]], "package_planning_phase": "completed", "next_action": "worker_claim_package_jobs"}
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
            self.store.enqueue_job(run["tenant_id"], {"job_type": "integration", "role": "integration", "run_id": run["id"], "resume_key": f"run:{run['id']}:integration:conflict:{package['id']}:{new_id()}", "payload": self._job_payload(run, {"conflicts": apply_result.get("conflicts", [])}), "max_attempts": self._job_attempts(run, "integration")})
            raise RuntimeError(f"agent patch conflict: {apply_result.get('conflicts')}")
        patch_set = self._record_json(
            project,
            run,
            "patch_set",
            f"{package['package_key']}.json",
            {
                "schema_version": "6.0",
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
                    "schema_version": "6.0",
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
                    "schema_version": "6.0",
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
            "schema_version": "6.0",
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
            apply_result = self.patch_runtime.apply_file_manifest_transaction(run_id=run["id"], project=project, layout=metadata.get("project_layout", {}), agent_output=output, allowed_paths=["**"], forbidden_paths=[".git/**", ".v6/**"], replace_conflicts=True, conflict_sources=[artifact.get("payload", {}) for artifact in self.store.list_artifacts(run["id"]) if artifact["kind"] == "integration_conflict"])
            changed_files = apply_result.get("changed_files", [])
            self._record_json(project, run, "patch_set", "integration.json", {"schema_version": "6.0", "role": "integration", "agent_output": output, **apply_result}, {"role": "integration"})
            self._record_json(project, run, "patch_transaction", f"{apply_result['transaction_id']}.json", apply_result, {"role": "integration"})
            self._write_patch_transaction_report(project, run)
        report = {"schema_version": "6.0", "ok": True, "status": "passed", "changed_files": changed_files, "summary": output.get("summary", "")}
        self._record_json(project, run, "integration_report", "integration-report.json", report)
        code_index, contract_index = self._refresh_indexes(project, run)
        metadata = dict((self._require_run(run["id"]).get("metadata") or metadata))
        context_snapshot = build_context_snapshot(requirements=metadata.get("requirements_analysis", {}), architecture=metadata.get("architecture_design", {}), package_plan=metadata.get("package_dag", {}), project_root=self.runtime.project_root(project), code_index=code_index, contract_index=contract_index)
        metadata.update({"context_snapshot": context_snapshot, "context_snapshot_id": context_snapshot["index_hash"]})
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "integration_completed", "integration_status": "passed", "next_action": "code_review"}
        self.store.update_run(run["id"], status="running", checkpoint="integration_completed", continuation=continuation, metadata=metadata)
        self.store.enqueue_job(run["tenant_id"], {"job_type": "code_review", "role": "review", "run_id": run["id"], "resume_key": f"run:{run['id']}:code_review", "payload": self._job_payload(run, {}), "max_attempts": self._job_attempts(run, "code_review")})
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
            self.store.enqueue_job(run["tenant_id"], {"job_type": "quality", "role": "qa", "run_id": run["id"], "resume_key": f"run:{run['id']}:quality", "payload": self._job_payload(run, {}), "max_attempts": self._job_attempts(run, "quality")})
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
        context_snapshot = build_context_snapshot(requirements=metadata.get("requirements_analysis", {}), architecture=metadata.get("architecture_design", {}), package_plan=metadata.get("package_dag", {}), project_root=self.runtime.project_root(project), code_index=code_index, contract_index=contract_index)
        mission_memory = build_layered_memory(
            project=project,
            requirements=metadata.get("requirements_analysis", {}),
            architecture=metadata.get("architecture_design", {}),
            package_plan=metadata.get("package_dag", {}),
            context_snapshot=context_snapshot,
            code_index=code_index,
            contract_index=contract_index,
            change_artifacts=artifacts,
        )
        self._record_json(project, run, "mission_memory", "mission-memory-quality.json", mission_memory)
        metadata.update({"context_snapshot": context_snapshot, "context_snapshot_id": context_snapshot["index_hash"], "mission_memory": mission_memory, "mission_memory_hash": mission_memory["memory_hash"]})
        run_context = {
            "run_id": run["id"],
            "checkpoint": run.get("checkpoint", ""),
            "continuation": self._live_continuation(self._require_run(run["id"])),
            "scale_profile": self._run_scale_profile(run, project),
            "live_ai_required": self._run_scale_profile(run, project).get("name") in {"large", "xlarge_100k"} and self.ai_scheduler._is_live_provider(),
            "architecture": metadata.get("architecture_design", {}),
            "agent_runs": [artifact for artifact in artifacts if artifact["kind"] == "agent_run"],
            "patch_sets": [artifact for artifact in artifacts if artifact["kind"] == "patch_set"],
            "packages": self.store.list_work_packages(run["id"]),
            "waves": self.store.list_waves(run["id"]),
            "jobs": self.store.list_jobs(run_id=run["id"]),
            "test_reports": [artifact for artifact in artifacts if artifact["kind"] == "test_report"],
            "test_execution_reports": [artifact for artifact in artifacts if artifact["kind"] == "test_execution_report"],
            "test_execution_report": test_execution_report,
            "code_reviews": [artifact for artifact in artifacts if artifact["kind"] == "code_review_report"],
            "release_notes": [artifact for artifact in artifacts if artifact["kind"] == "release_notes"],
            "agent_contract_reports": [artifact for artifact in artifacts if artifact["kind"] == "agent_contract_report"],
            "patch_transactions": [artifact for artifact in artifacts if artifact["kind"] == "patch_transaction"],
            "code_index": code_index,
            "contract_index": contract_index,
            "mission_memory": mission_memory,
            "ai_payload_budget": self._latest_payload_or_empty(artifacts, "ai_payload_budget"),
            "replay_projection": self.replay_projection(run["id"]),
            "checkpoint_resume": self.checkpoint_resume(run["id"]),
            "recovery_trace": self.recovery_trace(run["id"]),
            "provider_health": self.provider_health(),
            "ai_slots": self.ai_slot_snapshot(run["id"]),
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
            self.store.enqueue_job(run["tenant_id"], {"job_type": "release_notes", "role": "release", "run_id": run["id"], "resume_key": f"run:{run['id']}:release_notes", "payload": self._job_payload(run, {}), "max_attempts": self._job_attempts(run, "release_notes")})
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
        self.store.enqueue_job(run["tenant_id"], {"job_type": "release_candidate", "role": "release", "run_id": run["id"], "resume_key": f"run:{run['id']}:release_candidate", "payload": self._job_payload(run, {}), "max_attempts": self._job_attempts(run, "release_candidate")})
        self._write_manifest(project, run)
        return output

    def _execute_release_candidate(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        quality = self._latest_artifact_payload(run["id"], "quality_report")
        candidate = {"schema_version": "6.0", "candidate_id": new_id(), "run_id": run["id"], "status": "GO" if quality.get("ok") else "NO_GO", "project_root": str(self.runtime.project_root(project)), "delivery_root": ((run.get("metadata") or {}).get("project_layout") or {}).get("delivery_root", ""), "hard_gates_passed": bool(quality.get("ok"))}
        artifact = self._record_json(project, run, "release_candidate", f"release-candidate-{candidate['candidate_id']}.json", candidate)
        metadata = {**dict(run.get("metadata") or {}), "release_candidate_id": artifact["id"]}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "release_candidate_completed", "release_status": candidate["status"], "next_action": "apply" if quality.get("ok") else "repair_quality"}
        self.store.update_run(run["id"], status="release_ready" if quality.get("ok") else "no_go", checkpoint="release_candidate_completed", continuation=continuation, metadata=metadata)
        self._write_manifest(project, run)
        return candidate

    def _execute_apply(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        rollback_manifest = {"schema_version": "6.0", "run_id": run["id"], "candidate_id": (job.get("payload") or {}).get("candidate_id"), "status": "recorded", "project_root": str(self.runtime.project_root(project))}
        artifact = self._record_json(project, run, "rollback_manifest", "rollback-manifest.json", rollback_manifest)
        metadata = {**dict(run.get("metadata") or {}), "rollback_manifest_path": artifact["path"]}
        continuation = {**dict(run.get("continuation") or {}), "checkpoint": "apply_completed", "release_status": "applied", "next_action": "delivery_complete"}
        self.store.update_run(run["id"], status="completed", checkpoint="apply_completed", continuation=continuation, metadata=metadata)
        self._write_manifest(project, run)
        return {"status": "applied", "rollback_manifest_path": artifact["path"]}

    def _execute_rollback(self, job: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(job["run_id"])
        project = self._require_project(run["project_id"])
        report = {"schema_version": "6.0", "run_id": run["id"], "candidate_id": (job.get("payload") or {}).get("candidate_id"), "status": "rolled_back"}
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
            report = {"schema_version": "6.0", "ok": False, "status": "NO_GO", "repair_attempt": attempt, "next_action": "human_review", "failure_reason": payload.get("failure_reason", "repair_required")}
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
            apply_result = self.patch_runtime.apply_file_manifest_transaction(run_id=run["id"], project=project, layout=((run.get("metadata") or {}).get("project_layout") or {}), agent_output=output, allowed_paths=["**"], forbidden_paths=[".git/**", ".v6/**"], replace_conflicts=True)
            changed_files = apply_result.get("changed_files", [])
            self._record_json(project, run, "patch_set", f"repair-{attempt}.json", {"schema_version": "6.0", "role": "repair", "agent_output": output, **apply_result}, {"role": "repair", "repair_attempt": attempt})
            self._record_json(project, run, "patch_transaction", f"{apply_result['transaction_id']}.json", apply_result, {"role": "repair", "repair_attempt": attempt})
            self._write_patch_transaction_report(project, run)
        report = {"schema_version": "6.0", "ok": True, "status": "repair_applied", "repair_attempt": attempt, "changed_files": changed_files, "next_action": "code_review"}
        self._record_json(project, run, "repair_report", f"repair-report-{attempt}.json", report)
        self.store.update_run(run["id"], status="running", continuation={**dict(run.get("continuation") or {}), "repair_attempt": attempt, "next_action": "code_review"})
        self.store.enqueue_job(run["tenant_id"], {"job_type": "code_review", "role": "review", "run_id": run["id"], "resume_key": f"run:{run['id']}:code_review:repair:{attempt}", "payload": self._job_payload(run, {}), "max_attempts": self._job_attempts(run, "code_review")})
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
            role = self._normalize_package_role(str(package.get("role") or payload.get("role") or ""), str(payload.get("domain") or package.get("domain") or ""), str(payload.get("subsystem") or package.get("domain") or ""), str(payload.get("objective") or ""))
            if role and role != package.get("role"):
                updated_payload = dict(payload)
                updated_payload["role"] = role
                if package.get("role"):
                    updated_payload["source_role"] = package.get("role")
                self.store.update_work_package(package["id"], role=role, payload=updated_payload)
                package = self.store.get_work_package(package["id"]) or package
                payload = package.get("payload") or updated_payload
            depends_on = payload.get("depends_on") or []
            if any(dep not in completed_keys for dep in depends_on):
                continue
            job_type = self._job_type_for_package(payload)
            self.store.enqueue_job(
                run["tenant_id"],
                {
                    "job_type": job_type,
                    "role": role or package["role"],
                    "run_id": run["id"],
                    "work_package_id": package["id"],
                    "wave_id": package["wave_id"],
                    "resume_key": f"run:{run['id']}:package:{package['id']}:{job_type}",
                    "payload": self._job_payload(run, {"package_key": package["package_key"], "project_id": project["id"], "subsystem": payload.get("subsystem", package["domain"]), "depends_on": depends_on, "allowed_paths": payload.get("allowed_paths", []), "forbidden_paths": payload.get("forbidden_paths", [])}),
                    "max_attempts": self._job_attempts(run, job_type),
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
        self.store.enqueue_job(run["tenant_id"], {"job_type": "integration", "role": "integration", "run_id": run["id"], "resume_key": f"run:{run['id']}:integration", "payload": self._job_payload(run, {}), "max_attempts": self._job_attempts(run, "integration")})
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
                "payload": self._job_payload(run, {"failure_reason": reason, "failed_gate_report": failed_gate_report, "repair_attempt": attempt}),
                "max_attempts": self._job_attempts(run, "repair"),
            },
        )

    def _prepare_ai_payload(
        self,
        project: dict[str, Any],
        run: dict[str, Any],
        *,
        role: str,
        job: dict[str, Any],
        task_kind: str,
        system_prompt: str,
        user_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        profile = self._run_scale_profile(run, project)
        budget = resolve_ai_task_budget(task_kind, profile)
        body_limit = int(getattr(getattr(self.llm_client, "config", None), "max_request_body_bytes", 0) or ai_policy_payload_limits()["provider_body_limit_bytes"])
        report = payload_budget_report(
            system_prompt=system_prompt,
            user_payload=user_payload,
            task_budget=budget,
            scale_profile=profile,
            provider_body_limit_bytes=body_limit,
        )
        if report["ok"]:
            self._record_json(project, run, "ai_payload_budget", f"{job.get('id', new_id())}-{task_kind}-budget.json", report, {"role": role, "job_id": job.get("id", ""), "task_kind": task_kind})
            return user_payload, report

        trimmed_payload, trim_report = compact_payload_for_budget(user_payload, target_chars=int(report["budget_limit"] * 0.75))
        trimmed_report = payload_budget_report(
            system_prompt=system_prompt,
            user_payload=trimmed_payload,
            task_budget=budget,
            scale_profile=profile,
            provider_body_limit_bytes=body_limit,
            trim_report=trim_report,
        )
        if trimmed_report["ok"]:
            trimmed_report["budget_status"] = "trimmed_within_budget"
            self.store.add_event(run["tenant_id"], project["id"], run["id"], "ai_context_trimmed", {"job_id": job.get("id", ""), "task_kind": task_kind, "budget": trimmed_report})
            self._record_json(project, run, "ai_payload_budget", f"{job.get('id', new_id())}-{task_kind}-budget.json", trimmed_report, {"role": role, "job_id": job.get("id", ""), "task_kind": task_kind})
            return trimmed_payload, trimmed_report

        if self.ai_scheduler.llm_client is not None and task_kind != "context_summary":
            summary_payload = {
                "task_kind": task_kind,
                "role": role,
                "payload_outline": payload_outline(user_payload),
                "trim_report": trim_report,
                "trimmed_payload": trimmed_payload,
                "instruction": "Summarize this oversized agent context into compact JSON preserving project-specific facts, contracts, file paths, package objectives, risks, and acceptance criteria.",
            }
            summary_budget = get_ai_task_budget("context_summary")
            summary_report = payload_budget_report(
                system_prompt="You are context_summary_agent. Return strict JSON only with summary, retained_facts, dropped_context, risks, and resume_instructions.",
                user_payload=summary_payload,
                task_budget=summary_budget,
                scale_profile=profile,
                provider_body_limit_bytes=body_limit,
            )
            if not summary_report["ok"]:
                summary_payload, summary_trim = compact_payload_for_budget(summary_payload, target_chars=int(summary_report["budget_limit"] * 0.75))
                summary_report = payload_budget_report(
                    system_prompt="You are context_summary_agent. Return strict JSON only with summary, retained_facts, dropped_context, risks, and resume_instructions.",
                    user_payload=summary_payload,
                    task_budget=summary_budget,
                    scale_profile=profile,
                    provider_body_limit_bytes=body_limit,
                    trim_report=summary_trim,
                )
            if summary_report["ok"]:
                summary_call = self.ai_scheduler.call(
                    run_id=run["id"],
                    role="context",
                    job={**job, "job_type": "context_summary", "payload": self._job_payload(run, {})},
                    system_prompt="You are context_summary_agent. Return strict JSON only with summary, retained_facts, dropped_context, risks, and resume_instructions.",
                    user_payload=summary_payload,
                    task_kind="context_summary",
                    required=True,
                    payload_guard=summary_report,
                    store=self.store,
                    tenant_id=run["tenant_id"],
                )
                summary_call["parsed_response"] = _json_or_empty(summary_call.get("raw_response", {}))
                self._record_json(project, run, "agent_run", f"{summary_call['agent_run_id']}.json", summary_call, {"role": "context", "job_id": job.get("id", ""), "task_kind": "context_summary"})
                if summary_call.get("ok") and summary_call.get("parsed_response"):
                    compressed_payload = {
                        "project": self._project_prompt(project),
                        "compressed_context": summary_call["parsed_response"],
                        "original_payload_budget": report,
                        "trim_report": trim_report,
                        "resume_key": f"run:{run['id']}:{job.get('id', '')}:{task_kind}:compressed",
                    }
                    compressed_report = payload_budget_report(
                        system_prompt=system_prompt,
                        user_payload=compressed_payload,
                        task_budget=budget,
                        scale_profile=profile,
                        provider_body_limit_bytes=body_limit,
                        compression_applied=True,
                        summary_agent_run_id=summary_call["agent_run_id"],
                        trim_report=trim_report,
                    )
                    if compressed_report["ok"]:
                        compressed_report["budget_status"] = "ai_compressed_within_budget"
                        self.store.add_event(run["tenant_id"], project["id"], run["id"], "ai_context_compressed", {"job_id": job.get("id", ""), "task_kind": task_kind, "summary_agent_run_id": summary_call["agent_run_id"], "budget": compressed_report})
                        self._record_json(project, run, "ai_payload_budget", f"{job.get('id', new_id())}-{task_kind}-budget.json", compressed_report, {"role": role, "job_id": job.get("id", ""), "task_kind": task_kind})
                        return compressed_payload, compressed_report

        blocked_report = {**trimmed_report, "ok": False, "budget_status": "blocked_oversize_after_trim_and_summary"}
        continuation = {
            **dict(run.get("continuation") or {}),
            "failure_reason": "ai_payload_oversize_blocked",
            "next_action": "recover_with_compressed_context",
            "recovery_state": "recovering",
            "ai_payload_budget": blocked_report,
        }
        self.store.add_event(run["tenant_id"], project["id"], run["id"], "ai_payload_oversize_blocked", {"job_id": job.get("id", ""), "task_kind": task_kind, "budget": blocked_report})
        self._record_json(project, run, "ai_payload_budget", f"{job.get('id', new_id())}-{task_kind}-budget.json", blocked_report, {"role": role, "job_id": job.get("id", ""), "task_kind": task_kind})
        self.store.update_run(run["id"], status="recovering", continuation=continuation)
        raise ProviderCallError("AI payload exceeds provider/body budget after trim and AI summary", retryable=True)

    def _invoke_json_agent(self, project: dict[str, Any], run: dict[str, Any], *, role: str, job: dict[str, Any], system_prompt: str, user_payload: dict[str, Any], required: bool, task_kind: str) -> dict[str, Any]:
        retry_payload = dict(user_payload)
        retry_prompt = system_prompt
        last_error = "agent contract validation failed"
        for attempt in (1, 2):
            prepared_payload, payload_guard = self._prepare_ai_payload(project, run, role=role, job=job, task_kind=task_kind, system_prompt=retry_prompt, user_payload=retry_payload)
            payload = self.ai_scheduler.call(
                run_id=run["id"],
                role=role,
                job=job,
                system_prompt=retry_prompt,
                user_payload=prepared_payload,
                task_kind=task_kind,
                required=required,
                payload_guard=payload_guard,
                store=self.store,
                tenant_id=run["tenant_id"],
            )
            payload["parsed_response"] = _json_or_empty(payload.get("raw_response", {}))
            payload["contract_attempt"] = attempt
            if not payload.get("ok"):
                self._record_json(project, run, "agent_run", f"{payload['agent_run_id']}.json", payload, {"role": role, "job_id": job.get("id", ""), "task_kind": payload.get("task_kind", task_kind)})
                remaining_attempts = max(0, int(job.get("max_attempts") or 1) - int(job.get("attempts") or 0))
                retryable = bool(payload.get("retryable")) and remaining_attempts > 0
                recovery_state = "recovering" if retryable else "blocked"
                next_action = "retry_ai_call" if retryable else "repair_llm_connection"
                continuation = {
                    **dict(run.get("continuation") or {}),
                    "failure_reason": "llm_call_failed",
                    "next_action": next_action,
                    "recovery_state": recovery_state,
                    "retryable": bool(payload.get("retryable")),
                    "error_kind": payload.get("error_kind", ""),
                    "provider_health": payload.get("provider_health", {}),
                    "retry_after_seconds": int(payload.get("retry_after_seconds") or 0),
                }
                self.store.add_event(run["tenant_id"], project["id"], run["id"], "llm_call_failed", {"agent_run_id": payload["agent_run_id"], "role": role, "job_id": job.get("id", ""), "task_kind": payload.get("task_kind", task_kind), "error": str(payload.get("error", ""))[:1000], "error_kind": payload.get("error_kind", ""), "retryable": bool(payload.get("retryable")), "provider_health": payload.get("provider_health", {})})
                if required:
                    self.store.update_run(run["id"], status=recovery_state, continuation=continuation)
                    raise ProviderCallError(str(payload.get("error", "llm call failed")), retryable=retryable)
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
            "schema_version": "6.0",
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
            "schema_version": "6.0",
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
        scale_profile = self._run_scale_profile(run, self._require_project(run["project_id"]))
        continuation.update(
            {
                "schema_version": "6.0",
                "run_id": run["id"],
                "kernel_generation": scale_profile.get("kernel_generation", "100k_ai_native"),
                "mission_contract_version": scale_profile.get("mission_contract_version", "6.1"),
                "scale_profile": scale_profile,
                "checkpoint": run.get("checkpoint", continuation.get("checkpoint", "run_created")),
                "current_wave": self._current_wave(waves),
                "completed_waves": [wave["wave_key"] for wave in waves if wave.get("status") == "completed"],
                "completed_packages": [package["package_key"] for package in packages if package.get("status") == "completed"],
                "pending_packages": [package["package_key"] for package in packages if package.get("status") not in {"completed", "cancelled"}],
                "leased_jobs": [{"id": job["id"], "role": job["role"], "job_type": job["job_type"], "worker_id": job.get("worker_id", ""), "lease_until": job.get("lease_until")} for job in jobs if job.get("status") == "running"],
                "context_index_status": self._context_index_status(run),
                "artifact_preflight": self._artifact_preflight(run["id"]),
                "provider_health": self.provider_health(),
                "dead_letter_jobs": [{"id": job["id"], "job_type": job["job_type"], "role": job["role"], "last_error": str(job.get("last_error", ""))[:300]} for job in jobs if job.get("status") == "dead_letter"],
            }
        )
        continuation["next_action"] = self._next_action(run, jobs, continuation["artifact_preflight"])
        return continuation

    def _next_action(self, run: dict[str, Any], jobs: list[dict[str, Any]], preflight: dict[str, Any]) -> str:
        if run.get("status") in {"completed", "cancelled", "release_ready", "no_go", "blocked"}:
            return (run.get("continuation") or {}).get("next_action", run.get("status"))
        if run.get("status") == "recovering":
            if any(job for job in jobs if job["status"] in {"queued", "retry", "leased", "running"}):
                return "retry_ai_call"
            return (run.get("continuation") or {}).get("next_action", "recover_from_provider_failure")
        if not preflight.get("ok", True):
            return "repair_missing_artifacts"
        queued = [job for job in jobs if job["status"] in {"queued", "retry", "leased", "running"}]
        if any(job for job in queued if job.get("status") in {"leased", "running"}):
            return "job_running"
        if queued:
            return "claim_pending_jobs"
        return (run.get("continuation") or {}).get("next_action", "idle")

    def _context_for_package(self, run: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
        metadata = run.get("metadata") or {}
        context = package_context((metadata.get("context_snapshot") or {}), package)
        mission_memory = metadata.get("mission_memory") or {}
        if mission_memory:
            context["mission_memory"] = memory_for_package(mission_memory, package)
        return context

    def _context_index_status(self, run: dict[str, Any]) -> dict[str, Any]:
        checkpoint = run.get("checkpoint", "")
        if checkpoint in {"run_created", "requirements_completed", "architecture_completed"}:
            return {"ok": True, "missing": [], "schema_version": "6.0", "not_required_yet": True}
        return validate_context_snapshot((run.get("metadata") or {}).get("context_snapshot"))

    def _record_wave_report(self, project: dict[str, Any], run: dict[str, Any], wave_key: str, packages: list[dict[str, Any]]) -> dict[str, Any]:
        report = {"schema_version": "6.0", "run_id": run["id"], "wave_key": wave_key, "status": "passed" if all(package.get("status") == "completed" for package in packages) else "failed", "completed_packages": [package["package_key"] for package in packages if package.get("status") == "completed"], "package_count": len(packages), "risks": []}
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
            "schema_version": "6.0",
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

    def _build_architecture_seed(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        requirements = dict(metadata.get("requirements_analysis") or {})
        project_config = dict(project.get("config") or {})
        profile = self._run_scale_profile(run, project)
        scale_inference = dict(metadata.get("scale_inference") or {})
        return {
            "schema_version": "6.0",
            "phase": "architecture_seed",
            "project": {
                "id": project.get("id", ""),
                "name": project.get("name", ""),
                "title": project.get("title", ""),
                "project_path": project.get("project_path", ""),
            },
            "project_config": {
                "target_scale": project_config.get("target_scale", ""),
                "stack_pack": project_config.get("stack_pack", ""),
                "deployment_mode": project_config.get("deployment_mode", ""),
                "api_only": bool(project_config.get("api_only", False)),
                "effective_loc_target": int(project_config.get("effective_loc_target") or 0),
                "unattended_mode": project_config.get("unattended_mode", ""),
            },
            "scale_profile": {
                "name": profile.get("name", ""),
                "kernel_generation": profile.get("kernel_generation", ""),
                "mission_contract_version": profile.get("mission_contract_version", ""),
                "recursive_decomposition_depth": profile.get("recursive_decomposition_depth", 0),
                "wave_parallelism": profile.get("wave_parallelism", 0),
                "context_budget_chars": profile.get("context_budget_chars", 0),
                "package_loc_target": profile.get("package_loc_target", 0),
                "ai_retry_attempts": profile.get("ai_retry_attempts", 0),
            },
            "scale_inference": {
                "selected_scale": scale_inference.get("selected_scale", profile.get("name", "")),
                "confidence": scale_inference.get("confidence", ""),
                "reasons": list(scale_inference.get("reasons") or [])[:6],
            },
            "requirements": self._compact_architecture_requirements(requirements),
            "focus_tags": self._architecture_focus_tags(requirements, project_config),
        }

    def _architecture_surface_payload(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], architecture_seed: dict[str, Any]) -> dict[str, Any]:
        return {
            "architecture_seed": architecture_seed,
            "requirements": architecture_seed.get("requirements", {}),
            "instructions": "Return a concise architecture surface for this project. Keep every list short, concrete, and specific to the project context.",
        }

    def _architecture_layout_payload(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], architecture_seed: dict[str, Any], surface: dict[str, Any]) -> dict[str, Any]:
        return {
            "architecture_seed": architecture_seed,
            "architecture_surface": surface,
            "requirements": architecture_seed.get("requirements", {}),
            "instructions": "Return only the concrete project layout, entrypoints, directories, and validation commands. Do not return module contracts.",
        }

    def _architecture_contracts_payload(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], architecture_seed: dict[str, Any], surface: dict[str, Any], layout_draft: dict[str, Any]) -> dict[str, Any]:
        return {
            "architecture_seed": architecture_seed,
            "architecture_surface": surface,
            "architecture_layout": layout_draft,
            "requirements": architecture_seed.get("requirements", {}),
            "instructions": "Return only module boundaries, integration contracts, and implementation notes. Do not repeat directory layout.",
        }

    def _compact_architecture_requirements(self, requirements: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key in ("status", "summary"):
            value = requirements.get(key)
            if value is not None:
                compact[key] = _compact_text(str(value), 1500)
        for key in ("goals", "users", "constraints", "acceptance_criteria", "missing_information", "risks", "expected_terms"):
            value = requirements.get(key)
            if isinstance(value, list):
                compact[key] = [self._compact_architecture_item(item) for item in value[:6]]
        return compact

    def _compact_architecture_item(self, item: Any) -> Any:
        if isinstance(item, str):
            return _compact_text(item, 220)
        if isinstance(item, dict):
            compact: dict[str, Any] = {}
            for key, value in list(item.items())[:8]:
                compact[key] = _compact_text(value, 220) if isinstance(value, str) else value
            return compact
        return item

    def _architecture_focus_tags(self, requirements: dict[str, Any], project_config: dict[str, Any]) -> list[str]:
        text = " ".join(
            [
                str(requirements.get("summary") or ""),
                " ".join(str(item) for item in requirements.get("goals") or []),
                " ".join(str(item) for item in requirements.get("constraints") or []),
                " ".join(str(item) for item in requirements.get("acceptance_criteria") or []),
                " ".join(str(item) for item in requirements.get("expected_terms") or []),
                str(project_config.get("target_scale") or ""),
            ]
        ).lower()
        focus_map = {
            "auth": ("login", "logout", "password", "session", "auth", "rbac", "权限", "登录"),
            "dashboard": ("dashboard", "summary", "report", "overview", "仪表盘"),
            "inventory": ("inventory", "stock", "product", "warehouse", "入库", "出库", "库存", "产品"),
            "admin_ui": ("admin", "后台", "settings", "manage"),
            "data_history": ("history", "log", "record", "audit", "记录"),
            "pagination": ("pagination", "page", "分页"),
            "search_filter": ("search", "filter", "search box", "搜索", "筛选"),
            "validation": ("validation", "constraint", "negative", "不足", "校验"),
        }
        tags = [name for name, keywords in focus_map.items() if any(keyword in text for keyword in keywords)]
        if not tags:
            return ["general_delivery"]
        return tags[:6]

    def _merge_architecture_design(self, architecture_seed: dict[str, Any], surface: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
        layout = self._normalize_layout(structure)
        return {
            "schema_version": "6.0",
            "architecture_summary": _compact_text(str(surface.get("architecture_summary") or ""), 4000) or _compact_text(str(structure.get("architecture_summary") or ""), 4000),
            "technology_choices": surface.get("technology_choices") or [],
            "project_layout": layout,
            "module_boundaries": structure.get("module_boundaries") or [],
            "integration_contracts": structure.get("integration_contracts") or [],
            "design_principles": surface.get("design_principles") or [],
            "primary_risks": surface.get("primary_risks") or [],
            "scale_notes": surface.get("scale_notes") or [],
            "implementation_notes": structure.get("implementation_notes") or [],
            "architecture_seed": architecture_seed,
        }

    def _merge_architecture_structure(self, layout_draft: dict[str, Any], contracts_draft: dict[str, Any]) -> dict[str, Any]:
        layout = self._normalize_layout(layout_draft)
        if not layout.get("validation_commands") and contracts_draft.get("validation_commands"):
            layout["validation_commands"] = contracts_draft.get("validation_commands") or []
        return {
            "schema_version": "6.0",
            "project_layout": layout,
            "module_boundaries": contracts_draft.get("module_boundaries") or [],
            "integration_contracts": contracts_draft.get("integration_contracts") or [],
            "validation_commands": layout.get("validation_commands") or [],
            "implementation_notes": contracts_draft.get("implementation_notes") or [],
        }

    def _build_package_planning_seed(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        requirements = metadata.get("requirements_analysis") or {}
        architecture = metadata.get("architecture_design") or {}
        layout = self._normalize_layout({"project_layout": metadata.get("project_layout") or architecture.get("project_layout") or {}})
        profile = self._run_scale_profile(run, project)
        project_config = (run.get("metadata") or {}).get("project_config") or {}
        compact_requirements = {
            "status": requirements.get("status", ""),
            "summary": _compact_text(str(requirements.get("summary") or ""), 700),
            "goals": [_compact_text(str(item), 140) for item in list(requirements.get("goals") or [])[:4]],
            "constraints": [_compact_text(str(item), 140) for item in list(requirements.get("constraints") or [])[:4]],
            "acceptance_criteria": [_compact_text(str(item), 180) for item in list(requirements.get("acceptance_criteria") or [])[:4]],
            "risks": [_compact_text(str(item), 160) for item in list(requirements.get("risks") or [])[:4]],
        }
        compact_architecture = {
            "architecture_summary": _compact_text(str(architecture.get("architecture_summary") or ""), 900),
            "technology_choices": list(architecture.get("technology_choices") or [])[:6],
            "project_layout": {
                "source_root": layout.get("source_root", ""),
                "delivery_root": layout.get("delivery_root", ""),
                "entrypoints": list(layout.get("entrypoints") or [])[:6],
                "directories": [{"path": str(item.get("path") or "")} for item in list(layout.get("directories") or [])[:6] if isinstance(item, dict)],
                "validation_commands": [str(cmd) for cmd in list(layout.get("validation_commands") or [])[:4]],
            },
        }
        return {
            "schema_version": "6.1",
            "seed_profile": "compact_scope_current",
            "project": {
                "id": project.get("id", ""),
                "name": project.get("name", ""),
                "title": project.get("title", ""),
                "stack_pack": project_config.get("stack_pack", ""),
                "target_scale": project_config.get("target_scale", ""),
            },
            "scale_profile": {
                "name": profile.get("name", ""),
                "target_loc_hint": profile.get("target_loc_hint", 0),
                "recursive_decomposition_depth": profile.get("recursive_decomposition_depth", 0),
                "wave_parallelism": profile.get("wave_parallelism", 0),
                "max_waves": profile.get("max_waves", 0),
                "package_loc_target": profile.get("package_loc_target", 0),
            },
            "requirements": compact_requirements,
            "architecture": compact_architecture,
            "planning_rules": [
                "Use only project-specific packages with clear ownership.",
                "Keep package count minimal for the actual project size.",
            ],
        }

    def _package_scope_payload(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], planning_seed: dict[str, Any]) -> dict[str, Any]:
        return {
            "package_planning_seed": planning_seed,
            "instructions": "Return package candidates only. Do not return waves or depends_on. Keep the package set minimal and project-specific.",
        }

    def _package_wave_payload(self, project: dict[str, Any], run: dict[str, Any], metadata: dict[str, Any], planning_seed: dict[str, Any], scope_plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "package_planning_seed": {
                "schema_version": planning_seed.get("schema_version", "6.1"),
                "project": planning_seed.get("project", {}),
                "scale_profile": planning_seed.get("scale_profile", {}),
                "architecture": {
                    "project_layout": (planning_seed.get("architecture") or {}).get("project_layout", {}),
                },
            },
            "package_candidates": [self._compact_package_scope_item(item) for item in list(scope_plan.get("packages") or [])],
            "instructions": "Return waves plus one assignment per package candidate. Use dependencies only when required by actual package outputs.",
        }

    def _compact_package_scope_item(self, package: dict[str, Any]) -> dict[str, Any]:
        return {
            "package_key": str(package.get("package_key") or ""),
            "role": str(package.get("role") or ""),
            "domain": _compact_text(str(package.get("domain") or ""), 180),
            "subsystem": _compact_text(str(package.get("subsystem") or ""), 180),
            "allowed_paths": list(package.get("allowed_paths") or [])[:6],
            "objective": _compact_text(str(package.get("objective") or ""), 500),
            "expected_outputs": [self._compact_architecture_item(item) for item in list(package.get("expected_outputs") or [])[:6]],
            "acceptance_gates": [self._compact_architecture_item(item) for item in list(package.get("acceptance_gates") or [])[:6]],
        }

    def _normalize_package_role(self, role: str, domain: str = "", subsystem: str = "", objective: str = "") -> str:
        return canonical_worker_role(role, domain, subsystem, objective)

    def _merge_package_plan(self, scope_plan: dict[str, Any], wave_plan: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
        scope_packages = [dict(item) for item in list(scope_plan.get("packages") or []) if isinstance(item, dict)]
        if not scope_packages:
            raise AgentContractViolationError("package_scope_planning must return project-specific packages")
        known_keys = {str(package.get("package_key") or "").strip() for package in scope_packages}
        known_keys.discard("")
        waves = [dict(item) for item in list(wave_plan.get("waves") or []) if isinstance(item, dict)]
        assignments = [dict(item) for item in list(wave_plan.get("assignments") or []) if isinstance(item, dict)]
        if not assignments and isinstance(wave_plan.get("packages"), list):
            assignments = [
                {"package_key": item.get("package_key"), "wave_key": item.get("wave_key"), "depends_on": item.get("depends_on") or []}
                for item in wave_plan.get("packages") or []
                if isinstance(item, dict)
            ]
        assignment_by_key = {str(item.get("package_key") or ""): item for item in assignments if str(item.get("package_key") or "") in known_keys}
        if not waves:
            wave_keys = list(dict.fromkeys(str(item.get("wave_key") or "WAVE-001") for item in assignments))
            waves = [{"wave_key": wave_key, "sequence": index} for index, wave_key in enumerate(wave_keys or ["WAVE-001"], start=1)]
        normalized_waves: list[dict[str, Any]] = []
        seen_wave_keys: set[str] = set()
        for index, wave in enumerate(waves, start=1):
            wave_key = str(wave.get("wave_key") or f"WAVE-{index:03d}").strip() or f"WAVE-{index:03d}"
            if wave_key in seen_wave_keys:
                continue
            seen_wave_keys.add(wave_key)
            normalized_waves.append({"wave_key": wave_key, "sequence": int(wave.get("sequence") or index), "summary": wave.get("summary", "")})
        if not normalized_waves:
            normalized_waves = [{"wave_key": "WAVE-001", "sequence": 1, "summary": ""}]
        wave_keys = {wave["wave_key"] for wave in normalized_waves}
        packages: list[dict[str, Any]] = []
        for index, package in enumerate(scope_packages, start=1):
            package_key = str(package.get("package_key") or f"PKG-{index:03d}").strip() or f"PKG-{index:03d}"
            assignment = assignment_by_key.get(package_key) or {}
            wave_key = str(assignment.get("wave_key") or package.get("wave_key") or normalized_waves[0]["wave_key"]).strip()
            if wave_key not in wave_keys:
                wave_key = normalized_waves[0]["wave_key"]
            depends_on = []
            for dependency in list(assignment.get("depends_on") or package.get("depends_on") or []):
                dep_key = str(dependency or "").strip()
                if dep_key and dep_key in known_keys and dep_key != package_key and dep_key not in depends_on:
                    depends_on.append(dep_key)
            role = self._normalize_package_role(str(package.get("role") or ""), str(package.get("domain") or ""), str(package.get("subsystem") or ""), str(package.get("objective") or ""))
            packages.append(
                {
                    **package,
                    "package_key": package_key,
                    "role": role,
                    "source_role": package.get("role") or "",
                    "domain": package.get("domain") or role or "core",
                    "subsystem": package.get("subsystem") or package.get("domain") or role or "core",
                    "wave_key": wave_key,
                    "depends_on": depends_on,
                    "allowed_paths": package.get("allowed_paths") or [f"{layout.get('source_root') or 'src'}/**"],
                    "forbidden_paths": package.get("forbidden_paths") or [".git/**", ".v6/**"],
                    "objective": package.get("objective") or f"Implement {package_key}.",
                    "requirements_mapping": package.get("requirements_mapping") or [],
                    "expected_outputs": package.get("expected_outputs") or [],
                    "acceptance_gates": package.get("acceptance_gates") or [],
                }
            )
        return {"schema_version": "6.1", "waves": normalized_waves, "packages": packages}

    def _project_snapshot(self, project: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(project)
        snapshot["project_path_status"] = self.runtime.project_path_status(project)
        snapshot["resolved_project_root"] = snapshot["project_path_status"].get("project_root", "")
        snapshot["scale_profile"] = self._project_scale_profile(project)
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
        return {"schema_version": "6.0", "source_root": source_root, "delivery_root": delivery_root, "entrypoints": entrypoints, "directories": directories, "validation_commands": validation_commands}

    def _normalize_package_plan(self, plan: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
        waves = plan.get("waves") if isinstance(plan.get("waves"), list) else []
        packages = plan.get("packages") if isinstance(plan.get("packages"), list) else []
        if not waves:
            wave_keys = list(dict.fromkeys(str(package.get("wave_key") or f"WAVE-{index:03d}") for index, package in enumerate(packages, start=1)))
            waves = [{"wave_key": wave_key, "sequence": index, "status": "queued"} for index, wave_key in enumerate(wave_keys, start=1)]
        if not packages:
            raise AgentContractViolationError("package_planning must return project-specific packages; static package fallback is disabled")
        normalized_waves = []
        for index, wave in enumerate(waves, start=1):
            normalized_waves.append({"id": wave.get("id") or new_id(), "wave_key": wave.get("wave_key") or f"WAVE-{index:03d}", "sequence": int(wave.get("sequence") or index), "status": wave.get("status", "queued"), "summary": wave.get("summary", "")})
        wave_keys = {wave["wave_key"] for wave in normalized_waves}
        normalized_packages = []
        for index, package in enumerate(packages, start=1):
            role = self._normalize_package_role(str(package.get("role") or package.get("agent") or "backend"), str(package.get("domain") or ""), str(package.get("subsystem") or ""), str(package.get("objective") or package.get("summary") or ""))
            wave_key = package.get("wave_key") if package.get("wave_key") in wave_keys else normalized_waves[0]["wave_key"]
            allowed_paths = package.get("allowed_paths") or [f"{layout.get('source_root') or 'src'}/**"]
            normalized_packages.append(
                {
                    "id": package.get("id") or new_id(),
                    "package_key": package.get("package_key") or f"PKG-{index:03d}",
                    "role": role,
                    "source_role": package.get("role") or package.get("agent") or "",
                    "agent": package.get("agent") or f"{role}_agent",
                    "domain": package.get("domain") or role,
                    "subsystem": package.get("subsystem") or role,
                    "wave_key": wave_key,
                    "depends_on": package.get("depends_on") or [],
                    "allowed_paths": allowed_paths,
                    "forbidden_paths": package.get("forbidden_paths") or [".git/**", ".v6/**"],
                    "objective": package.get("objective") or package.get("summary") or f"Implement {role} package.",
                    "requirements_mapping": package.get("requirements_mapping") or package.get("requirements") or [],
                    "expected_outputs": package.get("expected_outputs") or [],
                    "acceptance_gates": package.get("acceptance_gates") or [],
                    "status": package.get("status", "queued"),
                }
            )
        return {"schema_version": "6.0", "waves": normalized_waves, "packages": normalized_packages}

    def _job_type_for_package(self, package: dict[str, Any]) -> str:
        role = self._normalize_package_role(str(package.get("role", "")), str(package.get("domain") or ""), str(package.get("subsystem") or ""), str(package.get("objective") or ""))
        if role == "docs":
            return "code_generation"
        if role == "release":
            return "release_notes"
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
