from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any

from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v4.llm_policy import AITaskBudget, get_ai_task_budget
from dev_orchestrator.v4.models import new_id, stable_json


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
    ) -> dict[str, Any]:
        budget = get_ai_task_budget(task_kind)
        agent_run_id = new_id()
        prompt_text = stable_json(user_payload)
        context_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        if self.llm_client is None:
            return self._skipped(agent_run_id, run_id, role, job, budget, context_hash, required)

        messages = [{"role": "user", "content": prompt_text}]
        started = time.time()
        with self._global, self._provider, self._run_semaphore(run_id):
            try:
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
                return self._failed(agent_run_id, run_id, role, job, budget, context_hash, elapsed_ms, str(exc))

        elapsed_ms = round((time.time() - started) * 1000)
        return {
            "schema_version": "4.5",
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
            "over_budget": elapsed_ms > budget.timeout_seconds * 1000,
            "budget": budget.to_dict(),
            "context_hash": context_hash,
            "prompt_summary": self._summary(user_payload),
            "raw_response": raw,
            "output_hash": hashlib.sha256(str(raw).encode("utf-8")).hexdigest(),
        }

    def _run_semaphore(self, run_id: str) -> threading.BoundedSemaphore:
        with self._lock:
            if run_id not in self._run_locks:
                self._run_locks[run_id] = threading.BoundedSemaphore(self.limits.run_concurrency)
            return self._run_locks[run_id]

    def _skipped(
        self,
        agent_run_id: str,
        run_id: str,
        role: str,
        job: dict[str, Any],
        budget: AITaskBudget,
        context_hash: str,
        required: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": "4.5",
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
    ) -> dict[str, Any]:
        return {
            "schema_version": "4.5",
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
            "over_budget": elapsed_ms > budget.timeout_seconds * 1000,
            "budget": budget.to_dict(),
            "context_hash": context_hash,
            "error": error,
        }

    def _summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        keys = sorted(payload.keys())
        return {"keys": keys, "bytes": len(stable_json(payload))}
