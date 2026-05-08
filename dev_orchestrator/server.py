from __future__ import annotations

from pathlib import Path

from dev_orchestrator.config import load_config
from dev_orchestrator.v6.api import run_api_server
from dev_orchestrator.v6.service import V6Orchestrator
from dev_orchestrator.v6.store import build_store


def run_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Compatibility wrapper around the V6 FastAPI control plane."""

    root = Path(__file__).resolve().parent.parent
    config = load_config(root)
    service = V6Orchestrator(build_store(config.database_url), config.workspace_root, config.identity.default_tenant)
    service.bootstrap()
    run_api_server(service, config, host=host, port=port)
