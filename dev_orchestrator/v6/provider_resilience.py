from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


TRANSIENT_TOKENS = (
    "remote disconnected",
    "connection aborted",
    "connection reset",
    "connection refused",
    "unexpected_eof",
    "eof occurred",
    "eof while reading",
    "server disconnected",
    "temporarily unavailable",
    "timeout",
    "timed out",
    "read timed out",
    "rate limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "gateway",
    "network",
    "transport",
)

NON_RETRYABLE_TOKENS = (
    "invalid api key",
    "incorrect api key",
    "authentication",
    "unauthorized",
    "permission denied",
    "forbidden",
    "model not found",
    "unsupported model",
    "invalid model",
    "context length",
    "maximum context",
    "400 bad request",
    "401",
    "403",
    "404",
)

PAYLOAD_OVERSIZE_TOKENS = (
    "context length",
    "maximum context",
    "context_length_exceeded",
    "request too large",
    "payload too large",
    "body too large",
    "413",
)


def classify_provider_error(error: str | Exception) -> dict[str, Any]:
    text = str(error or "")
    lowered = text.lower()
    payload_oversize = next((token for token in PAYLOAD_OVERSIZE_TOKENS if token in lowered), "")
    if payload_oversize:
        return {
            "error_kind": "provider_payload_oversize",
            "retryable": False,
            "reason": payload_oversize,
            "backoff_seconds": 0,
            "error_preview": text[:1000],
        }
    non_retryable = next((token for token in NON_RETRYABLE_TOKENS if token in lowered), "")
    if non_retryable:
        return {
            "error_kind": "provider_non_retryable",
            "retryable": False,
            "reason": non_retryable,
            "backoff_seconds": 0,
            "error_preview": text[:1000],
        }
    transient = next((token for token in TRANSIENT_TOKENS if token in lowered), "")
    if transient:
        backoff = _backoff_for_reason(transient)
        return {
            "error_kind": "provider_transient",
            "retryable": True,
            "reason": transient,
            "backoff_seconds": backoff,
            "error_preview": text[:1000],
        }
    return {
        "error_kind": "provider_unknown",
        "retryable": False,
        "reason": "unclassified",
        "backoff_seconds": 0,
        "error_preview": text[:1000],
    }


def _backoff_for_reason(reason: str) -> int:
    if reason in {"rate limit", "too many requests", "429"}:
        return 12
    if reason in {"timeout", "timed out", "read timed out"}:
        return 8
    if reason in {"500", "502", "503", "504", "gateway"}:
        return 6
    return 4


def provider_identity(llm_client: Any) -> str:
    config = getattr(llm_client, "config", None)
    if config is None:
        return "unconfigured"
    profile = str(getattr(config, "provider_profile", "") or "").strip()
    base = str(getattr(config, "api_base", "") or "").strip()
    model = str(getattr(config, "model", "") or "").strip()
    wire_api = str(getattr(config, "wire_api", "") or "").strip()
    parts = [part for part in (profile or "custom", wire_api, base, model) if part]
    return " | ".join(parts) if parts else "unknown-provider"


@dataclass
class ProviderState:
    provider: str
    status: str = "healthy"
    success_count: int = 0
    failure_count: int = 0
    failure_streak: int = 0
    last_error_kind: str = ""
    last_error: str = ""
    last_event_at: float = field(default_factory=time.time)
    retry_after_seconds: int = 0
    circuit_open_until: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "failure_streak": self.failure_streak,
            "last_error_kind": self.last_error_kind,
            "last_error": self.last_error,
            "last_event_at": self.last_event_at,
            "retry_after_seconds": self.retry_after_seconds,
            "circuit_open_until": self.circuit_open_until,
            "cooldown_remaining_seconds": max(0, round(self.circuit_open_until - time.time())),
        }


class ProviderHealthMap:
    def __init__(self) -> None:
        self._states: dict[str, ProviderState] = {}

    def record_success(self, provider: str) -> dict[str, Any]:
        state = self._state(provider)
        state.success_count += 1
        state.failure_streak = 0
        state.status = "healthy"
        state.last_error_kind = ""
        state.retry_after_seconds = 0
        state.circuit_open_until = 0.0
        state.last_event_at = time.time()
        return state.to_dict()

    def record_failure(self, provider: str, error: str, classification: dict[str, Any]) -> dict[str, Any]:
        state = self._state(provider)
        state.failure_count += 1
        state.failure_streak += 1
        state.last_error_kind = str(classification.get("error_kind") or "provider_unknown")
        state.last_error = str(error or "")[:1000]
        state.retry_after_seconds = int(classification.get("backoff_seconds") or 0)
        state.last_event_at = time.time()
        if classification.get("retryable"):
            state.status = "circuit_open" if state.failure_streak >= 5 else "degraded"
            state.circuit_open_until = time.time() + state.retry_after_seconds if state.retry_after_seconds else 0.0
        else:
            state.status = "blocked"
            state.circuit_open_until = 0.0
        return state.to_dict()

    def snapshot(self) -> dict[str, Any]:
        items = [state.to_dict() for state in self._states.values()]
        degraded = [item for item in items if item["status"] in {"degraded", "circuit_open", "blocked"}]
        return {
            "schema_version": "6.2",
            "ok": not any(item["status"] == "blocked" for item in items),
            "provider_count": len(items),
            "degraded_count": len(degraded),
            "items": items,
        }

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._states = {}
        for item in snapshot.get("items") or []:
            provider = str(item.get("provider") or "")
            if not provider:
                continue
            self._states[provider] = ProviderState(
                provider=provider,
                status=str(item.get("status") or "healthy"),
                success_count=int(item.get("success_count") or 0),
                failure_count=int(item.get("failure_count") or 0),
                failure_streak=int(item.get("failure_streak") or 0),
                last_error_kind=str(item.get("last_error_kind") or ""),
                last_error=str(item.get("last_error") or ""),
                last_event_at=float(item.get("last_event_at") or time.time()),
                retry_after_seconds=int(item.get("retry_after_seconds") or 0),
                circuit_open_until=float(item.get("circuit_open_until") or 0.0),
            )

    def _state(self, provider: str) -> ProviderState:
        key = provider or "unknown-provider"
        if key not in self._states:
            self._states[key] = ProviderState(provider=key)
        return self._states[key]
