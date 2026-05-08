from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import LLMError
from dev_orchestrator.v5.api import build_app
from dev_orchestrator.v5.release_quality import validate_ai_native_project
from dev_orchestrator.v5.runtime import AgentFileRuntime, PatchValidationError
from dev_orchestrator.v5.service import V5Orchestrator
from dev_orchestrator.v5.store import InMemoryV5Store
from dev_orchestrator.v5.worker import DurableWorker, WorkerStatusRegistry

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None


class WorkspaceSandbox:
    def __init__(self) -> None:
        self.path = (Path(__file__).resolve().parent.parent / "workspace" / "v5-test-scratch" / uuid.uuid4().hex).resolve()

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
                layout = {"source_root": "src", "delivery_root": "deploy", "entrypoints": ["deploy/index.html"], "directories": [{"path": "src"}, {"path": "deploy"}, {"path": "spec"}], "validation_commands": ["python -m pytest"]}
            else:
                layout = {"source_root": "app", "delivery_root": "web", "entrypoints": ["web/index.html"], "directories": [{"path": "app"}, {"path": "web"}, {"path": "database"}, {"path": "tests"}], "validation_commands": ["python -m unittest"]}
            return json.dumps({"architecture_summary": "AI planned layout", "technology_choices": ["plain web"], "project_layout": layout, "module_boundaries": []})
        if task == "package_planning":
            source_root = payload["architecture_design"]["project_layout"]["source_root"]
            delivery_root = payload["architecture_design"]["project_layout"]["delivery_root"]
            test_root = "spec" if source_root == "src" else "tests"
            packages = [
                {"package_key": "PKG-DATA", "role": "db", "domain": "data", "subsystem": "schema", "wave_key": "WAVE-001", "depends_on": [], "allowed_paths": [f"{source_root}/data/**"], "objective": "Create AI generated domain data."},
                {"package_key": "PKG-WEB", "role": "frontend", "domain": "ui", "subsystem": "browser", "wave_key": "WAVE-001", "depends_on": [], "allowed_paths": [f"{delivery_root}/**"], "objective": "Create AI generated UI."},
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
        model = "failing-v5-model"

    def __init__(self) -> None:
        self.config = self.Config()

    def chat(self, system_prompt: str, messages: list[dict], **kwargs) -> str:
        raise LLMError("simulated upstream failure")


def build_service(root: Path, llm_client=None) -> V5Orchestrator:
    store = InMemoryV5Store()
    service = V5Orchestrator(store=store, workspace_root=root / "workspace", tenant_id="local-workspace", llm_client=llm_client)
    service.bootstrap()
    return service


def drain_workers(service: V5Orchestrator, limit: int = 120) -> list[dict]:
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


class V5AgentNativeTests(unittest.TestCase):
    def test_ai_native_pipeline_generates_layout_files_patch_sets_and_release(self) -> None:
        with WorkspaceSandbox() as root:
            fake = NativeFakeLLMClient()
            service = build_service(root, fake)
            project = service.create_project({"name": "agent-native", "title": "Agent Native", "description": "Build a knowledge base with articles, categories, tags and search."})
            run = service.create_run(project["id"])

            results = drain_workers(service)
            final_run = service.get_run(run["id"])
            artifacts = service.list_artifacts(run["id"])
            project_root = root / "workspace" / "projects" / "agent-native"

            self.assertGreaterEqual(len(results), 10)
            self.assertEqual(final_run["status"], "release_ready")
            self.assertTrue((project_root / "app" / "data" / "schema.json").exists())
            self.assertTrue((project_root / "web" / "index.html").exists())
            self.assertTrue((project_root / "tests" / "ai_flow_test.md").exists())
            self.assertTrue(any(item["kind"] == "project_layout" for item in artifacts))
            self.assertTrue(any(item["kind"] == "package_dag" for item in artifacts))
            self.assertTrue(any(item["kind"] == "patch_set" for item in artifacts))
            self.assertTrue(any(item["kind"] == "release_notes" for item in artifacts))
            self.assertTrue(any(item["kind"] == "deploy_guide" for item in artifacts))
            task_kinds = {(item["payload"] or {}).get("task_kind") for item in artifacts if item["kind"] == "agent_run"}
            self.assertTrue({"requirements_analysis", "architecture_design", "package_planning", "code_generation", "test_generation", "security_review", "integration_merge", "code_review", "release_notes"}.issubset(task_kinds))

    def test_ai_can_choose_a_different_project_directory_shape(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, NativeFakeLLMClient(layout_variant="src"))
            project = service.create_project({"name": "src-layout", "title": "Src Layout", "description": "Build a knowledge base."})
            run = service.create_run(project["id"])

            drain_workers(service)
            layout = next(item for item in service.list_artifacts(run["id"]) if item["kind"] == "project_layout")["payload"]
            project_root = root / "workspace" / "projects" / "src-layout"

            self.assertEqual(layout["source_root"], "src")
            self.assertEqual(layout["delivery_root"], "deploy")
            self.assertTrue((project_root / "src" / "data" / "schema.json").exists())
            self.assertTrue((project_root / "deploy" / "index.html").exists())
            self.assertTrue((project_root / "spec" / "ai_flow_test.md").exists())

    def test_runtime_rejects_path_escape_and_forbidden_parts(self) -> None:
        with WorkspaceSandbox() as root:
            runtime = AgentFileRuntime(root / "workspace" / "projects")
            project = {"id": "p1", "name": "unsafe", "title": "Unsafe", "project_path": "", "config": {}}
            layout = {"source_root": "src", "delivery_root": "dist", "directories": [{"path": "src"}, {"path": "dist"}], "entrypoints": [], "validation_commands": []}

            with self.assertRaises(PatchValidationError):
                runtime.apply_file_manifest(project=project, layout=layout, agent_output={"files": [{"path": "../escape.txt", "action": "create", "content": "x"}]}, allowed_paths=["**"])
            with self.assertRaises(PatchValidationError):
                runtime.apply_file_manifest(project=project, layout=layout, agent_output={"files": [{"path": ".git/config", "action": "create", "content": "x"}]}, allowed_paths=["**"])

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

            self.assertEqual(client.get("/api/v5/health").json()["kernel"], "v5")
            self.assertEqual(client.get("/api/" + "v4/health").status_code, 404)
            self.assertEqual(client.get(f"/api/v5/runs/{run['id']}/agent-runs").status_code, 200)
            self.assertEqual(client.get(f"/api/v5/runs/{run['id']}/patch-sets").status_code, 200)
            self.assertEqual(client.get(f"/api/v5/runs/{run['id']}/project-layout").status_code, 200)
            self.assertEqual(client.get(f"/api/v5/runs/{run['id']}/dag").status_code, 200)
            self.assertEqual(client.get(f"/api/v5/runs/{run['id']}/code-review").status_code, 200)

    def test_durable_claim_is_role_scoped_and_idempotent(self) -> None:
        store = InMemoryV5Store()
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
