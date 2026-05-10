from __future__ import annotations

import hashlib
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v6.ai_payload import payload_budget_report
from dev_orchestrator.v6.llm_policy import AITaskBudget, DEFAULT_PROVIDER_BODY_LIMIT_BYTES, resolve_ai_task_budget
from dev_orchestrator.v6.models import new_id, stable_json
from dev_orchestrator.v6.profiles import resolve_scale_profile
from dev_orchestrator.v6.provider_resilience import ProviderHealthMap, classify_provider_error, provider_identity
from dev_orchestrator.v6.store import StoreError, V6Store


@dataclass
class AISchedulerLimits:
    global_concurrency: int = 6
    provider_concurrency: int = 4
    run_concurrency: int = 3


class AICallScheduler:
    def __init__(self, llm_client: OpenAICompatibleClient | None, limits: AISchedulerLimits | None = None):
        self.llm_client = llm_client
        self.limits = limits or AISchedulerLimits()
        self._global = threading.BoundedSemaphore(self.limits.global_concurrency)
        self._provider = threading.BoundedSemaphore(self.limits.provider_concurrency)
        self._run_locks: dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()
        self.provider_health = ProviderHealthMap()

    def call(
        self,
        *,
        run_id: str,
        role: str,
        job: dict[str, Any],
        system_prompt: str,
        user_payload: dict[str, Any],
        task_kind: str,
        required: bool,
        payload_guard: dict[str, Any] | None = None,
        store: V6Store | None = None,
        tenant_id: str = "",
        lease_seconds: int = 300,
        heartbeat_callback: Any | None = None,
    ) -> dict[str, Any]:
        budget = self._budget_for_job(task_kind, job)
        agent_run_id = new_id()
        queued_at = time.time()
        prompt_text = stable_json(user_payload)
        context_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        guard = payload_guard or payload_budget_report(
            system_prompt=system_prompt,
            user_payload=user_payload,
            task_budget=budget,
            scale_profile=resolve_scale_profile((job.get("payload") or {}).get("scale_profile") or job.get("scale_profile") or {}),
            provider_body_limit_bytes=self._provider_body_limit(),
        )
        if self.llm_client is None:
            payload = self._skipped(agent_run_id, run_id, role, job, budget, context_hash, required, guard)
            payload.update(self._timing_fields(queued_at=queued_at, started_at=queued_at, finished_at=time.time(), slot={}))
            return payload

        messages = [{"role": "user", "content": prompt_text}]
        started = time.time()
        provider = provider_identity(self.llm_client)
        profile = resolve_scale_profile((job.get("payload") or {}).get("scale_profile") or job.get("scale_profile") or {})
        provider_limit = max(1, int(profile.get("ai_provider_concurrency") or self.limits.provider_concurrency))
        run_limit = max(1, int(profile.get("ai_run_concurrency") or self.limits.run_concurrency))
        wait_seconds = max(0, int(profile.get("ai_slot_wait_seconds") or 0))
        slot: dict[str, Any] = {}
        try:
            if store is not None:
                slot = store.acquire_ai_slot(
                    tenant_id=tenant_id or str(job.get("tenant_id") or ""),
                    run_id=run_id,
                    provider=provider,
                    agent_run_id=agent_run_id,
                    task_kind=task_kind,
                    provider_limit=provider_limit,
                    run_limit=run_limit,
                    wait_seconds=wait_seconds,
                    lease_seconds=lease_seconds,
                )
        except StoreError as exc:
            elapsed_ms = round((time.time() - started) * 1000)
            classification = {"error_kind": "ai_concurrency_limited", "retryable": True, "reason": str(exc), "backoff_seconds": max(1, wait_seconds), "error_preview": str(exc)[:1000]}
            health = self._record_failure(store, provider, str(exc), classification)
            return self._failed(agent_run_id, run_id, role, job, budget, context_hash, elapsed_ms, str(exc), classification, health, guard, queued_at=queued_at, started_at=started, finished_at=time.time(), slot=slot)
        started = time.time()
        provider_context = nullcontext() if store is not None else self._provider
        run_context = nullcontext() if store is not None else self._run_semaphore(run_id)
        with self._global, provider_context, run_context:
            try:
                if heartbeat_callback:
                    heartbeat_callback()
                raw = self.llm_client.chat(
                    system_prompt,
                    messages,
                    max_tokens_override=budget.max_output_tokens,
                    timeout_override=budget.timeout_seconds,
                    reasoning_effort_override=budget.reasoning_effort,
                    retry_attempts_override=budget.retry_attempts,
                )
            except LLMError as exc:
                elapsed_ms = round((time.time() - started) * 1000)
                classification = classify_provider_error(exc)
                health = self._record_failure(store, provider, str(exc), classification)
                if slot:
                    store.release_ai_slot(slot["id"])
                return self._failed(agent_run_id, run_id, role, job, budget, context_hash, elapsed_ms, str(exc), classification, health, guard, queued_at=queued_at, started_at=started, finished_at=time.time(), slot=slot)
            except Exception:
                if slot:
                    store.release_ai_slot(slot["id"])
                raise
            finally:
                if heartbeat_callback:
                    heartbeat_callback()

        elapsed_ms = round((time.time() - started) * 1000)
        if slot:
            store.release_ai_slot(slot["id"])
        finished_at = time.time()
        provider_health = self._record_success(store, provider)
        return {
            "schema_version": "6.1",
            "ok": True,
            "agent_run_id": agent_run_id,
            "role": role,
            "job_id": job.get("id", ""),
            "job_type": job.get("job_type", ""),
            "run_id": run_id,
            "task_kind": budget.task_kind,
            "model": getattr(self.llm_client.config, "model", ""),
            "model_tier": budget.model_tier,
            "degraded": False,
            "quality_risk": "normal",
            "allow_degraded": budget.allow_degraded,
            "elapsed_ms": elapsed_ms,
            "timeout_seconds": budget.timeout_seconds,
            "timeout_exceeded": elapsed_ms > budget.timeout_seconds * 1000,
            "over_budget": self._payload_over_budget(guard),
            "payload_over_budget": self._payload_over_budget(guard),
            "budget": budget.to_dict(),
            "context_hash": context_hash,
            "prompt_summary": self._summary(user_payload),
            **self._guard_fields(guard),
            "raw_response": raw,
            "output_hash": hashlib.sha256(str(raw).encode("utf-8")).hexdigest(),
            "provider_health": provider_health,
            "live_provider": self._is_live_provider(),
            **self._timing_fields(queued_at=queued_at, started_at=started, finished_at=finished_at, slot=slot),
        }

    def _budget_for_job(self, task_kind: str, job: dict[str, Any]) -> AITaskBudget:
        profile_payload = (job.get("payload") or {}).get("scale_profile") or job.get("scale_profile") or {}
        profile = resolve_scale_profile(profile_payload)
        return resolve_ai_task_budget(task_kind, profile)

    def _run_semaphore(self, run_id: str) -> threading.BoundedSemaphore:
        with self._lock:
            if run_id not in self._run_locks:
                self._run_locks[run_id] = threading.BoundedSemaphore(self.limits.run_concurrency)
            return self._run_locks[run_id]

    def _provider_body_limit(self) -> int:
        config = getattr(self.llm_client, "config", None)
        return int(getattr(config, "max_request_body_bytes", 0) or DEFAULT_PROVIDER_BODY_LIMIT_BYTES)

    def _is_live_provider(self) -> bool:
        return isinstance(self.llm_client, OpenAICompatibleClient) and not bool(getattr(getattr(self.llm_client, "config", None), "use_mock", False))

    def _guard_fields(self, guard: dict[str, Any] | None) -> dict[str, Any]:
        guard = guard or {}
        return {
            "payload_chars": int(guard.get("payload_chars") or 0),
            "payload_bytes": int(guard.get("payload_bytes") or 0),
            "body_bytes": int(guard.get("body_bytes") or 0),
            "estimated_tokens": int(guard.get("estimated_tokens") or 0),
            "budget_limit": int(guard.get("budget_limit") or 0),
            "provider_body_limit_bytes": int(guard.get("provider_body_limit_bytes") or 0),
            "budget_status": str(guard.get("budget_status") or ""),
            "compression_applied": bool(guard.get("compression_applied")),
            "summary_agent_run_id": str(guard.get("summary_agent_run_id") or ""),
            "payload_budget": guard,
        }

    def _payload_over_budget(self, guard: dict[str, Any] | None) -> bool:
        guard = guard or {}
        if "ok" in guard:
            return not bool(guard.get("ok"))
        status = str(guard.get("budget_status") or "")
        return bool(status and status not in {"within_budget", "trimmed_within_budget", "ai_compressed_within_budget"})

    def _skipped(
        self,
        agent_run_id: str,
        run_id: str,
        role: str,
        job: dict[str, Any],
        budget: AITaskBudget,
        context_hash: str,
        required: bool,
        guard: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "6.1",
            "ok": True,
            "skipped": True,
            "reason": "llm_client_not_configured_for_local_test",
            "agent_run_id": agent_run_id,
            "role": role,
            "job_id": job.get("id", ""),
            "job_type": job.get("job_type", ""),
            "run_id": run_id,
            "task_kind": budget.task_kind,
            "model": "",
            "model_tier": budget.model_tier,
            "degraded": False,
            "quality_risk": "normal",
            "required": required,
            "budget": budget.to_dict(),
            "context_hash": context_hash,
            "timeout_exceeded": False,
            "over_budget": self._payload_over_budget(guard),
            "payload_over_budget": self._payload_over_budget(guard),
            **self._guard_fields(guard),
            "live_provider": False,
        }

    def _failed(
        self,
        agent_run_id: str,
        run_id: str,
        role: str,
        job: dict[str, Any],
        budget: AITaskBudget,
        context_hash: str,
        elapsed_ms: int,
        error: str,
        classification: dict[str, Any] | None = None,
        provider_health: dict[str, Any] | None = None,
        guard: dict[str, Any] | None = None,
        queued_at: float | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
        slot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        classification = classification or classify_provider_error(error)
        return {
            "schema_version": "6.1",
            "ok": False,
            "agent_run_id": agent_run_id,
            "role": role,
            "job_id": job.get("id", ""),
            "job_type": job.get("job_type", ""),
            "run_id": run_id,
            "task_kind": budget.task_kind,
            "model": getattr(self.llm_client.config, "model", "") if self.llm_client else "",
            "model_tier": budget.model_tier,
            "degraded": False,
            "quality_risk": "blocked",
            "allow_degraded": budget.allow_degraded,
            "elapsed_ms": elapsed_ms,
            "timeout_seconds": budget.timeout_seconds,
            "timeout_exceeded": elapsed_ms > budget.timeout_seconds * 1000,
            "over_budget": self._payload_over_budget(guard),
            "payload_over_budget": self._payload_over_budget(guard),
            "budget": budget.to_dict(),
            "context_hash": context_hash,
            "error": error,
            **self._guard_fields(guard),
            "error_kind": classification.get("error_kind", "provider_unknown"),
            "retryable": bool(classification.get("retryable")),
            "retry_reason": classification.get("reason", ""),
            "retry_after_seconds": int(classification.get("backoff_seconds") or 0),
            "provider_health": provider_health or {},
            "live_provider": self._is_live_provider(),
            **self._timing_fields(queued_at=queued_at or time.time(), started_at=started_at or time.time(), finished_at=finished_at or time.time(), slot=slot or {}),
        }

    def _summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        keys = sorted(payload.keys())
        return {"keys": keys, "bytes": len(stable_json(payload))}

    def _record_success(self, store: V6Store | None, provider: str) -> dict[str, Any]:
        if store is not None:
            return store.record_provider_success(provider)
        return self.provider_health.record_success(provider)

    def _record_failure(self, store: V6Store | None, provider: str, error: str, classification: dict[str, Any]) -> dict[str, Any]:
        if store is not None:
            return store.record_provider_failure(provider, error, classification)
        return self.provider_health.record_failure(provider, error, classification)

    def _timing_fields(self, *, queued_at: float, started_at: float, finished_at: float, slot: dict[str, Any]) -> dict[str, Any]:
        wait_ms = int(slot.get("wait_ms") or round(max(0.0, started_at - queued_at) * 1000))
        return {
            "queued_at": queued_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "wait_ms": wait_ms,
            "slot_id": str(slot.get("id") or ""),
            "concurrency_limited": wait_ms > 0,
        }

