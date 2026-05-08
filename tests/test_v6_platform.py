from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import LLMError
from dev_orchestrator.v6 import api as v6_api
from dev_orchestrator.v6.agent_contracts import validate_agent_contract
from dev_orchestrator.v6.api import build_app
from dev_orchestrator.v6.code_indexer import build_code_index
from dev_orchestrator.v6.contract_store import build_contract_index
from dev_orchestrator.v6.patch_runtime import TransactionalPatchRuntime
from dev_orchestrator.v6.release_quality import validate_ai_native_project
from dev_orchestrator.v6.runtime import AgentFileRuntime, PatchValidationError
from dev_orchestrator.v6.service import V6Orchestrator
from dev_orchestrator.v6.store import InMemoryV6Store
from dev_orchestrator.v6.test_runner import command_safety, run_validation_commands
from dev_orchestrator.v6.worker import DurableWorker, WorkerStatusRegistry

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None


class WorkspaceSandbox:
    def __init__(self) -> None:
        self.path = (Path(__file__).resolve().parent.parent / "workspace" / "v6-test-scratch" / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class NativeFakeLLMClient:
    class Config:
        model = "fake-agent-native"

    def __init__(self, layout_variant: str = "app") -> None:
        self.config = self.Config()
        self.calls: list[dict] = []
        self.layout_variant = layout_variant

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        payload = json.loads(messages[0]["content"])
        task = self._task(system_prompt)
        self.calls.append({"task": task, "system_prompt": system_prompt, "payload": payload, "kwargs": kwargs})
        if task == "requirements_analysis":
            return json.dumps({"status": "GO", "summary": "AI native knowledge base", "goals": ["articles", "search"], "acceptance_criteria": ["usable app"], "expected_terms": ["article", "category", "tag"]})
        if task == "architecture_design":
            if self.layout_variant == "src":
                layout = {"source_root": "src", "delivery_root": "deploy", "entrypoints": ["deploy/index.html"], "directories": [{"path": "src"}, {"path": "deploy"}, {"path": "spec"}], "validation_commands": ["python -c \"print('ok')\""]}
            else:
                layout = {"source_root": "app", "delivery_root": "web", "entrypoints": ["web/index.html"], "directories": [{"path": "app"}, {"path": "web"}, {"path": "database"}, {"path": "tests"}], "validation_commands": ["python -c \"print('ok')\""]}
            return json.dumps({"architecture_summary": "AI planned layout", "technology_choices": ["plain web"], "project_layout": layout, "module_boundaries": [], "integration_contracts": [{"type": "module_boundary", "name": "knowledge-base-boundary", "owner_package": "PKG-WEB"}]})
        if task == "package_planning":
            source_root = payload["architecture_design"]["project_layout"]["source_root"]
            delivery_root = payload["architecture_design"]["project_layout"]["delivery_root"]
            test_root = "spec" if source_root == "src" else "tests"
            packages = [
                {"package_key": "PKG-DATA", "role": "db", "domain": "data", "subsystem": "schema", "wave_key": "WAVE-001", "depends_on": [], "allowed_paths": [f"{source_root}/data/**"], "objective": "Create AI generated domain data.", "expected_outputs": [{"type": "db_table", "name": "articles", "owner_package": "PKG-DATA"}]},
                {"package_key": "PKG-WEB", "role": "frontend", "domain": "ui", "subsystem": "browser", "wave_key": "WAVE-001", "depends_on": [], "allowed_paths": [f"{delivery_root}/**"], "objective": "Create AI generated UI.", "expected_outputs": [{"type": "ui_route", "name": "/", "owner_package": "PKG-WEB"}]},
                {"package_key": "PKG-QA", "role": "qa", "domain": "qa", "subsystem": "tests", "wave_key": "WAVE-002", "depends_on": ["PKG-DATA", "PKG-WEB"], "allowed_paths": [f"{test_root}/**"], "objective": "Create AI generated tests."},
                {"package_key": "PKG-SEC", "role": "security", "domain": "security", "subsystem": "review", "wave_key": "WAVE-002", "depends_on": ["PKG-WEB"], "allowed_paths": [f"{source_root}/security/**"], "objective": "Create AI generated security notes."},
            ]
            return json.dumps({"waves": [{"wave_key": "WAVE-001", "sequence": 1}, {"wave_key": "WAVE-002", "sequence": 2}], "packages": packages})
        if task in {"code_generation", "test_generation", "security_review"}:
            package = payload["package"]
            allowed = package["allowed_paths"][0].replace("**", "").rstrip("/")
            filename = {
                "db": "schema.json",
                "frontend": "index.html",
                "qa": "ai_flow_test.md",
                "security": "threat-model.md",
            }.get(package["role"], "module.txt")
            content = f"AI agent {package['role']} produced {package['package_key']} for articles categories tags search.\n"
            if filename.endswith(".json"):
                content = json.dumps({"tables": ["articles"], "route": "/api/articles"}, indent=2)
            if filename.endswith(".html"):
                content = "<!doctype html><title>AI Knowledge Base</title><main>articles categories tags search</main>\n"
            return json.dumps({"agent": package["role"], "status": "GO", "summary": "created files", "files": [{"path": f"{allowed}/{filename}", "action": "create", "content": content}], "commands": [], "evidence": ["file generated by AI fake"], "risks": []})
        if task == "integration_merge":
            return json.dumps({"agent": "integration", "status": "GO", "summary": "no merge needed", "files": [], "commands": [], "evidence": ["packages integrated"], "risks": []})
        if task == "code_review":
            return json.dumps({"ok": True, "status": "GO", "findings": [], "required_fixes": [], "requirement_coverage": "covered"})
        if task == "release_notes":
            return json.dumps({"release_summary": "AI generated project is ready", "deploy_steps": ["open delivery root"], "validation_steps": ["inspect generated files"], "rollback": ["restore previous artifact"]})
        if task == "failure_analysis":
            return json.dumps({"agent": "repair", "status": "GO", "summary": "repair patch", "files": [{"path": "web/repair.txt", "action": "replace", "content": "repair"}], "commands": [], "evidence": ["repair generated"], "risks": []})
        raise AssertionError(f"unknown task {task}")

    def _task(self, prompt: str) -> str:
        for task in ("requirements_analysis", "architecture_design", "package_planning", "code_generation", "test_generation", "security_review", "integration_merge", "code_review", "release_notes", "failure_analysis"):
            if task in prompt:
                return task
        if "requirements_agent" in prompt:
            return "requirements_analysis"
        if "architect_agent" in prompt:
            return "architecture_design"
        if "planner_agent" in prompt:
            return "package_planning"
        if "integration_agent" in prompt:
            return "integration_merge"
        if "review_agent" in prompt:
            return "code_review"
        if "release_agent" in prompt:
            return "release_notes"
        if "repair_agent" in prompt:
            return "failure_analysis"
        return "code_generation"


class FailingLLMClient:
    class Config:
        model = "failing-v6-model"

    def __init__(self) -> None:
        self.config = self.Config()

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        raise LLMError("simulated upstream failure")


class ContractCheckLLMClient:
    def __init__(self, config) -> None:
        self.config = config

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        return json.dumps({"done": True, "summary": "contract-check", "artifacts": ["docs/contract-check.md"], "tool_calls": []})


class InvalidThenValidLLMClient(NativeFakeLLMClient):
    def __init__(self) -> None:
        super().__init__()
        self.invalid_returned = False

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        task = self._task(system_prompt)
        if task == "requirements_analysis" and not self.invalid_returned:
            self.invalid_returned = True
            return json.dumps({"summary": "missing status"})
        return super().chat(system_prompt, messages, **kwargs)


class AlwaysInvalidLLMClient(NativeFakeLLMClient):
    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        task = self._task(system_prompt)
        if task == "requirements_analysis":
            return "not json"
        return super().chat(system_prompt, messages, **kwargs)


def build_service(root: Path, llm_client=None) -> V6Orchestrator:
    store = InMemoryV6Store()
    service = V6Orchestrator(store=store, workspace_root=root / "workspace", tenant_id="local-workspace", llm_client=llm_client)
    service.bootstrap()
    return service


def drain_workers(service: V6Orchestrator, limit: int = 120) -> list[dict]:
    roles = ["requirements", "architect", "planner", "db", "backend", "frontend", "qa", "security", "integration", "review", "repair", "release", "docs"]
    results: list[dict] = []
    for _ in range(limit):
        claimed = False
        for role in roles:
            result = DurableWorker(service, role=role, worker_id=f"test-{role}", lease_seconds=30).run_once().to_dict()
            if result["claimed"]:
                claimed = True
                results.append(result)
        if not claimed:
            return results
    raise AssertionError("workers did not drain within limit")


class V6AgentNativeTests(unittest.TestCase):
    def test_ai_native_pipeline_generates_layout_files_patch_sets_and_release(self) -> None:
        with WorkspaceSandbox() as root:
            fake = NativeFakeLLMClient()
            service = build_service(root, fake)
            project = service.create_project({"name": "agent-native", "title": "Agent Native", "description": "Build a knowledge base with articles, categories, tags and search."})
            run = service.create_run(project["id"])

            results = drain_workers(service)
            final_run = service.get_run(run["id"])
            artifacts = service.list_artifacts(run["id"])
            project_root = root / "workspace" / "agent-native"

            self.assertGreaterEqual(len(results), 10)
            self.assertEqual(final_run["status"], "release_ready")
            self.assertTrue((project_root / "app" / "data" / "schema.json").exists())
            self.assertTrue((project_root / "web" / "index.html").exists())
            self.assertTrue((project_root / "tests" / "ai_flow_test.md").exists())
            self.assertTrue(any(item["kind"] == "project_layout" for item in artifacts))
            self.assertTrue(any(item["kind"] == "package_dag" for item in artifacts))
            self.assertTrue(any(item["kind"] == "patch_set" for item in artifacts))
            self.assertTrue(any(item["kind"] == "agent_contract_report" for item in artifacts))
            self.assertTrue(any(item["kind"] == "patch_transaction" for item in artifacts))
            self.assertTrue(any(item["kind"] == "test_execution_report" for item in artifacts))
            self.assertTrue(any(item["kind"] == "code_index" for item in artifacts))
            self.assertTrue(any(item["kind"] == "contract_index" for item in artifacts))
            self.assertTrue(any(item["kind"] == "release_notes" for item in artifacts))
            self.assertTrue(any(item["kind"] == "deploy_guide" for item in artifacts))
            quality = next(item for item in reversed(artifacts) if item["kind"] == "quality_report")["payload"]
            gate_names = {gate["name"] for gate in quality["gates"]}
            self.assertTrue({"test_command_safety_gate", "test_execution_gate", "validation_command_coverage_gate", "code_index_freshness_gate", "cross_package_contract_gate"}.issubset(gate_names))
            task_kinds = {(item["payload"] or {}).get("task_kind") for item in artifacts if item["kind"] == "agent_run"}
            self.assertTrue({"requirements_analysis", "architecture_design", "package_planning", "code_generation", "test_generation", "security_review", "integration_merge", "code_review", "release_notes"}.issubset(task_kinds))

    def test_agent_contract_violation_retries_once_then_recovers(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, InvalidThenValidLLMClient())
            project = service.create_project({"name": "contract-retry", "title": "Contract Retry", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            result = DurableWorker(service, role="requirements", worker_id="requirements", lease_seconds=30).run_once().to_dict()
            artifacts = service.list_artifacts(run["id"])

            self.assertEqual(result["status"], "completed")
            self.assertEqual(service.get_run(run["id"])["checkpoint"], "requirements_completed")
            self.assertTrue(any(item["kind"] == "agent_contract_violation" for item in artifacts))
            report = next(item for item in reversed(artifacts) if item["kind"] == "agent_contract_report")["payload"]
            self.assertTrue(report["ok"])
            self.assertEqual(report["violation_count"], 1)

    def test_agent_contract_second_failure_blocks_run(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, AlwaysInvalidLLMClient())
            project = service.create_project({"name": "contract-block", "title": "Contract Block", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            result = DurableWorker(service, role="requirements", worker_id="requirements", lease_seconds=30).run_once().to_dict()
            final_run = service.get_run(run["id"])
            artifacts = service.list_artifacts(run["id"])

            self.assertEqual(result["status"], "failed")
            self.assertEqual(final_run["status"], "blocked")
            self.assertEqual(final_run["continuation"]["next_action"], "repair_agent_contract")
            self.assertEqual(len([item for item in artifacts if item["kind"] == "agent_contract_violation"]), 2)

    def test_ai_can_choose_a_different_project_directory_shape(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient(layout_variant="src"))
            project = service.create_project({"name": "src-layout", "title": "Src Layout", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            drain_workers(service)
            layout = next(item for item in service.list_artifacts(run["id"]) if item["kind"] == "project_layout")["payload"]
            project_root = root / "workspace" / "src-layout"

            self.assertEqual(layout["source_root"], "src")
            self.assertEqual(layout["delivery_root"], "deploy")
            self.assertTrue((project_root / "src" / "data" / "schema.json").exists())
            self.assertTrue((project_root / "deploy" / "index.html").exists())
            self.assertTrue((project_root / "spec" / "ai_flow_test.md").exists())

    def test_project_path_uses_configured_folder_without_extra_projects_segment(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            default_project = service.create_project({"name": "path-default", "title": "Path Default", "description": "Build a knowledge base."})
            relative_project = service.create_project({"name": "path-relative", "title": "Path Relative", "description": "Build a knowledge base.", "project_path": "custom-output"})
            absolute_target = root / "external-output"
            absolute_project = service.create_project({"name": "path-absolute", "title": "Path Absolute", "description": "Build a knowledge base.", "project_path": str(absolute_target)})

            self.assertEqual(service.runtime.project_root(default_project), root / "workspace" / "path-default")
            self.assertEqual(service.runtime.project_root(relative_project), root / "workspace" / "custom-output")
            self.assertEqual(service.runtime.project_root(absolute_project), absolute_target)
            self.assertEqual(default_project["resolved_project_root"], str(root / "workspace" / "path-default"))

    def test_project_path_rejects_unusable_windows_absolute_path_in_posix_runtime(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            if service.runtime._can_use_configured_project_path("E:\\ProgramProjects\\Target"):
                self.skipTest("Windows absolute paths are valid on this runtime")
            with self.assertRaises(RuntimeError):
                service.create_project({"name": "bad-path", "title": "Bad Path", "description": "Build a knowledge base.", "project_path": "E:\\ProgramProjects\\Target"})

    def test_project_path_maps_windows_workspace_path_for_container_runtime(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            if service.runtime._can_use_configured_project_path("E:\\ProgramProjects\\AI_Agent\\workspace\\projects\\mapped-output"):
                self.skipTest("Windows absolute paths are native on this runtime")
            project = service.create_project({
                "name": "mapped-output",
                "title": "Mapped Output",
                "description": "Build a knowledge base.",
                "project_path": "E:\\ProgramProjects\\AI_Agent\\workspace\\projects\\mapped-output",
            })

            self.assertEqual(service.runtime.project_root(project), root / "workspace" / "mapped-output")
            self.assertEqual(project["project_path_status"]["source"], "windows_workspace_mapped_path")
            legacy_project = service.create_project({
                "name": "legacy-mapped-output",
                "title": "Legacy Mapped Output",
                "description": "Build a knowledge base.",
                "project_path": "E:\\ProgramProjects\\AI_Agent\\workspace\\projects\\projects\\legacy-mapped-output",
            })
            broad_workspace_project = service.create_project({
                "name": "broad-mapped-output",
                "title": "Broad Mapped Output",
                "description": "Build a knowledge base.",
                "project_path": "E:\\ProgramProjects\\AI_Agent\\workspace\\broad-mapped-output",
            })

            self.assertEqual(service.runtime.project_root(legacy_project), root / "workspace" / "legacy-mapped-output")
            self.assertEqual(service.runtime.project_root(broad_workspace_project), root / "workspace" / "broad-mapped-output")

    def test_runtime_rejects_path_escape_and_forbidden_parts(self) -> None:
        with WorkspaceSandbox() as root:
            runtime = AgentFileRuntime(root / "workspace" / "projects")
            project = {"id": "p1", "name": "unsafe", "title": "Unsafe", "project_path": "", "config": {}}
            layout = {"source_root": "src", "delivery_root": "dist", "directories": [{"path": "src"}, {"path": "dist"}], "entrypoints": [], "validation_commands": []}

            with self.assertRaises(PatchValidationError):
                runtime.apply_file_manifest(project=project, layout=layout, agent_output={"files": [{"path": "../escape.txt", "action": "create", "content": "x"}]}, allowed_paths=["**"])
            with self.assertRaises(PatchValidationError):
                runtime.apply_file_manifest(project=project, layout=layout, agent_output={"files": [{"path": ".git/config", "action": "create", "content": "x"}]}, allowed_paths=["**"])

    def test_transactional_patch_rejects_whole_patch_on_path_violation(self) -> None:
        with WorkspaceSandbox() as root:
            runtime = AgentFileRuntime(root / "workspace" / "projects")
            patch_runtime = TransactionalPatchRuntime(runtime)
            project = {"id": "p1", "name": "transactional", "title": "Transactional", "project_path": "", "config": {}}
            layout = {"source_root": "src", "delivery_root": "dist", "directories": [{"path": "src"}, {"path": "dist"}], "entrypoints": [], "validation_commands": []}

            with self.assertRaises(PatchValidationError):
                patch_runtime.apply_file_manifest_transaction(
                    run_id="run-1",
                    project=project,
                    layout=layout,
                    agent_output={"files": [{"path": "src/ok.txt", "action": "create", "content": "ok"}, {"path": "../escape.txt", "action": "create", "content": "bad"}]},
                    allowed_paths=["**"],
                )

            self.assertFalse((runtime.project_root(project) / "src" / "ok.txt").exists())

    def test_transactional_patch_reports_file_conflict_without_overwrite(self) -> None:
        with WorkspaceSandbox() as root:
            runtime = AgentFileRuntime(root / "workspace" / "projects")
            patch_runtime = TransactionalPatchRuntime(runtime)
            project = {"id": "p1", "name": "conflict", "title": "Conflict", "project_path": "", "config": {}}
            layout = {"source_root": "src", "delivery_root": "src", "directories": [{"path": "src"}], "entrypoints": [], "validation_commands": []}
            project_root = runtime.project_root(project)
            (project_root / "src").mkdir(parents=True)
            (project_root / "src" / "same.txt").write_text("base", encoding="utf-8")

            result = patch_runtime.apply_file_manifest_transaction(
                run_id="run-1",
                project=project,
                layout=layout,
                agent_output={"files": [{"path": "src/same.txt", "action": "create", "content": "new"}]},
                allowed_paths=["**"],
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["conflicts"][0]["reason"], "file_exists")
            self.assertEqual((project_root / "src" / "same.txt").read_text(encoding="utf-8"), "base")

    def test_validation_command_runner_executes_blocks_and_times_out(self) -> None:
        with WorkspaceSandbox() as root:
            project_root = root / "project"
            project_root.mkdir()

            passed = run_validation_commands(project_root, ["python -c \"print('ok')\""], timeout_seconds=10)
            blocked = run_validation_commands(project_root, ["git reset --hard"], timeout_seconds=10)
            timed_out = run_validation_commands(project_root, ["python -c \"import time; time.sleep(2)\""], timeout_seconds=1)

            self.assertTrue(passed["ok"])
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked["results"][0]["status"], "blocked")
            self.assertFalse(timed_out["ok"])
            self.assertEqual(timed_out["results"][0]["status"], "timeout")
            self.assertFalse(command_safety("python -c \"open('/tmp/x','w')\"")["ok"])

    def test_code_and_contract_index_extract_routes_tables_and_missing_consumers(self) -> None:
        with WorkspaceSandbox() as root:
            project_root = root / "project"
            (project_root / "src").mkdir(parents=True)
            (project_root / "src" / "app.py").write_text("from fastapi import FastAPI\napp=FastAPI()\n@app.get('/api/articles')\ndef list_articles():\n    pass\n", encoding="utf-8")
            (project_root / "src" / "schema.sql").write_text("CREATE TABLE articles (id int);\n", encoding="utf-8")
            package_plan = {"packages": [{"package_key": "PKG-BACKEND", "allowed_paths": ["src/**"], "expected_outputs": [], "contract_refs": ["missing-contract"]}]}

            code_index = build_code_index(project_root)
            contract_index = build_contract_index(architecture={}, package_plan=package_plan, patch_sets=[], code_index=code_index)

            self.assertTrue(any("/api/articles" in file_info["routes"] for file_info in code_index["files"]))
            self.assertTrue(any("articles" in file_info["db_tables"] for file_info in code_index["files"]))
            self.assertTrue(any(contract["type"] == "api_route" for contract in contract_index["contracts"]))
            self.assertTrue(contract_index["missing_consumers"])

    def test_agent_contract_schema_rules_reject_missing_content_action_and_layout(self) -> None:
        self.assertFalse(validate_agent_contract("file_manifest_patch", {"files": [{"path": "src/a.py", "action": "create"}]})["ok"])
        self.assertFalse(validate_agent_contract("file_manifest_patch", {"files": [{"path": "src/a.py", "action": "update", "content": "x"}]})["ok"])
        self.assertFalse(validate_agent_contract("architecture_design", {"project_layout": {"source_root": "src"}})["ok"])
        self.assertFalse(validate_agent_contract("code_review", {"ok": "true"})["ok"])

    def test_dag_dependencies_gate_downstream_packages(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            project = service.create_project({"name": "dag-test", "title": "Dag Test", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            DurableWorker(service, role="requirements", worker_id="requirements", lease_seconds=30).run_once()
            DurableWorker(service, role="architect", worker_id="architect", lease_seconds=30).run_once()
            DurableWorker(service, role="planner", worker_id="planner", lease_seconds=30).run_once()
            qa_claim = service.store.claim_job("local-workspace", "qa", "qa-too-early", 30)
            self.assertIsNone(qa_claim)

            DurableWorker(service, role="db", worker_id="db", lease_seconds=30).run_once()
            DurableWorker(service, role="frontend", worker_id="frontend", lease_seconds=30).run_once()
            qa_late = service.store.claim_job("local-workspace", "qa", "qa-ready", 30)
            self.assertIsNotNone(qa_late)

    def test_llm_failure_blocks_run_and_records_agent_run(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, FailingLLMClient())
            project = service.create_project({"name": "llm-failure", "title": "LLM Failure", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            result = DurableWorker(service, role="requirements", worker_id="requirements", lease_seconds=30).run_once().to_dict()
            final_run = service.get_run(run["id"])
            agent_runs = [artifact for artifact in service.list_artifacts(run["id"]) if artifact["kind"] == "agent_run"]

            self.assertEqual(result["status"], "failed")
            self.assertEqual(final_run["status"], "blocked")
            self.assertEqual((final_run["continuation"] or {}).get("failure_reason"), "llm_call_failed")
            self.assertEqual(len(agent_runs), 1)
            self.assertFalse(agent_runs[0]["payload"]["ok"])

    def test_quality_blocks_non_patch_origin_project_files(self) -> None:
        with WorkspaceSandbox() as root:
            project_root = root / "project"
            (project_root / "src").mkdir(parents=True)
            (project_root / "src" / "manual.py").write_text("print('manual')\n", encoding="utf-8")
            report = validate_ai_native_project(
                project_root=project_root,
                layout={"source_root": "src", "delivery_root": "src", "entrypoints": [], "directories": [{"path": "src"}], "validation_commands": []},
                run_context={"patch_sets": [], "agent_runs": [], "packages": [], "test_reports": [], "code_reviews": [], "release_notes": []},
            )

            self.assertFalse(report["ok"])
            self.assertTrue(any(gate["name"] == "patch_set_gate" and not gate["ok"] for gate in report["gates"]))
            self.assertTrue(any(gate["name"] == "ai_content_origin_gate" and not gate["ok"] for gate in report["gates"]))

    def test_delivery_export_copies_runnable_files_without_internal_artifacts(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            project = service.create_project({"name": "exportable", "title": "Exportable", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])
            drain_workers(service)
            project_root = service.runtime.project_root(project)
            (project_root / ".v6" / "rollback").mkdir(parents=True)
            (project_root / ".v6" / "rollback" / "internal.txt").write_text("internal", encoding="utf-8")
            (project_root / ".agent").mkdir(parents=True)
            (project_root / ".agent" / "trace.json").write_text("{}", encoding="utf-8")
            (project_root / "reports").mkdir(parents=True)
            (project_root / "reports" / "debug.json").write_text("{}", encoding="utf-8")
            target = root / "delivered" / "exportable"

            report = service.export_delivery(run["id"], str(target))

            self.assertTrue(report["ok"])
            self.assertGreater(report["copied_count"], 0)
            self.assertTrue((target / "web" / "index.html").exists())
            self.assertFalse((target / ".v6").exists())
            self.assertFalse((target / ".agent").exists())
            self.assertFalse((target / "reports").exists())
            artifacts = service.list_artifacts(run["id"])
            self.assertTrue(any(item["kind"] == "delivery_export" for item in artifacts))

    def test_delivery_export_api_endpoint(self) -> None:
        if TestClient is None:
            self.skipTest("fastapi test client unavailable")
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            project = service.create_project({"name": "export-api", "title": "Export Api", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])
            drain_workers(service)
            client = TestClient(build_app(service, load_config(root)))
            target = root / "exports-api" / "export-api"

            response = client.post(f"/api/v6/runs/{run['id']}/export-delivery", json={"target_path": str(target), "overwrite": False})

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertTrue((target / "web" / "index.html").exists())

    def test_repair_agent_applies_patch_and_requeues_review(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            project = service.create_project({"name": "repairable", "title": "Repairable", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])
            service.store.update_run(run["id"], metadata={"project_layout": {"source_root": "app", "delivery_root": "web", "entrypoints": [], "directories": [{"path": "web"}], "validation_commands": []}})
            repair_job = service._enqueue_repair_job(project, service.get_run(run["id"]), "manual", {"ok": False})

            result = DurableWorker(service, role="repair", worker_id="repair", lease_seconds=30).run_once().to_dict()
            jobs = service.list_jobs(run["id"])

            self.assertEqual(result["status"], "completed")
            self.assertTrue(any(job["job_type"] == "code_review" for job in jobs))

    def test_observability_api_exposes_agent_native_artifacts(self) -> None:
        if TestClient is None:
            self.skipTest("fastapi test client unavailable")
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            project = service.create_project({"name": "observable", "title": "Observable", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])
            drain_workers(service)
            client = TestClient(build_app(service, load_config(root)))

            self.assertEqual(client.get("/api/v6/health").json()["kernel"], "v6")
            self.assertEqual(client.get("/api/" + "v4/health").status_code, 404)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/agent-runs").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/patch-sets").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/agent-contract-report").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/patch-transactions").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/test-execution").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/code-index").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/contract-index").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/project-layout").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/dag").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/code-review").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/mission-state").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/mission-graph").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/recovery-trace").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/events").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/replay-projection").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/checkpoint-resume").status_code, 200)

    def test_model_settings_api_maps_custom_responses_preset(self) -> None:
        if TestClient is None:
            self.skipTest("fastapi test client unavailable")
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            client = TestClient(build_app(service, load_config(root)))

            response = client.put(
                "/api/v6/model-settings",
                json={
                    "model_provider": "custom",
                    "model": "gpt-5.3-codex",
                    "model_reasoning_effort": "xhigh",
                    "disable_response_storage": True,
                    "api_key": "test-key",
                    "model_providers": {
                        "custom": {
                            "name": "custom",
                            "wire_api": "responses",
                            "requires_openai_auth": True,
                            "base_url": "https://deepkey.top/v1",
                        }
                    },
                },
            )

            self.assertEqual(response.status_code, 200)
            llm = response.json()["llm"]
            self.assertEqual(llm["api_base"], "https://deepkey.top/v1")
            self.assertEqual(llm["model"], "gpt-5.3-codex")
            self.assertEqual(llm["wire_api"], "responses")
            self.assertEqual(llm["provider_profile"], "custom-responses-compatible")
            self.assertEqual(llm["api_path"], "/responses")
            self.assertTrue(llm["disable_response_storage"])
            self.assertEqual(llm["api_key"], "***")

    def test_model_settings_save_uses_input_values_and_syncs_env(self) -> None:
        if TestClient is None:
            self.skipTest("fastapi test client unavailable")
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient())
            client = TestClient(build_app(service, load_config(root)))

            response = client.put(
                "/api/v6/model-settings",
                json={
                    "model_provider": "custom",
                    "model": "not-a-preset-model",
                    "model_reasoning_effort": "medium",
                    "disable_response_storage": False,
                    "model_providers": {
                        "custom": {
                            "name": "custom",
                            "wire_api": "chat_completions",
                            "requires_openai_auth": True,
                            "base_url": "https://example.test/v9",
                        }
                    },
                },
            )

            self.assertEqual(response.status_code, 200)
            llm = response.json()["llm"]
            self.assertEqual(llm["api_base"], "https://example.test/v9")
            self.assertEqual(llm["model"], "not-a-preset-model")
            self.assertEqual(llm["wire_api"], "chat_completions")
            self.assertEqual(llm["provider_profile"], "custom-chat-compatible")
            self.assertEqual(llm["api_path"], "/chat/completions")
            self.assertFalse(llm["disable_response_storage"])
            env_text = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("OPENAI_MODEL=not-a-preset-model", env_text)
            self.assertIn("OPENAI_API_BASE=https://example.test/v9", env_text)
            self.assertIn("OPENAI_WIRE_API=chat_completions", env_text)

    def test_model_settings_test_connection_uses_current_payload(self) -> None:
        if TestClient is None:
            self.skipTest("fastapi test client unavailable")
        original_client = v6_api.OpenAICompatibleClient
        try:
            v6_api.OpenAICompatibleClient = ContractCheckLLMClient
            with WorkspaceSandbox() as root:
                service = build_service(root, NativeFakeLLMClient())
                client = TestClient(build_app(service, load_config(root)))

                response = client.post(
                    "/api/v6/model-settings/test",
                    json={
                        "model_provider": "custom",
                        "model": "probe-model",
                        "model_reasoning_effort": "low",
                        "disable_response_storage": True,
                        "model_providers": {
                            "custom": {
                                "name": "custom",
                                "wire_api": "responses",
                                "requires_openai_auth": True,
                                "base_url": "https://probe.test/v1",
                            }
                        },
                    },
                )

                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["llm"]["model"], "probe-model")
                self.assertEqual(payload["llm"]["api_base"], "https://probe.test/v1")
        finally:
            v6_api.OpenAICompatibleClient = original_client

    def test_durable_claim_is_role_scoped_and_idempotent(self) -> None:
        store = InMemoryV6Store()
        store.bootstrap()
        first = store.enqueue_job("local-workspace", {"job_type": "code_generation", "role": "backend", "run_id": "run-1", "resume_key": "run:run-1:package:1", "payload": {}})
        duplicate = store.enqueue_job("local-workspace", {"job_type": "code_generation", "role": "backend", "run_id": "run-1", "resume_key": "run:run-1:package:1", "payload": {}})

        wrong_role = store.claim_job("local-workspace", "frontend", "worker-front", 30)
        claimed = store.claim_job("local-workspace", "backend", "worker-back", 30)
        second_claim = store.claim_job("local-workspace", "backend", "worker-back-2", 30)
        running = store.start_job(claimed["id"], "worker-back", 30)

        self.assertEqual(first["id"], duplicate["id"])
        self.assertIsNone(wrong_role)
        self.assertEqual(claimed["id"], first["id"])
        self.assertEqual(running["status"], "running")
        self.assertIsNone(second_claim)

    def test_worker_status_registry_reports_process_shape(self) -> None:
        with WorkspaceSandbox() as root:
            registry = WorkerStatusRegistry(root / "workspace")
            registry.update("backend-test", {"role": "backend", "pid": 1234, "status": "idle", "current_job": None, "processed_job_count": 2, "last_error": "", "log_path": "logs/backend.log"})

            workers = registry.list()

            self.assertEqual(workers[0]["role"], "backend")
            self.assertEqual(workers[0]["processed_job_count"], 2)


if __name__ == "__main__":
    unittest.main()
