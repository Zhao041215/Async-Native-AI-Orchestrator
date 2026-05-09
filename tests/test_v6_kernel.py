from __future__ import annotations

import json
import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from dev_orchestrator.llm_client import LLMError
from dev_orchestrator.v6.ai_scheduler import AICallScheduler
from dev_orchestrator.v6.service import V6Orchestrator
from dev_orchestrator.v6.store import InMemoryV6Store
from dev_orchestrator.v6.worker import DurableWorker, default_role_concurrency, parse_role_concurrency
from dev_orchestrator.v6.profiles import QUALITY_GATES_100K, SCALE_PROFILES, list_scale_profiles, resolve_scale_profile
from dev_orchestrator.v6.provider_resilience import classify_provider_error
from dev_orchestrator.v6.quality import validate_100k_quality_gates


class WorkspaceSandbox:
    def __init__(self) -> None:
        self.path = (Path(__file__).resolve().parent.parent / "workspace" / "v6-test-scratch" / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class TransientFailingLLMClient:
    class Config:
        model = "transient-model"
        provider_profile = "custom-responses-compatible"
        api_base = "https://provider.test/v1"
        wire_api = "responses"

    def __init__(self, message: str = "Remote disconnected without response") -> None:
        self.config = self.Config()
        self.message = message

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        raise LLMError(self.message)


class MinimalOkLLMClient:
    class Config:
        model = "minimal-ok"
        provider_profile = "test"
        api_base = ""
        wire_api = "chat_completions"

    def __init__(self) -> None:
        self.config = self.Config()

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        return json.dumps({"status": "GO", "summary": "ok", "goals": [], "acceptance_criteria": []})


def build_service(root: Path, llm_client=None) -> V6Orchestrator:
    service = V6Orchestrator(store=InMemoryV6Store(), workspace_root=root / "workspace", tenant_id="local-workspace", llm_client=llm_client)
    service.bootstrap()
    return service


class V6KernelTests(unittest.TestCase):
    def test_scale_profiles_all_use_100k_kernel_and_differentiate_orchestration(self) -> None:
        profiles = {profile["name"]: profile for profile in list_scale_profiles()}

        self.assertEqual({"small", "medium", "large", "xlarge_100k"}, set(profiles))
        self.assertTrue(all(profile["kernel_generation"] == "100k_ai_native" for profile in profiles.values()))
        self.assertGreater(profiles["xlarge_100k"]["recursive_decomposition_depth"], profiles["small"]["recursive_decomposition_depth"])
        self.assertGreater(profiles["xlarge_100k"]["wave_parallelism"], profiles["small"]["wave_parallelism"])
        self.assertGreater(profiles["xlarge_100k"]["context_budget_chars"], profiles["small"]["context_budget_chars"])
        self.assertNotEqual(profiles["xlarge_100k"]["package_loc_target"], profiles["small"]["package_loc_target"])

    def test_profile_definitions_are_frozen_and_unknown_scales_are_rejected(self) -> None:
        profile = SCALE_PROFILES["small"]

        self.assertIsInstance(profile.job_attempts, MappingProxyType)
        self.assertIsInstance(profile.required_quality_gates, tuple)
        self.assertEqual(tuple(QUALITY_GATES_100K), profile.required_quality_gates)
        with self.assertRaises(TypeError):
            profile.job_attempts["quality"] = 99
        with self.assertRaises(ValueError):
            resolve_scale_profile({"target_scale": "does-not-exist"})

    def test_provider_error_classification_marks_remote_disconnect_retryable(self) -> None:
        result = classify_provider_error("LLM request failed: Remote disconnected without response")

        self.assertEqual(result["error_kind"], "provider_transient")
        self.assertTrue(result["retryable"])
        self.assertGreater(result["backoff_seconds"], 0)

    def test_provider_error_classification_marks_ssl_eof_retryable(self) -> None:
        result = classify_provider_error("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")

        self.assertEqual(result["error_kind"], "provider_transient")
        self.assertTrue(result["retryable"])

    def test_scheduler_records_provider_health_for_transient_failure(self) -> None:
        profile = resolve_scale_profile({"target_scale": "xlarge_100k"})
        scheduler = AICallScheduler(TransientFailingLLMClient())

        payload = scheduler.call(
            run_id="run-1",
            role="architect",
            job={"id": "job-1", "job_type": "architecture_design", "payload": {"scale_profile": profile}},
            system_prompt="architect_agent",
            user_payload={"project": {"name": "x"}, "requirements": ["a"]},
            task_kind="architecture_design",
            required=True,
        )

        self.assertFalse(payload["ok"])
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["error_kind"], "provider_transient")
        self.assertEqual(payload["provider_health"]["status"], "degraded")
        self.assertGreaterEqual(payload["budget"]["retry_attempts"], profile["ai_retry_attempts"])

    def test_scheduler_uses_durable_ai_slots_and_persistent_provider_health(self) -> None:
        profile = resolve_scale_profile({"target_scale": "xlarge_100k"})
        store = InMemoryV6Store()
        store.bootstrap()
        scheduler = AICallScheduler(MinimalOkLLMClient())

        payload = scheduler.call(
            run_id="run-slot",
            role="requirements",
            job={"id": "job-slot", "tenant_id": "local-workspace", "job_type": "requirements_analysis", "payload": {"scale_profile": profile}},
            system_prompt="requirements_agent",
            user_payload={"project": {"name": "slot"}, "requirements": ["a"]},
            task_kind="requirements_analysis",
            required=True,
            store=store,
            tenant_id="local-workspace",
        )
        slots = store.ai_slot_snapshot("run-slot")
        health = store.provider_health_snapshot()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["slot_id"])
        self.assertEqual(slots["slot_count"], 1)
        self.assertEqual(slots["active_count"], 0)
        self.assertEqual(health["provider_count"], 1)
        self.assertEqual(health["items"][0]["status"], "healthy")

    def test_scale_profile_defines_worker_and_ai_concurrency(self) -> None:
        profile = resolve_scale_profile({"target_scale": "xlarge_100k"})

        self.assertEqual(profile["ai_provider_concurrency"], 4)
        self.assertEqual(profile["ai_run_concurrency"], 4)
        self.assertGreater(profile["worker_role_concurrency"]["backend"], 1)
        self.assertEqual(parse_role_concurrency("backend=2,qa:3")["qa"], 3)
        self.assertGreater(default_role_concurrency("xlarge_100k")["frontend"], 1)

    def test_transient_llm_failure_sets_run_recovering_before_final_attempt(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, TransientFailingLLMClient())
            project = service.create_project(
                {
                    "name": "recovering-provider",
                    "title": "Recovering Provider",
                    "description": "Build a knowledge base.",
                    "target_scale": "xlarge_100k",
                }
            )
            run = service.create_run(project["id"])

            result = DurableWorker(service, role="requirements", worker_id="requirements", lease_seconds=30).run_once().to_dict()
            final_run = service.get_run(run["id"])
            jobs = service.list_jobs(run["id"])
            agent_runs = [artifact for artifact in service.list_artifacts(run["id"]) if artifact["kind"] == "agent_run"]

            self.assertEqual(result["status"], "failed")
            self.assertEqual(final_run["status"], "recovering")
            self.assertEqual(final_run["continuation"]["next_action"], "retry_ai_call")
            self.assertEqual(jobs[0]["status"], "retry")
            self.assertTrue(agent_runs[0]["payload"]["retryable"])
            self.assertEqual(agent_runs[0]["payload"]["error_kind"], "provider_transient")

    def test_mission_state_exposes_v6_profile_and_recovery_surface(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, MinimalOkLLMClient())
            project = service.create_project(
                {
                    "name": "mission-state",
                    "title": "Mission State",
                    "description": "Build a knowledge base.",
                    "target_scale": "large",
                }
            )
            run = service.create_run(project["id"])

            mission = service.mission_state(run["id"])

            self.assertEqual(mission["kernel"], "v6")
            self.assertEqual(mission["kernel_generation"], "100k_ai_native")
            self.assertEqual(mission["scale_profile"]["name"], "large")
            self.assertEqual(mission["recovery"]["dead_letter_count"], 0)
            self.assertIn("provider_health", mission)

    def test_run_events_flow_into_mission_graph_and_recovery_trace(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, TransientFailingLLMClient())
            project = service.create_project(
                {
                    "name": "event-flow",
                    "title": "Event Flow",
                    "description": "Build a knowledge base.",
                    "target_scale": "xlarge_100k",
                }
            )
            run = service.create_run(project["id"])

            DurableWorker(service, role="requirements", worker_id="requirements", lease_seconds=30).run_once()
            events = service.list_events(run["id"])
            mission = service.mission_state(run["id"])
            graph = service.mission_graph(run["id"])
            recovery = service.recovery_trace(run["id"])
            replay = service.replay_projection(run["id"])
            resume = service.checkpoint_resume(run["id"])

            self.assertTrue(any(event["kind"] == "mission_state_changed" for event in events))
            self.assertTrue(any(event["kind"] == "job.enqueued" for event in events))
            self.assertIn("mission_graph", mission)
            self.assertIn("event_summary", mission)
            self.assertEqual(graph["metrics"]["event_count"], len(events))
            self.assertGreaterEqual(recovery["provider_failure_count"], 1)
            self.assertIn("latest_recovery_events", recovery)
            self.assertTrue(replay["ok"])
            self.assertEqual(replay["projection"]["status"], service.get_run(run["id"])["status"])
            self.assertEqual(resume["next_action"], "worker_claim_pending_jobs")

    def test_expired_lease_requeues_and_records_recovery_event(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, MinimalOkLLMClient())
            project = service.create_project({"name": "lease-recovery", "title": "Lease Recovery", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            job = service.store.claim_job("local-workspace", "requirements", "worker-a", 1)
            self.assertIsNotNone(job)
            service.store.start_job(job["id"], "worker-a", 1)
            service.store.jobs[job["id"]]["lease_until"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
            result = service.requeue_expired_leases()
            jobs = service.list_jobs(run["id"])
            events = service.list_events(run["id"])

            self.assertEqual(result["requeued_count"], 1)
            self.assertEqual(jobs[0]["status"], "retry")
            self.assertTrue(any(event["kind"] == "job.requeued_after_expiry" for event in events))

    def test_dead_letter_requeue_creates_recovery_job_and_event(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, MinimalOkLLMClient())
            project = service.create_project({"name": "dead-letter", "title": "Dead Letter", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])
            job = service.store.claim_job("local-workspace", "requirements", "worker-a", 30)
            self.assertIsNotNone(job)
            service.store.start_job(job["id"], "worker-a", 30)
            service.store.fail_job(job["id"], "worker-a", "boom", retryable=False)

            result = service.requeue_dead_letter(job["id"])
            jobs = service.list_jobs(run["id"])
            events = service.list_events(run["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(len([item for item in jobs if item["status"] == "dead_letter"]), 1)
            self.assertEqual(len([item for item in jobs if item["status"] == "queued"]), 1)
            self.assertTrue(any(event["kind"] == "dead_letter.requeued" for event in events))

    def test_100k_quality_gate_set_covers_dag_contract_recovery_and_provider(self) -> None:
        profile = resolve_scale_profile({"target_scale": "xlarge_100k"})
        gates = validate_100k_quality_gates(
            {
                "run_id": "run-1",
                "checkpoint": "package_planning_completed",
                "continuation": {"checkpoint": "package_planning_completed", "next_action": "worker_claim_package_jobs"},
                "scale_profile": profile,
                "architecture": {"integration_contracts": [{"name": "api"}]},
                "packages": [
                    {"payload": {"package_key": "PKG-A", "role": "backend", "subsystem": "api", "depends_on": [], "allowed_paths": ["src/api/**"], "expected_outputs": [{"name": "api"}]}},
                    {"payload": {"package_key": "PKG-B", "role": "qa", "subsystem": "tests", "depends_on": ["PKG-A"], "allowed_paths": ["tests/**"], "expected_outputs": [{"name": "tests"}]}},
                ],
                "agent_runs": [],
                "patch_transactions": [],
                "provider_health": {"ok": True, "items": []},
                "ai_payload_budget": {"ok": True, "budget_status": "within_budget", "payload_chars": 1200, "body_bytes": 1800, "budget_limit": 9000, "provider_body_limit_bytes": 950000, "compression_applied": False, "summary_agent_run_id": "", "trim_report": {}},
                "replay_projection": {"ok": True, "source": "event_log", "projection_hash": "abc", "applied_event_count": 1, "drift": {"ok": True}},
                "checkpoint_resume": {"projection_consistent": True, "checkpoint": "package_planning_completed", "next_action": "worker_claim_package_jobs", "resumable": True, "dead_letter_count": 0},
                "recovery_trace": {"dead_letter_count": 0, "provider_failure_count": 0, "recovery_event_count": 0},
                "contract_index": {"contracts": [{"name": "api", "owner_package": "PKG-A"}]},
                "jobs": [],
                "waves": [{"wave_key": "WAVE-001"}, {"wave_key": "WAVE-002"}],
                "ai_slots": {"slot_count": 0, "active_count": 0, "items": []},
            }
        )
        gate_names = {gate["name"] for gate in gates}

        self.assertTrue(
            {
                "v6_scale_profile_gate",
                "package_dag_acyclic_gate",
                "contract_density_gate",
                "provider_resilience_gate",
                "ai_live_execution_gate",
                "ai_payload_budget_gate",
                "ai_context_compression_gate",
                "oversize_recovery_gate",
                "agent_output_diversity_gate",
                "event_replay_projection_gate",
                "checkpoint_resume_gate",
                "recovery_trace_gate",
                "recovery_checkpoint_gate",
                "parallel_execution_safety_gate",
                "durable_ai_slot_gate",
                "long_call_heartbeat_gate",
                "provider_circuit_recovery_gate",
                "patch_parallel_conflict_gate",
                "performance_budget_gate",
            }.issubset(gate_names)
        )
        self.assertTrue(all(gate["ok"] for gate in gates))


if __name__ == "__main__":
    unittest.main()
