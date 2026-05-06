from __future__ import annotations

import json
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
from dev_orchestrator.server import Application, build_handler
from dev_orchestrator.v2.service import V2Orchestrator
from dev_orchestrator.v2.storage import V2Storage


def build_test_config(root_dir: Path) -> AppConfig:
    config = AppConfig(
        root_dir=root_dir.resolve(),
        config_path=(root_dir / "orchestrator_config.json").resolve(),
        server=ServerConfig(),
        llm=LLMConfig(use_mock=True),
        runtime=RuntimeConfig(
            workspace_root="workspace/projects",
            db_path="workspace/test-v2-hosted.db",
            logs_path="logs",
        ),
        identity=IdentityConfig(),
        production=ProductionScaffoldConfig(),
    )
    ensure_paths(config)
    return config


class WorkspaceSandbox:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent / "workspace" / "test-scratch"
        self.path = (base / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class HostedGovernanceIntegrityTests(unittest.TestCase):
    def test_tenant_scoped_project_listing_isolated(self) -> None:
        with WorkspaceSandbox() as root_dir:
            config = build_test_config(root_dir)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)

            alpha = service.create_project(
                name="alpha-project",
                title="Alpha",
                description="Must support tenant alpha audit logs and worker jobs.",
                tenant_id="tenant-alpha",
                user_id="alice",
            )
            beta = service.create_project(
                name="beta-project",
                title="Beta",
                description="Must support tenant beta audit logs and worker jobs.",
                tenant_id="tenant-beta",
                user_id="bob",
            )

            alpha_projects = service.list_projects("tenant-alpha")
            beta_projects = service.list_projects("tenant-beta")

            self.assertEqual([item["id"] for item in alpha_projects], [alpha["id"]])
            self.assertEqual([item["id"] for item in beta_projects], [beta["id"]])

    def test_approval_persists_and_emits_audit_event(self) -> None:
        with WorkspaceSandbox() as root_dir:
            config = build_test_config(root_dir)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="approval-project",
                title="Approval Project",
                description="Must support API tenant isolation, SSO, audit logs, and contract tests.",
            )

            result = service.run_project_sync(project["id"])
            self.assertEqual(result["release_candidate"]["decision"], "GO")

            approval = service.approve(project["id"], approver="reviewer", note="ship it")
            refreshed = service.get_project(project["id"])
            audits = service.list_audit_events(project["tenant_id"])

            self.assertTrue(approval["ok"])
            self.assertEqual(refreshed["approval_state"], "approved")
            self.assertEqual(len(refreshed["approvals"]), 1)
            self.assertTrue(any(item["action"] == "approval.created" for item in audits))

    def test_no_go_candidate_cannot_be_approved(self) -> None:
        with WorkspaceSandbox() as root_dir:
            config = build_test_config(root_dir)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(name="blocked-project", title="Blocked", description="")
            result = service.run_project_sync(project["id"])

            self.assertEqual(result["release_candidate"]["decision"], "NO_GO")
            with self.assertRaises(ValueError):
                service.approve(project["id"], approver="reviewer")

            audits = service.list_audit_events(project["tenant_id"])
            self.assertTrue(any(item["action"] == "approval.rejected" for item in audits))

    def test_worker_job_lifecycle_is_recorded_for_run(self) -> None:
        with WorkspaceSandbox() as root_dir:
            config = build_test_config(root_dir)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="worker-project",
                title="Worker Project",
                description="Must support worker job lifecycle, audit logs, and release candidates.",
            )

            result = service.run_project_sync(project["id"])
            jobs = service.list_worker_jobs(project["tenant_id"])

            self.assertEqual(result["run"]["worker_job_id"], jobs[0]["id"])
            self.assertEqual(jobs[0]["status"], "finished")
            self.assertEqual(jobs[0]["attempts"], 1)

    def test_legacy_v1_routes_are_gone(self) -> None:
        with WorkspaceSandbox() as root_dir:
            config = build_test_config(root_dir)
            config.config_path.write_text(json.dumps(config.to_persisted_dict(), indent=2), encoding="utf-8")
            app = Application(root_dir)
            handler = build_handler(app)

            class Dummy(handler):
                def __init__(self) -> None:
                    pass

            dummy = Dummy()
            dummy.path = "/api/tasks"
            dummy.headers = {}
            captured = {}

            def write_json(payload, code=200):
                captured["payload"] = payload
                captured["code"] = int(code)

            dummy._write_json = write_json
            dummy._serve_static = lambda path: None
            dummy.do_GET()

            self.assertEqual(captured["code"], 410)
            self.assertIn("Legacy V1 APIs were removed", captured["payload"]["error"])


if __name__ == "__main__":
    unittest.main()
