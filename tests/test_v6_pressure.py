from __future__ import annotations

import shutil
from types import SimpleNamespace
from pathlib import Path
import unittest

from dev_orchestrator.llm_client import LLMError
from dev_orchestrator.v6.pressure import FaultInjectingLLMClient, build_pressure_benchmark_spec
from dev_orchestrator.v6.service import V6Orchestrator
from dev_orchestrator.v6.store import InMemoryV6Store
from dev_orchestrator.v6.llm_policy import get_ai_task_budget, resolve_ai_task_budget


class DummyPressureDelegate:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            provider_profile="custom-responses-compatible",
            api_base="https://pressure.test/v1",
            api_key="pressure-key",
            model="pressure-model",
            wire_api="responses",
            max_request_body_bytes=950000,
            use_mock=False,
        )
        self.chat_calls: list[dict] = []

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        self.chat_calls.append({"system_prompt": system_prompt, "messages": messages, "kwargs": kwargs})
        return "{\"ok\": true, \"summary\": \"delegated\"}"

    def build_request_preview(self, *args, **kwargs) -> dict:
        return {"ok": True, "args": args, "kwargs": kwargs, "payload": {"messages": kwargs.get("messages", [])}}

    def test_connection(self) -> dict:
        return {"ok": True, "message": "connected"}


class V6PressureTests(unittest.TestCase):
    def test_benchmark_spec_is_large_and_project_specific(self) -> None:
        spec = build_pressure_benchmark_spec("runtime/pressure-tests/unit-test/main")

        self.assertEqual(spec.project_payload["config"]["target_scale"], "xlarge_100k")
        self.assertEqual(spec.project_payload["config"]["effective_loc_target"], 100000)
        self.assertGreater(len(spec.requirements_text), 10000)
        self.assertIn("100k Real AI Pressure Benchmark", spec.requirements_text)
        self.assertIn("requirements_analysis", spec.fault_schedule)
        self.assertIn("code_review", spec.fault_schedule)
        self.assertGreater(len(spec.oversize_payload["code_index"]), 100)
        rendered = spec.to_dict()
        self.assertGreater(rendered["requirements_chars"], 10000)
        self.assertTrue(rendered["requirements_hash"])

    def test_100k_core_task_budgets_expand_beyond_small_task_defaults(self) -> None:
        profile = {
            "name": "xlarge_100k",
            "target_loc_hint": 100000,
            "context_budget_chars": 76000,
            "ai_retry_attempts": 4,
        }
        requirements_budget = resolve_ai_task_budget("requirements_analysis", profile)
        architecture_budget = resolve_ai_task_budget("architecture_design", profile)

        self.assertGreater(requirements_budget.max_input_chars, get_ai_task_budget("requirements_analysis").max_input_chars)
        self.assertGreater(architecture_budget.max_input_chars, get_ai_task_budget("architecture_design").max_input_chars)
        self.assertGreaterEqual(requirements_budget.retry_attempts, 4)

    def test_fault_injecting_client_raises_then_delegates(self) -> None:
        delegate = DummyPressureDelegate()
        client = FaultInjectingLLMClient(delegate, {"requirements_analysis": ("HTTP 429: rate limit",)})

        with self.assertRaises(LLMError):
            client.chat("You are requirements_agent. Return strict JSON only.", [{"role": "user", "content": "{}"}])

        response = client.chat("You are requirements_agent. Return strict JSON only.", [{"role": "user", "content": "{}"}])

        self.assertIn("delegated", response)
        self.assertEqual(len(delegate.chat_calls), 1)
        self.assertTrue(client.test_connection()["ok"])

    def test_ai_calls_surface_body_budget_fields(self) -> None:
        workspace_root = Path(__file__).resolve().parent / ".tmp_v6_pressure_workspace"
        shutil.rmtree(workspace_root, ignore_errors=True)
        try:
            service = V6Orchestrator(store=InMemoryV6Store(), workspace_root=workspace_root, llm_client=None)
            service.bootstrap()
            project = service.create_project(
                {
                    "name": "ai-call-budget-test",
                    "title": "AI Call Budget Test",
                    "project_path": "runtime/pressure-tests/ai-call-budget-test",
                    "config": {"target_scale": "small"},
                }
            )
            run = service.create_run(project["id"], "Budget test run.")
            service._record_json(
                project,
                run,
                "agent_run",
                "agent-run.json",
                {
                    "agent_run_id": "agent-run-001",
                    "payload_chars": 120,
                    "payload_bytes": 128,
                    "body_bytes": 256,
                    "estimated_tokens": 30,
                    "budget_status": "within_budget",
                    "compression_applied": True,
                    "summary_agent_run_id": "summary-run-001",
                    "budget_limit": 400,
                    "provider_body_limit_bytes": 1000,
                    "live_provider": True,
                    "model": "pressure-model",
                    "model_tier": "standard",
                    "payload_budget": {"budget_limit": 400, "provider_body_limit_bytes": 1000},
                },
                {"role": "review", "job_id": "job-1", "task_kind": "code_review"},
            )
            ai_calls = service.ai_calls(run["id"])

            self.assertEqual(len(ai_calls), 1)
            self.assertEqual(ai_calls[0]["body_bytes"], 256)
            self.assertEqual(ai_calls[0]["budget_limit"], 400)
            self.assertEqual(ai_calls[0]["provider_body_limit_bytes"], 1000)
            self.assertEqual(ai_calls[0]["summary_agent_run_id"], "summary-run-001")
            self.assertTrue(ai_calls[0]["live_provider"])
            self.assertEqual(ai_calls[0]["model"], "pressure-model")
            self.assertIn("wait_ms", ai_calls[0])
            self.assertIn("slot_id", ai_calls[0])
            self.assertIn("concurrency_limited", ai_calls[0])
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
