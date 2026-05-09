from __future__ import annotations

import json
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v6.agent_contracts import validate_agent_contract
from dev_orchestrator.v6.llm_policy import AI_TASK_BUDGETS
from dev_orchestrator.v6.models import new_id
from dev_orchestrator.v6.profiles import resolve_scale_profile
from dev_orchestrator.v6.service import ProviderCallError, V6Orchestrator
from dev_orchestrator.v6.store import InMemoryV6Store


class WorkspaceSandbox:
    def __init__(self) -> None:
        self.path = (Path(__file__).resolve().parent.parent / "workspace" / "v6-live-scratch" / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        import shutil

        shutil.rmtree(self.path, ignore_errors=True)


class V6LiveAITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent.parent
        config = load_config(root)
        llm = config.llm
        if llm.use_mock or not llm.api_base or not llm.api_key:
            raise AssertionError("Live AI tests require a configured provider, API base, model, and API key.")
        cls.root = root
        cls.config = config
        cls.live_llm = replace(
            llm,
            model_reasoning_effort="low",
            timeout_seconds=min(max(int(llm.timeout_seconds or 90), 60), 90),
            max_tokens=min(int(llm.max_tokens or 3200), 1200),
            retry_attempts=1,
        )
        cls.client = OpenAICompatibleClient(cls.live_llm)
        cls._original_budgets = dict(AI_TASK_BUDGETS)
        for task_kind in (
            "requirements_analysis",
            "architecture_design",
            "package_planning",
            "code_generation",
            "code_review",
            "failure_analysis",
            "release_notes",
            "context_summary",
        ):
            budget = AI_TASK_BUDGETS[task_kind]
            AI_TASK_BUDGETS[task_kind] = replace(
                budget,
                max_output_tokens=min(int(budget.max_output_tokens), 650),
                timeout_seconds=min(int(budget.timeout_seconds), 45),
                reasoning_effort="low",
                retry_attempts=1,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "_original_budgets"):
            AI_TASK_BUDGETS.clear()
            AI_TASK_BUDGETS.update(cls._original_budgets)

    def _service(self, *, max_request_body_bytes: int | None = None, workspace_root: Path | None = None) -> V6Orchestrator:
        llm_config = self.live_llm
        if max_request_body_bytes is not None:
            llm_config = replace(llm_config, max_request_body_bytes=max_request_body_bytes)
        service = V6Orchestrator(
            store=InMemoryV6Store(),
            workspace_root=workspace_root or self.root / "workspace",
            tenant_id="local-workspace",
            llm_client=OpenAICompatibleClient(llm_config),
        )
        service.bootstrap()
        return service

    def _invoke_live_agent(self, service: V6Orchestrator, project: dict, run: dict, *, role: str, task_kind: str, system_prompt: str, user_payload: dict) -> dict:
        last_error: ProviderCallError | None = None
        for attempt in range(3):
            job = {
                "id": new_id(),
                "job_type": task_kind,
                "role": role,
                "run_id": run["id"],
                "attempts": attempt,
                "max_attempts": 3,
                "payload": {
                    "scale_profile": {
                        **resolve_scale_profile({"target_scale": "xlarge_100k"}),
                        "ai_retry_attempts": 1,
                    }
                },
            }
            try:
                return service._invoke_json_agent(
                    project,
                    run,
                    role=role,
                    job=job,
                    task_kind=task_kind,
                    system_prompt=system_prompt,
                    user_payload=user_payload,
                    required=True,
                )
            except ProviderCallError as exc:
                last_error = exc
                if not bool(getattr(exc, "retryable", False)):
                    raise
                time.sleep(2 + attempt)
        assert last_error is not None
        raise last_error

    def _live_chat(self, *, system_prompt: str, messages: list[dict], timeout_override: int = 90, max_tokens_override: int = 64) -> str:
        last_error: LLMError | None = None
        for attempt in range(3):
            try:
                return self.client.chat(
                    system_prompt,
                    messages,
                    timeout_override=timeout_override,
                    max_tokens_override=max_tokens_override,
                    retry_attempts_override=4,
                )
            except LLMError as exc:
                last_error = exc
                time.sleep(2 + attempt)
        assert last_error is not None
        raise last_error

    def _live_json(self, *, system_prompt: str, messages: list[dict], timeout_override: int = 90, max_tokens_override: int = 64) -> dict:
        last_error: Exception | None = None
        for attempt in range(3):
            response_text = self._live_chat(
                system_prompt=system_prompt,
                messages=messages,
                timeout_override=timeout_override,
                max_tokens_override=max_tokens_override,
            )
            try:
                return json.loads(response_text)
            except json.JSONDecodeError as exc:
                last_error = exc
                time.sleep(1 + attempt)
        assert last_error is not None
        raise last_error

    def test_live_provider_connectivity_and_request_preview(self) -> None:
        preview = self.client.build_request_preview(
            "Reply with strict JSON only.",
            [{"role": "user", "content": "Return {\"ok\": true, \"message\": \"connected\"}."}],
            max_tokens_override=64,
        )
        response_text = self._live_chat(
            system_prompt="Reply with strict JSON only.",
            messages=[{"role": "user", "content": "Return {\"ok\": true, \"message\": \"connected\"}."}],
        )

        self.assertIn("connected", response_text.lower())
        self.assertIn("payload", preview)
        self.assertLessEqual(len(json.dumps(preview["payload"], ensure_ascii=True).encode("utf-8")), int(self.config.llm.max_request_body_bytes or 950000))

    def test_live_ai_core_roles_return_valid_project_specific_contracts(self) -> None:
        with WorkspaceSandbox() as root:
            service = self._service(workspace_root=root)
            project = service.create_project(
                {
                    "name": "live-ai-core",
                    "title": "Live AI Core",
                    "description": "Build a knowledge base for articles, categories, tags, and search.",
                    "project_path": "live-ai-core",
                    "config": {"target_scale": "xlarge_100k"},
                }
            )
            run = service.create_run(project["id"], "Build a knowledge base for articles, categories, tags, and search.")

            requirements = self._invoke_live_agent(
                service,
                project,
                run,
                role="requirements",
                task_kind="requirements_analysis",
                system_prompt="You are requirements_agent. Return compact strict JSON only with status, summary, goals, users, constraints, acceptance_criteria, missing_information, risks, expected_terms. Keep every list to at most 2 items.",
                user_payload={"project": service._project_prompt(project), "requirements_text": project["description"]},
            )
            self.assertTrue(validate_agent_contract("requirements_analysis", requirements)["ok"])
            self.assertIn("knowledge base", requirements.get("summary", "").lower())

            architecture = self._invoke_live_agent(
                service,
                project,
                run,
                role="architect",
                task_kind="architecture_design",
                system_prompt="You are architect_agent. Return compact strict JSON only with architecture_summary, technology_choices, project_layout, module_boundaries, integration_contracts. project_layout must include source_root, delivery_root, entrypoints, directories, validation_commands. Keep every list to at most 2 items.",
                user_payload={"project": service._project_prompt(project), "requirements_analysis": requirements},
            )
            self.assertTrue(validate_agent_contract("architecture_design", architecture)["ok"])
            self.assertIn("project_layout", architecture)

            package_plan = self._invoke_live_agent(
                service,
                project,
                run,
                role="planner",
                task_kind="package_planning",
                system_prompt="You are planner_agent. Return compact strict JSON only with waves and packages. Create exactly one backend package using allowed_paths [\"src/**\"]. Keep every list to at most 2 items.",
                user_payload={"project": service._project_prompt(project), "requirements_analysis": requirements, "architecture_design": architecture},
            )
            self.assertTrue(validate_agent_contract("package_planning", package_plan)["ok"])
            self.assertTrue(package_plan.get("packages"))

            code_patch = self._invoke_live_agent(
                service,
                project,
                run,
                role="backend",
                task_kind="code_generation",
                system_prompt="You are backend_agent. Return compact strict JSON only using the file-manifest patch protocol. Return exactly one file in files: path src/app.py, action create, content containing a tiny project-specific Python module for articles and tags. Return agent, status, summary, files, commands, evidence, risks. Keep every list to at most 2 items.",
                user_payload={"project": service._project_prompt(project), "package": {"package_key": "PKG-BACKEND", "role": "backend", "wave_key": "WAVE-001", "allowed_paths": ["src/**"], "objective": "Build the article domain backend."}, "context": {"requirements": requirements, "architecture": architecture, "package_plan": package_plan}},
            )
            self.assertTrue(validate_agent_contract("code_generation", code_patch)["ok"])
            self.assertTrue(code_patch.get("files"))

            review = self._invoke_live_agent(
                service,
                project,
                run,
                role="review",
                task_kind="code_review",
                system_prompt="You are review_agent. Return compact strict JSON only with ok, status, findings, required_fixes, requirement_coverage, security_notes. Keep every list to at most 2 items.",
                user_payload={"project": service._project_prompt(project), "files": [{"path": "src/app.py", "loc": 1}], "packages": package_plan.get("packages", []), "requirements": requirements, "contract_index": {}},
            )
            self.assertTrue(validate_agent_contract("code_review", review)["ok"])
            self.assertIn("ok", review)

            repair = self._invoke_live_agent(
                service,
                project,
                run,
                role="repair",
                task_kind="failure_analysis",
                system_prompt="You are repair_agent. Return compact strict JSON only. Return exactly one file in files: path src/app.py, action replace, content fixing the live AI repair check without changing unrelated files. Return agent, status, summary, files, commands, evidence, risks. Keep every list to at most 2 items.",
                user_payload={"failure": {"reason": "live_ai_repair_check"}, "layout": {"source_root": "src", "delivery_root": "web"}, "files": [{"path": "src/app.py", "loc": 1}], "artifacts": [], "contract_index": {}},
            )
            self.assertTrue(validate_agent_contract("failure_analysis", repair)["ok"])
            self.assertIsInstance(repair.get("files"), list)

            release = self._invoke_live_agent(
                service,
                project,
                run,
                role="release",
                task_kind="release_notes",
                system_prompt="You are release_agent. Return compact strict JSON only with release_summary, deploy_steps, validation_steps, rollback, changed_files. Keep every list to at most 2 items.",
                user_payload={"project": service._project_prompt(project), "layout": {"source_root": "src", "delivery_root": "web"}, "quality": {"ok": True}, "files": [{"path": "src/app.py", "loc": 1}]},
            )
            self.assertTrue(validate_agent_contract("release_notes", release)["ok"])
            self.assertTrue(release.get("release_summary", "").strip())

            ai_calls = service.ai_calls(run["id"])
            self.assertGreaterEqual(len(ai_calls), 7)
            self.assertTrue(all(call["live_provider"] for call in ai_calls))
            self.assertTrue(all(int(call["body_bytes"]) <= int(call["provider_body_limit_bytes"]) for call in ai_calls))

    def test_live_ai_oversize_payload_is_trimmed_and_compressed(self) -> None:
        with WorkspaceSandbox() as root:
            service = self._service(max_request_body_bytes=12000)
            project = service.create_project(
                {
                    "name": "live-ai-oversize",
                    "title": "Live AI Oversize",
                    "description": "Build a knowledge base with large context.",
                    "target_scale": "xlarge_100k",
                }
            )
            run = service.create_run(project["id"])
            profile = resolve_scale_profile({"target_scale": "xlarge_100k"})
            huge_payload = {
                f"section_{index:03d}": {
                    "summary": f"section-{index}-summary-" + ("x" * 120),
                    "items": [f"item-{index}-{inner}" for inner in range(20)],
                    "constraints": [f"constraint-{index}-{inner}" for inner in range(10)],
                }
                for index in range(240)
            }
            try:
                prepared_payload, budget_report = service._prepare_ai_payload(
                    project,
                    run,
                    role="review",
                    job={"id": new_id(), "job_type": "code_review", "payload": {"scale_profile": profile}},
                    task_kind="code_review",
                    system_prompt="You are review_agent. Return strict JSON only with ok, status, findings, required_fixes, requirement_coverage, security_notes.",
                    user_payload={
                        "project": service._project_prompt(project),
                        "code_index": huge_payload,
                        "contract_index": {"contracts": [{"name": f"contract-{index}", "owner_package": f"PKG-{index:03d}"} for index in range(240)]},
                        "change_memory": [{"artifact_id": str(index), "kind": "patch_set", "path": f"src/file-{index}.py", "changed_files": [f"src/file-{index}.py"]} for index in range(240)],
                    },
                )
            except ProviderCallError:
                final_run = service.get_run(run["id"])
                events = service.list_events(run["id"])
                self.assertEqual(final_run["status"], "recovering")
                self.assertTrue(any(event["kind"] == "ai_payload_oversize_blocked" for event in events))
                return

            self.assertLessEqual(int(budget_report["payload_chars"]), int(budget_report["budget_limit"]))
            self.assertLessEqual(int(budget_report["body_bytes"]), int(budget_report["provider_body_limit_bytes"]))
            self.assertIn(budget_report["budget_status"], {"trimmed_within_budget", "ai_compressed_within_budget", "within_budget"})
            self.assertTrue(prepared_payload)
