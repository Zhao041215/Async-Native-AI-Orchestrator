from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import AsyncLLMClient
from dev_orchestrator.v7.api import build_v7_app
from dev_orchestrator.v7.artifacts import ArtifactWriter
from dev_orchestrator.v7.observability import configure_logging, get_logger
from dev_orchestrator.v7.pipeline import PipelineOrchestrator
from dev_orchestrator.v7.profiles import resolve_scale_profile
from dev_orchestrator.v7.resilience import ProviderCircuitBreaker
from dev_orchestrator.v7.runtime import FileRuntime
from dev_orchestrator.v7.scheduler import AsyncAIScheduler
from dev_orchestrator.v7.store import InMemoryStore
from dev_orchestrator.v7.system_check import build_v7_system_check
from dev_orchestrator.v7.worker import WorkerSupervisor
from dev_orchestrator.v7.models import AISchedulerLimits


def _root() -> Path:
    return Path(__file__).resolve().parent


def _build_v7(memory_store: bool = True) -> tuple:
    """Build V7 components: store, scheduler, pipeline, runtime, artifacts."""
    configure_logging("INFO")
    root = _root()
    config = load_config(root)
    workspace_root = config.workspace_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    store = InMemoryStore()
    llm_client = AsyncLLMClient(config.llm)
    circuit_breaker = ProviderCircuitBreaker()
    scheduler = AsyncAIScheduler(llm_client, AISchedulerLimits(), circuit_breaker)
    runtime = FileRuntime(workspace_root)
    artifacts = ArtifactWriter(workspace_root / "artifacts")
    pipeline = PipelineOrchestrator(store, scheduler, runtime, artifacts)

    return store, scheduler, pipeline, runtime, artifacts, llm_client, config


def run_v7_api(host: str, port: int) -> None:
    """Start the V7 async FastAPI server."""
    import uvicorn

    store, scheduler, pipeline, runtime, artifacts, llm_client, config = _build_v7()
    static_dir = Path(__file__).resolve().parent / "dev_orchestrator" / "static"
    app = build_v7_app(pipeline, store, static_dir=static_dir, config=config)

    log = get_logger("v7_server")
    log.info("v7_api_starting", host=host, port=port)

    uvicorn.run(app, host=host, port=port, log_level="info")


def run_v7_worker(roles: str, tenant_id: str, poll_seconds: float, lease_seconds: int, role_concurrency: str) -> None:
    """Start V7 async workers."""
    store, scheduler, pipeline, runtime, artifacts, llm_client, config = _build_v7()

    role_list = [r.strip() for r in roles.split(",") if r.strip()] if roles else ["backend", "frontend", "qa", "security", "integration", "review", "release", "docs", "db"]

    concurrency: dict[str, int] = {}
    for item in (role_concurrency or "").split(","):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            concurrency[k.strip()] = int(v.strip())
    for role in role_list:
        concurrency.setdefault(role, 1)

    async def _run():
        await store.bootstrap()
        supervisor = WorkerSupervisor(
            pipeline=pipeline, store=store,
            tenant_id=tenant_id or config.identity.default_tenant,
            role_concurrency=concurrency,
            poll_seconds=poll_seconds, lease_seconds=lease_seconds,
        )
        await supervisor.start()

    asyncio.run(_run())


def run_v7_system_check() -> None:
    """Run V7 system health check."""
    root = _root()
    config = load_config(root)
    print(json.dumps(build_v7_system_check(root, config.to_dict()), indent=2, ensure_ascii=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Dev Orchestrator V7.")
    parser.add_argument("--api", action="store_true", help="Start the V7 FastAPI server (default).")
    parser.add_argument("--v7-api", action="store_true", help="Start the V7 async FastAPI server.")
    parser.add_argument("--v7-worker", action="store_true", help="Start V7 async workers.")
    parser.add_argument("--v7-system-check", action="store_true", help="Run V7 system health check.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--system-check", action="store_true", help="Run V7 system health check.")
    parser.add_argument("--roles", default="backend,frontend,qa,security,integration,review,release,docs,db", help="Comma-separated roles for workers.")
    parser.add_argument("--role-concurrency", default="", help="Comma-separated role=count overrides.")
    parser.add_argument("--tenant", default="", help="Tenant id for worker operations.")
    parser.add_argument("--poll-seconds", default=2.0, type=float)
    parser.add_argument("--lease-seconds", default=300, type=int)
    parser.add_argument("--memory-store", action="store_true", help="Development-only in-memory store.")
    args = parser.parse_args()

    if args.system_check or args.v7_system_check:
        run_v7_system_check()
        return
    if args.v7_worker:
        run_v7_worker(args.roles, args.tenant, args.poll_seconds, args.lease_seconds, args.role_concurrency)
        return

    # Default: start API server
    run_v7_api(args.host, args.port)


if __name__ == "__main__":
    main()
