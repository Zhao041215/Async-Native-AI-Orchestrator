"""V8 Agent Contract Models — schema-aligned with actual AI prompt outputs."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dev_orchestrator.v8.models import StrictModel


# ---------------------------------------------------------------------------
# Supporting typed element models
# ---------------------------------------------------------------------------

class TechnologyChoice(StrictModel):
    name: str
    category: str
    version: str = ""
    rationale: str = ""


class ModuleBoundary(StrictModel):
    # Bug 4 fix: aligned to actual CONTRACTS_PROMPT output fields
    # V7 had: name, owner_package — prompts return: module, responsibility
    module: str
    responsibility: str = ""
    public_api: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class IntegrationContract(StrictModel):
    # Bug 4 fix: prompts return {from, to, contract} not {source_package, target_package, ...}
    # Use alias so JSON key "from" (Python keyword) maps to from_package
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)
    from_package: str = Field(alias="from", default="")
    to_package: str = Field(alias="to", default="")
    contract: str = ""


class PackageCandidate(StrictModel):
    package_key: str
    role: str
    domain: str
    objective: str
    allowed_paths: list[str] = Field(default_factory=list)


class WaveSpec(StrictModel):
    wave_key: str
    sequence: int
    packages: list[str] = Field(default_factory=list)
    depends_on_waves: list[str] = Field(default_factory=list)


class FileOwner(StrictModel):
    path: str
    owner_package: str
    responsibility: str


class FilePlanEntry(StrictModel):
    path: str
    action: Literal["create", "replace", "delete"]
    description: str = ""


class MicroTaskSpec(StrictModel):
    micro_task_id: str
    description: str
    file_targets: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)


class FileEntry(StrictModel):
    path: str
    action: Literal["create", "replace", "delete"]
    content: str = ""


class ReviewIssue(StrictModel):
    severity: Literal["critical", "major", "minor"]
    file: str
    line: int = 0
    message: str


class SecurityFinding(StrictModel):
    severity: Literal["critical", "high", "medium", "low"]
    description: str
    file: str = ""


# ---------------------------------------------------------------------------
# Contract models — one per agent task_kind
# ---------------------------------------------------------------------------

class RequirementsContract(StrictModel):
    status: Literal["GO", "NO_GO"]
    summary: str = ""
    goals: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class ArchitectureSurfaceContract(StrictModel):
    architecture_summary: str
    technology_choices: list[TechnologyChoice] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class ArchitectureLayoutContract(StrictModel):
    source_root: str
    delivery_root: str
    # Bug 4 fix: prompts return list[{path, purpose}] not list[str]
    directories: list[dict[str, Any]] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)


class ArchitectureContractsContract(StrictModel):
    module_boundaries: list[ModuleBoundary] = Field(default_factory=list)
    integration_contracts: list[IntegrationContract] = Field(default_factory=list)


class PackageScopeContract(StrictModel):
    packages: list[PackageCandidate] = Field(default_factory=list)


class PackageWaveContract(StrictModel):
    waves: list[WaveSpec] = Field(default_factory=list)


class ImplementationContractPack(StrictModel):
    file_ownership: list[FileOwner] = Field(default_factory=list)
    acceptance_cases: list[str] = Field(default_factory=list)


class ImplementationScopeContract(StrictModel):
    file_plan: list[FilePlanEntry] = Field(default_factory=list)
    micro_tasks: list[MicroTaskSpec] = Field(default_factory=list)


class FileManifestPatch(StrictModel):
    agent: str
    status: Literal["GO", "NO_GO"]
    summary: str = ""
    files: list[FileEntry] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CodeReviewContract(StrictModel):
    ok: bool
    summary: str = ""
    issues: list[ReviewIssue] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class TestReportContract(StrictModel):
    ok: bool
    summary: str = ""
    tests_passed: int = 0
    tests_failed: int = 0
    failures: list[str] = Field(default_factory=list)


class SecurityReportContract(StrictModel):
    ok: bool
    summary: str = ""
    findings: list[SecurityFinding] = Field(default_factory=list)


class RepairReportContract(StrictModel):
    ok: bool
    summary: str = ""
    changes: list[str] = Field(default_factory=list)


class ReleaseNotesContract(StrictModel):
    summary: str
    deploy_steps: list[str] = Field(default_factory=list)
    validation_steps: list[str] = Field(default_factory=list)
    rollback_plan: list[str] = Field(default_factory=list)


class PackageSelfReviewContract(StrictModel):
    ok: bool
    summary: str = ""
    missing_files: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry: task_kind -> contract model
# ---------------------------------------------------------------------------

TASK_CONTRACTS: dict[str, type[BaseModel]] = {
    "requirements": RequirementsContract,
    "architecture_surface": ArchitectureSurfaceContract,
    "architecture_layout": ArchitectureLayoutContract,
    "architecture_contracts": ArchitectureContractsContract,
    "package_scope": PackageScopeContract,
    "package_wave": PackageWaveContract,
    "implementation_contract_pack": ImplementationContractPack,
    "implementation_scope": ImplementationScopeContract,
    "file_manifest_patch": FileManifestPatch,
    "code_review": CodeReviewContract,
    "test_report": TestReportContract,
    "security_report": SecurityReportContract,
    "repair_report": RepairReportContract,
    "release_notes": ReleaseNotesContract,
    "package_self_review": PackageSelfReviewContract,
}


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def validate_agent_contract(
    task_kind: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate *payload* against the contract for *task_kind*.

    Returns ``(parsed_data, errors)``.  On success ``parsed_data`` is the
    model's ``model_dump()`` and ``errors`` is empty.  On failure
    ``parsed_data`` is ``None`` and ``errors`` contains human-readable messages.
    Unknown task_kinds return ``(None, [error])`` rather than crashing.
    """
    model_cls = TASK_CONTRACTS.get(task_kind)
    if model_cls is None:
        return None, [f"unknown task_kind: {task_kind!r}"]

    try:
        instance = model_cls.model_validate(payload)
    except ValidationError as exc:
        errors: list[str] = []
        for err in exc.errors():
            loc = " -> ".join(str(part) for part in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        return None, errors

    return instance.model_dump(), []
