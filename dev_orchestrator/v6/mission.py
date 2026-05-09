from __future__ import annotations

from typing import Any

from dev_orchestrator.v6.kernel import build_checkpoint_summary, build_mission_graph, build_recovery_trace, summarize_events
from dev_orchestrator.v6.profiles import resolve_scale_profile


def build_mission_state(
    *,
    run: dict[str, Any],
    project: dict[str, Any],
    jobs: list[dict[str, Any]],
    waves: list[dict[str, Any]],
    packages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    provider_health: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    events = events or []
    metadata = run.get("metadata") or {}
    continuation = run.get("continuation") or {}
    profile = resolve_scale_profile(metadata.get("scale_profile") or metadata.get("project_config") or project.get("config") or {})
    job_counts = _counts(jobs, "status")
    package_counts = _counts(packages, "status")
    artifact_counts = _counts(artifacts, "kind")
    dead_letters = [job for job in jobs if job.get("status") == "dead_letter"]
    retryable_failures = [
        artifact.get("payload") or {}
        for artifact in artifacts
        if artifact.get("kind") == "agent_run" and not (artifact.get("payload") or {}).get("ok", True) and (artifact.get("payload") or {}).get("retryable")
    ]
    mission_graph = build_mission_graph(run=run, project=project, jobs=jobs, waves=waves, packages=packages, artifacts=artifacts, events=events)
    return {
        "schema_version": "6.1",
        "kernel": "v6",
        "kernel_generation": "100k_ai_native",
        "mission_contract_version": profile.get("mission_contract_version", "6.1"),
        "tenant_id": run.get("tenant_id", ""),
        "project_id": project.get("id", ""),
        "project_name": project.get("name", ""),
        "run_id": run.get("id", ""),
        "status": run.get("status", ""),
        "checkpoint": run.get("checkpoint", ""),
        "next_action": continuation.get("next_action", ""),
        "scale_profile": profile,
        "graph": {
            "wave_count": len(waves),
            "package_count": len(packages),
            "job_counts": job_counts,
            "package_counts": package_counts,
            "artifact_counts": artifact_counts,
            "current_wave": continuation.get("current_wave"),
            "completed_waves": continuation.get("completed_waves", []),
            "completed_packages": continuation.get("completed_packages", []),
        },
        "mission_graph": mission_graph,
        "checkpoint_trace": build_checkpoint_summary(run, events),
        "event_summary": summarize_events(events),
        "recovery": {
            "policy": profile.get("recovery_policy", ""),
            "dead_letter_count": len(dead_letters),
            "dead_letters": [_job_ref(job) for job in dead_letters[:20]],
            "retryable_provider_failures": len(retryable_failures),
            "last_failure_reason": continuation.get("failure_reason", ""),
            "trace": build_recovery_trace(run=run, jobs=jobs, events=events, provider_health=provider_health),
        },
        "provider_health": provider_health,
        "context": {
            "context_snapshot_id": metadata.get("context_snapshot_id", ""),
            "mission_memory_hash": (metadata.get("mission_memory") or {}).get("memory_hash", ""),
            "code_index_hash": metadata.get("code_index_hash", ""),
            "contract_index_hash": metadata.get("contract_index_hash", ""),
        },
    }


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _job_ref(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id", ""),
        "job_type": job.get("job_type", ""),
        "role": job.get("role", ""),
        "attempts": job.get("attempts", 0),
        "max_attempts": job.get("max_attempts", 0),
        "last_error": str(job.get("last_error", ""))[:300],
    }
