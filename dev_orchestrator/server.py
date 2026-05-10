from __future__ import annotations

import asyncio
from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import AsyncLLMClient
from dev_orchestrator.v7.api import build_v7_app
from dev_orchestrator.v7.artifacts import ArtifactWriter
from dev_orchestrator.v7.observability import configure_logging
from dev_orchestrator.v7.pipeline import PipelineOrchestrator
from dev_orchestrator.v7.resilience import ProviderCircuitBreaker
from dev_orchestrator.v7.runtime import FileRuntime
from dev_orchestrator.v7.scheduler import AsyncAIScheduler
from dev_orchestrator.v7.store import InMemoryStore
from dev_orchestrator.v7.models import AISchedulerLimits


def run_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Start the V7 async FastAPI control plane."""
    import uvicorn

    configure_logging("INFO")
    root = Path(__file__).resolve().parent.parent
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

    static_dir = Path(__file__).resolve().parent / "static"
    app = build_v7_app(pipeline, store, static_dir=static_dir)

    async def _startup():
        await store.bootstrap()

    uvicorn.run(app, host=host, port=port, log_level="info")
