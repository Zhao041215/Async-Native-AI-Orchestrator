from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from dev_orchestrator.v6.models import sha256_bytes, stable_json


SCHEMA_VERSION = "6.1"
KERNEL_GENERATION = "100k_ai_native"


def build_state_transition_event(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any] | None:
    previous = previous or {}
    current = current or {}
    changed_fields: dict[str, dict[str, Any]] = {}
    for key in ("status", "checkpoint", "continuation"):
        if previous.get(key) != current.get(key):
            changed_fields[key] = {"from": previous.get(key), "to": current.get(key)}
    if not changed_fields:
        return None
    continuation = current.get("continuation") or {}
    previous_continuation = previous.get("continuation") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": "mission_state_changed",
        "kernel_generation": KERNEL_GENERATION,
        "tenant_id": current.get("tenant_id", ""),
        "project_id": current.get("project_id", ""),
        "run_id": current.get("id", ""),
        "status": current.get("status", ""),
        "checkpoint": current.get("checkpoint", ""),
        "next_action": continuation.get("next_action", ""),
        "previous_status": previous.get("status", ""),
        "previous_checkpoint": previous.get("checkpoint", ""),
        "previous_next_action": previous_continuation.get("next_action", ""),
        "changed_fields": changed_fields,
    }


def summarize_events(events: list[dict[str, Any]], limit: int = 20) -> dict[str, Any]:
    ordered = _sorted_events(events)
    counts = Counter(_event_kind(event) for event in ordered)
    return {
        "schema_version": SCHEMA_VERSION,
        "count": len(ordered),
        "by_kind": dict(sorted(counts.items())),
        "latest": [_compact_event(event) for event in ordered[-max(1, limit):]],
        "state_transitions": [_compact_event(event) for event in ordered if _event_kind(event) == "mission_state_changed"][-max(1, limit):],
    }


def build_checkpoint_summary(run: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = _sorted_events(events)
    transitions = [event for event in ordered if _event_kind(event) == "mission_state_changed"]
    latest = transitions[-1] if transitions else {}
    continuation = run.get("continuation") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run.get("id", ""),
        "status": run.get("status", ""),
        "checkpoint": run.get("checkpoint", ""),
        "next_action": continuation.get("next_action", ""),
        "transition_count": len(transitions),
        "latest_transition": _compact_event(latest) if latest else {},
        "history": [_compact_event(event) for event in transitions[-20:]],
    }


def build_replay_projection(
    *,
    run: dict[str, Any],
    events: list[dict[str, Any]],
    jobs: list[dict[str, Any]] | None = None,
    waves: list[dict[str, Any]] | None = None,
    packages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ordered = _sorted_events(events)
    created = next((event for event in ordered if _event_kind(event) == "run_created" and (event.get("payload") or {}).get("initial_projection")), None)
    source = "event_log"
    if created:
        projection = _normalize_projection((created.get("payload") or {}).get("initial_projection") or {})
    else:
        source = "stored_projection_bootstrap"
        projection = _normalize_projection(run)

    applied: list[dict[str, Any]] = []
    for event in ordered:
        if _event_kind(event) != "mission_state_changed":
            continue
        payload = event.get("payload") or {}
        if payload.get("status"):
            projection["status"] = payload.get("status")
        if payload.get("checkpoint"):
            projection["checkpoint"] = payload.get("checkpoint")
        changed = payload.get("changed_fields") or {}
        continuation_change = changed.get("continuation") or {}
        if "to" in continuation_change and isinstance(continuation_change.get("to"), dict):
            projection["continuation"] = continuation_change["to"]
        elif payload.get("next_action"):
            projection.setdefault("continuation", {})["next_action"] = payload.get("next_action")
        projection["updated_at"] = event.get("created_at", projection.get("updated_at", ""))
        applied.append(_compact_event(event))

    projection["continuation"] = dict(projection.get("continuation") or {})
    drift = _projection_drift(run, projection)
    job_counts = _counts(jobs or [], "status")
    wave_counts = _counts(waves or [], "status")
    package_counts = _counts(packages or [], "status")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kernel_generation": KERNEL_GENERATION,
        "source": source,
        "run_id": run.get("id", projection.get("id", "")),
        "projection": projection,
        "projection_hash": sha256_bytes(stable_json(projection).encode("utf-8")),
        "applied_event_count": len(applied),
        "applied_events": applied[-50:],
        "drift": drift,
        "projection_metrics": {
            "event_count": len(ordered),
            "job_counts": job_counts,
            "wave_counts": wave_counts,
            "package_counts": package_counts,
        },
    }
    payload["ok"] = bool(drift["ok"])
    return payload


def build_checkpoint_resume_plan(
    *,
    run: dict[str, Any],
    events: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    waves: list[dict[str, Any]],
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    replay = build_replay_projection(run=run, events=events, jobs=jobs, waves=waves, packages=packages)
    continuation = run.get("continuation") or {}
    checkpoint = str(run.get("checkpoint") or continuation.get("checkpoint") or "run_created")
    queued = [job for job in jobs if job.get("status") in {"queued", "retry", "leased", "running"}]
    dead_letters = [job for job in jobs if job.get("status") == "dead_letter"]
    completed_jobs = [job for job in jobs if job.get("status") == "completed"]
    pending_packages = [package for package in packages if package.get("status") not in {"completed", "cancelled"}]

    if dead_letters:
        next_action = "dead_letter_requeue_or_recovery_wave"
        resume_from = checkpoint
        resumable = False
    elif queued:
        next_action = "worker_claim_pending_jobs"
        resume_from = checkpoint
        resumable = True
    elif checkpoint in {"run_created"}:
        next_action = "requirements_analysis"
        resume_from = "run_created"
        resumable = True
    elif checkpoint in {"requirements_completed"}:
        next_action = "architecture_design"
        resume_from = checkpoint
        resumable = True
    elif checkpoint in {"architecture_completed"}:
        next_action = "package_planning"
        resume_from = checkpoint
        resumable = True
    elif pending_packages:
        next_action = "worker_claim_package_jobs"
        resume_from = checkpoint
        resumable = True
    elif checkpoint in {"wave_completed", "package_completed", "wave_queued", "package_planning_completed"}:
        next_action = "integration"
        resume_from = checkpoint
        resumable = True
    elif checkpoint in {"integration_completed"}:
        next_action = "code_review"
        resume_from = checkpoint
        resumable = True
    elif checkpoint in {"review_completed"}:
        next_action = "quality"
        resume_from = checkpoint
        resumable = True
    elif checkpoint in {"quality_completed"}:
        next_action = "release_notes"
        resume_from = checkpoint
        resumable = True
    elif checkpoint in {"release_notes_completed"}:
        next_action = "release_candidate"
        resume_from = checkpoint
        resumable = True
    else:
        next_action = continuation.get("next_action") or "idle"
        resume_from = checkpoint
        resumable = run.get("status") not in {"completed", "cancelled", "release_ready", "rolled_back"}

    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_generation": KERNEL_GENERATION,
        "run_id": run.get("id", ""),
        "status": run.get("status", ""),
        "checkpoint": checkpoint,
        "resume_from": resume_from,
        "next_action": next_action,
        "resumable": bool(resumable),
        "projection_consistent": bool(replay.get("ok")),
        "replay_projection_hash": replay.get("projection_hash", ""),
        "pending_job_count": len(queued),
        "dead_letter_count": len(dead_letters),
        "pending_package_count": len(pending_packages),
        "idempotency": {
            "active_resume_keys": [job.get("resume_key", "") for job in queued],
            "completed_resume_keys": [job.get("resume_key", "") for job in completed_jobs[-50:]],
        },
    }


def build_mission_graph(
    *,
    run: dict[str, Any],
    project: dict[str, Any],
    jobs: list[dict[str, Any]],
    waves: list[dict[str, Any]],
    packages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_events = _sorted_events(events)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    run_node = {
        "id": f"run:{run.get('id', '')}",
        "type": "run",
        "label": project.get("name") or run.get("id", ""),
        "status": run.get("status", ""),
        "checkpoint": run.get("checkpoint", ""),
        "next_action": (run.get("continuation") or {}).get("next_action", ""),
    }
    nodes.append(run_node)

    wave_nodes: dict[str, str] = {}
    for wave in sorted(waves, key=lambda item: int(item.get("sequence") or 0)):
        node_id = f"wave:{wave.get('wave_key') or wave.get('id', '')}"
        wave_nodes[str(wave.get("wave_key") or wave.get("id") or node_id)] = node_id
        nodes.append(
            {
                "id": node_id,
                "type": "wave",
                "label": wave.get("wave_key", ""),
                "status": wave.get("status", ""),
                "sequence": wave.get("sequence", 0),
            }
        )
        edges.append({"from": run_node["id"], "to": node_id, "type": "contains"})

    package_nodes_by_id: dict[str, str] = {}
    package_nodes_by_key: dict[str, str] = {}
    for package in packages:
        payload = package.get("payload") or package
        package_id = str(package.get("id") or payload.get("id") or "")
        package_key = str(package.get("package_key") or payload.get("package_key") or package_id)
        node_id = f"package:{package_key}"
        package_nodes_by_id[package_id] = node_id
        package_nodes_by_key[package_key] = node_id
        nodes.append(
            {
                "id": node_id,
                "type": "package",
                "label": package_key,
                "status": package.get("status", payload.get("status", "")),
                "role": package.get("role", payload.get("role", "")),
                "wave_key": package.get("wave_key", payload.get("wave_key", "")),
                "subsystem": package.get("subsystem", payload.get("subsystem", "")),
            }
        )
        wave_key = str(package.get("wave_key") or payload.get("wave_key") or "")
        if wave_key and wave_key in wave_nodes:
            edges.append({"from": wave_nodes[wave_key], "to": node_id, "type": "contains"})
        for dependency in payload.get("depends_on") or package.get("depends_on") or []:
            dependency_key = str(dependency)
            dependency_node = package_nodes_by_key.get(dependency_key) or f"package:{dependency_key}"
            edges.append({"from": dependency_node, "to": node_id, "type": "depends_on"})

    active_jobs = sorted(jobs, key=lambda item: _event_timestamp(item.get("created_at") or item.get("updated_at")))
    for job in active_jobs[:150]:
        job_node_id = f"job:{job.get('id', '')}"
        nodes.append(
            {
                "id": job_node_id,
                "type": "job",
                "label": job.get("job_type", ""),
                "status": job.get("status", ""),
                "role": job.get("role", ""),
                "attempts": job.get("attempts", 0),
            }
        )
        target = package_nodes_by_id.get(str(job.get("work_package_id") or ""))
        if not target:
            payload = job.get("payload") or {}
            target = package_nodes_by_key.get(str(payload.get("package_key") or ""))
        if not target:
            target = run_node["id"]
        edges.append({"from": target, "to": job_node_id, "type": "produces"})

    job_counts = _counts(jobs, "status")
    package_counts = _counts(packages, "status")
    wave_counts = _counts(waves, "status")
    artifact_counts = _counts(artifacts, "kind")
    event_counts = Counter(_event_kind(event) for event in ordered_events)

    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_generation": KERNEL_GENERATION,
        "run": {
            "id": run.get("id", ""),
            "status": run.get("status", ""),
            "checkpoint": run.get("checkpoint", ""),
            "next_action": (run.get("continuation") or {}).get("next_action", ""),
        },
        "project": {
            "id": project.get("id", ""),
            "name": project.get("name", ""),
            "title": project.get("title", ""),
        },
        "nodes": nodes,
        "edges": edges,
        "metrics": {
            "wave_count": len(waves),
            "package_count": len(packages),
            "job_count": len(jobs),
            "artifact_count": len(artifacts),
            "event_count": len(ordered_events),
            "job_counts": job_counts,
            "package_counts": package_counts,
            "wave_counts": wave_counts,
            "artifact_counts": artifact_counts,
            "event_counts": dict(sorted(event_counts.items())),
        },
        "checkpoint": build_checkpoint_summary(run, ordered_events),
        "replay_projection": build_replay_projection(run=run, events=ordered_events, jobs=jobs, waves=waves, packages=packages),
        "checkpoint_resume": build_checkpoint_resume_plan(run=run, events=ordered_events, jobs=jobs, waves=waves, packages=packages),
        "events": summarize_events(ordered_events),
    }


def build_recovery_trace(
    *,
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    provider_health: dict[str, Any],
) -> dict[str, Any]:
    ordered = _sorted_events(events)
    retryable_failures = [event for event in ordered if _event_kind(event) == "llm_call_failed" and bool((event.get("payload") or {}).get("retryable"))]
    recoveries = [event for event in ordered if _event_kind(event) in {"llm_call_failed", "job.failed", "job.requeued_after_expiry", "mission_state_changed"}]
    dead_letters = [job for job in jobs if job.get("status") == "dead_letter"]
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": (run.get("continuation") or {}).get("recovery_policy", ""),
        "dead_letter_count": len(dead_letters),
        "dead_letters": [_job_ref(job) for job in dead_letters[:20]],
        "retryable_provider_failure_count": len(retryable_failures),
        "provider_failure_count": len([event for event in ordered if _event_kind(event) == "llm_call_failed"]),
        "recovery_event_count": len(recoveries),
        "latest_recovery_events": [_compact_event(event) for event in recoveries[-20:]],
        "provider_health": provider_health,
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


def _compact_event(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {}
    payload = event.get("payload") or {}
    return {
        "id": event.get("id", ""),
        "kind": _event_kind(event),
        "created_at": event.get("created_at", ""),
        "run_id": event.get("run_id", ""),
        "project_id": event.get("project_id", ""),
        "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
    }


def _normalize_projection(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id", ""),
        "tenant_id": run.get("tenant_id", ""),
        "project_id": run.get("project_id", ""),
        "status": run.get("status", "queued"),
        "checkpoint": run.get("checkpoint", "run_created"),
        "continuation": dict(run.get("continuation") or {}),
        "metadata": dict(run.get("metadata") or {}),
        "created_at": run.get("created_at", ""),
        "updated_at": run.get("updated_at", ""),
    }


def _projection_drift(stored: dict[str, Any], replayed: dict[str, Any]) -> dict[str, Any]:
    fields = ("status", "checkpoint")
    differences = []
    for field in fields:
        if stored.get(field) != replayed.get(field):
            differences.append({"field": field, "stored": stored.get(field), "replayed": replayed.get(field)})
    stored_next = (stored.get("continuation") or {}).get("next_action")
    replayed_next = (replayed.get("continuation") or {}).get("next_action")
    if stored_next != replayed_next:
        differences.append({"field": "continuation.next_action", "stored": stored_next, "replayed": replayed_next})
    return {"ok": not differences, "differences": differences}


def _event_kind(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return str(event.get("kind") or event.get("event_type") or payload.get("event_type") or "unknown")


def _event_timestamp(value: Any) -> datetime:
    raw = str(value or "")
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        timestamp = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _sorted_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda item: _event_timestamp(item.get("created_at")))
