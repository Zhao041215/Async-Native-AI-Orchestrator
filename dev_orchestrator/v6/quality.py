from __future__ import annotations

from typing import Any

from dev_orchestrator.v6.profiles import resolve_scale_profile


def augment_quality_report(report: dict[str, Any], run_context: dict[str, Any]) -> dict[str, Any]:
    augmented = dict(report)
    gates = list(augmented.get("gates") or [])
    gates.extend(validate_100k_quality_gates(run_context))
    critical_failures = [gate for gate in gates if not gate.get("ok") and gate.get("severity") == "critical"]
    augmented["schema_version"] = "6.0"
    augmented["kernel_generation"] = "100k_ai_native"
    augmented["gates"] = gates
    augmented["ok"] = not critical_failures
    augmented["status"] = "GO" if augmented["ok"] else "NO_GO"
    augmented["v6_gate_count"] = len([gate for gate in gates if str(gate.get("name", "")).startswith(("v6_", "mission_", "package_", "provider_", "recovery_", "performance_"))])
    return augmented


def validate_100k_quality_gates(run_context: dict[str, Any]) -> list[dict[str, Any]]:
    profile = resolve_scale_profile(run_context.get("scale_profile") or run_context.get("project_config") or {})
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
    severity = "critical" if profile.get("name") in {"large", "xlarge_100k"} else "major"
    run_id_present = bool(run_context.get("run_id"))

    dag_report = _dag_report(packages)
    ownership_report = _ownership_report(packages)
    contract_report = _contract_density_report(profile, packages, contract_index, run_context.get("architecture") or {})
    provider_report = _provider_resilience_report(agent_runs, provider_health)
    patch_report = _patch_transaction_report(patch_transactions)
    performance_report = _performance_budget_report(agent_runs, profile)
    replay_report = _event_replay_report(replay_projection, run_id_present)
    checkpoint_report = _checkpoint_resume_report(checkpoint_resume, run_id_present)
    recovery_report = _recovery_trace_report(recovery_trace, run_id_present)

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
    return {"ok": not missing_classification, "failed_agent_runs": len(failed_payloads), "missing_classification": missing_classification, "provider_health": provider_health}


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
    over_profile = [payload.get("agent_run_id", "") for payload in payloads if int(((payload.get("prompt_summary") or {}).get("bytes") or 0)) > budget > 0]
    over_task = [payload.get("agent_run_id", "") for payload in payloads if payload.get("over_budget")]
    return {"ok": not over_profile and not over_task, "max_prompt_bytes": max_prompt, "context_budget_chars": budget, "over_profile_budget": over_profile, "over_task_budget": over_task}


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
