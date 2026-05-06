from __future__ import annotations

import shutil
import unittest
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
from dev_orchestrator.v2.automation import build_project_blueprint
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
            db_path="workspace/automation-test.db",
            logs_path="logs",
        ),
        identity=IdentityConfig(),
        production=ProductionScaffoldConfig(),
    )
    ensure_paths(config)
    return config


class WorkspaceSandbox:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent / "workspace" / "automation-scratch"
        self.path = (base / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class AutomationMaturityTests(unittest.TestCase):
    def test_medium_project_blueprint_has_decomposition_score(self) -> None:
        bundle = parse_requirements(
            """
            Must support tenant-scoped customer onboarding workflow.
            Must provide API authorization and audit logs.
            Must provide frontend dashboard status labels.
            Must provide contract tests and release approval evidence.
            Must provide Docker deployment and observability checks.
            """
        )

        blueprint = build_project_blueprint(bundle, "supervised_auto")

        self.assertGreaterEqual(blueprint["decomposition_score"], 55)
        self.assertEqual(blueprint["automation_mode"], "supervised_auto")
        self.assertIn(blueprint["project_size"], {"medium", "large"})
        self.assertTrue(blueprint["subsystem_boundaries"])

    def test_under_specified_project_blocks_with_repair_prompt(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            service = V2Orchestrator(config=config, storage=V2Storage(config.db_path))
            project = service.create_project(
                name="underspecified",
                title="Underspecified",
                description="Must build something useful for users.",
            )

            result = service.run_project_sync(project["id"])

            self.assertEqual(result["release_candidate"]["decision"], "NO_GO")
            self.assertEqual(result["run"]["status"], "blocked")
            self.assertEqual(result["run"]["continuation_state"]["next_action"], "repair_requirements")
            self.assertTrue(result["release_candidate"]["automation"]["project_blueprint"]["actionable_repair_prompts"])

    def test_successful_run_records_automation_metadata_and_safeguards(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="automation-good",
                title="Automation Good",
                automation_mode="full_auto_candidate",
                description=(
                    "Must support resume parsing API, tenant isolation, audit logs, SSO, "
                    "frontend dashboard labels, contract tests, Docker deployment, and release approvals."
                ),
            )

            result = service.run_project_sync(project["id"])
            run = result["run"]
            candidate = result["release_candidate"]

            self.assertEqual(candidate["decision"], "GO")
            self.assertGreaterEqual(run["decomposition_score"], 55)
            self.assertEqual(run["autonomy_level"], 3)
            self.assertTrue(run["quality_gate_history"])
            self.assertEqual(run["continuation_state"]["state"], "release_candidate_ready")
            self.assertTrue(candidate["release_safeguards"]["must_requirements_mapped"])
            self.assertTrue(candidate["release_safeguards"]["tests_exist"])


if __name__ == "__main__":
    unittest.main()
