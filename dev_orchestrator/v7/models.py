"""V7 Typed Domain Models - replaces all dict[str, Any] with strict Pydantic models."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --- Base Models ---

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LooseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --- Enums ---

class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    recovering = "recovering"
    blocked = "blocked"
    no_go = "no_go"
    completed = "completed"
    cancelled = "cancelled"
    release_ready = "release_ready"
    rolled_back = "rolled_back"


class JobStatus(str, Enum):
    queued = "queued"
    leased = "leased"
    running = "running"
    completed = "completed"
    retry = "retry"
    dead_letter = "dead_letter"
    cancelled = "cancelled"
    paused = "paused"


class JobType(str, Enum):
    requirements_analysis = "requirements_analysis"
    architecture_design = "architecture_design"
    package_planning = "package_planning"
    code_generation = "code_generation"
    test_generation = "test_generation"
    security_review = "security_review"
    code_review = "code_review"
    integration = "integration"
    quality = "quality"
    release_candidate = "release_candidate"
    release_notes = "release_notes"
    apply = "apply"
    rollback = "rollback"
    repair = "repair"


class PackageRole(str, Enum):
    requirements = "requirements"
    planner = "planner"
    architect = "architect"
    db = "db"
    frontend = "frontend"
    backend = "backend"
    docs = "docs"
    qa = "qa"
    security = "security"
    integration = "integration"
    review = "review"
    repair = "repair"
    release = "release"


class ProviderHealthStatus(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    circuit_open = "circuit_open"
    half_open = "half_open"
    blocked = "blocked"


# --- Constants ---

DEFAULT_TENANT = "local-workspace"

ROLES: tuple[str, ...] = tuple(r.value for r in PackageRole)
JOB_TYPES: tuple[str, ...] = tuple(j.value for j in JobType)

CHECKPOINTS: tuple[str, ...] = (
    "run_created",
    "requirements_completed",
    "architecture_completed",
    "package_planning_completed",
    "wave_queued",
    "package_completed",
    "wave_completed",
    "integration_completed",
    "review_completed",
    "quality_completed",
    "release_notes_completed",
    "release_candidate_completed",
    "apply_completed",
    "rollback_completed",
)

FORBIDDEN_RELEASE_NAMES = frozenset({
    ".agent", ".git", ".pytest_cache", "__pycache__",
    "b", "reports", "tmp", ".v6", ".v7",
})


# --- Utility Functions ---

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str, fallback: str = "project") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or fallback


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def source_line_count(path: Path) -> int:
    if not path.exists() or path.is_dir():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "//", "/*", "*", "<!--")):
            continue
        count += 1
    return count


def effective_loc(root: Path) -> dict[str, Any]:
    source_suffixes = {
        ".php", ".py", ".js", ".jsx", ".ts", ".tsx",
        ".css", ".sql", ".json", ".yml", ".yaml",
    }
    total = 0
    files: list[dict[str, Any]] = []
    if not root.exists():
        return {"total": 0, "files": []}
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in FORBIDDEN_RELEASE_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in source_suffixes:
            continue
        loc = source_line_count(path)
        total += loc
        files.append({"path": str(path.relative_to(root)), "loc": loc})
    return {"total": total, "files": files}


# --- Core Domain Models ---

class ScaleProfileData(StrictModel):
    name: str = "medium"
    label: str = ""
    kernel_generation: str = "v7_ai_native"
    mission_contract_version: str = "7.0"
    target_loc_hint: int = 20000
    package_loc_target: int = 1100
    recursive_decomposition_depth: int = 3
    max_waves: int = 7
    wave_parallelism: int = 3
    ai_provider_concurrency: int = 2
    ai_run_concurrency: int = 2
    ai_slot_wait_seconds: int = 20
    context_budget_chars: int = 36000
    ai_retry_attempts: int = 3
    contract_density: str = "dense"
    qa_depth: str = "broad"
    security_depth: str = "threat_model"
    recovery_policy: str = "checkpoint_retry_dead_letter_requeue"
    checkpoint_interval_packages: int = 2
    provider_failover_required: bool = False
    job_attempts: dict[str, int] = Field(default_factory=dict)
    worker_role_concurrency: dict[str, int] = Field(default_factory=dict)
    required_quality_gates: list[str] = Field(default_factory=list)


class ProjectConfig(StrictModel):
    name: str
    title: str = ""
    description: str = ""
    project_path: str = ""
    target_scale: str = "auto"
    scale_profile: ScaleProfileData | None = None


class ContinuationState(StrictModel):
    next_action: str = ""
    checkpoint: str = ""
    current_wave: str | None = None
    current_package: str | None = None
    failure_reason: str = ""
    recovery_state: str = ""
    repair_attempt: int = 0


class RequirementsResult(StrictModel):
    status: Literal["ok", "blocked"] = "ok"
    summary: str = ""
    goals: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    tech_preferences: list[str] = Field(default_factory=list)
    scale_signals: dict[str, Any] = Field(default_factory=dict)


class ArchitectureResult(StrictModel):
    architecture_summary: str = ""
    technology_choices: list[dict[str, str]] = Field(default_factory=list)
    project_layout: dict[str, Any] = Field(default_factory=dict)
    module_boundaries: list[dict[str, Any]] = Field(default_factory=list)
    integration_contracts: list[dict[str, Any]] = Field(default_factory=list)
    design_principles: list[str] = Field(default_factory=list)
    primary_risks: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)


class PackageDAG(StrictModel):
    packages: list[dict[str, Any]] = Field(default_factory=list)
    waves: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, str]] = Field(default_factory=list)


class RunMetadata(StrictModel):
    requirements_text: str = ""
    scale_profile: ScaleProfileData = Field(default_factory=ScaleProfileData)
    project_config: ProjectConfig | None = None
    requirements: RequirementsResult | None = None
    architecture: ArchitectureResult | None = None
    package_plan: PackageDAG | None = None
    context_snapshot_hash: str = ""
    mission_memory_hash: str = ""
    retry_counts: dict[str, int] = Field(default_factory=dict)


class Project(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT
    name: str
    title: str = ""
    description: str = ""
    project_path: str = ""
    config: ProjectConfig = Field(default_factory=lambda: ProjectConfig(name="untitled"))
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Run(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT
    project_id: str
    status: RunStatus = RunStatus.queued
    checkpoint: str = "run_created"
    continuation: ContinuationState = Field(default_factory=ContinuationState)
    metadata: RunMetadata = Field(default_factory=RunMetadata)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Job(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT
    run_id: str
    job_type: JobType
    role: PackageRole
    status: JobStatus = JobStatus.queued
    resume_key: str = ""
    work_package_id: str | None = None
    wave_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 3
    worker_id: str = ""
    lease_until: datetime | None = None
    heartbeat_at: datetime | None = None
    last_error: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Wave(StrictModel):
    id: str = Field(default_factory=new_id)
    run_id: str
    wave_key: str
    sequence: int
    status: str = "queued"


class WorkPackage(StrictModel):
    id: str = Field(default_factory=new_id)
    run_id: str
    wave_id: str
    wave_key: str
    package_key: str
    role: PackageRole
    domain: str = ""
    status: JobStatus = JobStatus.queued
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    objective: str = ""
    expected_outputs: list[str] = Field(default_factory=list)
    acceptance_gates: list[str] = Field(default_factory=list)


class AISlot(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT
    run_id: str
    provider: str
    agent_run_id: str
    task_kind: str
    status: Literal["active", "expired", "released"] = "active"
    acquired_at: datetime = Field(default_factory=utc_now)
    lease_until: datetime = Field(default_factory=utc_now)
    released_at: datetime | None = None


class ProviderState(StrictModel):
    provider: str
    status: ProviderHealthStatus = ProviderHealthStatus.healthy
    success_count: int = 0
    failure_count: int = 0
    failure_streak: int = 0
    last_error_kind: str = ""
    last_error: str = ""
    last_event_at: float = 0.0
    retry_after_seconds: int = 0
    circuit_open_until: float = 0.0


class ErrorClassification(StrictModel):
    error_kind: str
    retryable: bool
    backoff_seconds: float
    reason: str


class ContractValidation(StrictModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    contract_type: str = ""


class PayloadBudgetReport(StrictModel):
    input_chars: int = 0
    max_input_chars: int = 0
    within_budget: bool = True
    trimmed: bool = False


class AICallResult(StrictModel):
    ok: bool
    agent_run_id: str
    role: str
    job_id: str
    task_kind: str
    model: str = ""
    elapsed_ms: int = 0
    raw_response: str = ""
    parsed_response: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None
    retryable: bool = False
    contract_validation: ContractValidation | None = None
    payload_budget: PayloadBudgetReport | None = None


class Event(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT
    run_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    sequence: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class Artifact(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT
    project_id: str
    run_id: str
    job_id: str = ""
    kind: str
    key: str
    content_type: str = "application/json"
    content: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class AgentRun(StrictModel):
    id: str = Field(default_factory=new_id)
    tenant_id: str = DEFAULT_TENANT
    run_id: str
    job_id: str
    role: str
    task_kind: str
    model: str = ""
    status: str = "ok"
    elapsed_ms: int = 0
    input_chars: int = 0
    output_chars: int = 0
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now)


# --- AI Scheduler Models ---

class AISchedulerLimits(StrictModel):
    global_concurrency: int = 6
    provider_concurrency: int = 4
    run_concurrency: int = 3


class AITaskBudget(StrictModel):
    task_kind: str
    max_input_chars: int = 36000
    max_output_tokens: int = 8000
    timeout_seconds: int = 120
    reasoning_effort: str = "high"
    retry_attempts: int = 3
    model_tier: str = "tier_standard"
    allow_degraded: bool = False
    critical: bool = True


# --- Quality Gate Models ---

class GateResult(StrictModel):
    name: str
    ok: bool
    severity: Literal["critical", "major", "info"] = "major"
    details: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int = 0
    profile_observation_only: bool = False


class QualityReport(StrictModel):
    ok: bool
    status: Literal["GO", "NO_GO"] = "GO"
    gates: list[GateResult] = Field(default_factory=list)
    critical_failures: int = 0
    major_failures: int = 0
    total_gates: int = 0
    elapsed_ms: int = 0
