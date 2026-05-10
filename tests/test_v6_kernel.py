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
from dev_orchestrator.v6.profiles import QUALITY_GATES_100K, QUALITY_GATES_MEDIUM, QUALITY_GATES_SMALL, SCALE_PROFILES, list_scale_profiles, resolve_scale_profile
from dev_orchestrator.v6.provider_resilience import classify_provider_error
from dev_orchestrator.v6.quality import validate_100k_quality_gates
from dev_orchestrator.v6.scale_inference import infer_scale_from_package_plan, infer_scale_from_requirements
from dev_orchestrator.v6.llm_policy import resolve_ai_task_budget


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


class SlowOkLLMClient:
    class Config:
        model = "slow-ok"
        provider_profile = "test"
        api_base = ""
        wire_api = "chat_completions"

    def __init__(self, sleep_seconds: float = 0.02) -> None:
        self.config = self.Config()
        self.sleep_seconds = sleep_seconds

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        import time

        time.sleep(self.sleep_seconds)
        return json.dumps({"status": "GO", "summary": "slow ok", "goals": [], "acceptance_criteria": []})


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
        self.assertEqual(tuple(QUALITY_GATES_SMALL), profile.required_quality_gates)
        self.assertEqual(tuple(QUALITY_GATES_MEDIUM), SCALE_PROFILES["medium"].required_quality_gates)
        self.assertEqual(tuple(QUALITY_GATES_100K), SCALE_PROFILES["xlarge_100k"].required_quality_gates)
        with self.assertRaises(TypeError):
            profile.job_attempts["quality"] = 99
        with self.assertRaises(ValueError):
            resolve_scale_profile({"target_scale": "does-not-exist"})

    def test_small_profile_uses_100k_kernel_without_100k_hard_gate_burden(self) -> None:
        small = resolve_scale_profile({"target_scale": "small"})
        xlarge = resolve_scale_profile({"target_scale": "xlarge_100k"})

        self.assertEqual(small["kernel_generation"], xlarge["kernel_generation"])
        self.assertLess(small["max_waves"], xlarge["max_waves"])
        self.assertLess(small["ai_provider_concurrency"], xlarge["ai_provider_concurrency"])
        self.assertLess(small["job_attempts"]["architecture_design"], xlarge["job_attempts"]["architecture_design"])
        self.assertNotIn("parallel_execution_safety_gate", small["required_quality_gates"])
        self.assertIn("parallel_execution_safety_gate", xlarge["required_quality_gates"])

    def test_auto_scale_resolves_to_medium_starting_point(self) -> None:
        profile = resolve_scale_profile({"target_scale": "auto"})
        blank = resolve_scale_profile({})

        self.assertEqual(profile["name"], "medium")
        self.assertEqual(blank["name"], "medium")

    def test_package_plan_inference_identifies_large_work(self) -> None:
        plan = {
            "waves": [{"wave_key": f"WAVE-{index:03d}", "sequence": index} for index in range(1, 10)],
            "packages": [
                {
                    "package_key": f"PKG-{index:03d}",
                    "role": ["backend", "frontend", "qa", "security", "docs", "db"][index % 6],
                    "wave_key": f"WAVE-{(index % 9) + 1:03d}",
                    "depends_on": [f"PKG-{max(0, index - 1):03d}"] if index else [],
                    "allowed_paths": [f"src/pkg-{index}/**"],
                }
                for index in range(30)
            ],
        }

        inference = infer_scale_from_package_plan(plan)

        self.assertEqual(inference["selected_scale"], "xlarge_100k")

    def test_requirements_inference_defers_xlarge_when_only_ai_output_is_verbose(self) -> None:
        requirements = {
            "summary": "Smoke test platform validation with generated notes about security, API, database, cache, monitoring, backup, rollback, observability, load testing, audit, tenant, privacy, webhook, export, report, dashboard, frontend, backend, workflow, notification, docker, deploy, e2e, benchmark.",
            "goals": ["security API database cache monitoring backup rollback observability load test audit tenant webhook export report"] * 8,
            "users": ["operator", "qa", "developer", "admin"],
            "constraints": ["security compliance high availability performance docker monitoring backup rollback privacy RBAC"] * 8,
            "acceptance_criteria": ["api contract e2e load benchmark deployment guide audit report workflow dashboard"] * 12,
            "risks": ["provider outage data migration permission leakage large request payloads"] * 4,
            "expected_terms": ["tenant role invoice webhook report audit cache security performance"] * 8,
        }

        inference = infer_scale_from_requirements(requirements, "Build a V6 smoke project.")

        self.assertEqual(inference["selected_scale"], "large")
        self.assertIn("xlarge_deferred_until_architecture_or_package_evidence", inference["reasons"])

    def test_requirements_inference_allows_xlarge_for_real_large_original_document(self) -> None:
        large_doc = (
            "Build a 100k enterprise multi-tenant platform with security, audit, compliance, integrations, "
            "database migrations, analytics, observability, rollback, e2e testing, load testing, backup, monitoring, "
            "high availability, performance budgets, API contracts, workflow automation, billing, reports, cache, "
            "privacy, RBAC, and distributed deployment.\n"
            * 35
        )
        requirements = {
            "summary": "Enterprise platform.",
            "goals": ["multi tenant admin", "analytics", "billing", "workflow", "integration", "audit"],
            "constraints": ["security", "performance", "compliance", "high availability", "backup", "monitoring"],
            "acceptance_criteria": ["e2e tests", "load tests", "rollback", "security review", "deployment guide", "api contracts"],
            "risks": ["data migration", "permission leakage", "provider outage", "large request payloads"],
        }

        inference = infer_scale_from_requirements(requirements, large_doc)

        self.assertEqual(inference["selected_scale"], "xlarge_100k")

    def test_inventory_crud_requirement_stays_medium_under_auto_scale(self) -> None:
        inventory_request = """
        1. User Login
        Simple admin login with Chinese UI (用户名, 密码, 登录)
        Session-based auth; logout link (退出登录) on every page
        2. Dashboard
        Displays summary in Chinese: total products, total stock, low-stock count
        Low-stock alert list shown directly on the dashboard
        3. Product Management (产品管理)
        Paginated table with Chinese headers: 产品编号, 产品名称, 规格型号, 单位, 当前库存, 最低库存警戒, 操作
        Add product form with Chinese labels (产品名称, 规格型号, 单位, 最低库存警戒)
        Edit and delete functions; delete triggers a Chinese confirmation dialog
        Search box with button labeled "搜索"
        4. Stock In (入库管理)
        Form: select product, quantity (入库数量), remark (备注), date (入库日期)
        Submit button: "确认入库"
        On success: stock increases, message "入库成功" displayed
        Recent inbound records shown below the form
        5. Stock Out (出库管理)
        Form: select product, quantity (出库数量), remark (备注), date (出库日期)
        Validation prevents negative stock; error message: "库存不足，当前库存为 X"
        On success: stock decreases, message "出库成功" displayed
        Recent outbound records shown below the form
        6. Current Stock (库存查询)
        Chinese table headers: 产品名称, 规格型号, 单位, 当前库存, 最低库存警戒, 状态
        Status column shows "正常" or "库存不足"
        Filter: "仅显示库存不足产品"
        7. Movement History (出入库记录)
        Combined log with headers: 日期, 类型 (入库/出库), 产品, 数量, 备注
        Filter by date range, product, and type
        Simple pagination (上一页, 下一页)
        8. Low-Stock Alerts
        Dashboard warning: "警告：以下产品库存不足"
        Quick link to jump to the Stock In page
        9. Admin Settings
        Password change page with Chinese labels (原密码, 新密码, 确认新密码)
        10. Frontend pages use Chinese for all labels, buttons, and messages
        """
        requirements = {
            "summary": "Chinese inventory management CRUD application.",
            "goals": [
                "admin login",
                "dashboard",
                "product management",
                "stock in",
                "stock out",
                "current stock",
                "movement history",
                "low stock alerts",
                "admin settings",
            ],
            "constraints": ["Chinese UI", "session auth", "pagination", "search", "negative stock validation"],
            "acceptance_criteria": ["all labels in Chinese", "stock in increases stock", "stock out prevents negative inventory"],
            "risks": ["inventory calculation correctness"],
            "expected_terms": ["产品", "库存", "入库", "出库", "登录", "分页", "搜索"],
        }

        inference = infer_scale_from_requirements(requirements, inventory_request)

        self.assertEqual(inference["selected_scale"], "medium")
        self.assertIn("bounded_crud_product_capped_medium", inference["reasons"])

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
        self.assertFalse(payload["payload_over_budget"])
        self.assertFalse(payload["over_budget"])

    def test_scheduler_separates_payload_budget_from_timeout_budget(self) -> None:
        scheduler = AICallScheduler(SlowOkLLMClient())
        budget = resolve_ai_task_budget("requirements_analysis", {"target_scale": "small"})

        payload = scheduler._failed(
            agent_run_id="agent-timeout",
            run_id="run-timeout",
            role="requirements",
            job={"id": "job-timeout", "job_type": "requirements_analysis", "payload": {"scale_profile": {"target_scale": "small"}}},
            budget=budget,
            context_hash="context-hash",
            elapsed_ms=(budget.timeout_seconds * 1000) + 1,
            error="Timed out after budget.",
            guard={"ok": True, "budget_status": "within_budget", "payload_chars": 120, "body_bytes": 240, "budget_limit": 9000, "provider_body_limit_bytes": 950000},
        )

        self.assertTrue(payload["timeout_exceeded"])
        self.assertFalse(payload["payload_over_budget"])
        self.assertFalse(payload["over_budget"])

    def test_scheduler_marks_only_real_payload_guard_failure_as_over_budget(self) -> None:
        scheduler = AICallScheduler(SlowOkLLMClient(sleep_seconds=0))

        payload = scheduler.call(
            run_id="run-payload-budget",
            role="architect",
            job={"id": "job-payload-budget", "job_type": "architecture_design", "payload": {"scale_profile": {"target_scale": "small"}}},
            system_prompt="architect_agent",
            user_payload={"project": {"name": "payload-budget"}, "requirements": ["a"]},
            task_kind="architecture_design",
            required=True,
            payload_guard={"ok": False, "budget_status": "over_context_budget", "payload_chars": 13000, "body_bytes": 14000, "budget_limit": 12000, "provider_body_limit_bytes": 950000},
        )

        self.assertTrue(payload["payload_over_budget"])
        self.assertTrue(payload["over_budget"])

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

    def test_worker_projects_run_running_when_job_starts(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, MinimalOkLLMClient())
            project = service.create_project({"name": "run-projection", "title": "Run Projection", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            job = service.store.claim_job("local-workspace", "requirements", "worker-a", 30)
            self.assertIsNotNone(job)
            running = service.store.start_job(job["id"], "worker-a", 30)
            DurableWorker(service, role="requirements", worker_id="worker-a")._mark_run_running(running)
            projected = service.get_run(run["id"])

            self.assertEqual(projected["status"], "running")
            self.assertEqual(projected["continuation"]["next_action"], "job_running")
            self.assertEqual(projected["continuation"]["active_job"]["id"], job["id"])

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

    def test_small_quality_profile_keeps_100k_observability_without_blocking_large_only_gates(self) -> None:
        profile = resolve_scale_profile({"target_scale": "small"})
        gates = validate_100k_quality_gates(
            {
                "run_id": "run-small",
                "checkpoint": "package_planning_completed",
                "continuation": {"checkpoint": "package_planning_completed", "next_action": "worker_claim_package_jobs"},
                "scale_profile": profile,
                "packages": [],
                "agent_runs": [],
                "provider_health": {"ok": True, "items": []},
                "ai_payload_budget": {"ok": True, "budget_status": "within_budget", "payload_chars": 1200, "body_bytes": 1800, "budget_limit": 9000, "provider_body_limit_bytes": 950000},
                "replay_projection": {"ok": True, "source": "event_log", "projection_hash": "abc", "applied_event_count": 1, "drift": {"ok": True}},
                "checkpoint_resume": {"projection_consistent": True, "checkpoint": "package_planning_completed", "next_action": "worker_claim_package_jobs", "resumable": True, "dead_letter_count": 0},
                "recovery_trace": {"dead_letter_count": 0, "provider_failure_count": 0, "recovery_event_count": 0},
                "jobs": [],
                "waves": [],
                "ai_slots": {"slot_count": 0, "active_count": 0, "items": []},
            }
        )
        by_name = {gate["name"]: gate for gate in gates}

        self.assertEqual(by_name["parallel_execution_safety_gate"]["severity"], "info")
        self.assertTrue(by_name["parallel_execution_safety_gate"]["profile_observation_only"])
        self.assertEqual(by_name["provider_resilience_gate"]["severity"], "critical")


if __name__ == "__main__":
    unittest.main()
