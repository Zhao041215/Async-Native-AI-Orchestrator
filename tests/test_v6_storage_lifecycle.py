from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.v6.api import build_app
from dev_orchestrator.v6.service import V6Orchestrator
from dev_orchestrator.v6.storage_lifecycle import StorageLifecyclePolicy
from dev_orchestrator.v6.store import InMemoryV6Store
from dev_orchestrator.v6.system_check import build_v6_system_check

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


def build_service(root: Path, policy: StorageLifecyclePolicy | None = None) -> V6Orchestrator:
    service = V6Orchestrator(
        store=InMemoryV6Store(),
        workspace_root=root / "workspace" / "projects",
        tenant_id="local-workspace",
        llm_client=None,
        logs_root=root / "logs",
        storage_policy=policy or StorageLifecyclePolicy(exported_worktree_retention_days=7, workspace_max_bytes=1024 * 1024),
    )
    service.bootstrap()
    return service


def create_exported_run(service: V6Orchestrator, root: Path) -> tuple[dict, dict, Path]:
    project = service.create_project({"name": "storage-app", "title": "Storage App", "description": "Build storage app."})
    run = service.create_run(project["id"])
    if hasattr(service.store, "jobs"):
        for job in service.store.jobs.values():
            if job.get("run_id") == run["id"]:
                job["status"] = "completed"
    project_root = service.runtime.project_root(project)
    (project_root / "web").mkdir(parents=True, exist_ok=True)
    (project_root / "web" / "index.html").write_text("<h1>storage app</h1>\n", encoding="utf-8")
    service.store.update_run(run["id"], status="release_ready", checkpoint="release_candidate_completed", metadata={**dict(run.get("metadata") or {}), "project_root": str(project_root)})
    target = root / "delivered" / "storage-app"
    service.export_delivery(run["id"], str(target))
    return project, service.get_run(run["id"]), target


class V6StorageLifecycleTests(unittest.TestCase):
    def test_export_records_receipt_and_storage_state_without_touching_export(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root)
            _, run, target = create_exported_run(service, root)

            storage = service.run_storage(run["id"])
            artifacts = service.list_artifacts(run["id"])

            self.assertEqual(storage["state"], "exported")
            self.assertTrue((target / "web" / "index.html").exists())
            self.assertTrue(any(item["kind"] == "delivery_export_receipt" for item in artifacts))
            self.assertEqual((service.get_run(run["id"])["metadata"]["storage_lifecycle"])["state"], "exported")

    def test_prune_dry_run_reports_without_deleting_worktree(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, StorageLifecyclePolicy(exported_worktree_retention_days=7, workspace_max_bytes=1024 * 1024))
            project, run, _ = create_exported_run(service, root)
            project_root = service.runtime.project_root(project)

            result = service.prune_run_storage(run["id"], dry_run=True, force=True)

            self.assertTrue(result["plan"]["ok"])
            self.assertTrue(result["result"]["dry_run"])
            self.assertTrue(project_root.exists())
            self.assertGreater(result["plan"]["estimated_reclaim_bytes"], 0)

    def test_prune_apply_deletes_only_managed_worktree_and_keeps_export(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root)
            project, run, target = create_exported_run(service, root)
            project_root = service.runtime.project_root(project)

            result = service.prune_run_storage(run["id"], dry_run=False, force=True)

            self.assertTrue(result["result"]["ok"])
            self.assertFalse(project_root.exists())
            self.assertTrue((target / "web" / "index.html").exists())
            final_run = service.get_run(run["id"])
            self.assertEqual(final_run["metadata"]["storage_lifecycle"]["state"], "pruned")
            event_kinds = [event["kind"] for event in service.list_events(run["id"])]
            self.assertIn("storage.gc_planned", event_kinds)
            self.assertIn("storage.gc_completed", event_kinds)

    def test_active_jobs_block_prune_even_when_force_is_true(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root)
            _, run, _ = create_exported_run(service, root)
            service.store.enqueue_job(
                run["tenant_id"],
                {
                    "job_type": "code_generation",
                    "role": "backend",
                    "run_id": run["id"],
                    "resume_key": f"run:{run['id']}:active-storage",
                    "payload": {},
                },
            )

            result = service.prune_run_storage(run["id"], dry_run=False, force=True)

            self.assertFalse(result["plan"]["ok"])
            self.assertIn("active_run_or_job", result["plan"]["blocked_reasons"])

    def test_project_path_outside_runtime_is_never_pruned(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root)
            external_root = root / "external-project"
            external_root.mkdir(parents=True)
            (external_root / "app.py").write_text("print('external')\n", encoding="utf-8")
            project = service.create_project({"name": "external-storage", "project_path": str(external_root), "description": "External path."})
            run = service.create_run(project["id"])
            service.store.update_run(run["id"], status="release_ready", metadata={**dict(run.get("metadata") or {}), "project_root": str(external_root)})
            service.export_delivery(run["id"], str(root / "delivered" / "external-storage"))

            result = service.prune_run_storage(run["id"], dry_run=False, force=True)

            self.assertFalse(result["plan"]["ok"])
            self.assertTrue(external_root.exists())
            self.assertIn("project_root_outside_managed_runtime", result["plan"]["blocked_reasons"])

    def test_archive_records_manifest_and_updates_state(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root)
            _, run, _ = create_exported_run(service, root)

            result = service.archive_run_storage(run["id"], include_worktree=True, dry_run=False)

            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["archive_path"]).exists())
            self.assertGreater(result["file_count"], 0)
            self.assertEqual(service.get_run(run["id"])["metadata"]["storage_lifecycle"]["state"], "archived")

    def test_storage_report_quota_and_gc_candidates(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root, StorageLifecyclePolicy(exported_worktree_retention_days=7, workspace_max_bytes=64))
            _, run, _ = create_exported_run(service, root)

            report = service.storage_report()
            gc = service.gc_storage(dry_run=True, force=True, include_logs=False)

            self.assertEqual(report["quota"]["status"], "blocked")
            self.assertTrue(any(item["plan"]["run_id"] == run["id"] for item in gc["run_results"]))

    def test_api_exposes_storage_report_prune_and_archive(self) -> None:
        if TestClient is None:
            self.skipTest("fastapi test client unavailable")
        with WorkspaceSandbox() as root:
            service = build_service(root)
            _, run, _ = create_exported_run(service, root)
            client = TestClient(build_app(service, load_config(root)))

            self.assertEqual(client.get("/api/v6/storage-report").status_code, 200)
            self.assertEqual(client.get(f"/api/v6/runs/{run['id']}/storage").status_code, 200)
            prune = client.post(f"/api/v6/runs/{run['id']}/prune", json={"dry_run": True, "force": True})
            archive = client.post(f"/api/v6/runs/{run['id']}/archive", json={"dry_run": True, "include_worktree": True})
            gc = client.post("/api/v6/storage/gc", json={"dry_run": True, "force": True, "include_logs": False})

            self.assertEqual(prune.status_code, 200)
            self.assertTrue(prune.json()["plan"]["ok"])
            self.assertEqual(archive.status_code, 200)
            self.assertTrue(archive.json()["archive"]["ok"])
            self.assertEqual(gc.status_code, 200)

    def test_system_check_reports_storage_lifecycle_gate(self) -> None:
        with WorkspaceSandbox() as root:
            (root / "workspace" / "projects").mkdir(parents=True)
            (root / "logs").mkdir(parents=True)
            config = {
                "runtime": {
                    "runtime_root": "workspace/projects",
                    "workspace_root": "workspace/projects",
                    "logs_path": "logs",
                    "workspace_max_bytes": 1024 * 1024,
                    "exported_worktree_retention_days": 7,
                }
            }

            report = build_v6_system_check(root, config, strict_db=False)

            self.assertTrue(report["checks"]["storage_lifecycle_gate"]["ok"], json.dumps(report, indent=2))


if __name__ == "__main__":
    unittest.main()
