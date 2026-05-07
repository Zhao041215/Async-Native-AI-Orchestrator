from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from dev_orchestrator.v4.planner import build_blueprint
from dev_orchestrator.v4.release_quality import validate_release_structure
from dev_orchestrator.v4.service import V4Orchestrator
from dev_orchestrator.v4.stack_packs import build_product_contract, decide_stack_pack, list_stack_packs
from dev_orchestrator.v4.store import InMemoryV4Store
from dev_orchestrator.v4.worker import DurableWorker, WorkerStatusRegistry


class WorkspaceSandbox:
    def __init__(self) -> None:
        self.path = (Path(__file__).resolve().parent.parent / "workspace" / "v4-test-scratch" / uuid.uuid4().hex).resolve()

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def build_service(root: Path) -> V4Orchestrator:
    store = InMemoryV4Store()
    service = V4Orchestrator(store=store, workspace_root=root / "workspace", tenant_id="local-workspace")
    service.bootstrap()
    return service


def drain_workers(service: V4Orchestrator, limit: int = 100) -> list[dict]:
    roles = ["chief", "architect", "backend", "frontend", "integration", "qa", "release", "security", "planner"]
    results: list[dict] = []
    for _ in range(limit):
        claimed = False
        for role in roles:
            worker = DurableWorker(service, role=role, worker_id=f"test-{role}", lease_seconds=30)
            result = worker.run_once().to_dict()
            if result["claimed"]:
                claimed = True
                results.append(result)
        if not claimed:
            return results
    raise AssertionError("workers did not drain within limit")


class V4StackPackTests(unittest.TestCase):
    def test_registry_contains_first_wave_stack_packs(self) -> None:
        ids = {item["id"] for item in list_stack_packs()}

        self.assertEqual(
            {
                "php_mysql_single_dir",
                "laravel_mysql",
                "node_express_mysql",
                "react_node_mysql",
                "nextjs_prisma_postgres",
                "python_fastapi_postgres",
                "static_spa_api",
            },
            ids,
        )

    def test_stack_selection_respects_explicit_and_inferred_rules(self) -> None:
        explicit = decide_stack_pack("Build a SaaS console", requested_stack_pack="php_mysql_single_dir")
        baota = decide_stack_pack("宝塔 PHP MySQL 传统后台管理系统")
        saas = decide_stack_pack("Modern multi-tenant SaaS tenant console with audit and reports")
        rag = decide_stack_pack("AI RAG document search API with embeddings and data processing")
        ambiguous = decide_stack_pack("Build something useful")

        self.assertEqual(explicit["stack_pack"], "php_mysql_single_dir")
        self.assertEqual(baota["stack_pack"], "php_mysql_single_dir")
        self.assertEqual(saas["stack_pack"], "nextjs_prisma_postgres")
        self.assertEqual(rag["stack_pack"], "python_fastapi_postgres")
        self.assertFalse(ambiguous["ok"])

    def test_xlarge_requires_enterprise_saas_domains(self) -> None:
        project = {
            "id": "project-1",
            "config": {"target_scale": "xlarge_100k", "stack_pack": "nextjs_prisma_postgres", "effective_loc_target": 100000},
        }

        blueprint = build_blueprint(project, "Build a dashboard")

        self.assertFalse(blueprint["ok"])
        self.assertEqual(blueprint["no_go_reason"], "underspecified_xlarge_100k")


class V4DurableKernelTests(unittest.TestCase):
    def test_durable_claim_is_role_scoped_and_idempotent(self) -> None:
        store = InMemoryV4Store()
        store.bootstrap()
        first = store.enqueue_job(
            "local-workspace",
            {
                "job_type": "package",
                "role": "backend",
                "run_id": "run-1",
                "resume_key": "run:run-1:package:1",
                "payload": {},
            },
        )
        duplicate = store.enqueue_job(
            "local-workspace",
            {
                "job_type": "package",
                "role": "backend",
                "run_id": "run-1",
                "resume_key": "run:run-1:package:1",
                "payload": {},
            },
        )

        wrong_role = store.claim_job("local-workspace", "frontend", "worker-front", 30)
        claimed = store.claim_job("local-workspace", "backend", "worker-back", 30)
        second_claim = store.claim_job("local-workspace", "backend", "worker-back-2", 30)
        running = store.start_job(claimed["id"], "worker-back", 30)

        self.assertEqual(first["id"], duplicate["id"])
        self.assertIsNone(wrong_role)
        self.assertEqual(claimed["id"], first["id"])
        self.assertEqual(claimed["status"], "leased")
        self.assertEqual(running["status"], "running")
        self.assertIsNone(second_claim)

    def test_expired_lease_requeues_then_dead_letters(self) -> None:
        store = InMemoryV4Store()
        store.bootstrap()
        store.enqueue_job(
            "local-workspace",
            {
                "job_type": "package",
                "role": "backend",
                "run_id": "run-1",
                "resume_key": "run:run-1:package:dead",
                "payload": {},
                "max_attempts": 1,
            },
        )
        claimed = store.claim_job("local-workspace", "backend", "worker-back", -1)
        self.assertIsNotNone(claimed)

        count = store.requeue_expired_jobs("local-workspace")
        jobs = store.list_jobs(run_id="run-1")

        self.assertEqual(count, 1)
        self.assertEqual(jobs[0]["status"], "dead_letter")


class V4DeliveryFlowTests(unittest.TestCase):
    def test_worker_pipeline_generates_clean_php_release_and_delivery_artifacts(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root)
            project = service.create_project(
                {
                    "name": "deployable-admin",
                    "title": "Deployable Admin",
                    "description": "宝塔 PHP MySQL 传统后台，包含管理员登录、健康检查、审计和报表。",
                    "stack_pack": "auto",
                    "effective_loc_target": 1000,
                }
            )
            run = service.create_run(project["id"])

            results = drain_workers(service)
            final_run = service.get_run(run["id"])
            artifacts = service.list_artifacts(run["id"])
            release = root / "workspace" / "projects" / "deployable-admin" / "release"

            self.assertGreaterEqual(len(results), 8)
            self.assertEqual(final_run["status"], "release_ready")
            self.assertTrue((release / "index.php").exists())
            self.assertTrue((release / ".env.example").exists())
            self.assertTrue((release / ".htaccess").exists())
            self.assertFalse((release / "public").exists())
            self.assertTrue(any(item["kind"] == "deploy_guide" for item in artifacts))
            self.assertTrue(any(item["kind"] == "release_candidate" for item in artifacts))

            report = validate_release_structure(release, build_product_contract("php_mysql_single_dir"), "php_mysql_single_dir")
            self.assertTrue(report["ok"], report)

    def test_resume_blocks_when_artifact_file_is_missing(self) -> None:
        with WorkspaceSandbox() as root:
            service = build_service(root)
            project = service.create_project(
                {
                    "name": "missing-artifact",
                    "title": "Missing Artifact",
                    "description": "宝塔 PHP MySQL 后台系统。",
                    "stack_pack": "auto",
                }
            )
            run = service.create_run(project["id"])
            drain_workers(service)
            artifacts = service.list_artifacts(run["id"])
            target = next(item for item in artifacts if item["path"])
            Path(target["path"]).unlink()

            resumed = service.resume_run(run["id"])
            continuation = service.continuation(run["id"])

            self.assertEqual(resumed["status"], "blocked")
            self.assertEqual(continuation["next_action"], "repair_missing_artifacts")

    def test_worker_status_registry_reports_process_shape(self) -> None:
        with WorkspaceSandbox() as root:
            registry = WorkerStatusRegistry(root / "workspace")
            registry.update(
                "backend-test",
                {
                    "role": "backend",
                    "pid": 1234,
                    "status": "idle",
                    "current_job": None,
                    "processed_job_count": 2,
                    "last_error": "",
                    "log_path": "logs/backend.log",
                },
            )

            workers = registry.list()

            self.assertEqual(workers[0]["role"], "backend")
            self.assertEqual(workers[0]["processed_job_count"], 2)


if __name__ == "__main__":
    unittest.main()
