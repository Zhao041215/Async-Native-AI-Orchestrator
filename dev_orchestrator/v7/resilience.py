"""V7 Circuit breaker and error classification module.

Classifies errors by *exception type* (not substring matching) and implements
a three-state circuit breaker per provider: closed -> open -> half_open -> closed.
Non-retryable errors transition to ``blocked`` which never auto-recovers.
"""
from __future__ import annotations

import asyncio
import random
import time
from enum import Enum

import httpx

from dev_orchestrator.llm_client import LLMError
from dev_orchestrator.v7.models import ErrorClassification, ProviderState, ProviderHealthStatus


# ---------------------------------------------------------------------------
# Error classification (type-based, not substring)
# ---------------------------------------------------------------------------

_CONTEXT_LENGTH_TOKENS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
)


def classify_error(error: Exception) -> ErrorClassification:
    """Classify an exception by its *type*, returning an ``ErrorClassification``.

    Unlike the V6 substring-based approach this function inspects the concrete
    exception class and (where relevant) HTTP status codes to decide the
    category.
    """

    # --- httpx.HTTPStatusError --- match by status code -------------------
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        retry_after = _parse_retry_after(error.response.headers)

        if status == 429:
            return ErrorClassification(
                error_kind="rate_limited",
                retryable=True,
                backoff_seconds=float(retry_after or 12),
                reason=f"HTTP 429 rate-limited (retry-after={retry_after})",
            )

        if status in (500, 502, 503, 504):
            return ErrorClassification(
                error_kind="server_error",
                retryable=True,
                backoff_seconds=6.0,
                reason=f"HTTP {status} server error",
            )

        if status in (401, 403):
            return ErrorClassification(
                error_kind="auth_error",
                retryable=False,
                backoff_seconds=0.0,
                reason=f"HTTP {status} authentication/authorization failure",
            )

        if status == 400:
            body = str(error.response.text or "").lower()
            is_context = any(tok in body for tok in _CONTEXT_LENGTH_TOKENS)
            return ErrorClassification(
                error_kind="payload_oversize" if is_context else "bad_request",
                retryable=False,
                backoff_seconds=0.0,
                reason="context length exceeded" if is_context else f"HTTP 400 bad request",
            )

        if status == 404:
            return ErrorClassification(
                error_kind="not_found",
                retryable=False,
                backoff_seconds=0.0,
                reason="HTTP 404 not found",
            )

        if status == 413:
            return ErrorClassification(
                error_kind="payload_oversize",
                retryable=False,
                backoff_seconds=0.0,
                reason="HTTP 413 payload too large",
            )

        # Other 4xx / 5xx not explicitly handled above
        return ErrorClassification(
            error_kind="http_error",
            retryable=status >= 500,
            backoff_seconds=4.0 if status >= 500 else 0.0,
            reason=f"HTTP {status}",
        )

    # --- httpx transport errors (connection / read / write / protocol) ---
    if isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)):
        return ErrorClassification(
            error_kind="connection_error",
            retryable=True,
            backoff_seconds=4.0,
            reason=f"{type(error).__name__}: {error}",
        )

    # --- httpx timeout ----------------------------------------------------
    if isinstance(error, httpx.TimeoutException):
        return ErrorClassification(
            error_kind="timeout",
            retryable=True,
            backoff_seconds=8.0,
            reason=f"Timeout: {error}",
        )

    # --- LLMError wrapping HTTP status codes ------------------------------
    if isinstance(error, LLMError):
        msg = str(error)
        # Parse "HTTP {status}: {body}" pattern from llm_client
        if msg.startswith("HTTP "):
            try:
                status = int(msg.split(":")[0].split()[1])
            except (IndexError, ValueError):
                status = 0
            if status == 429:
                return ErrorClassification(error_kind="rate_limited", retryable=True, backoff_seconds=12.0, reason=msg[:200])
            if status in (500, 502, 503, 504):
                return ErrorClassification(error_kind="server_error", retryable=True, backoff_seconds=6.0, reason=msg[:200])
            if status in (401, 403):
                return ErrorClassification(error_kind="auth_error", retryable=False, backoff_seconds=0.0, reason=msg[:200])
            if status >= 500:
                return ErrorClassification(error_kind="server_error", retryable=True, backoff_seconds=4.0, reason=msg[:200])
            return ErrorClassification(error_kind="http_error", retryable=False, backoff_seconds=0.0, reason=msg[:200])
        if "Timed out" in msg or "timeout" in msg.lower():
            return ErrorClassification(error_kind="timeout", retryable=True, backoff_seconds=8.0, reason=msg[:200])
        if "Connection error" in msg or "disconnected" in msg.lower() or "server" in msg.lower():
            return ErrorClassification(error_kind="connection_error", retryable=True, backoff_seconds=4.0, reason=msg[:200])

    # --- Anything else ----------------------------------------------------
    return ErrorClassification(
        error_kind="unknown",
        retryable=False,
        backoff_seconds=0.0,
        reason=f"{type(error).__name__}: {error}",
    )


def _parse_retry_after(headers: httpx.Headers) -> int:
    """Extract ``Retry-After`` header value as integer seconds (0 if absent)."""
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitState(str, Enum):
    """Three-state circuit breaker plus a terminal ``blocked`` state."""

    closed = "closed"
    open = "open"
    half_open = "half_open"
    blocked = "blocked"


class ProviderCircuitBreaker:
    """Per-provider circuit breaker with jittered exponential backoff.

    State machine
    -------------
    * **closed** -- healthy; requests pass through.  After ``failure_threshold``
      consecutive failures the circuit moves to *open*.
    * **open** -- rejecting requests.  After ``recovery_timeout`` seconds the
      circuit moves to *half_open* to allow a single probe.
    * **half_open** -- exactly **one** in-flight probe is allowed.  Success
      returns to *closed*; failure re-opens.
    * **blocked** -- non-retryable error encountered.  Never auto-recovers;
      requires explicit ``reset()``.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._states: dict[str, ProviderState] = {}
        self._half_open_inflight: set[str] = set()  # providers with an active half-open probe
        self._lock = asyncio.Lock()

    # -- public API --------------------------------------------------------

    async def allow_request(self, provider: str) -> bool:
        """Return ``True`` if a request to *provider* is allowed right now."""
        async with self._lock:
            state = self._ensure(provider)
            now = time.time()

            if state.status == ProviderHealthStatus.blocked:
                return False

            if state.status == ProviderHealthStatus.circuit_open:
                if now >= state.circuit_open_until:
                    # Transition to half-open
                    state.status = ProviderHealthStatus.half_open
                    state.last_event_at = now
                    return True  # allow the probe
                return False

            if state.status == ProviderHealthStatus.half_open:
                # Allow exactly one probe; reject if one is already in-flight
                if provider in self._half_open_inflight:
                    return False
                self._half_open_inflight.add(provider)
                return True

            # closed -- always allow
            return True

    async def record_success(self, provider: str) -> ProviderState:
        """Record a successful call to *provider*."""
        async with self._lock:
            self._half_open_inflight.discard(provider)
            state = self._ensure(provider)
            now = time.time()
            state.success_count += 1
            state.failure_streak = 0
            state.last_error_kind = ""
            state.last_error = ""
            state.last_event_at = now
            state.retry_after_seconds = 0
            state.circuit_open_until = 0.0
            state.status = ProviderHealthStatus.healthy
            return state.model_copy()

    async def record_failure(
        self,
        provider: str,
        error: str,
        classification: ErrorClassification,
    ) -> ProviderState:
        """Record a failed call and update circuit state accordingly."""
        async with self._lock:
            self._half_open_inflight.discard(provider)
            state = self._ensure(provider)
            now = time.time()

            state.failure_count += 1
            state.failure_streak += 1
            state.last_error_kind = classification.error_kind
            state.last_error = str(error)[:1000]
            state.last_event_at = now

            # Non-retryable -> blocked (terminal)
            if not classification.retryable:
                state.status = ProviderHealthStatus.blocked
                state.retry_after_seconds = 0
                state.circuit_open_until = 0.0
                return state.model_copy()

            # Retryable -- apply jittered backoff
            jitter = random.uniform(0.8, 1.2)
            backoff = classification.backoff_seconds * jitter
            state.retry_after_seconds = int(classification.backoff_seconds)

            if state.failure_streak >= self._failure_threshold:
                state.status = ProviderHealthStatus.circuit_open
                state.circuit_open_until = now + backoff
            else:
                state.status = ProviderHealthStatus.degraded
                state.circuit_open_until = 0.0

            return state.model_copy()

    async def get_state(self, provider: str) -> ProviderState:
        """Return a *copy* of the current state for *provider*."""
        async with self._lock:
            return self._ensure(provider).model_copy()

    async def snapshot(self) -> dict[str, ProviderState]:
        """Return a snapshot of all tracked provider states."""
        async with self._lock:
            return {k: v.model_copy() for k, v in self._states.items()}

    async def reset(self, provider: str) -> ProviderState:
        """Explicitly reset a provider to healthy (useful to unblock ``blocked``)."""
        async with self._lock:
            state = self._ensure(provider)
            now = time.time()
            state.status = ProviderHealthStatus.healthy
            state.failure_streak = 0
            state.last_error_kind = ""
            state.last_error = ""
            state.last_event_at = now
            state.retry_after_seconds = 0
            state.circuit_open_until = 0.0
            return state.model_copy()

    # -- internals ---------------------------------------------------------

    def _ensure(self, provider: str) -> ProviderState:
        """Return existing state or create a fresh one (caller must hold lock)."""
        key = provider or "unknown-provider"
        if key not in self._states:
            self._states[key] = ProviderState(provider=key)
        return self._states[key]
