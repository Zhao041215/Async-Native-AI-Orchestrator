from __future__ import annotations

import argparse
import json
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.healthcheck import build_system_check
from dev_orchestrator.llm_client import OpenAICompatibleClient
from dev_orchestrator.llm_contract_check import run_llm_contract_check
from dev_orchestrator.server import run_server
from dev_orchestrator.v2.acceptance import run_v2_acceptance_check
from dev_orchestrator.v2.quality import evaluate_project_quality
from dev_orchestrator.v2.requirements import parse_requirements
from dev_orchestrator.v2.service import V2Orchestrator
from dev_orchestrator.v2.worker import DEFAULT_WORKER_ROLES, DurableWorker, LocalWorkerSupervisor


def _build_v2() -> tuple[Path, V2Orchestrator]:
    root = Path(__file__).resolve().parent
    config = load_config(root)
    return root, V2Orchestrator(config=config)


def run_system_check() -> None:
    root, service = _build_v2()
    print(json.dumps(build_system_check(root, service.config.to_dict(), service.storage), indent=2, ensure_ascii=True))


def run_acceptance_check() -> None:
    root = Path(__file__).resolve().parent
    config = load_config(root)
    print(json.dumps(run_v2_acceptance_check(config), indent=2, ensure_ascii=True))


def run_llm_handshake_check() -> None:
    root = Path(__file__).resolve().parent
    config = load_config(root)
    client = OpenAICompatibleClient(config.llm)
    print(json.dumps(run_llm_contract_check(client), indent=2, ensure_ascii=True))


def run_v2_regression_check(project_path: str) -> None:
    root = Path(project_path or "E:/test").resolve()
    requirement_candidates = list(root.glob("*Requirements*.md")) + list(root.glob("*Development Document*.md"))
    requirement_text = ""
    if requirement_candidates:
        requirement_text = requirement_candidates[0].read_text(encoding="utf-8", errors="ignore")
    elif (root / "README.md").exists():
        requirement_text = (root / "README.md").read_text(encoding="utf-8", errors="ignore")
    bundle = parse_requirements(requirement_text)
    quality = evaluate_project_quality(root, bundle)
    print(json.dumps(quality, indent=2, ensure_ascii=True))


def run_v2_project_once(title: str, description: str, project_name: str, project_path: str | None) -> None:
    _, service = _build_v2()
    project = service.create_project(
        name=project_name or title or "v2-project",
        title=title or project_name or "V2 Project",
        description=description,
        project_path=project_path,
    )
    result = service.run_project_sync(project["id"])
    print(json.dumps(result, indent=2, ensure_ascii=True))


def run_worker_process(role: str, tenant_id: str, worker_id: str, once: bool, poll_seconds: float, lease_seconds: int, heartbeat_seconds: int, max_jobs: int) -> None:
    _, service = _build_v2()
    worker = DurableWorker(
        service=service,
        role=role,
        tenant_id=tenant_id or service.default_tenant["id"],
        worker_id=worker_id,
        poll_seconds=poll_seconds,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )
    if once:
        print(json.dumps(worker.run_once(), indent=2, ensure_ascii=True))
        return
    result = worker.run_forever(max_jobs=max_jobs if max_jobs > 0 else None)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=True))


def run_worker_supervisor(roles: str, tenant_id: str, poll_seconds: float, lease_seconds: int, heartbeat_seconds: int) -> None:
    root, service = _build_v2()
    role_list = [item.strip().lower() for item in roles.split(",") if item.strip()] if roles else list(DEFAULT_WORKER_ROLES)
    supervisor = LocalWorkerSupervisor(
        root_dir=root,
        roles=role_list,
        tenant_id=tenant_id or service.default_tenant["id"],
        poll_seconds=poll_seconds,
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )
    print(json.dumps({"started": supervisor.start()}, indent=2, ensure_ascii=True))
    supervisor.run_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the hosted V2 control plane.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--system-check", action="store_true")
    parser.add_argument("--acceptance-check", action="store_true")
    parser.add_argument("--llm-contract-check", action="store_true")
    parser.add_argument("--v2-regression-check", action="store_true")
    parser.add_argument("--v2-run-once", action="store_true")
    parser.add_argument("--worker", action="store_true", help="Run one durable V2 worker process.")
    parser.add_argument("--worker-supervisor", action="store_true", help="Start and supervise local durable worker processes.")
    parser.add_argument("--role", default="planner", help="Worker role for --worker.")
    parser.add_argument("--roles", default=",".join(DEFAULT_WORKER_ROLES), help="Comma-separated roles for --worker-supervisor.")
    parser.add_argument("--tenant", default="", help="Tenant id for worker operations.")
    parser.add_argument("--worker-id", default="", help="Stable worker id; generated when omitted.")
    parser.add_argument("--once", action="store_true", help="Claim and execute at most one durable job.")
    parser.add_argument("--poll-seconds", default=2.0, type=float)
    parser.add_argument("--lease-seconds", default=300, type=int)
    parser.add_argument("--heartbeat-seconds", default=30, type=int)
    parser.add_argument("--max-jobs", default=0, type=int)
    parser.add_argument("--project-path", default="", help="Project path for V2 operations.")
    parser.add_argument("--title", default="", help="Title for --v2-run-once.")
    parser.add_argument("--description", default="", help="Description for --v2-run-once.")
    parser.add_argument("--project-name", default="", help="Project name for --v2-run-once.")
    args = parser.parse_args()

    if args.system_check:
        run_system_check()
        return
    if args.acceptance_check:
        run_acceptance_check()
        return
    if args.llm_contract_check:
        run_llm_handshake_check()
        return
    if args.v2_regression_check:
        run_v2_regression_check(args.project_path or "E:/test")
        return
    if args.v2_run_once:
        run_v2_project_once(args.title, args.description, args.project_name, args.project_path or None)
        return
    if args.worker:
        run_worker_process(
            args.role,
            args.tenant,
            args.worker_id,
            args.once,
            args.poll_seconds,
            args.lease_seconds,
            args.heartbeat_seconds,
            args.max_jobs,
        )
        return
    if args.worker_supervisor:
        run_worker_supervisor(args.roles, args.tenant, args.poll_seconds, args.lease_seconds, args.heartbeat_seconds)
        return

    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
