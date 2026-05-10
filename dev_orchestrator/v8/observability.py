"""Structured logging and metrics for the V7 orchestrator.

Uses structlog for JSON/console structured logging with automatic injection
of correlation_id, run_id, and job_id from async context variables.
"""

from __future__ import annotations

import contextvars
import logging
import time
from dataclasses import dataclass, field

import structlog

# ---------------------------------------------------------------------------
# Context variables for async-safe correlation propagation
# ---------------------------------------------------------------------------
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)
_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "job_id", default=None
)


def set_correlation_id(cid: str) -> contextvars.Token:
    """Set the correlation ID for the current async context."""
    return _correlation_id.set(cid)


def set_run_id(rid: str) -> contextvars.Token:
    """Set the run ID for the current async context."""
    return _run_id.set(rid)


def set_job_id(jid: str) -> contextvars.Token:
    """Set the job ID for the current async context."""
    return _job_id.set(jid)


# ---------------------------------------------------------------------------
# structlog processor that injects context vars into every log entry
# ---------------------------------------------------------------------------
def _add_context_vars(
    logger: logging.Logger, method_name: str, event_dict: dict
) -> dict:
    """structlog processor: attach correlation/run/job IDs to every log line."""
    cid = _correlation_id.get()
    rid = _run_id.get()
    jid = _job_id.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    if rid is not None:
        event_dict["run_id"] = rid
    if jid is not None:
        event_dict["job_id"] = jid
    return event_dict


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog with JSON renderer and stdlib integration.

    Call once at application startup.  Subsequent calls reconfigure silently.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_context_vars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger bound to *name*.

    Prefer ``get_logger(__name__)`` at module level.
    """
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------
@dataclass
class MetricsCollector:
    """Lightweight in-process metrics for AI calls and job outcomes."""

    ai_calls_total: int = 0
    ai_calls_success: int = 0
    ai_calls_failure: int = 0
    ai_latency_sum_ms: float = 0.0
    jobs_completed: int = 0
    jobs_failed: int = 0

    # per-call latency samples (ring buffer style, caller can trim)
    _latency_samples: list[float] = field(default_factory=list, repr=False)

    def record_ai_call(self, latency_ms: float, success: bool) -> None:
        """Record a single AI/LLM call."""
        self.ai_calls_total += 1
        self.ai_latency_sum_ms += latency_ms
        self._latency_samples.append(latency_ms)
        if success:
            self.ai_calls_success += 1
        else:
            self.ai_calls_failure += 1

    def record_job(self, success: bool) -> None:
        """Record completion of a job (success or failure)."""
        if success:
            self.jobs_completed += 1
        else:
            self.jobs_failed += 1

    @property
    def ai_call_success_rate(self) -> float:
        """Fraction of AI calls that succeeded (0.0 if no calls yet)."""
        if self.ai_calls_total == 0:
            return 0.0
        return self.ai_calls_success / self.ai_calls_total

    @property
    def ai_avg_latency_ms(self) -> float:
        """Average latency per AI call in milliseconds."""
        if self.ai_calls_total == 0:
            return 0.0
        return self.ai_latency_sum_ms / self.ai_calls_total

    @property
    def job_success_rate(self) -> float:
        """Fraction of jobs that completed successfully (0.0 if none yet)."""
        total = self.jobs_completed + self.jobs_failed
        if total == 0:
            return 0.0
        return self.jobs_completed / total

    def snapshot(self) -> dict:
        """Return a plain dict of all metrics (useful for health endpoints)."""
        return {
            "ai_calls_total": self.ai_calls_total,
            "ai_calls_success": self.ai_calls_success,
            "ai_calls_failure": self.ai_calls_failure,
            "ai_avg_latency_ms": round(self.ai_avg_latency_ms, 2),
            "ai_call_success_rate": round(self.ai_call_success_rate, 4),
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "job_success_rate": round(self.job_success_rate, 4),
        }
