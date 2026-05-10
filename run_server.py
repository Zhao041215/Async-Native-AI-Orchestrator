from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# psycopg requires SelectorEventLoop on Windows (ProactorEventLoop is incompatible)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import AsyncLLMClient
from dev_orchestrator.v8.api import build_v8_app
from dev_orchestrator.v8.artifacts import ArtifactWriter
from dev_orchestrator.v8.models import AISchedulerLimits
from dev_orchestrator.v8.observability import configure_logging, get_logger
from dev_orchestrator.v8.pipeline import PipelineOrchestrator
from dev_orchestrator.v8.profiles import resolve_scale_profile
from dev_orchestrator.v8.resilience import ProviderCircuitBreaker
from dev_orchestrator.v8.runtime import FileRuntime
from dev_orchestrator.v8.scheduler import AsyncAIScheduler
from dev_orchestrator.v8.worker import WorkerSupervisor


def _root() -> Path:
    return Path(__file__).resolve().parent


def _build_v8(use_memory_store: bool = False) -> tuple:
    """Build V8 components: store, scheduler, pipeline, runtime, artifacts."""
    configure_logging("INFO")
    root = _root()
    config = load_config(root)
    workspace_root = config.workspace_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    database_url = config.database_url or os.environ.get("DATABASE_URL", "")
    store_backend = os.environ.get("STORE_BACKEND", "postgres")

    if database_url and store_backend != "memory" and not use_memory_store:
        from dev_orchestrator.v8.store_pg import PostgresStore
        store = PostgresStore(database_url)
    else:
        log = get_logger("run_server")
        log.warning("no_database_url_using_memory_store",
                    detail="Set DATABASE_URL for production. Running with in-memory store.")
        from dev_orchestrator.v8.store_memory import InMemoryStore
        store = InMemoryStore()

    llm_client = AsyncLLMClient(config.llm)
    circuit_breaker = ProviderCircuitBreaker()
    scheduler = AsyncAIScheduler(llm_client, AISchedulerLimits(), circuit_breaker)
    runtime = FileRuntime(workspace_root)
    artifacts = ArtifactWriter(workspace_root / "artifacts")
    pipeline = PipelineOrchestrator(store, scheduler, runtime, artifacts)

    return store, scheduler, pipeline, runtime, artifacts, llm_client, config


def run_v8_api(host: str, port: int, memory_store: bool = False) -> None:
    """Start the V8 async FastAPI server."""
    import selectors
    import uvicorn

    store, scheduler, pipeline, runtime, artifacts, llm_client, config = _build_v8(memory_store)
    static_dir = Path(__file__).resolve().parent / "dev_orchestrator" / "static"
    app = build_v8_app(pipeline, store, static_dir=static_dir, config=config, artifact_writer=artifacts)

    log = get_logger("v8_server")
    log.info("v8_api_starting", host=host, port=port)

    cfg = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(cfg)

    loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


def run_v8_worker(
    roles: str,
    tenant_id: str,
    poll_seconds: float,
    lease_seconds: int,
    role_concurrency: str,
    memory_store: bool = False,
) -> None:
    """Start V8 async workers."""
    store, scheduler, pipeline, runtime, artifacts, llm_client, config = _build_v8(memory_store)

    role_list = (
        [r.strip() for r in roles.split(",") if r.strip()]
        if roles
        else ["backend", "frontend", "qa", "security", "integration", "review", "release", "docs", "db"]
    )
    concurrency: dict[str, int] = {}
    for item in (role_concurrency or "").split(","):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            concurrency[k.strip()] = int(v.strip())
    for role in role_list:
        concurrency.setdefault(role, 1)

    async def _run() -> None:
        await store.bootstrap()
        supervisor = WorkerSupervisor(
            pipeline=pipeline, store=store,
            tenant_id=tenant_id or config.identity.default_tenant,
            role_concurrency=concurrency,
            poll_seconds=poll_seconds, lease_seconds=lease_seconds,
        )
        await supervisor.start()

    asyncio.run(_run())


def run_v8_system_check() -> None:
    """Run V8 system health check."""
    root = _root()
    config = load_config(root)
    database_url = config.database_url or os.environ.get("DATABASE_URL", "")
    check = {
        "schema_version": "8.0",
        "kernel": "v8_pg_native",
        "store": "postgres" if database_url else "memory",
        "database_url_set": bool(database_url),
        "config": config.to_dict() if hasattr(config, "to_dict") else {},
    }
    print(json.dumps(check, indent=2, ensure_ascii=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Dev Orchestrator V8.")
    parser.add_argument("--api", action="store_true", help="Start the V8 FastAPI server (default).")
    parser.add_argument("--v8-api", action="store_true", help="Start the V8 async FastAPI server.")
    parser.add_argument("--v8-worker", action="store_true", help="Start V8 async workers.")
    parser.add_argument("--v8-system-check", action="store_true", help="Run V8 system health check.")
    # Keep V7 flags for backward compat — they map to V8 now
    parser.add_argument("--v7-api", action="store_true", help="(Alias) Start the V8 async FastAPI server.")
    parser.add_argument("--v7-worker", action="store_true", help="(Alias) Start V8 async workers.")
    parser.add_argument("--v7-system-check", action="store_true", help="(Alias) Run V8 system health check.")
    parser.add_argument("--system-check", action="store_true", help="Run V8 system health check.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--roles", default="backend,frontend,qa,security,integration,review,release,docs,db")
    parser.add_argument("--role-concurrency", default="")
    parser.add_argument("--tenant", default="")
    parser.add_argument("--poll-seconds", default=2.0, type=float)
    parser.add_argument("--lease-seconds", default=300, type=int)
    parser.add_argument("--memory-store", action="store_true", help="Force in-memory store (local dev, no DB).")
    args = parser.parse_args()

    if args.system_check or args.v8_system_check or args.v7_system_check:
        run_v8_system_check()
        return
    if args.v8_worker or args.v7_worker:
        run_v8_worker(
            args.roles, args.tenant, args.poll_seconds, args.lease_seconds,
            args.role_concurrency, args.memory_store,
        )
        return

    # Default: start API server
    run_v8_api(args.host, args.port, args.memory_store)


if __name__ == "__main__":
    main()
