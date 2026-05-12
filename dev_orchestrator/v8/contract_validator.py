"""V8 Contract Validator — wires validate_agent_contract into the scheduler call path.

Bug 6 fix: contracts.py existed in V7 but validate_agent_contract() was never called.
This module provides the integration layer and defines which failures are blocking.
"""
from __future__ import annotations

from typing import Any

from dev_orchestrator.v8.contracts import validate_agent_contract
from dev_orchestrator.v8.models import ContractValidation

# task_kinds where a contract failure should block pipeline advancement
BLOCKING_CONTRACT_KINDS: frozenset[str] = frozenset({
    "requirements",
    "package_scope",
    "package_wave",
    "file_manifest_patch",
})

# task_kinds where unknown/missing contract is not an error (informational only)
ADVISORY_CONTRACT_KINDS: frozenset[str] = frozenset({
    "architecture_surface",
    "architecture_layout",
    "architecture_contracts",
    "code_review",
    "security_report",
    "test_report",
    "repair_report",
    "release_notes",
    "package_self_review",
    "code_generation",
    "test_generation",
    "security_review",
    "integration",
})


def validate_and_classify(
    task_kind: str,
    parsed: dict[str, Any],
) -> ContractValidation:
    """Validate parsed AI output against the registered contract.

    Returns a ContractValidation. Unknown task_kinds get ok=True so that
    future task_kinds don't break existing pipeline runs.
    """
    if not task_kind or task_kind not in {
        **{k: True for k in BLOCKING_CONTRACT_KINDS},
        **{k: True for k in ADVISORY_CONTRACT_KINDS},
    }:
        return ContractValidation(ok=True, contract_type=task_kind)

    _data, errors = validate_agent_contract(task_kind, parsed)
    return ContractValidation(
        ok=len(errors) == 0,
        errors=errors,
        contract_type=task_kind,
    )


def is_blocking_failure(validation: ContractValidation) -> bool:
    """Return True if this contract failure should block pipeline advancement."""
    if validation.ok:
        return False
    return validation.contract_type in BLOCKING_CONTRACT_KINDS
