"""Async LLM client using httpx with connection pooling and proper error handling."""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from dev_orchestrator.config import LLMConfig


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str
    description: str
    default_wire_api: str
    default_api_path: str
    supports_responses: bool
    supports_chat_completions: bool

    def to_dict(self) -> dict:
        return asdict(self)


PROVIDER_PROFILES: dict[str, ProviderProfile] = {
    "openai-chat-completions": ProviderProfile(
        id="openai-chat-completions",
        label="OpenAI Chat Completions",
        description="OpenAI-compatible /chat/completions endpoint.",
        default_wire_api="chat_completions",
        default_api_path="/chat/completions",
        supports_responses=False,
        supports_chat_completions=True,
    ),
    "openai-responses": ProviderProfile(
        id="openai-responses",
        label="OpenAI Responses",
        description="OpenAI Responses API-compatible /responses endpoint.",
        default_wire_api="responses",
        default_api_path="/responses",
        supports_responses=True,
        supports_chat_completions=False,
    ),
    "custom-chat-compatible": ProviderProfile(
        id="custom-chat-compatible",
        label="Custom Chat Compatible",
        description="Third-party OpenAI-style chat completions endpoint.",
        default_wire_api="chat_completions",
        default_api_path="/chat/completions",
        supports_responses=False,
        supports_chat_completions=True,
    ),
    "custom-responses-compatible": ProviderProfile(
        id="custom-responses-compatible",
        label="Custom Responses Compatible",
        description="Third-party OpenAI-style responses endpoint.",
        default_wire_api="responses",
        default_api_path="/responses",
        supports_responses=True,
        supports_chat_completions=False,
    ),
}

PROFILE_ALIASES = {
    "chat": "openai-chat-completions",
    "chat-completions": "openai-chat-completions",
    "chat_completions": "openai-chat-completions",
    "responses": "openai-responses",
    "response": "openai-responses",
}


def list_provider_profiles() -> list[dict]:
    return [profile.to_dict() for profile in PROVIDER_PROFILES.values()]


def _normalize_wire_api(value: str) -> str:
    normalized = (value or "chat_completions").strip().lower().replace("-", "_")
    if normalized in {"chat", "chat_completion", "chat_completions"}:
        return "chat_completions"
    if normalized in {"response", "responses"}:
        return "responses"
    raise LLMError(f"Unsupported wire API: {value}")


def resolve_provider_profile(config: LLMConfig) -> ProviderProfile:
    requested = (config.provider_profile or "").strip().lower().replace("_", "-")
    if not requested:
        wire_api = _normalize_wire_api(config.wire_api)
        requested = "openai-responses" if wire_api == "responses" else "openai-chat-completions"
    requested = PROFILE_ALIASES.get(requested, requested)
    profile = PROVIDER_PROFILES.get(requested)
    if not profile:
        raise LLMError(f"Unsupported provider profile: {config.provider_profile}")
    return profile


def _effective_wire_api(config: LLMConfig, profile: ProviderProfile) -> str:
    if (config.provider_profile or "").strip():
        return profile.default_wire_api
    return _normalize_wire_api(config.wire_api or profile.default_wire_api)


def _effective_capability(config_value: bool | None, profile_value: bool) -> bool:
    return profile_value if config_value is None else bool(config_value)


def _build_url(config: LLMConfig, profile: ProviderProfile) -> str:
    api_base = (config.api_base or "").strip()
    api_path = (config.api_path or profile.default_api_path).strip() or profile.default_api_path
    if api_path.startswith(("http://", "https://")):
        return api_path
    parsed = urlparse(api_base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMError("Invalid API base URL. Use an absolute http(s) URL.")
    path = api_path if api_path.startswith("/") else f"/{api_path}"
    return api_base.rstrip("/") + path


def _build_headers(config: LLMConfig) -> dict[str, str]:
    auth_header = (config.auth_header or "Authorization").strip() or "Authorization"
    auth_scheme = (config.auth_scheme or "").strip()
    auth_value = f"{auth_scheme} {config.api_key}" if auth_scheme else config.api_key
    headers = {
        "Content-Type": "application/json",
        auth_header: auth_value,
    }
    for key, value in (config.extra_headers or {}).items():
        if str(key).strip():
            headers[str(key)] = str(value)
    return headers


def _build_payload(
    config: LLMConfig,
    wire_api: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
) -> dict:
    effective_temperature = config.temperature if temperature is None else temperature
    effective_reasoning_effort = config.model_reasoning_effort if reasoning_effort is None else reasoning_effort
    if wire_api == "responses":
        payload = {
            "model": config.model,
            "input": [{"role": "system", "content": system_prompt}] + messages,
            "temperature": effective_temperature,
            "max_output_tokens": max_tokens,
        }
        if effective_reasoning_effort:
            payload["reasoning"] = {"effort": effective_reasoning_effort}
        if config.disable_response_storage:
            payload["store"] = False
        return payload
    return {
        "model": config.model,
        "temperature": effective_temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
    }


def _stringify_content_item(item: object) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    if isinstance(item.get("text"), str):
        return item["text"]
    if isinstance(item.get("content"), str):
        return item["content"]
    if isinstance(item.get("output_text"), str):
        return item["output_text"]
    if item.get("type") == "text":
        text_payload = item.get("text")
        if isinstance(text_payload, dict) and isinstance(text_payload.get("value"), str):
            return text_payload["value"]
    return ""


def _extract_message_content(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text = "".join(_stringify_content_item(item) for item in content)
        return text.strip()
    for fallback_key in ("text", "output_text"):
        value = message.get(fallback_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            text = "".join(_stringify_content_item(item) for item in value)
            if text.strip():
                return text.strip()
    tool_calls = message.get("tool_calls")
    if tool_calls:
        return json.dumps({"done": False, "summary": "", "artifacts": [], "tool_calls": tool_calls}, ensure_ascii=True)
    return ""


def _extract_chat_completions_content(response_payload: dict) -> str:
    choices = response_payload.get("choices") or []
    if not choices:
        preview = json.dumps(response_payload, ensure_ascii=True)[:800]
        raise LLMError(f"No choices returned from model endpoint. Response preview: {preview}")
    message = choices[0].get("message") or {}
    extracted = _extract_message_content(message)
    if extracted:
        return extracted
    preview = json.dumps(response_payload, ensure_ascii=True)[:800]
    raise LLMError(f"Model response did not contain usable message content. Response preview: {preview}")


def _extract_responses_content(response_payload: dict) -> str:
    if isinstance(response_payload.get("output_text"), str) and response_payload["output_text"].strip():
        return response_payload["output_text"].strip()
    outputs = response_payload.get("output") or []
    text_chunks: list[str] = []
    for output_item in outputs:
        if not isinstance(output_item, dict):
            continue
        content_items = output_item.get("content") or []
        if isinstance(content_items, list):
            for content_item in content_items:
                chunk = _stringify_content_item(content_item)
                if chunk:
                    text_chunks.append(chunk)
    combined = "".join(text_chunks).strip()
    if combined:
        return combined
    preview = json.dumps(response_payload, ensure_ascii=True)[:800]
    raise LLMError(f"Responses API payload did not contain usable text output. Response preview: {preview}")


class AsyncLLMClient:
    """Async LLM client with httpx connection pooling and proper error handling."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._client_lock:
            # Double-check after acquiring lock
            if self._client is not None and not self._client.is_closed:
                return self._client
            timeout_val = float(self.config.timeout_seconds or 90)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=timeout_val,
                    write=10.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=0,
                    keepalive_expiry=0,
                ),
                http1=True,
                http2=False,
                follow_redirects=True,
            )
            return self._client

    def provider_identity(self) -> str:
        try:
            profile = resolve_provider_profile(self.config)
            return profile.id
        except LLMError:
            parts = (self.config.api_base or "unknown").split("/")
            return parts[2] if len(parts) > 2 else "unknown"

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        timeout: int | None = None,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        retry_attempts: int | None = None,
    ) -> str:
        if self.config.use_mock:
            raise LLMError("Mock mode is enabled; remote chat should not be called.")
        if not self.config.api_base or not self.config.api_key:
            raise LLMError("Missing API base or API key.")

        profile = resolve_provider_profile(self.config)
        wire_api = _effective_wire_api(self.config, profile)
        supports_chat = _effective_capability(self.config.supports_chat_completions, profile.supports_chat_completions)
        supports_responses = _effective_capability(self.config.supports_responses, profile.supports_responses)
        if wire_api == "responses" and not supports_responses:
            raise LLMError(f"Provider profile {profile.id} does not support the Responses wire API.")
        if wire_api == "chat_completions" and not supports_chat:
            raise LLMError(f"Provider profile {profile.id} does not support the Chat Completions wire API.")

        effective_max_tokens = max_tokens or self.config.max_tokens
        effective_timeout = timeout or self.config.timeout_seconds

        payload = _build_payload(
            self.config, wire_api, system_prompt, messages,
            effective_max_tokens, reasoning_effort=reasoning_effort, temperature=temperature,
        )
        url = _build_url(self.config, profile)
        headers = _build_headers(self.config)

        data = json.dumps(payload).encode("utf-8")
        max_body = int(getattr(self.config, "max_request_body_bytes", 0) or 0)
        if max_body > 0 and len(data) > max_body:
            raise LLMError(f"Request body too large: {len(data)} bytes exceeds configured limit {max_body} bytes.")

        attempts = max(1, int(retry_attempts if retry_attempts is not None else self.config.retry_attempts or 1))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                client = await self._get_client()
                response = await client.post(url, content=data, headers=headers, timeout=float(effective_timeout))
                response.raise_for_status()
                response_payload = response.json()
                if wire_api == "responses":
                    return _extract_responses_content(response_payload)
                return _extract_chat_completions_content(response_payload)
            except httpx.HTTPStatusError as exc:
                body = ""
                try:
                    body = exc.response.text[:2000]
                except Exception:
                    pass
                last_error = LLMError(f"HTTP {exc.response.status_code}: {body}")
            except httpx.ConnectError as exc:
                last_error = LLMError(f"Connection error: {exc}")
            except httpx.ReadTimeout as exc:
                last_error = LLMError(f"Timed out after {effective_timeout} seconds: {exc}")
            except httpx.WriteTimeout as exc:
                last_error = LLMError(f"Write timed out: {exc}")
            except httpx.PoolTimeout as exc:
                last_error = LLMError(f"Pool timeout: {exc}")
            except json.JSONDecodeError:
                last_error = LLMError("Model endpoint returned non-JSON response.")
            except LLMError:
                raise
            except Exception as exc:
                last_error = LLMError(f"Unexpected error: {exc}")

            if attempt < attempts:
                backoff = float(self.config.retry_backoff_seconds or 3)
                jitter = random.uniform(0.5, 1.5)
                await asyncio.sleep(backoff * jitter)

        if last_error is not None:
            raise last_error
        raise LLMError("All retry attempts exhausted with no error captured.")

    async def test_connection(self) -> dict[str, Any]:
        started = time.time()
        try:
            profile = resolve_provider_profile(self.config)
            response_text = await self.chat(
                "Reply with a short JSON object confirming model connectivity.",
                [{"role": "user", "content": 'Return {"ok": true, "message": "connected"}.'}],
                max_tokens=min(int(self.config.max_tokens or 64), 64),
                timeout=min(int(self.config.timeout_seconds or 30), 30),
            )
            return {
                "ok": True,
                "profile": profile.to_dict(),
                "wire_api": _effective_wire_api(self.config, profile),
                "url": _build_url(self.config, profile),
                "model": self.config.model,
                "elapsed_ms": round((time.time() - started) * 1000),
                "response_preview": response_text[:600],
            }
        except (LLMError, Exception) as exc:
            profile = None
            try:
                profile = resolve_provider_profile(self.config).to_dict()
            except LLMError:
                profile = {}
            return {
                "ok": False,
                "profile": profile,
                "wire_api": self.config.wire_api,
                "url": "",
                "model": self.config.model,
                "elapsed_ms": round((time.time() - started) * 1000),
                "error": str(exc),
            }

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Keep synchronous client for backward compatibility during transition
class OpenAICompatibleClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        *,
        max_tokens_override: int | None = None,
        timeout_override: int | None = None,
        reasoning_effort_override: str | None = None,
        temperature_override: float | None = None,
        retry_attempts_override: int | None = None,
    ) -> str:
        if self.config.use_mock:
            raise LLMError("Mock mode is enabled; remote chat should not be called.")
        if not self.config.api_base or not self.config.api_key:
            raise LLMError("Missing API base or API key.")

        profile = resolve_provider_profile(self.config)
        wire_api = _effective_wire_api(self.config, profile)
        supports_chat = _effective_capability(self.config.supports_chat_completions, profile.supports_chat_completions)
        supports_responses = _effective_capability(self.config.supports_responses, profile.supports_responses)
        if wire_api == "responses" and not supports_responses:
            raise LLMError(f"Provider profile {profile.id} does not support the Responses wire API.")
        if wire_api == "chat_completions" and not supports_chat:
            raise LLMError(f"Provider profile {profile.id} does not support the Chat Completions wire API.")

        effective_max_tokens = max_tokens_override or self.config.max_tokens
        effective_timeout = timeout_override or self.config.timeout_seconds

        payload = _build_payload(
            self.config, wire_api, system_prompt, messages,
            effective_max_tokens, reasoning_effort=reasoning_effort_override, temperature=temperature_override,
        )
        url = _build_url(self.config, profile)
        headers = _build_headers(self.config)

        data = json.dumps(payload).encode("utf-8")
        max_body = int(getattr(self.config, "max_request_body_bytes", 0) or 0)
        if max_body > 0 and len(data) > max_body:
            raise LLMError(f"Request body too large: {len(data)} bytes exceeds configured limit {max_body} bytes.")

        attempts_source = retry_attempts_override if retry_attempts_override is not None else self.config.retry_attempts
        attempts = max(1, int(attempts_source or 1))
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=float(effective_timeout)) as client:
                    response = client.post(url, content=data, headers=headers)
                    response.raise_for_status()
                    response_payload = response.json()
                if wire_api == "responses":
                    return _extract_responses_content(response_payload)
                return _extract_chat_completions_content(response_payload)
            except httpx.HTTPStatusError as exc:
                body = ""
                try:
                    body = exc.response.text[:2000]
                except Exception:
                    pass
                last_error = LLMError(f"HTTP {exc.response.status_code}: {body}")
            except httpx.ConnectError as exc:
                last_error = LLMError(f"Connection error: {exc}")
            except httpx.TimeoutException as exc:
                last_error = LLMError(f"Timed out after {effective_timeout} seconds: {exc}")
            except json.JSONDecodeError:
                last_error = LLMError("Model endpoint returned non-JSON response.")
            except LLMError:
                raise
            except Exception as exc:
                last_error = LLMError(f"Unexpected error: {exc}")

            if attempt < attempts:
                time.sleep(max(0, int(self.config.retry_backoff_seconds or 0)))

        if last_error is not None:
            raise last_error
        raise LLMError("All retry attempts exhausted.")

    def build_request_preview(
        self,
        system_prompt: str = "You are testing model connectivity.",
        messages: list[dict] | None = None,
        *,
        max_tokens_override: int | None = None,
        reasoning_effort_override: str | None = None,
    ) -> dict:
        profile = resolve_provider_profile(self.config)
        wire_api = _effective_wire_api(self.config, profile)
        max_tokens = max_tokens_override or self.config.max_tokens
        payload = _build_payload(
            self.config, wire_api, system_prompt, messages or [],
            max_tokens, reasoning_effort=reasoning_effort_override,
        )
        headers = _build_headers(self.config)
        masked_headers = {
            key: "***" if key.lower() in {"authorization", "x-api-key", "api-key"} else value
            for key, value in headers.items()
        }
        return {
            "profile": profile.to_dict(),
            "wire_api": wire_api,
            "url": _build_url(self.config, profile),
            "headers": masked_headers,
            "payload": payload,
        }

    def test_connection(self) -> dict:
        started = time.time()
        try:
            profile = resolve_provider_profile(self.config)
            response_text = self.chat(
                "Reply with a short JSON object confirming model connectivity.",
                [{"role": "user", "content": 'Return {"ok": true, "message": "connected"}.'}],
                max_tokens_override=min(int(self.config.max_tokens or 64), 64),
                timeout_override=min(int(self.config.timeout_seconds or 30), 30),
            )
            return {
                "ok": True,
                "profile": profile.to_dict(),
                "wire_api": _effective_wire_api(self.config, profile),
                "url": _build_url(self.config, profile),
                "model": self.config.model,
                "elapsed_ms": round((time.time() - started) * 1000),
                "response_preview": response_text[:600],
            }
        except (LLMError, Exception) as exc:
            profile = None
            try:
                profile = resolve_provider_profile(self.config).to_dict()
            except LLMError:
                profile = {}
            return {
                "ok": False,
                "profile": profile,
                "wire_api": self.config.wire_api,
                "url": "",
                "model": self.config.model,
                "elapsed_ms": round((time.time() - started) * 1000),
                "error": str(exc),
            }
