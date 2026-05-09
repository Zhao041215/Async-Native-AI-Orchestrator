from __future__ import annotations

from typing import Any

from dev_orchestrator.v6.profiles import resolve_scale_profile


def augment_quality_report(report: dict[str, Any], run_context: dict[str, Any]) -> dict[str, Any]:
    augmented = dict(report)
    gates = list(augmented.get("gates") or [])
    gates.extend(validate_100k_quality_gates(run_context))
    critical_failures = [gate for gate in gates if not gate.get("ok") and gate.get("severity") == "critical"]
    augmented["schema_version"] = "6.1"
    augmented["kernel_generation"] = "100k_ai_native"
    augmented["gates"] = gates
    augmented["ok"] = not critical_failures
    augmented["status"] = "GO" if augmented["ok"] else "NO_GO"
    augmented["v6_gate_count"] = len([gate for gate in gates if str(gate.get("name", "")).startswith(("v6_", "mission_", "package_", "provider_", "recovery_", "performance_", "ai_", "oversize_", "agent_"))])
    return augmented


def validate_100k_quality_gates(run_context: dict[str, Any]) -> list[dict[str, Any]]:
    profile = dict(resolve_scale_profile(run_context.get("scale_profile") or run_context.get("project_config") or {}))
    profile["live_ai_required"] = bool(run_context.get("live_ai_required") or profile.get("live_ai_required"))
    packages = _package_payloads(run_context.get("packages") or [])
    waves = run_context.get("waves") or []
    contract_index = run_context.get("contract_index") or {}
    agent_runs = run_context.get("agent_runs") or []
    patch_transactions = run_context.get("patch_transactions") or []
    continuation = run_context.get("continuation") or {}
    provider_health = run_context.get("provider_health") or {}
    replay_projection = run_context.get("replay_projection") or {}
    checkpoint_resume = run_context.get("checkpoint_resume") or {}
    recovery_trace = run_context.get("recovery_trace") or {}
    ai_payload_budget = run_context.get("ai_payload_budget") or {}
    severity = "critical" if profile.get("name") in {"large", "xlarge_100k"} else "major"
    run_id_present = bool(run_context.get("run_id"))

    dag_report = _dag_report(packages)
    ownership_report = _ownership_report(packages)
    contract_report = _contract_density_report(profile, packages, contract_index, run_context.get("architecture") or {})
    provider_report = _provider_resilience_report(agent_runs, provider_health)
    patch_report = _patch_transaction_report(patch_transactions)
    parallel_report = _parallel_execution_report(run_context, profile)
    slot_report = _durable_ai_slot_report(run_context, profile)
    heartbeat_report = _long_call_heartbeat_report(run_context)
    circuit_report = _provider_circuit_report(provider_health, recovery_trace)
    patch_parallel_report = _patch_parallel_conflict_report(run_context, patch_transactions)
    performance_report = _performance_budget_report(agent_runs, profile)
    replay_report = _event_replay_report(replay_projection, run_id_present)
    checkpoint_report = _checkpoint_resume_report(checkpoint_resume, run_id_present)
    recovery_report = _recovery_trace_report(recovery_trace, run_id_present)
    ai_live_report = _ai_live_execution_report(agent_runs, profile)
    ai_budget_report = _ai_payload_budget_report(ai_payload_budget, profile)
    ai_compression_report = _ai_context_compression_report(ai_payload_budget, profile)
    oversize_recovery_report = _oversize_recovery_report(recovery_trace, ai_payload_budget, run_id_present)
    diversity_report = _agent_output_diversity_report(agent_runs, run_context)

    return [
        _gate(
            "v6_scale_profile_gate",
            profile.get("kernel_generation") == "100k_ai_native" and bool(profile.get("mission_contract_version")),
            "critical",
            {"profile": profile.get("name", ""), "mission_contract_version": profile.get("mission_contract_version", "")},
        ),
        _gate(
            "mission_state_contract_gate",
            (not run_id_present) or bool(continuation.get("checkpoint") or run_context.get("checkpoint")),
            "critical",
            {"run_id": run_context.get("run_id", ""), "checkpoint": continuation.get("checkpoint") or run_context.get("checkpoint", "")},
        ),
        _gate("package_dag_acyclic_gate", dag_report["ok"], severity, dag_report),
        _gate("package_ownership_gate", ownership_report["ok"], severity, ownership_report),
        _gate("contract_density_gate", contract_report["ok"], severity, contract_report),
        _gate("provider_resilience_gate", provider_report["ok"], "critical", provider_report),
        _gate("ai_live_execution_gate", ai_live_report["ok"], "critical", ai_live_report),
        _gate("ai_payload_budget_gate", ai_budget_report["ok"], "critical", ai_budget_report),
        _gate("ai_context_compression_gate", ai_compression_report["ok"], "critical", ai_compression_report),
        _gate("oversize_recovery_gate", oversize_recovery_report["ok"], "critical", oversize_recovery_report),
        _gate("agent_output_diversity_gate", diversity_report["ok"], "critical", diversity_report),
        _gate("event_replay_projection_gate", replay_report["ok"], "critical", replay_report),
        _gate("checkpoint_resume_gate", checkpoint_report["ok"], "critical", checkpoint_report),
        _gate("recovery_trace_gate", recovery_report["ok"], "critical", recovery_report),
        _gate(
            "recovery_checkpoint_gate",
            bool(continuation.get("next_action")) and bool(continuation.get("checkpoint") or run_context.get("checkpoint")),
            "critical",
            {"next_action": continuation.get("next_action", ""), "checkpoint": continuation.get("checkpoint") or run_context.get("checkpoint", "")},
        ),
        _gate("patch_transaction_addressing_gate", patch_report["ok"], severity, patch_report),
        _gate("ai_native_no_template_fallback_gate", _agent_native_report(agent_runs)["ok"], "critical", _agent_native_report(agent_runs)),
        _gate("parallel_execution_safety_gate", parallel_report["ok"], "critical", parallel_report),
        _gate("durable_ai_slot_gate", slot_report["ok"], "critical", slot_report),
        _gate("long_call_heartbeat_gate", heartbeat_report["ok"], "critical", heartbeat_report),
        _gate("provider_circuit_recovery_gate", circuit_report["ok"], "critical", circuit_report),
        _gate("patch_parallel_conflict_gate", patch_parallel_report["ok"], severity, patch_parallel_report),
        _gate("performance_budget_gate", performance_report["ok"], "major", performance_report),
    ]


def _gate(name: str, ok: bool, severity: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "severity": severity, "details": details}


def _package_payloads(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads = []
    for package in packages:
        payload = dict(package.get("payload") or package)
        payload.setdefault("status", package.get("status", payload.get("status", "")))
        payloads.append(payload)
    return payloads


def _dag_report(packages: list[dict[str, Any]]) -> dict[str, Any]:
    if not packages:
        return {"ok": True, "not_required_yet": True, "package_count": 0, "missing_dependencies": [], "cycles": [], "duplicate_keys": []}
    keys = [str(package.get("package_key") or "") for package in packages if package.get("package_key")]
    key_set = set(keys)
    missing = []
    graph: dict[str, list[str]] = {}
    for package in packages:
        key = str(package.get("package_key") or "")
        deps = [str(dep) for dep in package.get("depends_on") or []]
        graph[key] = deps
        missing.extend({"package_key": key, "missing_dependency": dep} for dep in deps if dep not in key_set)
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            cycles.append(trail + [node])
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep, trail + [node])
        visiting.remove(node)
        visited.add(node)

    for key in keys:
        visit(key, [])
    duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
    return {"ok": bool(keys) and not missing and not cycles and not duplicate_keys, "package_count": len(keys), "missing_dependencies": missing[:50], "cycles": cycles[:10], "duplicate_keys": duplicate_keys}


def _ownership_report(packages: list[dict[str, Any]]) -> dict[str, Any]:
    if not packages:
        return {"ok": True, "not_required_yet": True, "ownerless": [], "broad_multi_owner": [], "owned_package_count": 0}
    ownerless = [
        package.get("package_key", "")
        for package in packages
        if not package.get("role") or not package.get("subsystem") or not package.get("allowed_paths")
    ]
    path_owner_count: dict[str, int] = {}
    for package in packages:
        for path in package.get("allowed_paths") or []:
            path_owner_count[str(path)] = path_owner_count.get(str(path), 0) + 1
    broad_multi_owner = [path for path, count in path_owner_count.items() if count > 1 and path in {"**", "*", "./**"}]
    return {"ok": bool(packages) and not ownerless and not broad_multi_owner, "ownerless": ownerless, "broad_multi_owner": broad_multi_owner, "owned_package_count": len(packages) - len(ownerless)}


def _contract_density_report(profile: dict[str, Any], packages: list[dict[str, Any]], contract_index: dict[str, Any], architecture: dict[str, Any]) -> dict[str, Any]:
    if not packages:
        return {"ok": True, "not_required_yet": True, "density": str(profile.get("contract_density") or "standard"), "observed_contract_signals": 0, "minimum": 0}
    contracts = contract_index.get("contracts") or []
    architecture_contracts = architecture.get("integration_contracts") or []
    expected_outputs = [output for package in packages for output in (package.get("expected_outputs") or [])]
    density = str(profile.get("contract_density") or "standard")
    if density == "standard":
        minimum = 1 if len(packages) > 1 else 0
    elif density == "dense":
        minimum = max(1, len(packages) // 2)
    else:
        minimum = max(2, len(packages))
    observed = len(contracts) + len(architecture_contracts) + len(expected_outputs)
    return {"ok": observed >= minimum, "density": density, "observed_contract_signals": observed, "minimum": minimum, "contract_index_count": len(contracts), "architecture_contract_count": len(architecture_contracts), "expected_output_count": len(expected_outputs)}


def _provider_resilience_report(agent_runs: list[dict[str, Any]], provider_health: dict[str, Any]) -> dict[str, Any]:
    failed_payloads = [artifact.get("payload") or {} for artifact in agent_runs if not (artifact.get("payload") or {}).get("ok", True)]
    missing_classification = [payload.get("agent_run_id", "") for payload in failed_payloads if "retryable" not in payload or not payload.get("error_kind")]
    oversize_failures = [payload.get("agent_run_id", "") for payload in failed_payloads if payload.get("error_kind") == "provider_payload_oversize"]
    return {"ok": not missing_classification, "failed_agent_runs": len(failed_payloads), "missing_classification": missing_classification, "oversize_failures": oversize_failures, "provider_health": provider_health}


def _patch_transaction_report(patch_transactions: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = [artifact.get("payload") or {} for artifact in patch_transactions]
    missing_ids = [artifact.get("id", "") for artifact, payload in zip(patch_transactions, payloads) if not payload.get("transaction_id")]
    conflict_count = sum(len(payload.get("conflicts") or []) for payload in payloads)
    return {"ok": not missing_ids, "transaction_count": len(payloads), "missing_transaction_ids": missing_ids, "conflict_count": conflict_count}


def _agent_native_report(agent_runs: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = [artifact.get("payload") or {} for artifact in agent_runs]
    skipped_required = [payload.get("agent_run_id", "") for payload in payloads if payload.get("skipped") and payload.get("required", True)]
    return {"ok": not skipped_required, "agent_run_count": len(payloads), "skipped_required": skipped_required}


def _performance_budget_report(agent_runs: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    payloads = [artifact.get("payload") or {} for artifact in agent_runs]
    prompt_bytes = [int(((payload.get("prompt_summary") or {}).get("bytes") or 0)) for payload in payloads]
    max_prompt = max(prompt_bytes) if prompt_bytes else 0
    budget = int(profile.get("context_budget_chars") or 0)
    over_profile = [payload.get("agent_run_id", "") for payload in payloads if int((payload.get("payload_chars") or 0)) > budget > 0]
    allowed_statuses = {"", "within_budget", "trimmed_within_budget", "ai_compressed_within_budget"}
    over_task = [payload.get("agent_run_id", "") for payload in payloads if payload.get("budget_status") not in allowed_statuses]
    return {"ok": not over_profile and not over_task, "max_prompt_bytes": max_prompt, "context_budget_chars": budget, "over_profile_budget": over_profile, "over_task_budget": over_task}


def _parallel_execution_report(run_context: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    waves = run_context.get("waves") or []
    packages = run_context.get("packages") or []
    limit = max(1, int(profile.get("wave_parallelism") or 1))
    wave_counts: dict[str, int] = {}
    for package in packages:
        wave_key = str(package.get("wave_key") or (package.get("payload") or {}).get("wave_key") or "")
        if wave_key:
            wave_counts[wave_key] = wave_counts.get(wave_key, 0) + 1
    over_limit = {key: count for key, count in wave_counts.items() if count > max(limit * 3, limit)}
    return {"ok": not over_limit, "wave_parallelism": limit, "wave_count": len(waves), "package_count": len(packages), "over_limit_waves": over_limit}


def _durable_ai_slot_report(run_context: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    ai_slots = run_context.get("ai_slots") or {}
    agent_runs = [artifact.get("payload") or {} for artifact in run_context.get("agent_runs") or []]
    live_runs = [run for run in agent_runs if run.get("live_provider")]
    if not live_runs and profile.get("name") not in {"large", "xlarge_100k"}:
        return {"ok": True, "not_required_yet": True, "slot_count": int(ai_slots.get("slot_count") or 0)}
    missing_slot = [run.get("agent_run_id", "") for run in live_runs if not run.get("slot_id")]
    return {"ok": not missing_slot, "slot_count": int(ai_slots.get("slot_count") or 0), "active_count": int(ai_slots.get("active_count") or 0), "missing_slot_agent_runs": missing_slot}


def _long_call_heartbeat_report(run_context: dict[str, Any]) -> dict[str, Any]:
    jobs = run_context.get("jobs") or []
    running = [job for job in jobs if job.get("status") in {"leased", "running"}]
    missing = [job.get("id", "") for job in running if not job.get("heartbeat_at")]
    return {"ok": not missing, "running_job_count": len(running), "missing_heartbeat_jobs": missing}


def _provider_circuit_report(provider_health: dict[str, Any], recovery_trace: dict[str, Any]) -> dict[str, Any]:
    items = provider_health.get("items") or []
    blocked = [item for item in items if item.get("status") == "blocked"]
    circuit = [item for item in items if item.get("status") == "circuit_open"]
    if blocked:
        return {"ok": False, "blocked_providers": blocked, "circuit_open": circuit}
    if circuit:
        return {"ok": bool(recovery_trace), "blocked_providers": [], "circuit_open": circuit, "recovery_event_count": recovery_trace.get("recovery_event_count", 0)}
    return {"ok": True, "blocked_providers": [], "circuit_open": circuit}


def _patch_parallel_conflict_report(run_context: dict[str, Any], patch_transactions: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = [artifact.get("payload") or {} for artifact in patch_transactions]
    conflicts = [payload for payload in payloads if payload.get("conflicts")]
    unresolved = [payload.get("transaction_id", "") for payload in conflicts if not payload.get("ok")]
    jobs = run_context.get("jobs") or []
    integration_jobs = [job for job in jobs if job.get("job_type") == "integration"]
    return {"ok": not unresolved or bool(integration_jobs), "conflict_count": len(conflicts), "unresolved_conflict_transactions": unresolved, "integration_job_count": len(integration_jobs)}


def _ai_live_execution_report(agent_runs: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    live_runs = [artifact.get("payload") or {} for artifact in agent_runs if (artifact.get("payload") or {}).get("live_provider")]
    live_required = bool(profile.get("live_ai_required"))
    if not live_required:
        return {"ok": True, "not_required_yet": True, "live_run_count": len(live_runs)}
    return {
        "ok": bool(live_runs),
        "live_run_count": len(live_runs),
        "required_for_profile": profile.get("name", ""),
        "sample_models": sorted({str(run.get("model") or "") for run in live_runs if run.get("model")})[:10],
    }


def _ai_payload_budget_report(ai_payload_budget: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if not ai_payload_budget:
        if profile.get("name") not in {"large", "xlarge_100k"}:
            return {"ok": True, "not_required_yet": True, "reason": "missing_ai_payload_budget"}
        return {"ok": False, "reason": "missing_ai_payload_budget"}
    if profile.get("name") in {"large", "xlarge_100k"}:
        return {
            "ok": bool(ai_payload_budget.get("budget_limit")) and bool(ai_payload_budget.get("provider_body_limit_bytes")),
            "budget_status": ai_payload_budget.get("budget_status", ""),
            "payload_chars": ai_payload_budget.get("payload_chars", 0),
            "body_bytes": ai_payload_budget.get("body_bytes", 0),
            "budget_limit": ai_payload_budget.get("budget_limit", 0),
            "provider_body_limit_bytes": ai_payload_budget.get("provider_body_limit_bytes", 0),
        }
    return {"ok": True, "not_required_yet": True, "budget_status": ai_payload_budget.get("budget_status", "")}


def _ai_context_compression_report(ai_payload_budget: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if not ai_payload_budget:
        if profile.get("name") not in {"large", "xlarge_100k"}:
            return {"ok": True, "not_required_yet": True, "reason": "missing_ai_payload_budget"}
        return {"ok": False, "reason": "missing_ai_payload_budget"}
    if profile.get("name") in {"large", "xlarge_100k"}:
        if ai_payload_budget.get("ok"):
            return {"ok": True, "compression_applied": bool(ai_payload_budget.get("compression_applied")), "summary_agent_run_id": ai_payload_budget.get("summary_agent_run_id", ""), "budget_status": ai_payload_budget.get("budget_status", "")}
        return {"ok": bool(ai_payload_budget.get("compression_applied")) or ai_payload_budget.get("budget_status") == "within_budget", "compression_applied": bool(ai_payload_budget.get("compression_applied")), "budget_status": ai_payload_budget.get("budget_status", "")}
    return {"ok": True, "not_required_yet": True, "compression_applied": bool(ai_payload_budget.get("compression_applied"))}


def _oversize_recovery_report(recovery_trace: dict[str, Any], ai_payload_budget: dict[str, Any], run_id_present: bool) -> dict[str, Any]:
    if not run_id_present:
        return {"ok": True, "not_required_yet": True}
    oversize = str((ai_payload_budget or {}).get("budget_status") or "")
    if oversize == "within_budget":
        return {"ok": True, "oversize_blocked": False}
    if oversize:
        return {"ok": bool(recovery_trace), "oversize_blocked": True, "recovery_event_count": recovery_trace.get("recovery_event_count", 0), "dead_letter_count": recovery_trace.get("dead_letter_count", 0)}
    return {"ok": True, "oversize_blocked": False}


def _agent_output_diversity_report(agent_runs: list[dict[str, Any]], run_context: dict[str, Any]) -> dict[str, Any]:
    payloads = [artifact.get("payload") or {} for artifact in agent_runs]
    task_kinds = sorted({str(payload.get("task_kind") or "") for payload in payloads if payload.get("task_kind")})
    models = sorted({str(payload.get("model") or "") for payload in payloads if payload.get("model")})
    prompt_hashes = sorted({str(payload.get("context_hash") or "") for payload in payloads if payload.get("context_hash")})
    role_count = len({str(payload.get("role") or "") for payload in payloads if payload.get("role")})
    if not payloads:
        return {"ok": True, "not_required_yet": True, "task_kinds": [], "models": []}
    if run_context.get("scale_profile", {}).get("name") in {"large", "xlarge_100k"}:
        return {
            "ok": len(task_kinds) >= 3 and len(models) >= 1 and len(prompt_hashes) >= 2 and role_count >= 2,
            "task_kinds": task_kinds,
            "models": models,
            "prompt_hash_count": len(prompt_hashes),
            "role_count": role_count,
        }
    return {"ok": True, "task_kinds": task_kinds, "models": models, "prompt_hash_count": len(prompt_hashes), "role_count": role_count}


def _event_replay_report(replay_projection: dict[str, Any], run_id_present: bool) -> dict[str, Any]:
    if not run_id_present:
        return {"ok": True, "not_required_yet": True}
    if not replay_projection:
        return {"ok": False, "reason": "missing_replay_projection"}
    return {
        "ok": bool(replay_projection.get("ok")) and bool(replay_projection.get("projection_hash")),
        "source": replay_projection.get("source", ""),
        "projection_hash": replay_projection.get("projection_hash", ""),
        "applied_event_count": replay_projection.get("applied_event_count", 0),
        "drift": replay_projection.get("drift", {}),
    }


def _checkpoint_resume_report(checkpoint_resume: dict[str, Any], run_id_present: bool) -> dict[str, Any]:
    if not run_id_present:
        return {"ok": True, "not_required_yet": True}
    if not checkpoint_resume:
        return {"ok": False, "reason": "missing_checkpoint_resume"}
    return {
        "ok": bool(checkpoint_resume.get("projection_consistent")) and bool(checkpoint_resume.get("checkpoint")) and bool(checkpoint_resume.get("next_action")),
        "checkpoint": checkpoint_resume.get("checkpoint", ""),
        "next_action": checkpoint_resume.get("next_action", ""),
        "resumable": checkpoint_resume.get("resumable", False),
        "dead_letter_count": checkpoint_resume.get("dead_letter_count", 0),
    }


def _recovery_trace_report(recovery_trace: dict[str, Any], run_id_present: bool) -> dict[str, Any]:
    if not run_id_present:
        return {"ok": True, "not_required_yet": True}
    if not recovery_trace:
        return {"ok": False, "reason": "missing_recovery_trace"}
    return {
        "ok": "dead_letter_count" in recovery_trace and "provider_failure_count" in recovery_trace,
        "dead_letter_count": recovery_trace.get("dead_letter_count", 0),
        "provider_failure_count": recovery_trace.get("provider_failure_count", 0),
        "recovery_event_count": recovery_trace.get("recovery_event_count", 0),
    }
