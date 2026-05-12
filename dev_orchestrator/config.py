from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass
class LLMConfig:
    use_mock: bool = True
    api_base: str = ""
    api_key: str = ""
    model: str = "gpt-4.1"
    model_reasoning_effort: str = ""
    disable_response_storage: bool = False
    wire_api: str = "chat_completions"
    provider_profile: str = ""
    api_path: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    extra_headers: dict[str, str] = field(default_factory=dict)
    supports_responses: bool | None = None
    supports_chat_completions: bool | None = None
    temperature: float = 0.2
    max_tokens: int = 3200
    timeout_seconds: int = 90
    retry_attempts: int = 2
    retry_backoff_seconds: int = 3
    max_request_body_bytes: int = 950000


@dataclass
class RuntimeConfig:
    runtime_root: str = ""
    workspace_root: str = "workspace/projects"
    db_path: str = "workspace/orchestrator.db"
    database_url: str = "postgresql+psycopg://orchestrator:orchestrator@localhost:5432/orchestrator"
    logs_path: str = "logs"
    python_cmd: str = "python"
    max_shell_seconds: int = 120
    max_role_runs_per_task: int = 20
    max_llm_calls_per_task: int = 40
    max_conflicts_per_task: int = 8
    max_resume_attempts_per_task: int = 6
    max_failures_per_task: int = 8
    deployment_mode: str = "docker-compose"
    queue_mode: str = "postgres-durable"
    sandbox_mode: str = "tenant-governed-worktree"
    workspace_max_bytes: int = 50 * 1024 * 1024 * 1024
    gc_dry_run_default: bool = True
    exported_worktree_retention_days: int = 7
    artifact_retention_days: int = 30
    failed_run_retention_days: int = 30
    log_retention_days: int = 14
    pressure_retention_days: int = 7
    archive_enabled: bool = True


@dataclass
class IdentityConfig:
    mode: str = "hosted-dev-session"
    user_header: str = "X-Local-User"
    tenant_header: str = "X-Tenant-Id"
    default_user: str = "local-operator"
    tenant_mode: str = "required-tenant"
    default_tenant: str = "local-workspace"
    require_identity: bool = True
    require_tenant: bool = True


@dataclass
class ProductionScaffoldConfig:
    queue_backend: str = "postgres-durable"
    worker_model: str = "multi-process-worker"
    deployment_target: str = "docker-compose"
    diagnostics_enabled: bool = True
    future_auth_enabled: bool = True


@dataclass
class AppConfig:
    root_dir: Path
    config_path: Path
    server: ServerConfig
    llm: LLMConfig
    runtime: RuntimeConfig
    identity: IdentityConfig
    production: ProductionScaffoldConfig

    @property
    def workspace_root(self) -> Path:
        configured = self.runtime.runtime_root or self.runtime.workspace_root
        return (self.root_dir / configured).resolve()

    @property
    def db_path(self) -> Path:
        return (self.root_dir / self.runtime.db_path).resolve()

    @property
    def database_url(self) -> str:
        return self.runtime.database_url

    @property
    def logs_path(self) -> Path:
        return (self.root_dir / self.runtime.logs_path).resolve()

    def to_dict(self) -> dict:
        llm = _masked_llm_dict(self.llm)
        return {
            "server": asdict(self.server),
            "llm": llm,
            "runtime": asdict(self.runtime),
            "identity": asdict(self.identity),
            "production": asdict(self.production),
        }

    def to_persisted_dict(self) -> dict:
        llm = asdict(self.llm)
        llm["api_key"] = ""
        llm["extra_headers"] = _drop_sensitive_headers(llm.get("extra_headers", {}))
        return {
            "server": asdict(self.server),
            "llm": llm,
            "runtime": asdict(self.runtime),
            "identity": asdict(self.identity),
            "production": asdict(self.production),
        }


def _secret_store_path(root_dir: Path) -> Path:
    return (root_dir / "workspace" / "secrets" / "local-llm.json").resolve()


def _load_local_llm_secrets(root_dir: Path) -> dict[str, str]:
    path = _secret_store_path(root_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if key in {"api_key"} and str(value)
    }


def _save_local_llm_secrets(root_dir: Path, secrets: dict[str, str]) -> None:
    path = _secret_store_path(root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_local_llm_secrets(root_dir)
    existing.update({key: value for key, value in secrets.items() if value})
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _deep_get(payload: dict, key: str, default: dict) -> dict:
    value = payload.get(key)
    return value if isinstance(value, dict) else default


def _normalize_llm_payload(payload: dict) -> dict:
    allowed = set(LLMConfig.__dataclass_fields__)
    normalized = {key: value for key, value in payload.items() if key in allowed}
    if "extra_headers" in normalized and not isinstance(normalized["extra_headers"], dict):
        normalized["extra_headers"] = {}
    if isinstance(normalized.get("extra_headers"), dict):
        normalized["extra_headers"] = {
            str(key): str(value)
            for key, value in normalized["extra_headers"].items()
            if str(key).strip()
        }
    return normalized


def _normalize_runtime_payload(payload: dict) -> dict:
    allowed = set(RuntimeConfig.__dataclass_fields__)
    normalized = {key: value for key, value in payload.items() if key in allowed}
    deployment_aliases = {
        "local-single-user": "hosted-multi-user-local",
        "local": "hosted-multi-user-local",
    }
    queue_aliases = {
        "in_process": "external-worker",
        "in-process": "external-worker",
        "same-process-thread": "external-worker",
        "thread": "external-worker",
    }
    sandbox_aliases = {
        "role-governed-local": "tenant-governed-worktree",
        "local": "tenant-governed-worktree",
    }
    normalized["deployment_mode"] = deployment_aliases.get(
        str(normalized.get("deployment_mode", "")).strip().lower(),
        normalized.get("deployment_mode", RuntimeConfig.deployment_mode),
    )
    normalized["queue_mode"] = queue_aliases.get(
        str(normalized.get("queue_mode", "")).strip().lower(),
        normalized.get("queue_mode", RuntimeConfig.queue_mode),
    )
    normalized["sandbox_mode"] = sandbox_aliases.get(
        str(normalized.get("sandbox_mode", "")).strip().lower(),
        normalized.get("sandbox_mode", RuntimeConfig.sandbox_mode),
    )
    return normalized


def _normalize_identity_payload(payload: dict) -> dict:
    allowed = set(IdentityConfig.__dataclass_fields__)
    normalized = {key: value for key, value in payload.items() if key in allowed}
    mode_aliases = {
        "single-local-operator": "hosted-dev-session",
        "single-user": "hosted-dev-session",
    }
    tenant_aliases = {
        "single-tenant": "required-tenant",
        "local": "required-tenant",
    }
    normalized["mode"] = mode_aliases.get(
        str(normalized.get("mode", "")).strip().lower(),
        normalized.get("mode", IdentityConfig.mode),
    )
    normalized["tenant_mode"] = tenant_aliases.get(
        str(normalized.get("tenant_mode", "")).strip().lower(),
        normalized.get("tenant_mode", IdentityConfig.tenant_mode),
    )
    normalized.setdefault("require_identity", True)
    normalized.setdefault("require_tenant", True)
    return normalized


def _normalize_production_payload(payload: dict) -> dict:
    allowed = set(ProductionScaffoldConfig.__dataclass_fields__)
    normalized = {key: value for key, value in payload.items() if key in allowed}
    queue_aliases = {
        "in-process": "postgres-durable",
        "in_process": "postgres-durable",
        "sqlite-durable": "postgres-durable",
    }
    worker_aliases = {
        "same-process-thread": "multi-process-worker",
        "local-thread": "multi-process-worker",
        "hosted-worker": "multi-process-worker",
    }
    deployment_aliases = {
        "local-dev": "docker-compose",
        "local": "docker-compose",
    }
    normalized["queue_backend"] = queue_aliases.get(
        str(normalized.get("queue_backend", "")).strip().lower(),
        normalized.get("queue_backend", ProductionScaffoldConfig.queue_backend),
    )
    normalized["worker_model"] = worker_aliases.get(
        str(normalized.get("worker_model", "")).strip().lower(),
        normalized.get("worker_model", ProductionScaffoldConfig.worker_model),
    )
    normalized["deployment_target"] = deployment_aliases.get(
        str(normalized.get("deployment_target", "")).strip().lower(),
        normalized.get("deployment_target", ProductionScaffoldConfig.deployment_target),
    )
    normalized.setdefault("future_auth_enabled", True)
    return normalized


def _is_sensitive_header(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("authorization", "api-key", "apikey", "token", "secret", "key"))


def _drop_sensitive_headers(headers: dict | object) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if not _is_sensitive_header(str(key))
    }


def _masked_headers(headers: dict | object) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): "***" if _is_sensitive_header(str(key)) and str(value) else str(value)
        for key, value in headers.items()
    }


def _masked_llm_dict(llm_config: LLMConfig) -> dict:
    llm = asdict(llm_config)
    if llm.get("api_key"):
        llm["api_key"] = "***"
    llm["extra_headers"] = _masked_headers(llm.get("extra_headers", {}))
    return llm


def _env_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _env_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _env_json_object(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): str(item) for key, item in parsed.items()}


def load_config(root_dir: Path) -> AppConfig:
    config_path = (root_dir / "orchestrator_config.json").resolve()
    payload = {}
    if config_path.exists():
        payload = json.loads(config_path.read_text(encoding="utf-8"))

    server = ServerConfig(**_deep_get(payload, "server", {}))
    llm = LLMConfig(**_normalize_llm_payload(_deep_get(payload, "llm", {})))
    env_api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEV_ORCHESTRATOR_API_KEY")
    env_api_base = os.environ.get("OPENAI_API_BASE") or os.environ.get("DEV_ORCHESTRATOR_API_BASE")
    env_model = os.environ.get("OPENAI_MODEL") or os.environ.get("DEV_ORCHESTRATOR_MODEL")
    env_wire_api = os.environ.get("DEV_ORCHESTRATOR_WIRE_API") or os.environ.get("OPENAI_WIRE_API")
    env_provider_profile = os.environ.get("DEV_ORCHESTRATOR_PROVIDER_PROFILE") or os.environ.get("OPENAI_PROVIDER_PROFILE")
    env_api_path = os.environ.get("DEV_ORCHESTRATOR_API_PATH") or os.environ.get("OPENAI_API_PATH")
    env_reasoning_effort = os.environ.get("DEV_ORCHESTRATOR_REASONING_EFFORT") or os.environ.get("OPENAI_REASONING_EFFORT") or os.environ.get("MODEL_REASONING_EFFORT")
    env_disable_response_storage = _env_bool(os.environ.get("DEV_ORCHESTRATOR_DISABLE_RESPONSE_STORAGE") or os.environ.get("OPENAI_DISABLE_RESPONSE_STORAGE"))
    env_auth_header = os.environ.get("DEV_ORCHESTRATOR_AUTH_HEADER")
    env_auth_scheme = os.environ.get("DEV_ORCHESTRATOR_AUTH_SCHEME")
    env_extra_headers = _env_json_object(os.environ.get("DEV_ORCHESTRATOR_EXTRA_HEADERS"))
    env_timeout = _env_int(os.environ.get("DEV_ORCHESTRATOR_TIMEOUT_SECONDS"))
    env_retries = _env_int(os.environ.get("DEV_ORCHESTRATOR_RETRY_ATTEMPTS"))
    env_max_tokens = _env_int(os.environ.get("DEV_ORCHESTRATOR_MAX_TOKENS"))
    env_max_body = _env_int(os.environ.get("DEV_ORCHESTRATOR_MAX_REQUEST_BODY_BYTES"))
    env_temperature = _env_float(os.environ.get("DEV_ORCHESTRATOR_TEMPERATURE"))
    env_supports_responses = _env_bool(os.environ.get("DEV_ORCHESTRATOR_SUPPORTS_RESPONSES"))
    env_supports_chat = _env_bool(os.environ.get("DEV_ORCHESTRATOR_SUPPORTS_CHAT_COMPLETIONS"))
    local_secrets = _load_local_llm_secrets(root_dir)
    if env_api_key:
        llm.api_key = env_api_key
    elif local_secrets.get("api_key"):
        llm.api_key = local_secrets["api_key"]
    if env_api_base:
        llm.api_base = env_api_base
    if env_model:
        llm.model = env_model
    if env_reasoning_effort:
        llm.model_reasoning_effort = env_reasoning_effort
    if env_disable_response_storage is not None:
        llm.disable_response_storage = env_disable_response_storage
    if env_wire_api:
        llm.wire_api = env_wire_api
    if env_provider_profile:
        llm.provider_profile = env_provider_profile
    if env_api_path:
        llm.api_path = env_api_path
    if env_auth_header:
        llm.auth_header = env_auth_header
    if env_auth_scheme is not None:
        llm.auth_scheme = env_auth_scheme
    if env_extra_headers is not None:
        llm.extra_headers = env_extra_headers
    if env_timeout is not None:
        llm.timeout_seconds = env_timeout
    if env_retries is not None:
        llm.retry_attempts = env_retries
    if env_max_tokens is not None:
        llm.max_tokens = env_max_tokens
    if env_max_body is not None:
        llm.max_request_body_bytes = env_max_body
    if env_temperature is not None:
        llm.temperature = env_temperature
    if env_supports_responses is not None:
        llm.supports_responses = env_supports_responses
    if env_supports_chat is not None:
        llm.supports_chat_completions = env_supports_chat
    runtime = RuntimeConfig(**_normalize_runtime_payload(_deep_get(payload, "runtime", {})))
    env_runtime_root = os.environ.get("AI_AGENT_RUNTIME_ROOT")
    env_workspace_max = _env_int(os.environ.get("AI_AGENT_WORKSPACE_MAX_BYTES"))
    env_gc_dry_run = _env_bool(os.environ.get("AI_AGENT_GC_DRY_RUN_DEFAULT"))
    env_exported_retention = _env_int(os.environ.get("AI_AGENT_EXPORTED_WORKTREE_RETENTION_DAYS"))
    env_artifact_retention = _env_int(os.environ.get("AI_AGENT_ARTIFACT_RETENTION_DAYS"))
    env_failed_retention = _env_int(os.environ.get("AI_AGENT_FAILED_RUN_RETENTION_DAYS"))
    env_log_retention = _env_int(os.environ.get("AI_AGENT_LOG_RETENTION_DAYS"))
    env_pressure_retention = _env_int(os.environ.get("AI_AGENT_PRESSURE_RETENTION_DAYS"))
    env_archive_enabled = _env_bool(os.environ.get("AI_AGENT_ARCHIVE_ENABLED"))
    env_database_url = os.environ.get("DATABASE_URL")
    if env_runtime_root:
        runtime.runtime_root = env_runtime_root
    if env_workspace_max is not None:
        runtime.workspace_max_bytes = env_workspace_max
    if env_gc_dry_run is not None:
        runtime.gc_dry_run_default = env_gc_dry_run
    if env_exported_retention is not None:
        runtime.exported_worktree_retention_days = env_exported_retention
    if env_artifact_retention is not None:
        runtime.artifact_retention_days = env_artifact_retention
    if env_failed_retention is not None:
        runtime.failed_run_retention_days = env_failed_retention
    if env_log_retention is not None:
        runtime.log_retention_days = env_log_retention
    if env_pressure_retention is not None:
        runtime.pressure_retention_days = env_pressure_retention
    if env_archive_enabled is not None:
        runtime.archive_enabled = env_archive_enabled
    if env_database_url:
        runtime.database_url = env_database_url
    identity = IdentityConfig(**_normalize_identity_payload(_deep_get(payload, "identity", {})))
    production = ProductionScaffoldConfig(**_normalize_production_payload(_deep_get(payload, "production", {})))
    config = AppConfig(
        root_dir=root_dir.resolve(),
        config_path=config_path,
        server=server,
        llm=llm,
        runtime=runtime,
        identity=identity,
        production=production,
    )
    ensure_paths(config)
    return config


def save_config(config: AppConfig) -> None:
    ensure_paths(config)
    config.config_path.write_text(
        json.dumps(config.to_persisted_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def update_config(config: AppConfig, payload: dict) -> AppConfig:
    server_payload = _deep_get(payload, "server", {})
    llm_payload = _deep_get(payload, "llm", {})
    runtime_payload = _deep_get(payload, "runtime", {})
    identity_payload = _deep_get(payload, "identity", {})
    production_payload = _deep_get(payload, "production", {})

    for key, value in server_payload.items():
        if hasattr(config.server, key):
            setattr(config.server, key, value)
    normalized_llm = _normalize_llm_payload(llm_payload)
    incoming_api_key = normalized_llm.get("api_key")
    if incoming_api_key not in {"", "***", None}:
        _save_local_llm_secrets(config.root_dir, {"api_key": str(incoming_api_key)})
    for key, value in normalized_llm.items():
        if key == "api_key" and value in {"", "***", None}:
            continue
        if hasattr(config.llm, key):
            setattr(config.llm, key, value)
    for key, value in runtime_payload.items():
        if hasattr(config.runtime, key):
            setattr(config.runtime, key, value)
    for key, value in identity_payload.items():
        if hasattr(config.identity, key):
            setattr(config.identity, key, value)
    for key, value in production_payload.items():
        if hasattr(config.production, key):
            setattr(config.production, key, value)

    ensure_paths(config)
    save_config(config)
    return config


def ensure_paths(config: AppConfig) -> None:
    config.workspace_root.mkdir(parents=True, exist_ok=True)
    config.logs_path.mkdir(parents=True, exist_ok=True)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
