from __future__ import annotations

import argparse
import json
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import OpenAICompatibleClient
from dev_orchestrator.llm_contract_check import run_llm_contract_check
from dev_orchestrator.v6.pressure import PressureFaultConfig, PressureTestRunner
from dev_orchestrator.v6.service import V6Orchestrator
from dev_orchestrator.v6.store import InMemoryV6Store, build_store
from dev_orchestrator.v6.system_check import build_v6_system_check
from dev_orchestrator.v6.worker import DEFAULT_WORKER_ROLES, DurableWorker, WorkerSupervisor, default_role_concurrency, parse_role_concurrency


def _root() -> Path:
    return Path(__file__).resolve().parent


def _build_v6(memory_store: bool = False) -> tuple[Path, V6Orchestrator]:
    root = _root()
    config = load_config(root)
    store = InMemoryV6Store() if memory_store else build_store(config.database_url)
    llm_client = None if memory_store else OpenAICompatibleClient(config.llm)
    service = V6Orchestrator(store=store, workspace_root=config.workspace_root, tenant_id=config.identity.default_tenant, llm_client=llm_client)
    service.bootstrap(attempts=30, delay_seconds=1.0)
    return root, service


def run_system_check(strict_db: bool = False) -> None:
    root = _root()
    config = load_config(root)
    print(json.dumps(build_v6_system_check(root, config.to_dict(), strict_db=strict_db), indent=2, ensure_ascii=True))


def purge_retired_records(database_url: str = "") -> None:
    root = _root()
    config = load_config(root)
    if database_url:
        config.runtime.database_url = database_url
    store = build_store(config.database_url)
    store.bootstrap()
    print(json.dumps(store.purge_retired_generation_records(), indent=2, ensure_ascii=True))


def run_llm_handshake_check() -> None:
    root = _root()
    config = load_config(root)
    client = OpenAICompatibleClient(config.llm)
    print(json.dumps(run_llm_contract_check(client), indent=2, ensure_ascii=True))


def run_pressure_test(timeout_minutes: int, output_path: str, disable_faults: bool, oversize_limit_bytes: int, database_url: str = "") -> None:
    root = _root()
    config = load_config(root)
    if database_url:
        config.runtime.database_url = database_url
    if config.llm.use_mock or not config.llm.api_base or not config.llm.api_key:
        raise RuntimeError("Pressure test requires a real AI provider configuration.")
    store = build_store(config.database_url)
    service = V6Orchestrator(store=store, workspace_root=config.workspace_root, tenant_id=config.identity.default_tenant, llm_client=OpenAICompatibleClient(config.llm))
    try:
        service.bootstrap(attempts=1, delay_seconds=0.0)
    except Exception as exc:
        raise RuntimeError(
            "Pressure test requires a reachable V6 Postgres database. "
            f"Configured database_url={config.database_url!r}. "
            "When running on the host, use --pressure-database-url postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator "
            "or start the docker-compose.v6.yml stack before running the pressure harness."
        ) from exc
    runner = PressureTestRunner(
        root_dir=root,
        service=service,
        base_llm_client=service.llm_client or OpenAICompatibleClient(config.llm),
        output_dir=root / "logs" / "pressure",
        fault_config=PressureFaultConfig(
            timeout_seconds=max(1, int(timeout_minutes or 1)) * 60,
            oversize_body_limit_bytes=max(1024, int(oversize_limit_bytes or 1024)),
            inject_transient_faults=not disable_faults,
        ),
    )
    report = runner.run()
    if output_path:
        output_file = (root / output_path).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))


def run_worker_process(role: str, tenant_id: str, worker_id: str, once: bool, poll_seconds: float, lease_seconds: int, max_jobs: int, memory_store: bool = False) -> None:
    _, service = _build_v6(memory_store=memory_store)
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


def run_worker_supervisor(roles: str, tenant_id: str, poll_seconds: float, lease_seconds: int, role_concurrency: str, target_scale: str) -> None:
    root = _root()
    config = load_config(root)
    role_list = [item.strip().lower() for item in roles.split(",") if item.strip()] if roles else list(DEFAULT_WORKER_ROLES)
    default_concurrency = default_role_concurrency(target_scale or "xlarge_100k")
    concurrency = {role: int(default_concurrency.get(role, 1)) for role in role_list}
    concurrency.update(parse_role_concurrency(role_concurrency or ""))
    supervisor = WorkerSupervisor(
        root_dir=root,
        workspace_root=config.workspace_root,
        roles=role_list,
        tenant_id=tenant_id or config.identity.default_tenant,
        poll_seconds=poll_seconds,
        lease_seconds=lease_seconds,
        concurrency=concurrency,
    )
    print(json.dumps({"started": supervisor.start()}, indent=2, ensure_ascii=True))
    supervisor.run_forever()


def run_api(host: str, port: int, memory_store: bool = False) -> None:
    from dev_orchestrator.v6.api import run_api_server

    root = _root()
    config = load_config(root)
    store = InMemoryV6Store() if memory_store else build_store(config.database_url)
    llm_client = None if memory_store else OpenAICompatibleClient(config.llm)
    service = V6Orchestrator(store=store, workspace_root=config.workspace_root, tenant_id=config.identity.default_tenant, llm_client=llm_client)
    service.bootstrap(attempts=1 if memory_store else 30, delay_seconds=1.0)
    run_api_server(service, config, host=host or config.server.host, port=port or config.server.port)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the V6 demand-driven autonomous delivery control plane.")
    parser.add_argument("--api", action="store_true", help="Start the V6 FastAPI control plane.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--system-check", action="store_true")
    parser.add_argument("--purge-retired-records", action="store_true")
    parser.add_argument("--purge-database-url", default="", help="Override the V6 Postgres URL for retired record cleanup.")
    parser.add_argument("--strict-db", action="store_true", help="Require Postgres bootstrap during --system-check.")
    parser.add_argument("--llm-contract-check", action="store_true")
    parser.add_argument("--worker", action="store_true", help="Run one V6 durable worker process.")
    parser.add_argument("--worker-supervisor", action="store_true", help="Start and supervise V6 role worker processes.")
    parser.add_argument("--memory-store", action="store_true", help="Development-only in-memory store for opening the V6 console without Postgres.")
    parser.add_argument("--pressure-test", action="store_true", help="Run the real-AI 100k pressure harness.")
    parser.add_argument("--pressure-timeout-minutes", default=240, type=int)
    parser.add_argument("--pressure-output", default="", help="Optional JSON output path relative to the repository root.")
    parser.add_argument("--pressure-no-faults", action="store_true", help="Disable transient fault injection for the pressure benchmark.")
    parser.add_argument("--pressure-oversize-limit-bytes", default=12000, type=int)
    parser.add_argument("--pressure-database-url", default="", help="Override the V6 Postgres URL for host-run pressure tests.")
    parser.add_argument("--role", default="backend", help="Worker role for --worker.")
    parser.add_argument("--roles", default=",".join(DEFAULT_WORKER_ROLES), help="Comma-separated roles for --worker-supervisor.")
    parser.add_argument("--role-concurrency", default="", help="Comma-separated role=count overrides for --worker-supervisor.")
    parser.add_argument("--supervisor-scale", default="xlarge_100k", help="Scale profile used for default worker-supervisor concurrency.")
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
    if args.purge_retired_records:
        purge_retired_records(args.purge_database_url)
        return
    if args.llm_contract_check:
        run_llm_handshake_check()
        return
    if args.pressure_test:
        run_pressure_test(args.pressure_timeout_minutes, args.pressure_output, args.pressure_no_faults, args.pressure_oversize_limit_bytes, args.pressure_database_url)
        return
    if args.worker:
        run_worker_process(args.role, args.tenant, args.worker_id, args.once, args.poll_seconds, args.lease_seconds, args.max_jobs, memory_store=args.memory_store)
        return
    if args.worker_supervisor:
        run_worker_supervisor(args.roles, args.tenant, args.poll_seconds, args.lease_seconds, args.role_concurrency, args.supervisor_scale)
        return

    run_api(args.host, args.port, memory_store=args.memory_store)


if __name__ == "__main__":
    main()
