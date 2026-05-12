"""Shared fixtures for V8 orchestrator tests."""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dev_orchestrator.v8.models import (
    AICallResult,
    AISchedulerLimits,
    AITaskBudget,
    new_id,
)


# ---------------------------------------------------------------------------
# In-memory store stub
# ---------------------------------------------------------------------------

class InMemoryStore:
    """Minimal in-memory store for unit tests."""

    def __init__(self) -> None:
        self._artifacts: list[dict[str, Any]] = []
        self._agent_runs: list[dict[str, Any]] = []
        self._provider_failures: list[tuple] = []
        self._provider_successes: list[str] = []

    async def list_artifacts(self, run_id: str, kind: str | None = None) -> list[dict]:
        results = [a for a in self._artifacts if a.get("run_id") == run_id]
        if kind is not None:
            results = [a for a in results if a.get("kind") == kind]
        return results

    async def record_agent_run(self, agent_run: Any) -> None:
        self._agent_runs.append(
            agent_run.model_dump() if hasattr(agent_run, "model_dump") else dict(agent_run)
        )

    async def record_provider_failure(self, provider: str, error: str, kind: str) -> None:
        self._provider_failures.append((provider, error, kind))

    async def record_provider_success(self, provider: str) -> None:
        self._provider_successes.append(provider)

    async def acquire_ai_slot(self, **kwargs: Any) -> Any:
        from unittest.mock import MagicMock
        slot = MagicMock()
        slot.id = new_id()
        return slot

    async def release_ai_slot(self, slot_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Mock scheduler
# ---------------------------------------------------------------------------

class MockScheduler:
    """Scheduler that returns a configurable response without calling LLM."""

    def __init__(self, response: dict[str, Any] | None = None, ok: bool = True) -> None:
        self._response = response or {}
        self._ok = ok
        self.calls: list[dict[str, Any]] = []

    async def call(self, *, run_id: str, role: str, job_id: str, task_kind: str,
                   system_prompt: str, user_payload: dict, budget: AITaskBudget,
                   store: Any, tenant_id: str, heartbeat_callback: Any = None) -> AICallResult:
        self.calls.append({"run_id": run_id, "role": role, "task_kind": task_kind})
        if self._ok:
            return AICallResult(
                ok=True, agent_run_id=new_id(), role=role,
                job_id=job_id, task_kind=task_kind,
                raw_response=json.dumps(self._response, ensure_ascii=False),
            )
        return AICallResult(
            ok=False, agent_run_id=new_id(), role=role,
            job_id=job_id, task_kind=task_kind,
            error="mock_failure", error_kind="mock_failure", retryable=False,
        )

    async def evict_stale_run_sems(self, active_run_ids: set) -> None:
        pass

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def mock_scheduler() -> MockScheduler:
    return MockScheduler(response={"ok": True, "summary": "test"})


@pytest.fixture
def failing_scheduler() -> MockScheduler:
    return MockScheduler(ok=False)
