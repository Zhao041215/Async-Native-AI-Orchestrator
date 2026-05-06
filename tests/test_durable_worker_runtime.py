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
from dev_orchestrator.v2.models import utc_now
from dev_orchestrator.v2.service import V2Orchestrator
from dev_orchestrator.v2.storage import V2Storage


def build_config(root: Path, queue_mode: str = "external-worker") -> AppConfig:
    config = AppConfig(
        root_dir=root.resolve(),
        config_path=(root / "orchestrator_config.json").resolve(),
        server=ServerConfig(),
        llm=LLMConfig(use_mock=True),
        runtime=RuntimeConfig(
            workspace_root="workspace/projects",
            db_path="workspace/durable-worker.db",
            logs_path="logs",
            queue_mode=queue_mode,
        ),
        identity=IdentityConfig(),
        production=ProductionScaffoldConfig(),
    )
    ensure_paths(config)
    return config


class WorkspaceSandbox:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent / "workspace" / "durable-worker-scratch"
        self.path = (base / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class DurableWorkerRuntimeTests(unittest.TestCase):
    def test_external_worker_claims_queued_run_and_completes(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="external-worker",
                title="External Worker",
                description=(
                    "Must support tenant scoped API workflow, RBAC SSO auth, audit logs, "
                    "frontend console dashboard, contract tests, Docker deployment, and release approvals."
                ),
            )

            run = service.start_project_run(project["id"])
            first_job = storage.list_durable_jobs(run_id=run["id"])[0]
            self.assertEqual(first_job["status"], "queued")

            result = service.run_worker_once(tenant_id=project["tenant_id"], role="planner", worker_id="test-worker")
            refreshed = service.get_run(run["id"])

            self.assertTrue(result["claimed"])
            self.assertTrue(result["ok"])
            self.assertEqual(refreshed["durable_queue_state"]["state"], "completed")
            self.assertTrue(Path(refreshed["artifact_manifest_path"]).exists())

    def test_expired_lease_requeues_then_dead_letters_at_max_attempts(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            storage = V2Storage(config.db_path)
            storage.get_or_create_tenant("local-workspace")
            job = storage.enqueue_durable_job(
                {
                    "tenant_id": "local-workspace",
                    "project_id": "project-1",
                    "run_id": "run-1",
                    "kind": "run",
                    "role": "planner",
                    "resume_key": "run:run-1:planner",
                    "max_attempts": 1,
                    "payload": {"project_id": "project-1"},
                }
            )
            storage.lease_durable_job_by_id(job["id"], "worker-a", "2000-01-01T00:00:00+00:00")

            expired = storage.requeue_expired_jobs(utc_now())

            self.assertEqual(expired[0]["status"], "dead_letter")
            self.assertEqual(storage.get_durable_job(job["id"])["error"], "lease_timeout")

    def test_pause_and_resume_requeue_unclaimed_job_at_boundary(self) -> None:
        with WorkspaceSandbox() as root:
            config = build_config(root)
            storage = V2Storage(config.db_path)
            service = V2Orchestrator(config=config, storage=storage)
            project = service.create_project(
                name="pause-resume-worker",
                title="Pause Resume Worker",
                description=(
                    "Must support tenant scoped API workflow, RBAC SSO auth, audit logs, "
                    "frontend console dashboard, contract tests, Docker deployment, and release approvals."
                ),
            )
            run = service.start_project_run(project["id"])

            paused = service.pause_run(run["id"])
            paused_job = storage.list_durable_jobs(run_id=run["id"])[0]
            resumed = service.resume_run(run["id"])
            resumed_job = storage.list_durable_jobs(run_id=run["id"])[0]

            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused_job["status"], "paused")
            self.assertEqual(resumed["status"], "running")
            self.assertEqual(resumed_job["status"], "queued")


if __name__ == "__main__":
    unittest.main()
