"""V8 Event Sourcing Kernel - checkpoint/resume, replay projection, mission graph."""
from __future__ import annotations

from collections import Counter
from typing import Any

from dev_orchestrator.v8.models import Event, Run, Job, Wave, WorkPackage, sha256_bytes, stable_json

SCHEMA_VERSION = "7.0"
KERNEL_GENERATION = "v7_ai_native"

CHECKPOINT_TO_ACTION: dict[str, str] = {
    "run_created": "requirements_analysis",
    "requirements_completed": "architecture_design",
    "architecture_completed": "package_planning",
    "package_planning_completed": "wave_execution",
    "wave_queued": "wave_execution",
    "package_completed": "wave_execution",
    "wave_completed": "integration",
    "integration_completed": "code_review",
    "review_completed": "quality",
    "quality_completed": "release_notes",
    "release_notes_completed": "release_candidate",
    "release_candidate_completed": "release_ready",
    "apply_completed": "completed",
    "rollback_completed": "rolled_back",
}


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


def build_checkpoint_resume_plan(
    *,
    run: dict[str, Any],
    events: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    waves: list[dict[str, Any]],
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    continuation = run.get("continuation") or {}
    checkpoint = str(run.get("checkpoint") or continuation.get("checkpoint") or "run_created")
    queued = [job for job in jobs if job.get("status") in {"queued", "retry", "leased", "running"}]
    dead_letters = [job for job in jobs if job.get("status") == "dead_letter"]
    completed_jobs = [job for job in jobs if job.get("status") == "completed"]
    pending_packages = [pkg for pkg in packages if pkg.get("status") not in {"completed", "cancelled"}]

    if dead_letters:
        next_action = "dead_letter_requeue"
        resumable = False
    elif queued:
        next_action = "worker_claim_pending_jobs"
        resumable = True
    elif pending_packages:
        next_action = "worker_claim_package_jobs"
        resumable = True
    else:
        next_action = CHECKPOINT_TO_ACTION.get(checkpoint, continuation.get("next_action", "idle"))
        resumable = run.get("status") not in {"completed", "cancelled", "release_ready", "rolled_back"}

    return {
        "schema_version": SCHEMA_VERSION,
        "kernel_generation": KERNEL_GENERATION,
        "run_id": run.get("id", ""),
        "status": run.get("status", ""),
        "checkpoint": checkpoint,
        "resume_from": checkpoint,
        "next_action": next_action,
        "resumable": bool(resumable),
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
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    run_node = {
        "id": f"run:{run.get('id', '')}",
        "type": "run",
        "label": project.get("name") or run.get("id", ""),
        "status": run.get("status", ""),
        "checkpoint": run.get("checkpoint", ""),
    }
    nodes.append(run_node)

    for wave in sorted(waves, key=lambda w: int(w.get("sequence") or 0)):
        wave_id = f"wave:{wave.get('wave_key') or wave.get('id', '')}"
        nodes.append({
            "id": wave_id,
            "type": "wave",
            "label": wave.get("wave_key", ""),
            "status": wave.get("status", ""),
            "sequence": wave.get("sequence", 0),
        })
        edges.append({"from": run_node["id"], "to": wave_id, "type": "contains"})

    for pkg in packages:
        pkg_id = f"pkg:{pkg.get('package_key') or pkg.get('id', '')}"
        wave_key = pkg.get("wave_key", "")
        nodes.append({
            "id": pkg_id,
            "type": "package",
            "label": pkg.get("package_key", ""),
            "status": pkg.get("status", ""),
            "role": pkg.get("role", ""),
        })
        edges.append({"from": f"wave:{wave_key}", "to": pkg_id, "type": "contains"})
        for dep in pkg.get("depends_on") or []:
            edges.append({"from": f"pkg:{dep}", "to": pkg_id, "type": "depends_on"})

    for job in jobs[:200]:
        job_id = f"job:{job.get('id', '')}"
        nodes.append({
            "id": job_id,
            "type": "job",
            "label": job.get("job_type", ""),
            "status": job.get("status", ""),
        })
        pkg_ref = job.get("work_package_id", "")
        if pkg_ref:
            edges.append({"from": f"pkg:{pkg_ref}", "to": job_id, "type": "executes"})

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run.get("id", ""),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def build_recovery_trace(
    *,
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    dead_letters = [j for j in jobs if j.get("status") == "dead_letter"]
    retry_jobs = [j for j in jobs if j.get("status") == "retry"]
    recovery_events = [e for e in events if "recover" in _event_kind(e)]
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run.get("id", ""),
        "status": run.get("status", ""),
        "dead_letter_count": len(dead_letters),
        "retry_count": len(retry_jobs),
        "recovery_event_count": len(recovery_events),
        "dead_letters": [{"job_id": j.get("id"), "job_type": j.get("job_type"), "error": j.get("last_error", "")} for j in dead_letters[:20]],
        "continuation": run.get("continuation") or {},
    }


# --- Internal helpers ---

def _sorted_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda e: (e.get("sequence") or 0, e.get("created_at") or ""))


def _event_kind(event: dict[str, Any]) -> str:
    return event.get("event_type") or event.get("kind") or (event.get("payload") or {}).get("event_type") or ""


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": _event_kind(event),
        "sequence": event.get("sequence", 0),
        "created_at": event.get("created_at", ""),
        "run_id": event.get("run_id", ""),
        "status": event.get("status", ""),
        "checkpoint": event.get("checkpoint", ""),
    }


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        val = str(item.get(key) or "unknown")
        counts[val] = counts.get(val, 0) + 1
    return counts
