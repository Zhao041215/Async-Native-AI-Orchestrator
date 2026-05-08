from __future__ import annotations

import argparse
import json
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import OpenAICompatibleClient
from dev_orchestrator.llm_contract_check import run_llm_contract_check
from dev_orchestrator.v5.service import V5Orchestrator
from dev_orchestrator.v5.store import InMemoryV5Store, build_store
from dev_orchestrator.v5.system_check import build_v5_system_check
from dev_orchestrator.v5.worker import DEFAULT_WORKER_ROLES, DurableWorker, WorkerSupervisor


def _root() -> Path:
    return Path(__file__).resolve().parent


def _build_v5(memory_store: bool = False) -> tuple[Path, V5Orchestrator]:
    root = _root()
    config = load_config(root)
    store = InMemoryV5Store() if memory_store else build_store(config.database_url)
    llm_client = None if memory_store else OpenAICompatibleClient(config.llm)
    service = V5Orchestrator(store=store, workspace_root=config.workspace_root, tenant_id=config.identity.default_tenant, llm_client=llm_client)
    service.bootstrap(attempts=30, delay_seconds=1.0)
    return root, service


def run_system_check(strict_db: bool = False) -> None:
    root = _root()
    config = load_config(root)
    print(json.dumps(build_v5_system_check(root, config.to_dict(), strict_db=strict_db), indent=2, ensure_ascii=True))


def run_llm_handshake_check() -> None:
    root = _root()
    config = load_config(root)
    client = OpenAICompatibleClient(config.llm)
    print(json.dumps(run_llm_contract_check(client), indent=2, ensure_ascii=True))


def run_worker_process(role: str, tenant_id: str, worker_id: str, once: bool, poll_seconds: float, lease_seconds: int, max_jobs: int, memory_store: bool = False) -> None:
    _, service = _build_v5(memory_store=memory_store)
    worker = DurableWorker(
        service=service,
        role=role,
        tenant_id=tenant_id or service.tenant_id,
        worker_id=worker_id,
        poll_seconds=poll_seconds,
        lease_seconds=lease_seconds,
    )
    if once:
        print(json.dumps(worker.run_once().to_dict(), indent=2, ensure_ascii=True))
        return
    print(json.dumps(worker.run_forever(max_jobs=max_jobs if max_jobs > 0 else None), indent=2, ensure_ascii=True))


def run_worker_supervisor(roles: str, tenant_id: str, poll_seconds: float, lease_seconds: int) -> None:
    root = _root()
    config = load_config(root)
    role_list = [item.strip().lower() for item in roles.split(",") if item.strip()] if roles else list(DEFAULT_WORKER_ROLES)
    supervisor = WorkerSupervisor(
        root_dir=root,
        workspace_root=config.workspace_root,
        roles=role_list,
        tenant_id=tenant_id or config.identity.default_tenant,
        poll_seconds=poll_seconds,
        lease_seconds=lease_seconds,
    )
    print(json.dumps({"started": supervisor.start()}, indent=2, ensure_ascii=True))
    supervisor.run_forever()


def run_api(host: str, port: int, memory_store: bool = False) -> None:
    from dev_orchestrator.v5.api import run_api_server

    root = _root()
    config = load_config(root)
    store = InMemoryV5Store() if memory_store else build_store(config.database_url)
    llm_client = None if memory_store else OpenAICompatibleClient(config.llm)
    service = V5Orchestrator(store=store, workspace_root=config.workspace_root, tenant_id=config.identity.default_tenant, llm_client=llm_client)
    service.bootstrap(attempts=1 if memory_store else 30, delay_seconds=1.0)
    run_api_server(service, config, host=host or config.server.host, port=port or config.server.port)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the V5 demand-driven autonomous delivery control plane.")
    parser.add_argument("--api", action="store_true", help="Start the V5 FastAPI control plane.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--system-check", action="store_true")
    parser.add_argument("--strict-db", action="store_true", help="Require Postgres bootstrap during --system-check.")
    parser.add_argument("--llm-contract-check", action="store_true")
    parser.add_argument("--worker", action="store_true", help="Run one V5 durable worker process.")
    parser.add_argument("--worker-supervisor", action="store_true", help="Start and supervise V5 role worker processes.")
    parser.add_argument("--memory-store", action="store_true", help="Development-only in-memory store for opening the V5 console without Postgres.")
    parser.add_argument("--role", default="backend", help="Worker role for --worker.")
    parser.add_argument("--roles", default=",".join(DEFAULT_WORKER_ROLES), help="Comma-separated roles for --worker-supervisor.")
    parser.add_argument("--tenant", default="", help="Tenant id for worker operations.")
    parser.add_argument("--worker-id", default="", help="Stable worker id; generated when omitted.")
    parser.add_argument("--once", action="store_true", help="Claim and execute at most one durable job.")
    parser.add_argument("--poll-seconds", default=2.0, type=float)
    parser.add_argument("--lease-seconds", default=300, type=int)
    parser.add_argument("--max-jobs", default=0, type=int)
    args = parser.parse_args()

    if args.system_check:
        run_system_check(strict_db=args.strict_db)
        return
    if args.llm_contract_check:
        run_llm_handshake_check()
        return
    if args.worker:
        run_worker_process(args.role, args.tenant, args.worker_id, args.once, args.poll_seconds, args.lease_seconds, args.max_jobs, memory_store=args.memory_store)
        return
    if args.worker_supervisor:
        run_worker_supervisor(args.roles, args.tenant, args.poll_seconds, args.lease_seconds)
        return

    run_api(args.host, args.port, memory_store=args.memory_store)


if __name__ == "__main__":
    main()
