from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v6.models import ROLES, new_id, sha256_bytes, slugify, stable_json
from dev_orchestrator.v6.profiles import resolve_scale_profile
from dev_orchestrator.v6.service import ProviderCallError, V6Orchestrator
from dev_orchestrator.v6.worker import DurableWorker


DEFAULT_PRESSURE_WORKER_ROLES = tuple(ROLES)
DEFAULT_PRESSURE_TIMEOUT_SECONDS = 60 * 60 * 4
DEFAULT_PRESSURE_IDLE_SLEEP_SECONDS = 0.5
DEFAULT_OVERSIZE_BODY_LIMIT_BYTES = 12_000
TERMINAL_RUN_STATUSES = {"blocked", "cancelled", "completed", "dead_letter", "failed", "no_go"}


@dataclass(frozen=True)
class PressureBenchmarkSpec:
    name: str
    project_payload: dict[str, Any]
    requirements_text: str
    fault_schedule: dict[str, tuple[str, ...]]
    oversize_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project_payload": self.project_payload,
            "requirements_text": self.requirements_text,
            "fault_schedule": {key: list(value) for key, value in self.fault_schedule.items()},
            "oversize_payload": self.oversize_payload,
            "requirements_chars": len(self.requirements_text),
            "requirements_hash": sha256_bytes(self.requirements_text.encode("utf-8")),
        }


@dataclass(frozen=True)
class PressureFaultConfig:
    transient_failures: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "requirements_analysis": ("HTTP 429: rate limit",),
            "architecture_design": ("Remote disconnected without response: simulated provider disconnect",),
            "package_planning": ("Timed out after 90 seconds.",),
            "code_generation": ("HTTP 429: rate limit",),
            "integration_merge": ("Remote disconnected without response: simulated provider disconnect",),
            "code_review": ("Timed out after 90 seconds.",),
            "release_notes": ("HTTP 429: rate limit",),
        }
    )
    worker_roles: tuple[str, ...] = DEFAULT_PRESSURE_WORKER_ROLES
    timeout_seconds: int = DEFAULT_PRESSURE_TIMEOUT_SECONDS
    idle_sleep_seconds: float = DEFAULT_PRESSURE_IDLE_SLEEP_SECONDS
    oversize_body_limit_bytes: int = DEFAULT_OVERSIZE_BODY_LIMIT_BYTES
    inject_transient_faults: bool = True


@dataclass
class PressureReport:
    schema_version: str
    name: str
    ok: bool
    started_at: str
    finished_at: str
    elapsed_seconds: float
    benchmark: dict[str, Any]
    ai_policy: dict[str, Any]
    main_run: dict[str, Any]
    probes: dict[str, Any]
    metrics: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "ok": self.ok,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "benchmark": self.benchmark,
            "ai_policy": self.ai_policy,
            "main_run": self.main_run,
            "probes": self.probes,
            "metrics": self.metrics,
            "errors": self.errors,
        }


class FaultInjectingLLMClient(OpenAICompatibleClient):
    def __init__(self, delegate: OpenAICompatibleClient, faults: dict[str, tuple[str, ...]] | None = None):
        self._delegate = delegate
        self.config = delegate.config
        self._faults = {key: tuple(value) for key, value in (faults or {}).items()}
        self._call_counts: Counter[str] = Counter()

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        task_kind = self._infer_task_kind(system_prompt, messages)
        self._call_counts[task_kind] += 1
        faults = self._faults.get(task_kind, ())
        call_index = self._call_counts[task_kind] - 1
        if call_index < len(faults):
            raise LLMError(faults[call_index])
        return self._delegate.chat(system_prompt, messages, **kwargs)

    def build_request_preview(self, *args, **kwargs) -> dict:
        return self._delegate.build_request_preview(*args, **kwargs)

    def test_connection(self) -> dict:
        return self._delegate.test_connection()

    def _infer_task_kind(self, system_prompt: str, messages: list[dict]) -> str:
        prompt = str(system_prompt or "").lower()
        message_blob = stable_json(messages).lower()
        text = f"{prompt}\n{message_blob}"
        for token, task_kind in (
            ("requirements_analysis", "requirements_analysis"),
            ("requirements_agent", "requirements_analysis"),
            ("architecture_design", "architecture_design"),
            ("architect_agent", "architecture_design"),
            ("package_planning", "package_planning"),
            ("planner_agent", "package_planning"),
            ("integration_merge", "integration_merge"),
            ("integration_agent", "integration_merge"),
            ("code_review", "code_review"),
            ("review_agent", "code_review"),
            ("release_notes", "release_notes"),
            ("release_agent", "release_notes"),
            ("failure_analysis", "failure_analysis"),
            ("repair_agent", "failure_analysis"),
            ("context_summary", "context_summary"),
        ):
            if token in text:
                return task_kind
        if "security_review" in text or "security_agent" in text:
            return "security_review"
        if "test_generation" in text or "qa_agent" in text:
            return "test_generation"
        if "code_generation" in text or "backend_agent" in text or "frontend_agent" in text or "docs_agent" in text or "db_agent" in text:
            return "code_generation"
        return "code_generation"


def build_pressure_benchmark_spec(project_path: str) -> PressureBenchmarkSpec:
    project_name = "v6-100k-pressure-benchmark"
    title = "V6 100k Real AI Pressure Benchmark"
    requirements_text = _build_pressure_requirements_text()
    oversize_payload = _build_oversize_payload(project_name)
    fault_schedule = {
        "requirements_analysis": ("HTTP 429: rate limit",),
        "architecture_design": ("Remote disconnected without response: simulated provider disconnect",),
        "package_planning": ("Timed out after 90 seconds.",),
        "code_generation": ("HTTP 429: rate limit",),
        "integration_merge": ("Remote disconnected without response: simulated provider disconnect",),
        "code_review": ("Timed out after 90 seconds.",),
        "release_notes": ("HTTP 429: rate limit",),
    }
    return PressureBenchmarkSpec(
        name=project_name,
        project_payload={
            "name": project_name,
            "title": title,
            "description": "Real AI pressure benchmark for long-context 100k mission execution.",
            "project_path": project_path,
            "config": {
                "target_scale": "xlarge_100k",
                "stack_pack": "benchmark",
                "deployment_mode": "docker-compose-v6",
                "api_only": False,
                "effective_loc_target": 100000,
                "unattended_mode": "on",
            },
        },
        requirements_text=requirements_text,
        fault_schedule=fault_schedule,
        oversize_payload=oversize_payload,
    )


class PressureTestRunner:
    def __init__(
        self,
        *,
        root_dir: Path,
        service: V6Orchestrator,
        base_llm_client: OpenAICompatibleClient,
        output_dir: Path,
        fault_config: PressureFaultConfig | None = None,
    ):
        self.root_dir = root_dir
        self.service = service
        self.base_llm_client = base_llm_client
        self.output_dir = output_dir
        self.fault_config = fault_config or PressureFaultConfig()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict[str, Any]:
        started_at = time.time()
        started_label = _iso_now()
        errors: list[str] = []
        ai_connection = self._ensure_live_provider()
        benchmark_id = f"pressure-{new_id()[:8]}"
        benchmark_path = f"runtime/pressure-tests/{benchmark_id}/main"
        spec = build_pressure_benchmark_spec(benchmark_path)
        main_result: dict[str, Any] = {}
        probes: dict[str, Any] = {}

        try:
            main_result = self._run_main_benchmark(spec, benchmark_id)
            probes["lease_expiry"] = self._run_lease_expiry_probe(benchmark_id)
            probes["dead_letter"] = self._run_dead_letter_probe(benchmark_id)
            probes["oversize"] = self._run_oversize_probe(benchmark_id)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            main_result.setdefault("ok", False)
            main_result.setdefault("error", str(exc))
            main_result.setdefault("status", "failed")
        finished_at = _iso_now()
        elapsed_seconds = round(time.time() - started_at, 3)
        ai_policy = self.service.ai_policy()
        metrics = self._build_metrics(main_result, probes, ai_connection, elapsed_seconds)
        ok = bool(main_result.get("ok")) and all(bool(probe.get("ok")) for probe in probes.values()) and not errors
        report = PressureReport(
            schema_version="6.1",
            name=spec.name,
            ok=ok and not errors,
            started_at=started_label,
            finished_at=finished_at,
            elapsed_seconds=elapsed_seconds,
            benchmark=spec.to_dict(),
            ai_policy=ai_policy,
            main_run=main_result,
            probes=probes,
            metrics=metrics,
            errors=errors,
        )
        self._write_report(report)
        if main_result.get("project_id") and main_result.get("run_id"):
            self.service._record_json(
                self.service.get_project(main_result.get("project_id", "")) or {"name": spec.name, "tenant_id": self.service.tenant_id},
                self.service.get_run(main_result.get("run_id", "")) or {"id": main_result.get("run_id", ""), "tenant_id": self.service.tenant_id, "project_id": main_result.get("project_id", "")},
                "pressure_report",
                "pressure-report.json",
                report.to_dict(),
                {"benchmark_name": spec.name, "source": "pressure_runner"},
            )
        return report.to_dict()

    def _ensure_live_provider(self) -> dict[str, Any]:
        if self.base_llm_client is None:
            raise RuntimeError("pressure test requires a live AI provider")
        connection = self.base_llm_client.test_connection()
        if not connection.get("ok"):
            raise RuntimeError(f"pressure test requires a live AI provider: {connection.get('error', 'unknown error')}")
        return connection

    def _run_main_benchmark(self, spec: PressureBenchmarkSpec, benchmark_id: str) -> dict[str, Any]:
        original_client = self.service.llm_client
        if self.fault_config.inject_transient_faults:
            live_client = FaultInjectingLLMClient(self.base_llm_client, spec.fault_schedule)
        else:
            live_client = self.base_llm_client
        self.service.update_llm_client(live_client)
        project: dict[str, Any] = {}
        run: dict[str, Any] = {}
        try:
            project = self.service.create_project(spec.project_payload, tenant_id=self.service.tenant_id)
            run = self.service.create_run(project["id"], spec.requirements_text)
            self.service._record_json(
                project,
                run,
                "pressure_benchmark",
                "pressure-benchmark.json",
                spec.to_dict(),
                {"benchmark_name": spec.name, "source": "pressure_runner"},
            )
            run_result = self._drain_until_terminal(run["id"], benchmark_id, require_completion=False)
            run_result["ok"] = run_result.get("status") == "completed"
            if not run_result["ok"]:
                run_result["error"] = f"pressure benchmark did not complete: {run_result.get('status', 'unknown')}"
            run_result.update(
                {
                    "project_id": project["id"],
                    "project_name": project["name"],
                    "project_path": project.get("project_path", ""),
                    "benchmark_id": benchmark_id,
                    "spec_hash": sha256_bytes(stable_json(spec.to_dict()).encode("utf-8")),
                }
            )
            return run_result
        except Exception as exc:
            if run.get("id"):
                run_result = self._drain_until_terminal(run["id"], benchmark_id, require_completion=False)
                run_result.update(
                    {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "project_id": project.get("id", ""),
                        "project_name": project.get("name", ""),
                        "project_path": project.get("project_path", ""),
                        "benchmark_id": benchmark_id,
                        "spec_hash": sha256_bytes(stable_json(spec.to_dict()).encode("utf-8")),
                    }
                )
                return run_result
            raise
        finally:
            self.service.update_llm_client(original_client)

    def _run_lease_expiry_probe(self, benchmark_id: str) -> dict[str, Any]:
        project = self.service.create_project(
            {
                "name": f"{benchmark_id}-lease-probe",
                "title": "Pressure Lease Expiry Probe",
                "description": "Disposable probe for lease expiry recovery.",
                "project_path": f"runtime/pressure-tests/{benchmark_id}/lease-probe",
                "config": {
                    "target_scale": "small",
                    "stack_pack": "benchmark",
                    "deployment_mode": "docker-compose-v6",
                    "api_only": True,
                    "effective_loc_target": 1000,
                    "unattended_mode": "on",
                },
            },
            tenant_id=self.service.tenant_id,
        )
        run = self.service.create_run(project["id"], "Lease expiry probe for durable job recovery.")
        job = self._claim_probe_job(run["id"], "requirements")
        if not job:
            raise RuntimeError("lease expiry probe could not claim a job")
        started = self.service.store.start_job(job["id"], job["worker_id"], 1)
        if not started:
            raise RuntimeError("lease expiry probe could not start a job")
        time.sleep(1.5)
        sweep = self.service.requeue_expired_leases()
        worker_result = DurableWorker(self.service, role="requirements", tenant_id=self.service.tenant_id, worker_id=f"pressure-{benchmark_id}-lease").run_once().to_dict()
        recovery_trace = self.service.recovery_trace(run["id"])
        return {
            "project_id": project["id"],
            "run_id": run["id"],
            "job_id": job["id"],
            "requeue_sweep": sweep,
            "job_status": next((item["status"] for item in self.service.list_jobs(run["id"]) if item["id"] == job["id"]), ""),
            "worker_result": worker_result,
            "recovery_trace": recovery_trace,
            "ok": bool(worker_result["claimed"]) and bool(recovery_trace.get("recovery_event_count", 0)) and sweep.get("requeued_count", 0) >= 1,
        }

    def _run_dead_letter_probe(self, benchmark_id: str) -> dict[str, Any]:
        project = self.service.create_project(
            {
                "name": f"{benchmark_id}-dead-letter-probe",
                "title": "Pressure Dead Letter Probe",
                "description": "Disposable probe for dead-letter recovery.",
                "project_path": f"runtime/pressure-tests/{benchmark_id}/dead-letter-probe",
                "config": {
                    "target_scale": "small",
                    "stack_pack": "benchmark",
                    "deployment_mode": "docker-compose-v6",
                    "api_only": True,
                    "effective_loc_target": 1000,
                    "unattended_mode": "on",
                },
            },
            tenant_id=self.service.tenant_id,
        )
        run = self.service.create_run(project["id"], "Dead letter probe for durable job recovery.")
        job = self._claim_probe_job(run["id"], "requirements")
        if not job:
            raise RuntimeError("dead letter probe could not claim a job")
        started = self.service.store.start_job(job["id"], job["worker_id"], 15)
        if not started:
            raise RuntimeError("dead letter probe could not start a job")
        self.service.store.fail_job(job["id"], job["worker_id"], "forced dead letter probe", retryable=False)
        dead_letter_jobs = [item for item in self.service.list_jobs(run["id"]) if item.get("status") == "dead_letter"]
        requeued = self.service.requeue_dead_letter(job["id"])
        worker_result = DurableWorker(self.service, role="requirements", tenant_id=self.service.tenant_id, worker_id=f"pressure-{benchmark_id}-dead-letter").run_once().to_dict()
        recovery_trace = self.service.recovery_trace(run["id"])
        return {
            "project_id": project["id"],
            "run_id": run["id"],
            "job_id": job["id"],
            "dead_letter_count": len(dead_letter_jobs),
            "requeue_result": requeued,
            "worker_result": worker_result,
            "recovery_trace": recovery_trace,
            "ok": bool(requeued.get("ok")) and bool(dead_letter_jobs) and bool(worker_result["claimed"]),
        }

    def _run_oversize_probe(self, benchmark_id: str) -> dict[str, Any]:
        original_client = self.service.llm_client
        oversize_client = OpenAICompatibleClient(
            _clone_llm_config(self.base_llm_client.config, max_request_body_bytes=self.fault_config.oversize_body_limit_bytes)
        )
        self.service.update_llm_client(oversize_client)
        try:
            project = self.service.create_project(
                {
                    "name": f"{benchmark_id}-oversize-probe",
                    "title": "Pressure Oversize Probe",
                    "description": "Disposable probe for oversize context recovery.",
                    "project_path": f"runtime/pressure-tests/{benchmark_id}/oversize-probe",
                    "config": {
                        "target_scale": "xlarge_100k",
                        "stack_pack": "benchmark",
                        "deployment_mode": "docker-compose-v6",
                        "api_only": False,
                        "effective_loc_target": 100000,
                        "unattended_mode": "on",
                    },
                },
                tenant_id=self.service.tenant_id,
            )
            run = self.service.create_run(project["id"], "Oversize payload recovery probe for the 100k kernel.")
            job = {"id": new_id(), "job_type": "code_review", "payload": {"scale_profile": resolve_scale_profile({"target_scale": "xlarge_100k"})}}
            prepared_payload, budget_report = self.service._prepare_ai_payload(
                project,
                run,
                role="review",
                job=job,
                task_kind="code_review",
                system_prompt="You are review_agent. Return strict JSON only with ok, status, findings, required_fixes, requirement_coverage, security_notes.",
                user_payload={
                    "project": self.service._project_prompt(project),
                    **spec_like_oversize_payload(),
                },
            )
            run_after = self.service.get_run(run["id"])
            return {
                "project_id": project["id"],
                "run_id": run["id"],
                "ok": True,
                "status": run_after.get("status", ""),
                "budget_report": budget_report,
                "prepared_payload_chars": len(stable_json(prepared_payload)),
                "recovery_trace": self.service.recovery_trace(run["id"]),
            }
        except ProviderCallError as exc:
            blocked_run = self.service.get_run(run["id"]) if "run" in locals() else {}
            recovery = self.service.recovery_trace(run["id"]) if "run" in locals() else {}
            recovery_result: dict[str, Any] = {}
            if "run" in locals():
                self.service.update_llm_client(self.base_llm_client)
                recovery_result = self.service.recover_run(run["id"])
                recovery = self.service.recovery_trace(run["id"])
            return {
                "project_id": project["id"] if "project" in locals() else "",
                "run_id": run["id"] if "run" in locals() else "",
                "ok": True,
                "blocked": True,
                "error": str(exc),
                "run_status": blocked_run.get("status", ""),
                "run_status_after_recover": (self.service.get_run(run["id"]) or {}).get("status", "") if "run" in locals() else "",
                "recovery_result": recovery_result,
                "recovery_trace": recovery,
            }
        finally:
            self.service.update_llm_client(original_client)

    def _drain_until_terminal(self, run_id: str, benchmark_id: str, *, require_completion: bool = True) -> dict[str, Any]:
        run = self.service.get_run(run_id) or {}
        profile = resolve_scale_profile((run.get("metadata") or {}).get("scale_profile") or {})
        role_concurrency = profile.get("worker_role_concurrency") or {}
        workers = []
        for role in self.fault_config.worker_roles:
            for index in range(max(1, int(role_concurrency.get(role, 1)))):
                workers.append(
                    DurableWorker(
                        service=self.service,
                        role=role,
                        tenant_id=self.service.tenant_id,
                        worker_id=f"{benchmark_id}-{role}-{index + 1}",
                        poll_seconds=self.fault_config.idle_sleep_seconds,
                        lease_seconds=60,
                    )
                )
        started = time.time()
        last_progress = started
        progress_events = Counter()
        job_failures = Counter()
        while time.time() - started <= self.fault_config.timeout_seconds:
            run = self.service.get_run(run_id)
            if not run:
                raise RuntimeError(f"run not found: {run_id}")
            if run.get("status") in TERMINAL_RUN_STATUSES and run.get("status") != "completed":
                break
            if run.get("status") == "release_ready":
                self._enqueue_apply_if_needed(run_id)
            if run.get("status") == "completed":
                break
            if self.service.store.list_jobs(run_id=run_id, status=None) and not self._has_active_jobs(run_id):
                self._enqueue_apply_if_needed(run_id)
            self.service.requeue_expired_leases()
            claimed = False
            for worker in workers:
                result = worker.run_once().to_dict()
                if result["claimed"]:
                    claimed = True
                    progress_events[result["job_type"]] += 1
                    if result["status"] == "failed":
                        job_failures[result["job_type"]] += 1
            if claimed:
                last_progress = time.time()
                continue
            if time.time() - last_progress > 60:
                break
            time.sleep(self.fault_config.idle_sleep_seconds)
        final_run = self.service.get_run(run_id) or {}
        ai_calls = self.service.ai_calls(run_id)
        ai_violations = [
            call
            for call in ai_calls
            if int(call.get("payload_chars") or 0) > int((call.get("payload_budget") or {}).get("budget_limit") or call.get("budget_limit") or 0)
            or int(call.get("body_bytes") or call.get("payload_bytes") or 0) > int((call.get("payload_budget") or {}).get("provider_body_limit_bytes") or call.get("provider_body_limit_bytes") or 0)
        ]
        if require_completion and final_run.get("status") != "completed":
            raise RuntimeError(f"pressure benchmark did not complete: {final_run.get('status', 'unknown')}")
        if ai_violations:
            raise RuntimeError(f"pressure benchmark produced oversize AI payloads: {len(ai_violations)}")
        return {
            "run_id": run_id,
            "status": final_run.get("status", ""),
            "checkpoint": final_run.get("checkpoint", ""),
            "continuation": final_run.get("continuation", {}),
            "ai_call_count": len(ai_calls),
            "max_payload_chars": max((int(call.get("payload_chars") or 0) for call in ai_calls), default=0),
            "max_payload_bytes": max((int(call.get("payload_bytes") or 0) for call in ai_calls), default=0),
            "max_body_bytes": max((int(call.get("body_bytes") or 0) for call in ai_calls), default=0),
            "compression_count": len([call for call in ai_calls if call.get("compression_applied")]),
            "summary_agent_runs": [call.get("summary_agent_run_id", "") for call in ai_calls if call.get("summary_agent_run_id")],
            "progress_events": dict(progress_events),
            "job_failures": dict(job_failures),
            "mission_state": self.service.mission_state(run_id),
            "mission_graph": self.service.mission_graph(run_id),
            "recovery_trace": self.service.recovery_trace(run_id),
            "replay_projection": self.service.replay_projection(run_id),
            "checkpoint_resume": self.service.checkpoint_resume(run_id),
            "context_index": self.service.context_index(run_id),
            "quality_report": self.service._latest_artifact_payload(run_id, "quality_report"),
            "release_notes": self.service._latest_artifact_payload(run_id, "release_notes"),
            "release_candidate": self.service._latest_artifact_payload(run_id, "release_candidate"),
            "provider_health": self.service.provider_health(),
            "artifacts": {
                "count": len(self.service.list_artifacts(run_id)),
                "kinds": Counter(artifact["kind"] for artifact in self.service.list_artifacts(run_id)),
            },
            "events": {
                "count": len(self.service.list_events(run_id)),
                "kinds": Counter(event["kind"] for event in self.service.list_events(run_id)),
            },
        }

    def _enqueue_apply_if_needed(self, run_id: str) -> None:
        if any(job for job in self.service.store.list_jobs(run_id=run_id) if job["job_type"] == "apply" and job["status"] in {"queued", "retry", "leased", "running"}):
            return
        candidate = self.service._latest_artifact_payload(run_id, "release_candidate")
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id:
            self.service.enqueue_apply(candidate_id)

    def _has_active_jobs(self, run_id: str) -> bool:
        return bool([job for job in self.service.store.list_jobs(run_id=run_id) if job["status"] in {"queued", "retry", "leased", "running"}])

    def _claim_probe_job(self, run_id: str, role: str) -> dict[str, Any] | None:
        claimed = self.service.store.claim_job(self.service.tenant_id, role, f"pressure-{role}-{new_id()[:8]}", 30)
        if claimed:
            return claimed
        self.service.store.enqueue_job(
            self.service.tenant_id,
            {
                "job_type": "requirements_analysis",
                "role": role,
                "run_id": run_id,
                "resume_key": f"run:{run_id}:{role}:probe",
                "payload": {"scale_profile": resolve_scale_profile({"target_scale": "small"})},
                "max_attempts": 2,
            },
        )
        return self.service.store.claim_job(self.service.tenant_id, role, f"pressure-{role}-{new_id()[:8]}", 30)

    def _build_metrics(self, main_result: dict[str, Any], probes: dict[str, Any], ai_connection: dict[str, Any], elapsed_seconds: float) -> dict[str, Any]:
        main_ai_calls = main_result.get("ai_call_count", 0)
        main_payload_bytes = main_result.get("max_payload_bytes", 0)
        main_payload_chars = main_result.get("max_payload_chars", 0)
        main_body_bytes = main_result.get("max_body_bytes", 0)
        return {
            "elapsed_seconds": elapsed_seconds,
            "ai_connection": ai_connection,
            "main_ai_call_count": main_ai_calls,
            "main_max_payload_bytes": main_payload_bytes,
            "main_max_payload_chars": main_payload_chars,
            "main_max_body_bytes": main_body_bytes,
            "main_compression_count": main_result.get("compression_count", 0),
            "main_recovery_event_count": (main_result.get("recovery_trace") or {}).get("recovery_event_count", 0),
            "main_dead_letter_count": (main_result.get("recovery_trace") or {}).get("dead_letter_count", 0),
            "probe_names": sorted(probes.keys()),
            "probe_dead_letter_ok": bool((probes.get("dead_letter") or {}).get("ok")),
            "probe_lease_expiry_ok": bool((probes.get("lease_expiry") or {}).get("ok")),
            "probe_oversize_ok": bool((probes.get("oversize") or {}).get("ok")),
        }

    def _write_report(self, report: PressureReport) -> Path:
        path = self.output_dir / f"{slugify(report.name)}-pressure-report.json"
        path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=True), encoding="utf-8")
        return path


def _build_pressure_requirements_text() -> str:
    sections = [
        (
            "Mission",
            [
                "Build a real AI-native benchmark product that can survive very large requirements, project layouts, package graphs, and recovery cycles without falling back to templates or fake outputs.",
                "The benchmark must stress the V6 kernel as a production control plane. The team should be able to move from requirements to release using live AI calls, durable jobs, patch transactions, quality gates, and recovery checkpoints.",
            ],
        ),
        (
            "Product Scope",
            [
                "The product is a multi-tenant knowledge and operations platform for articles, releases, audits, search, notifications, and long-running work tracking. It should remain coherent even when the project expands to many subsystems.",
                "Each subsystem must have explicit ownership, bounded file paths, and a contract surface that the planner can reason about. The planner should treat package decomposition as the core tool for scaling the design.",
            ],
        ),
        (
            "AI Orchestration",
            [
                "Every planning, architecture, implementation, review, repair, and release task must be produced by an AI role. The system may validate, compress, or route work, but it should not replace the AI with template output.",
                "If any prompt budget becomes too large, the system must trim context, summarize with a real AI context summary call, and resume from a checkpoint rather than letting the request body explode.",
            ],
        ),
        (
            "Runtime Expectations",
            [
                "The benchmark should create enough files, contracts, and change memory to exercise incremental indexing, mission memory, event replay, recovery trace generation, and worker lease handling.",
                "The run should surface provider health, queue health, retry behavior, and dead-letter handling. If a worker loses a lease or a provider blips, the run must continue from a checkpoint instead of silently stopping.",
            ],
        ),
        (
            "Delivery Requirements",
            [
                "Code generation should produce complete files, not fragments. Test generation should produce runnable validation. Integration should merge packages. Review should inspect the whole graph. Release should produce deploy and rollback guidance.",
                "After the release candidate is ready, the system must support applying the candidate and optionally exercising rollback material. The benchmark is only successful when the delivery chain is complete and the control surface shows the full trace.",
            ],
        ),
        (
            "Large Scale Decomposition",
            [
                "The planner should not treat scale as a cosmetic package split. It needs to recursively decompose the product into wave-sized mission slices with explicit owners, contract boundaries, and dependency direction that can survive a 100k project without turning every package into a monolith.",
                "Each package should be small enough for a worker to reason about in one bounded context but rich enough that the integration graph still exposes cross-package contracts, shared utilities, and release-order pressure. The benchmark should punish any algorithm that keeps adding one more layer of generic glue instead of making ownership clearer.",
            ],
        ),
        (
            "Integration Integrity",
            [
                "Integration is not just a merge step. It is the place where the build should prove that generated code, generated tests, quality gates, and release materials all agree on the same product story, the same file paths, and the same execution entry points.",
                "If the implementation drifts from the contract, the system must show that drift as a traceable failure, repair it through a dedicated recovery path, and then re-run the relevant validation instead of pretending the issue vanished when a single job returned success.",
            ],
        ),
        (
            "Observability and Recovery",
            [
                "The benchmark needs a clear recovery trace, event log, replay projection, and checkpoint resume record so operators can understand why the run paused, what data was retained, and which step resumed from the durable boundary.",
                "A real 100k kernel has to survive provider interruptions, worker restarts, and lease expiry while keeping the mission graph legible. The control plane should make these transitions visible without requiring an operator to inspect raw database rows.",
            ],
        ),
        (
            "Performance Envelope",
            [
                "The benchmark should stress queue throughput, payload trimming, summary generation, body-size budgeting, patch application, and recovery throughput at the same time. A system that passes only when the documents are tiny is not ready for a large mission graph.",
                "Performance here means the system keeps moving while the payloads get larger and the tasks get more interdependent. The desired outcome is not raw speed at any cost; it is steady progress with bounded memory growth, predictable retries, and no runaway prompt body sizes.",
            ],
        ),
        (
            "Operational Safety",
            [
                "Every large run should preserve rollback material, maintain package ownership, and emit enough evidence to tell whether the failure came from provider health, prompt budget pressure, contract mismatch, or a genuine code defect.",
                "The pressure harness should surface misbehavior without polluting the user project tree or mutating unrelated outputs. A safe large-scale harness is one that leaves clean artifacts, repeatable state, and a clear restart path after failure.",
            ],
        ),
        (
            "Reliability",
            [
                "The benchmark must deliberately exercise transient failures like rate limiting, disconnects, and timeouts so that retry, backoff, and provider health classification are observable in the event log.",
                "Large context and long documentation are part of the test. The benchmark should be able to ingest many thousands of characters of requirements and still keep each AI call within body limits through trimming and compression.",
            ],
        ),
        (
            "Acceptance Criteria",
            [
                "A successful run will show project creation, run creation, requirements analysis, architecture design, package planning, code generation, integration, review, quality, release notes, release candidate, and application of the candidate.",
                "The run must emit AI call telemetry, context index evidence, mission graph data, replay projection data, checkpoint resume data, recovery trace data, and quality gates. Any request that exceeds body limits should be handled through the dedicated recovery path.",
            ],
        ),
    ]
    appendix = [
        (
            "Identity and tenant isolation",
            "The benchmark must keep all artifacts, events, runs, and workers tenant-scoped so that the pressure workload does not leak across runs.",
        ),
        (
            "Project registry",
            "The project registry should support a clear title, description, and path, and the path must remain inside the workspace sandbox.",
        ),
        (
            "Content ingestion",
            "The system should be able to ingest a large requirements document with structured sections, long acceptance lists, and realistic product pressure.",
        ),
        (
            "Package planning",
            "The planner should produce a small but meaningful DAG with distinct ownership and enough breadth to exercise wave scheduling and dependency checks.",
        ),
        (
            "Code generation",
            "Each package should result in real source files with path-safe manifests and should be mergeable with the transaction-based patch runtime.",
        ),
        (
            "Test generation",
            "The benchmark should ask for validation commands and tests that are specific to the generated project, not generic placeholders.",
        ),
        (
            "Security review",
            "Security review should inspect boundaries, risky dependencies, and path safety, then return actionable evidence for the control plane.",
        ),
        (
            "Release management",
            "Release notes, release candidate, apply, and rollback should all be visible as first-class steps with status transitions and artifacts.",
        ),
        (
            "Audit trail",
            "The benchmark should leave enough artifacts and events that a later operator can understand why each major decision was made.",
        ),
        (
            "Search and indexing",
            "Incremental code and contract indexing should stay current as files are added, and the benchmark should surface the latest index hashes.",
        ),
        (
            "Observability",
            "Provider health, mission graph, recovery trace, and replay projection should be available for inspection while the run is still active.",
        ),
        (
            "Recovery checkpoints",
            "The benchmark must be able to resume from checkpoints after retryable failures, lease expiries, or manual recovery requests.",
        ),
        (
            "Long-context handling",
            "Very large request bodies must be trimmed before send, summarized when necessary, and blocked only after a recovery event has been recorded.",
        ),
        (
            "Dead-letter management",
            "Dead-letter jobs should be observable and recoverable so the system can requeue them instead of silently discarding the work.",
        ),
        (
            "API surface",
            "The benchmark must keep the V6 control plane useful by exposing mission state, events, context index, AI policy, and recovery traces.",
        ),
        (
            "Console UX",
            "The console should surface the run graph and recovery data clearly enough that an operator can understand pressure-test failures quickly.",
        ),
        (
            "Quality gates",
            "The result should pass the 100k quality gates around DAG health, contracts, provider resilience, payload budgets, and recovery evidence.",
        ),
        (
            "Performance budgets",
            "The harness should report the largest prompt body, payload size, and retry count so that regressions are visible immediately.",
        ),
        (
            "Data retention",
            "The benchmark should leave a useful history of the run without polluting the user project tree with ad hoc temporary files.",
        ),
        (
            "Operations",
            "The system must keep running even when some tasks are retried, and the pressure harness should expose whether the queue drained cleanly.",
        ),
        (
            "Failure matrix",
            "Rate limiting, timeout, disconnect, lease expiry, and dead-letter requeue all need to appear in the test story so the failure surface is realistic.",
        ),
        (
            "Delivery completeness",
            "The benchmark ends only when the project has a release candidate, an applied release path, and a final validation pass on the generated code.",
        ),
        (
            "System boundaries",
            "The benchmark should keep all long-running pressure artifacts inside the dedicated runtime pressure-test path so that the user workspace remains clean and deterministic.",
        ),
        (
            "Context budget discipline",
            "The harness should prove that the kernel can reject, trim, summarize, and resume large inputs without falling back to direct oversized requests.",
        ),
        (
            "Package evidence",
            "Each package should leave enough output for an operator to understand what was generated, why it was generated, and how it was validated.",
        ),
        (
            "Change history",
            "The run should preserve enough event history to reconstruct the plan even after several retries and recovery cycles have passed.",
        ),
        (
            "Schema alignment",
            "The pressure benchmark should use the same V6 mission contract and kernel generation naming that production uses so the test is measuring the real system, not a toy variant.",
        ),
        (
            "Failure visibility",
            "Any oversize block, dead-letter requeue, or lease expiry should appear in the report so the resulting control story includes both success and the reasons it took work to get there.",
        ),
        (
            "Composable AI calls",
            "The payload should be structured so that the summary agent, the planner, the implementation agent, the review agent, and the release agent each see only what they need, not the entire repository at once.",
        ),
        (
            "Runtime discipline",
            "The benchmark should let the worker fleet do the work while the control plane records the evidence, instead of forcing every decision through a single synchronous call path.",
        ),
        (
            "Release confidence",
            "The final artifact should make it obvious whether the code is ready to ship, what was proven during the run, and what remains if the run stopped before completion.",
        ),
    ]
    parts = [
        "# V6 100k Real AI Pressure Benchmark",
        "",
        "This document intentionally stretches the prompt budget with realistic product and operational detail so the V6 kernel has to trim, summarize, recover, and integrate under load.",
        "",
    ]
    for heading, paragraphs in sections:
        parts.append(f"## {heading}")
        for paragraph in paragraphs:
            parts.extend([paragraph, ""])
    parts.append("## Acceptance Matrix")
    for index, (name, requirement) in enumerate(appendix, start=1):
        parts.append(f"{index}. {name}: {requirement}")
    parts.append("")
    parts.append("## Final Instruction")
    parts.append("The system should behave like a production delivery engine, not a template demo, and every major task should be able to survive big documentation, retries, and recovery.")
    return "\n".join(parts)


def _build_oversize_payload(project_name: str) -> dict[str, Any]:
    code_index = {
        f"src/modules/module-{index:03d}.py": {
            "summary": f"module-{index}-summary-" + ("x" * 120),
            "symbols": [f"symbol-{index}-{inner}" for inner in range(12)],
            "dependencies": [f"module-{max(0, index - 1):03d}", f"module-{max(0, index - 2):03d}"],
        }
        for index in range(180)
    }
    contract_index = {
        "contracts": [
            {
                "name": f"contract-{index:03d}",
                "owner_package": f"PKG-{index:03d}",
                "consumer_packages": [f"PKG-{max(0, index - 1):03d}"],
                "signals": [f"signal-{index}-{inner}" for inner in range(8)],
            }
            for index in range(180)
        ],
        "missing_consumers": [],
        "index_hash": "",
    }
    change_memory = [
        {
            "artifact_id": f"artifact-{index:03d}",
            "kind": "patch_set",
            "path": f"src/generated/file-{index:03d}.py",
            "changed_files": [f"src/generated/file-{index:03d}.py"],
            "notes": "pressure-test-change",
        }
        for index in range(180)
    ]
    return {
        "project": {
            "name": project_name,
            "title": "Oversize context probe",
            "description": "A deliberately large payload for trimming and compression validation.",
        },
        "code_index": code_index,
        "contract_index": contract_index,
        "change_memory": change_memory,
        "notes": "This payload intentionally exceeds the normal body limit so the kernel must trim and summarize before sending.",
    }


def _clone_llm_config(config: Any, *, max_request_body_bytes: int) -> Any:
    from dataclasses import replace

    return replace(config, max_request_body_bytes=max_request_body_bytes)


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def spec_like_oversize_payload() -> dict[str, Any]:
    return _build_oversize_payload("v6-100k-pressure-benchmark")
