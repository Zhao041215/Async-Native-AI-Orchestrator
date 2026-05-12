"""V8 Scale Profiles - defines tuning parameters for different project sizes."""
from __future__ import annotations

from types import MappingProxyType
from typing import Any

from dev_orchestrator.v8.models import ScaleProfileData

KERNEL_GENERATION = "v8_pg_native"
MISSION_CONTRACT_VERSION = "8.0"

DEFAULT_JOB_ATTEMPTS = MappingProxyType({
    "requirements_analysis": 3,
    "architecture_design": 3,
    "package_planning": 3,
    "code_generation": 3,
    "test_generation": 3,
    "security_review": 3,
    "integration": 3,
    "code_review": 3,
    "quality": 2,
    "release_notes": 2,
    "release_candidate": 2,
    "repair": 2,
    "apply": 1,
    "rollback": 1,
})

DEFAULT_WORKER_CONCURRENCY = MappingProxyType({
    "requirements": 1, "architect": 1, "planner": 1, "db": 1,
    "backend": 1, "frontend": 1, "qa": 1, "security": 1,
    "integration": 1, "review": 1, "repair": 1, "release": 1, "docs": 1,
})

XLARGE_WORKER_CONCURRENCY = MappingProxyType({
    "requirements": 1, "architect": 1, "planner": 1, "db": 2,
    "backend": 3, "frontend": 3, "qa": 3, "security": 2,
    "integration": 1, "review": 1, "repair": 1, "release": 1, "docs": 2,
})

ENTERPRISE_WORKER_CONCURRENCY = MappingProxyType({
    "requirements": 1, "architect": 2, "planner": 1, "db": 3,
    "backend": 5, "frontend": 5, "qa": 4, "security": 3,
    "integration": 2, "review": 2, "repair": 2, "release": 1, "docs": 3,
})

QUALITY_GATES_SMALL = (
    "scale_profile_gate", "mission_state_contract_gate",
    "package_dag_acyclic_gate", "package_ownership_gate",
    "implementation_split_gate", "provider_resilience_gate",
    "ai_live_execution_gate", "ai_payload_budget_gate",
    "event_replay_projection_gate", "checkpoint_resume_gate",
    "recovery_trace_gate", "patch_transaction_addressing_gate",
    "ai_native_no_template_fallback_gate", "long_call_heartbeat_gate",
    "frontend_ux_gate", "performance_budget_gate",
)

QUALITY_GATES_MEDIUM = tuple(dict.fromkeys(QUALITY_GATES_SMALL + (
    "contract_density_gate", "micro_patch_integrity_gate",
    "package_self_review_gate", "ai_context_compression_gate",
    "oversize_recovery_gate", "agent_output_diversity_gate",
    "durable_ai_slot_gate", "patch_parallel_conflict_gate",
)))

QUALITY_GATES_100K = tuple(dict.fromkeys(QUALITY_GATES_MEDIUM + (
    "parallel_execution_safety_gate", "provider_circuit_recovery_gate",
)))

QUALITY_GATES_ENTERPRISE = tuple(dict.fromkeys(QUALITY_GATES_100K + (
    "cross_subsystem_contract_gate", "incremental_delivery_gate",
    "rollback_materials_gate", "multi_provider_failover_gate",
)))

SCALE_PROFILES = MappingProxyType({
    "small": ScaleProfileData(
        name="small", label="Small product (5k LOC)",
        kernel_generation=KERNEL_GENERATION, mission_contract_version=MISSION_CONTRACT_VERSION,
        target_loc_hint=5000, package_loc_target=700, recursive_decomposition_depth=2,
        max_waves=4, wave_parallelism=2, ai_provider_concurrency=1, ai_run_concurrency=1,
        ai_slot_wait_seconds=10, context_budget_chars=22000, ai_retry_attempts=2,
        contract_density="standard", qa_depth="focused", security_depth="baseline",
        recovery_policy="checkpoint_retry_then_repair", checkpoint_interval_packages=2,
        provider_failover_required=False,
        max_output_tokens_code=4000, reasoning_effort_code="medium",
        max_packages_per_plan=6,
        job_attempts={**DEFAULT_JOB_ATTEMPTS, "architecture_design": 2, "package_planning": 2},
        worker_role_concurrency=dict(DEFAULT_WORKER_CONCURRENCY),
        required_quality_gates=list(QUALITY_GATES_SMALL),
    ),
    "medium": ScaleProfileData(
        name="medium", label="Medium product (20k LOC)",
        kernel_generation=KERNEL_GENERATION, mission_contract_version=MISSION_CONTRACT_VERSION,
        target_loc_hint=20000, package_loc_target=1100, recursive_decomposition_depth=3,
        max_waves=7, wave_parallelism=3, ai_provider_concurrency=2, ai_run_concurrency=2,
        ai_slot_wait_seconds=20, context_budget_chars=36000, ai_retry_attempts=3,
        contract_density="dense", qa_depth="broad", security_depth="threat_model",
        recovery_policy="checkpoint_retry_dead_letter_requeue", checkpoint_interval_packages=2,
        provider_failover_required=False,
        max_output_tokens_code=8000, reasoning_effort_code="medium",
        max_packages_per_plan=12,
        job_attempts={**DEFAULT_JOB_ATTEMPTS, "quality": 3, "release_notes": 3},
        worker_role_concurrency={**dict(DEFAULT_WORKER_CONCURRENCY), "backend": 2, "frontend": 2, "qa": 2},
        required_quality_gates=list(QUALITY_GATES_MEDIUM),
    ),
    "large": ScaleProfileData(
        name="large", label="Large product (60k LOC)",
        kernel_generation=KERNEL_GENERATION, mission_contract_version=MISSION_CONTRACT_VERSION,
        target_loc_hint=60000, package_loc_target=1500, recursive_decomposition_depth=4,
        max_waves=12, wave_parallelism=4, ai_provider_concurrency=3, ai_run_concurrency=3,
        ai_slot_wait_seconds=45, context_budget_chars=52000, ai_retry_attempts=4,
        contract_density="dense", qa_depth="system", security_depth="abuse_and_dependency_review",
        recovery_policy="checkpoint_retry_dead_letter_recovery_wave", checkpoint_interval_packages=1,
        provider_failover_required=True,
        max_output_tokens_code=12000, reasoning_effort_code="high",
        max_packages_per_plan=25,
        job_attempts={**DEFAULT_JOB_ATTEMPTS, "code_generation": 4, "test_generation": 4, "security_review": 4, "code_review": 4},
        worker_role_concurrency={**dict(XLARGE_WORKER_CONCURRENCY), "backend": 2, "frontend": 2, "qa": 2},
        required_quality_gates=list(QUALITY_GATES_100K),
    ),
    "xlarge_100k": ScaleProfileData(
        name="xlarge_100k", label="100k LOC mission profile",
        kernel_generation=KERNEL_GENERATION, mission_contract_version=MISSION_CONTRACT_VERSION,
        target_loc_hint=100000, package_loc_target=1800, recursive_decomposition_depth=5,
        max_waves=18, wave_parallelism=6, ai_provider_concurrency=4, ai_run_concurrency=4,
        ai_slot_wait_seconds=60, context_budget_chars=76000, ai_retry_attempts=4,
        contract_density="exhaustive", qa_depth="system_performance_security",
        security_depth="full_review_with_rollback_materials",
        recovery_policy="event_replay_checkpoint_dead_letter_recovery_wave",
        checkpoint_interval_packages=1, provider_failover_required=True,
        max_output_tokens_code=16000, reasoning_effort_code="high",
        max_packages_per_plan=50,
        job_attempts={**DEFAULT_JOB_ATTEMPTS, "code_generation": 4, "test_generation": 4, "security_review": 4, "integration": 4, "code_review": 4, "repair": 3},
        worker_role_concurrency=dict(XLARGE_WORKER_CONCURRENCY),
        required_quality_gates=list(QUALITY_GATES_100K),
    ),
    "xxlarge_300k": ScaleProfileData(
        name="xxlarge_300k", label="300k LOC super-scale profile",
        kernel_generation=KERNEL_GENERATION, mission_contract_version=MISSION_CONTRACT_VERSION,
        target_loc_hint=300000, package_loc_target=2000, recursive_decomposition_depth=6,
        max_waves=30, wave_parallelism=8, ai_provider_concurrency=6, ai_run_concurrency=5,
        ai_slot_wait_seconds=90, context_budget_chars=100000, ai_retry_attempts=5,
        contract_density="exhaustive", qa_depth="system_performance_security",
        security_depth="full_review_with_rollback_materials",
        recovery_policy="event_replay_checkpoint_dead_letter_recovery_wave",
        checkpoint_interval_packages=1, provider_failover_required=True,
        max_output_tokens_code=16000, reasoning_effort_code="high",
        max_packages_per_plan=100,
        job_attempts={**DEFAULT_JOB_ATTEMPTS, "code_generation": 5, "test_generation": 5, "security_review": 5, "integration": 5, "code_review": 5, "repair": 4},
        worker_role_concurrency=dict(ENTERPRISE_WORKER_CONCURRENCY),
        required_quality_gates=list(QUALITY_GATES_ENTERPRISE),
    ),
    "enterprise_1m": ScaleProfileData(
        name="enterprise_1m", label="Enterprise 1M+ LOC profile",
        kernel_generation=KERNEL_GENERATION, mission_contract_version=MISSION_CONTRACT_VERSION,
        target_loc_hint=1000000, package_loc_target=2000, recursive_decomposition_depth=8,
        max_waves=60, wave_parallelism=10, ai_provider_concurrency=8, ai_run_concurrency=6,
        ai_slot_wait_seconds=120, context_budget_chars=120000, ai_retry_attempts=5,
        contract_density="exhaustive", qa_depth="system_performance_security",
        security_depth="full_review_with_rollback_materials",
        recovery_policy="event_replay_checkpoint_dead_letter_recovery_wave",
        checkpoint_interval_packages=1, provider_failover_required=True,
        max_output_tokens_code=16000, reasoning_effort_code="high",
        max_packages_per_plan=200,
        job_attempts={**DEFAULT_JOB_ATTEMPTS, "code_generation": 5, "test_generation": 5, "security_review": 5, "integration": 5, "code_review": 5, "repair": 5},
        worker_role_concurrency=dict(ENTERPRISE_WORKER_CONCURRENCY),
        required_quality_gates=list(QUALITY_GATES_ENTERPRISE),
    ),
})

SCALE_ALIASES = {
    "": "medium", "auto": "medium", "adaptive": "medium",
    "tiny": "small", "small": "small", "s": "small",
    "medium": "medium", "m": "medium",
    "large": "large", "l": "large",
    "xlarge": "xlarge_100k", "xl": "xlarge_100k", "100k": "xlarge_100k", "xlarge_100k": "xlarge_100k",
    "xxlarge": "xxlarge_300k", "xxl": "xxlarge_300k", "300k": "xxlarge_300k",
    "enterprise": "enterprise_1m", "1m": "enterprise_1m",
}


def _resolve_profile_name(raw: str, *, default: str = "medium") -> str:
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return default
    if normalized in SCALE_PROFILES:
        return normalized
    if normalized in SCALE_ALIASES:
        return SCALE_ALIASES[normalized]
    raise ValueError(f"unknown target_scale: {raw}")


def resolve_scale_profile(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    if isinstance(config.get("scale_profile"), dict):
        config = {**config, **config["scale_profile"]}
    name = _resolve_profile_name(config.get("name") or config.get("target_scale"))
    base = SCALE_PROFILES[name].model_dump()
    base.update({k: v for k, v in config.items() if v not in (None, "") and k in base})
    base["kernel_generation"] = KERNEL_GENERATION
    base["mission_contract_version"] = MISSION_CONTRACT_VERSION
    return base


def list_scale_profiles() -> list[dict[str, Any]]:
    return [profile.model_dump() for profile in SCALE_PROFILES.values()]


def scale_job_attempts(task_kind: str, profile_or_config: dict[str, Any] | None) -> int:
    profile = resolve_scale_profile(profile_or_config or {})
    attempts = profile.get("job_attempts") or {}
    return int(attempts.get(task_kind) or DEFAULT_JOB_ATTEMPTS.get(task_kind) or 3)
