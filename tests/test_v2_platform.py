from __future__ import annotations

import unittest
import shutil
import uuid
from pathlib import Path

from dev_orchestrator.config import (
    AppConfig,
    IdentityConfig,
    LLMConfig,
    ProductionScaffoldConfig,
    RuntimeConfig,
    ServerConfig,
    ensure_paths,
)
from dev_orchestrator.v2.agent_runtime import (
    AgentRuntimeResult,
    AgentContractError,
    AgentRunner,
    ScriptedAgent,
    blocked_validation_from_contract_error,
    parse_agent_response,
)
from dev_orchestrator.v2.executor import V2ChiefExecutor
from dev_orchestrator.v2.git_runtime import GitRuntime
from dev_orchestrator.v2.quality import evaluate_project_quality
from dev_orchestrator.v2.requirements import parse_requirements
from dev_orchestrator.v2.service import V2Orchestrator
from dev_orchestrator.v2.storage import V2Storage


def build_config(root: Path) -> AppConfig:
    config = AppConfig(
        root_dir=root.resolve(),
        config_path=(root / "orchestrator_config.json").resolve(),
        server=ServerConfig(),
        llm=LLMConfig(use_mock=True),
        runtime=RuntimeConfig(
            workspace_root="workspace/projects",
            db_path="workspace/v2-test.db",
            logs_path="logs",
        ),
        identity=IdentityConfig(),
        production=ProductionScaffoldConfig(),
    )
    ensure_paths(config)
    return config


class V2PlatformTests(unittest.TestCase):
    def test_requirement_contract_maps_must_items_to_work_packages(self) -> None:
        text = """
        Must support multi-tenant HR AI recruitment with resume parsing and job matching.
        Must provide Simplified Chinese zh-CN frontend labels for AI assistant flows.
        Must enforce GDPR/PIPL privacy, audit logs, SSO, and tenant isolation.
        Should provide Kubernetes and Helm deployment boundaries.
        """

        bundle = parse_requirements(text)

        self.assertGreaterEqual(len(bundle["atoms"]), 4)
        self.assertEqual(bundle["coverage"]["must_coverage_percent"], 100)
        self.assertTrue(any(item["owner_role"] == "frontend-lead" for item in bundle["work_packages"]))
        self.assertTrue(any(item["owner_role"] == "security-reviewer" for item in bundle["work_packages"]))

    def test_invalid_agent_json_blocks_without_fallback(self) -> None:
        with self.assertRaises(AgentContractError) as raised:
            parse_agent_response("not json")

        blocked = blocked_validation_from_contract_error("frontend-lead", raised.exception)

        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["artifacts"], [])
        self.assertIn("fallback artifacts were generated", blocked["summary"])

    def test_git_sandbox_initializes_non_git_project_and_creates_worktree(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "workspace" / "v2-test-scratch" / uuid.uuid4().hex).resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / "README.md").write_text("# demo\n", encoding="utf-8")
            git = GitRuntime(project_root, root / "worktrees")

            sandbox = git.ensure_repository()
            worktree = git.create_worktree("run-1", "backend-lead", "WP-001-demo", 1, sandbox["base_sha"])

            self.assertTrue((project_root / ".git").exists())
            self.assertTrue(Path(worktree["path"]).exists())
            self.assertEqual(worktree["base_sha"], sandbox["base_sha"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_agent_patch_is_captured_from_real_git_diff(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "workspace" / "v2-test-scratch" / uuid.uuid4().hex).resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
            project_root = root / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            git = GitRuntime(project_root, root / "worktrees")
            sandbox = git.ensure_repository()
            worktree = git.create_worktree("run-2", "backend-lead", "WP-001-demo", 1, sandbox["base_sha"])
            target = Path(worktree["path"]) / "apps" / "api" / "demo.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def answer():\n    return 42\n", encoding="utf-8")

            patch = git.write_patch(Path(worktree["path"]), sandbox["base_sha"], root / "demo.patch")
            integration = git.create_worktree("run-2", "integration", "candidate", 1, sandbox["base_sha"])
            apply_result = git.apply_patch(Path(integration["path"]), Path(patch["patch_path"]))

            self.assertFalse(patch["empty"])
            self.assertIn("apps/api/demo.py", patch["files_changed"])
            self.assertTrue(apply_result["ok"])
            self.assertTrue((Path(integration["path"]) / "apps" / "api" / "demo.py").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_v2_run_executes_real_worktree_patch_and_test_loop(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "workspace" / "v2-test-scratch" / uuid.uuid4().hex).resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
            config = build_config(root)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="hr-ai",
                title="HR AI Platform",
                description=(
                    "Must support resume parsing, HR policy Q&A RAG, attrition prediction, "
                    "tenant isolation, SSO, audit logs, and zh-CN frontend labels."
                ),
            )

            result = service.run_project_sync(project["id"])

            self.assertEqual(result["run"]["status"], "release_candidate_ready")
            self.assertEqual(result["release_candidate"]["decision"], "GO")
            self.assertTrue(result["release_candidate"]["release_patch_path"])
            self.assertTrue(storage.list_agent_runs(result["run"]["id"]))
            self.assertTrue(storage.list_patch_sets(result["run"]["id"]))
            self.assertTrue(storage.list_test_runs(result["run"]["id"]))
            self.assertTrue(Path(result["release_candidate"]["release_patch_path"]).exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_test_failure_triggers_repair_attempt_and_rerun(self) -> None:
        class RepairingAgent(ScriptedAgent):
            def run(self, worktree_path, work_package, requirement_bundle, attempt, failure_context=""):
                result = super().run(worktree_path, work_package, requirement_bundle, attempt, failure_context)
                tests_dir = Path(worktree_path) / "tests"
                tests_dir.mkdir(parents=True, exist_ok=True)
                if attempt == 1 and work_package.get("owner_role") == "qa-automation":
                    (tests_dir / "test_forced_repair.py").write_text(
                        "import unittest\n\nclass ForcedRepairTests(unittest.TestCase):\n    def test_forced_repair(self):\n        self.assertTrue(False)\n",
                        encoding="utf-8",
                    )
                if attempt > 1:
                    (tests_dir / "test_forced_repair.py").write_text(
                        "import unittest\n\nclass ForcedRepairTests(unittest.TestCase):\n    def test_forced_repair(self):\n        self.assertTrue(True)\n",
                        encoding="utf-8",
                    )
                return result

        root = (Path(__file__).resolve().parent.parent / "workspace" / "v2-test-scratch" / uuid.uuid4().hex).resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
            config = build_config(root)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="repair-loop",
                title="Repair Loop",
                description="Must support backend API tenant isolation, audit logs, and contract tests.",
            )
            run = storage.create_run(
                {
                    "id": str(uuid.uuid4()),
                    "project_id": project["id"],
                    "status": "running",
                    "chief_summary": "test",
                    "created_at": "now",
                    "updated_at": "now",
                }
            )
            executor = V2ChiefExecutor(
                config=config,
                storage=storage,
                agent_runner=AgentRunner(config, scripted_agent=RepairingAgent()),
                max_repair_rounds=3,
            )

            result = executor.execute_run(project["id"], run["id"])
            agent_runs = storage.list_agent_runs(run["id"])
            test_runs = storage.list_test_runs(run["id"])

            self.assertEqual(result["release_candidate"]["decision"], "GO")
            self.assertTrue(any(item["attempt"] > 1 for item in agent_runs))
            self.assertTrue(any(item["status"] == "failed" for item in test_runs))
            self.assertTrue(any(item["status"] == "passed" for item in test_runs))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_e_test_regression_is_rejected_when_present(self) -> None:
        project_root = Path("E:/test")
        if not project_root.exists():
            self.skipTest("E:/test regression fixture is not present in this environment.")
        requirement_text = (project_root / "Enterprise-level HR AI Full-Stack Development Document.md").read_text(
            encoding="utf-8",
            errors="ignore",
        )
        bundle = parse_requirements(requirement_text)

        quality = evaluate_project_quality(project_root, bundle)
        codes = {finding["code"] for finding in quality["findings"]}

        self.assertEqual(quality["status"], "failed")
        self.assertFalse(quality["release_candidate_allowed"])
        self.assertIn("frontend_prd_leak", codes)
        self.assertIn("generic_operations_template_detected", codes)
        self.assertIn("qa_tests_fake_operations_template", codes)
        self.assertIn("fallback_or_normalized_artifacts", codes)


if __name__ == "__main__":
    unittest.main()
