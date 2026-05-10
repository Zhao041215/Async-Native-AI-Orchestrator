"""V8 Async AI Call Scheduler — three-level semaphore gating with circuit breaker.

Bug 6 fix: validate_agent_contract() is now called after every successful LLM
response. Blocking contract failures are surfaced on the AICallResult and
recorded on AgentRun so the contract_violations endpoint can expose them.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Protocol, runtime_checkable

from dev_orchestrator.llm_client import AsyncLLMClient, LLMError
from dev_orchestrator.v8.contract_validator import validate_and_classify
from dev_orchestrator.v8.models import (
    AICallResult,
    AISchedulerLimits,
    AISlot,
    AITaskBudget,
    AgentRun,
    ContractValidation,
    ErrorClassification,
    new_id,
    stable_json,
    utc_now,
)
from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.resilience import ProviderCircuitBreaker, classify_error

log = get_logger(__name__)


@runtime_checkable
class AbstractStore(Protocol):
    """Minimal store interface required by the scheduler."""

    async def acquire_ai_slot(
        self, *, tenant_id: str, run_id: str, provider: str,
        agent_run_id: str, task_kind: str, provider_limit: int,
        run_limit: int, wait_seconds: int, lease_seconds: int,
    ) -> AISlot: ...

    async def release_ai_slot(self, slot_id: str) -> None: ...

    async def record_agent_run(self, agent_run: AgentRun) -> None: ...


class AsyncAIScheduler:
    """Async AI call scheduler with three-level concurrency control.

    Concurrency hierarchy (all three must be acquired, never bypassed):
    1. global  -- system-wide cap across all providers and runs.
    2. provider -- per-provider cap (one semaphore per provider string).
    3. run -- per-run cap to prevent a single run from starving others.
    """

    def __init__(
        self,
        llm_client: AsyncLLMClient,
        limits: AISchedulerLimits,
        circuit_breaker: ProviderCircuitBreaker,
    ) -> None:
        self._llm_client = llm_client
        self._limits = limits
        self._circuit_breaker = circuit_breaker
        self._global_sem = asyncio.Semaphore(limits.global_concurrency)
        self._provider_sems: dict[str, asyncio.Semaphore] = {}
        self._provider_sem_lock = asyncio.Lock()
        self._run_sems: dict[str, asyncio.Semaphore] = {}
        self._run_sem_lock = asyncio.Lock()

    # -- context budget enforcement ----------------------------------------

    @staticmethod
    def _estimate_chars(obj: Any) -> int:
        if isinstance(obj, str):
            return len(obj)
        if isinstance(obj, dict):
            return sum(len(k) + 4 + AsyncAIScheduler._estimate_chars(v) for k, v in obj.items())
        if isinstance(obj, (list, tuple)):
            return sum(AsyncAIScheduler._estimate_chars(v) for v in obj)
        return len(str(obj))

    @staticmethod
    def _truncate_payload(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
        """Truncate a payload to fit within max_chars, largest strings first."""
        serialized = stable_json(payload)
        if len(serialized) <= max_chars:
            return payload

        paths: list[tuple[list[str | int], int]] = []

        def _walk(obj: Any, path: list[str | int]) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _walk(v, path + [k])
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _walk(v, path + [i])
            elif isinstance(obj, str) and len(obj) > 200:
                paths.append((list(path), len(obj)))

        _walk(payload, [])
        paths.sort(key=lambda x: x[1], reverse=True)

        result = json.loads(json.dumps(payload))
        budget_over = len(serialized) - max_chars

        for path, length in paths:
            if budget_over <= 0:
                break
            node = result
            for key in path[:-1]:
                node = node[key]
            leaf_key = path[-1]
            original = node[leaf_key]
            keep_chars = max(500, length - int(budget_over * 1.2))
            if keep_chars < length:
                node[leaf_key] = original[:keep_chars] + f"\n... [truncated from {length} chars]"
                budget_over -= (length - keep_chars)

        return result

    # -- public API --------------------------------------------------------

    async def call(
        self,
        *,
        run_id: str,
        role: str,
        job_id: str,
        task_kind: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        budget: AITaskBudget,
        store: AbstractStore,
        tenant_id: str,
        heartbeat_callback: Any | None = None,
    ) -> AICallResult:
        max_retries = self._limits.scheduler_max_retries  # Bug 6 fix: configurable, was hardcoded 2
        for attempt in range(max_retries + 1):
            result = await self._call_once(
                run_id=run_id, role=role, job_id=job_id, task_kind=task_kind,
                system_prompt=system_prompt, user_payload=user_payload,
                budget=budget, store=store, tenant_id=tenant_id,
                heartbeat_callback=heartbeat_callback,
            )
            if result.ok or not result.retryable or attempt >= max_retries:
                return result
            if result.error_kind == "circuit_open":
                return result
            backoff = random.uniform(2.0, 6.0) * (attempt + 1)
            log.info("scheduler_retry", run_id=run_id, task_kind=task_kind,
                     attempt=attempt + 1, backoff=round(backoff, 1), error=result.error_kind)
            await asyncio.sleep(backoff)
        return result  # type: ignore[possibly-undefined]

    async def _call_once(
        self,
        *,
        run_id: str,
        role: str,
        job_id: str,
        task_kind: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        budget: AITaskBudget,
        store: AbstractStore,
        tenant_id: str,
        heartbeat_callback: Any | None = None,
    ) -> AICallResult:
        agent_run_id = new_id()
        provider = self._llm_client.provider_identity()

        max_input = budget.max_input_chars
        estimated = self._estimate_chars(user_payload)
        if estimated > max_input:
            log.warning("payload_truncated", run_id=run_id, job_id=job_id,
                        estimated_chars=estimated, max_chars=max_input)
            user_payload = self._truncate_payload(user_payload, max_input)

        messages = [{"role": "user", "content": stable_json(user_payload)}]

        if not await self._circuit_breaker.allow_request(provider):
            log.warning("circuit_breaker_rejected", provider=provider, run_id=run_id, job_id=job_id)
            return AICallResult(
                ok=False, agent_run_id=agent_run_id, role=role,
                job_id=job_id, task_kind=task_kind,
                error="circuit breaker open", error_kind="circuit_open", retryable=True,
            )

        slot_data: dict[str, Any] = {}
        try:
            slot_data = await store.acquire_ai_slot(
                tenant_id=tenant_id, run_id=run_id, provider=provider,
                agent_run_id=agent_run_id, task_kind=task_kind,
                provider_limit=self._limits.provider_concurrency,
                run_limit=self._limits.run_concurrency,
                wait_seconds=budget.timeout_seconds,
                lease_seconds=budget.timeout_seconds * 2,
            )
        except Exception as exc:
            classification = classify_error(exc)
            await self._circuit_breaker.record_failure(provider, str(exc), classification)
            log.warning("slot_acquisition_failed", error=str(exc), run_id=run_id)
            return AICallResult(
                ok=False, agent_run_id=agent_run_id, role=role,
                job_id=job_id, task_kind=task_kind,
                error=str(exc), error_kind=classification.error_kind,
                retryable=classification.retryable,
            )

        slot_id = str(slot_data.id or "")
        started_at = time.monotonic()

        try:
            async with self._global_sem:
                provider_sem = await self._get_provider_sem(provider)
                run_sem = await self._get_run_sem(run_id)
                async with provider_sem:
                    async with run_sem:
                        return await self._execute_call(
                            run_id=run_id, role=role, job_id=job_id,
                            task_kind=task_kind, system_prompt=system_prompt,
                            messages=messages, budget=budget, store=store,
                            tenant_id=tenant_id, agent_run_id=agent_run_id,
                            provider=provider, slot_id=slot_id,
                            started_at=started_at,
                            heartbeat_callback=heartbeat_callback,
                        )
        except BaseException:
            await self._safe_release_slot(store, slot_id)
            raise

    async def evict_stale_run_sems(self, active_run_ids: set[str]) -> None:
        async with self._run_sem_lock:
            stale = [rid for rid in self._run_sems if rid not in active_run_ids]
            for rid in stale:
                del self._run_sems[rid]
        if stale:
            log.debug("evicted_stale_run_sems", count=len(stale))

    async def close(self) -> None:
        self._provider_sems.clear()
        async with self._run_sem_lock:
            self._run_sems.clear()
        log.info("scheduler_closed")

    # -- internals ---------------------------------------------------------

    async def _execute_call(
        self, *, run_id: str, role: str, job_id: str, task_kind: str,
        system_prompt: str, messages: list[dict[str, str]],
        budget: AITaskBudget, store: AbstractStore, tenant_id: str,
        agent_run_id: str, provider: str, slot_id: str,
        started_at: float, heartbeat_callback: Any | None,
    ) -> AICallResult:
        if heartbeat_callback is not None:
            self._invoke_heartbeat(heartbeat_callback)

        try:
            raw_response = await self._llm_client.chat(
                system_prompt, messages,
                max_tokens=budget.max_output_tokens,
                timeout=budget.timeout_seconds,
                reasoning_effort=budget.reasoning_effort,
                retry_attempts=budget.retry_attempts,
            )
        except LLMError as exc:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            classification = classify_error(exc)
            await self._circuit_breaker.record_failure(provider, str(exc), classification)
            await self._safe_release_slot(store, slot_id)
            log.warning("ai_call_failed", error=str(exc)[:500],
                        error_kind=classification.error_kind, run_id=run_id, elapsed_ms=elapsed_ms)
            return AICallResult(
                ok=False, agent_run_id=agent_run_id, role=role,
                job_id=job_id, task_kind=task_kind, elapsed_ms=elapsed_ms,
                error=str(exc), error_kind=classification.error_kind,
                retryable=classification.retryable,
            )
        except Exception:
            await self._safe_release_slot(store, slot_id)
            raise

        if heartbeat_callback is not None:
            self._invoke_heartbeat(heartbeat_callback)

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        await self._circuit_breaker.record_success(provider)
        await self._safe_release_slot(store, slot_id)

        # Bug 6 fix: validate AI output against the registered contract
        contract_validation = ContractValidation(ok=True, contract_type=task_kind)
        try:
            parsed = json.loads(raw_response)
            if isinstance(parsed, dict):
                contract_validation = validate_and_classify(task_kind, parsed)
        except (json.JSONDecodeError, Exception):
            pass

        if not contract_validation.ok:
            log.warning(
                "contract_violation",
                run_id=run_id, task_kind=task_kind,
                errors=contract_validation.errors[:5],
            )

        agent_run = AgentRun(
            id=agent_run_id, tenant_id=tenant_id, run_id=run_id,
            job_id=job_id, role=role, task_kind=task_kind,
            model=getattr(self._llm_client.config, "model", ""),
            status="ok", elapsed_ms=elapsed_ms,
            input_chars=len(system_prompt) + sum(len(m.get("content", "")) for m in messages),
            output_chars=len(raw_response),
            contract_ok=contract_validation.ok,
            contract_errors=contract_validation.errors,
        )
        try:
            await store.record_agent_run(agent_run)
        except Exception:
            log.debug("record_agent_run_failed", agent_run_id=agent_run_id)

        log.info("ai_call_ok", run_id=run_id, job_id=job_id, role=role,
                 elapsed_ms=elapsed_ms, output_chars=len(raw_response),
                 contract_ok=contract_validation.ok)
        return AICallResult(
            ok=True, agent_run_id=agent_run_id, role=role,
            job_id=job_id, task_kind=task_kind,
            model=getattr(self._llm_client.config, "model", ""),
            elapsed_ms=elapsed_ms, raw_response=raw_response,
            contract_validation=contract_validation,
        )

    async def _get_provider_sem(self, provider: str) -> asyncio.Semaphore:
        async with self._provider_sem_lock:
            sem = self._provider_sems.get(provider)
            if sem is None:
                sem = asyncio.Semaphore(self._limits.provider_concurrency)
                self._provider_sems[provider] = sem
            return sem

    async def _get_run_sem(self, run_id: str) -> asyncio.Semaphore:
        async with self._run_sem_lock:
            sem = self._run_sems.get(run_id)
            if sem is None:
                sem = asyncio.Semaphore(self._limits.run_concurrency)
                self._run_sems[run_id] = sem
            return sem

    @staticmethod
    async def _safe_release_slot(store: AbstractStore, slot_id: str) -> None:
        if not slot_id:
            return
        try:
            await store.release_ai_slot(slot_id)
        except Exception as exc:
            log.debug("slot_release_error", slot_id=slot_id, error=str(exc))

    @staticmethod
    def _invoke_heartbeat(callback: Any) -> None:
        try:
            result = callback()
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)
        except Exception:
            pass


class SkipAIScheduler:
    """Mock scheduler returning ok=False with 'ai_skipped' for testing."""

    async def call(
        self, *, run_id: str, role: str, job_id: str, task_kind: str,
        system_prompt: str, user_payload: dict[str, Any],
        budget: AITaskBudget, store: AbstractStore, tenant_id: str,
        heartbeat_callback: Any | None = None,
    ) -> AICallResult:
        return AICallResult(
            ok=False, agent_run_id=new_id(), role=role,
            job_id=job_id, task_kind=task_kind,
            error="ai_skipped", error_kind="ai_skipped", retryable=False,
        )

    async def evict_stale_run_sems(self, active_run_ids: set[str]) -> None:
        pass

    async def close(self) -> None:
        pass
