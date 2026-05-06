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
            db_path="workspace/autonomy-foundation.db",
            logs_path="logs",
        ),
        identity=IdentityConfig(),
        production=ProductionScaffoldConfig(),
    )
    ensure_paths(config)
    return config


class WorkspaceSandbox:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent / "workspace" / "autonomy-foundation-scratch"
        self.path = (base / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class AutonomousFoundationTests(unittest.TestCase):
    def test_run_records_durable_queue_continuation_artifacts_and_benchmark(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="foundation-good",
                title="Foundation Good",
                description=(
                    "Must support tenant scoped API workflow, RBAC SSO auth, audit logs, "
                    "frontend console dashboard, contract tests, Docker deployment, and release approvals."
                ),
            )

            result = service.run_project_sync(project["id"])
            run = result["run"]
            continuation = service.get_run_continuation(run["id"])
            artifacts = service.get_run_artifacts(run["id"])

            self.assertEqual(run["durable_queue_state"]["state"], "completed")
            self.assertTrue(run["artifact_manifest_path"])
            self.assertTrue(Path(run["artifact_manifest_path"]).exists())
            self.assertTrue(run["context_snapshot_id"])
            self.assertGreaterEqual(run["benchmark_score"], 0)
            self.assertIn("effective_loc", run["effective_loc_metrics"])
            self.assertEqual(continuation["durable_queue_state"]["state"], "completed")
            self.assertTrue(any(item["kind"] == "manifest" for item in artifacts["items"]))
            self.assertTrue(storage.list_code_index(project["id"], run_id=run["id"]))
            self.assertTrue(storage.list_benchmark_runs(project_id=project["id"]))

    def test_xlarge_blueprint_validation_blocks_missing_enterprise_domains(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            service = V2Orchestrator(config=config, storage=V2Storage(config.db_path))
            project = service.create_project(
                name="xlarge-gap",
                title="Xlarge Gap",
                target_scale="xlarge_100k",
                description="Must support a frontend dashboard and API.",
            )

            result = service.validate_project_blueprint(project["id"])

            self.assertFalse(result["valid"])
            self.assertIn("xlarge_readiness", result["blueprint"])
            self.assertIn("deployment", result["blueprint"]["xlarge_readiness"]["missing_domains"])

    def test_auto_apply_prepares_rollback_and_smoke_failure_rolls_back(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="auto-rollback",
                title="Auto Rollback",
                unattended_mode="auto_publish_with_rollback",
                description=(
                    "Must support tenant scoped API workflow, RBAC SSO auth, audit logs, "
                    "frontend console dashboard, contract tests, Docker deployment, and release approvals."
                ),
            )
            result = service.run_project_sync(project["id"])
            candidate = result["release_candidate"]
            self.assertEqual(candidate["decision"], "GO")

            tests_dir = Path(project["project_path"]) / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_smoke_failure.py").write_text(
                "import unittest\n\nclass SmokeFailureTests(unittest.TestCase):\n    def test_smoke_failure(self):\n        self.assertTrue(False)\n",
                encoding="utf-8",
            )

            applied = service.auto_apply_release_candidate(candidate["id"])
            rollback = storage.latest_release_rollback_for_candidate(candidate["id"])

            self.assertFalse(applied["ok"])
            self.assertEqual(rollback["status"], "applied")
            self.assertEqual(storage.get_release_candidate(candidate["id"])["status"], "rolled_back")
            self.assertTrue(Path(rollback["manifest_path"]).exists())
            self.assertFalse((Path(project["project_path"]) / "apps" / "api" / "WP_001_backend_domain_and_api.py").exists())


if __name__ == "__main__":
    unittest.main()
