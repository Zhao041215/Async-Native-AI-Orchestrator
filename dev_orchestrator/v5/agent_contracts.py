from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProjectLayout(ContractModel):
    source_root: str
    delivery_root: str
    entrypoints: list[Any] = Field(default_factory=list)
    directories: list[Any] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)

    @field_validator("source_root", "delivery_root")
    @classmethod
    def _non_empty_root(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("layout root must not be empty")
        return value


class RequirementsAnalysis(ContractModel):
    status: str
    summary: str
    goals: list[Any] = Field(default_factory=list)
    users: list[Any] = Field(default_factory=list)
    constraints: list[Any] = Field(default_factory=list)
    acceptance_criteria: list[Any] = Field(default_factory=list)
    missing_information: list[Any] = Field(default_factory=list)
    risks: list[Any] = Field(default_factory=list)
    expected_terms: list[Any] = Field(default_factory=list)


class ArchitectureDesign(ContractModel):
    architecture_summary: str = ""
    technology_choices: list[Any] = Field(default_factory=list)
    project_layout: ProjectLayout
    module_boundaries: list[Any] = Field(default_factory=list)
    integration_contracts: list[Any] = Field(default_factory=list)


class PackageContract(ContractModel):
    package_key: str
    role: str
    wave_key: str
    depends_on: list[str]
    allowed_paths: list[str]
    objective: str
    domain: str = ""
    subsystem: str = ""
    forbidden_paths: list[str] = Field(default_factory=list)
    requirements_mapping: list[Any] = Field(default_factory=list)
    expected_outputs: list[Any] = Field(default_factory=list)
    acceptance_gates: list[Any] = Field(default_factory=list)


class WaveContract(ContractModel):
    wave_key: str
    sequence: int = 1


class PackagePlanning(ContractModel):
    waves: list[WaveContract]
    packages: list[PackageContract]


class ManifestFile(ContractModel):
    path: str
    action: Literal["create", "replace", "delete"]
    content: str

    @field_validator("path")
    @classmethod
    def _non_empty_path(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("file path must not be empty")
        return value


class FileManifestPatch(ContractModel):
    agent: str = ""
    status: str = "GO"
    summary: str = ""
    files: list[ManifestFile] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    risks: list[Any] = Field(default_factory=list)


class CodeReview(ContractModel):
    ok: StrictBool
    status: str = "GO"
    findings: list[Any] = Field(default_factory=list)
    required_fixes: list[Any] = Field(default_factory=list)
    requirement_coverage: Any = ""
    security_notes: list[Any] = Field(default_factory=list)


class TestReport(ContractModel):
    ok: StrictBool
    status: str = "GO"
    commands: list[str] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)


class SecurityReport(ContractModel):
    ok: StrictBool
    status: str = "GO"
    evidence: list[Any] = Field(default_factory=list)
    risks: list[Any] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)


class RepairReport(ContractModel):
    ok: StrictBool = True
    status: str = "GO"
    repair_attempt: int = 1
    changed_files: list[str] = Field(default_factory=list)
    next_action: str = ""


class ReleaseNotes(ContractModel):
    release_summary: str
    deploy_steps: list[Any] = Field(default_factory=list)
    validation_steps: list[Any] = Field(default_factory=list)
    rollback: list[Any] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)


TASK_CONTRACTS: dict[str, type[BaseModel]] = {
    "requirements_analysis": RequirementsAnalysis,
    "architecture_design": ArchitectureDesign,
    "package_planning": PackagePlanning,
    "file_manifest_patch": FileManifestPatch,
    "code_generation": FileManifestPatch,
    "test_generation": FileManifestPatch,
    "security_review": FileManifestPatch,
    "integration_merge": FileManifestPatch,
    "failure_analysis": FileManifestPatch,
    "code_review": CodeReview,
    "test_report": TestReport,
    "security_report": SecurityReport,
    "repair_report": RepairReport,
    "release_notes": ReleaseNotes,
}


def agent_contract_schema(task_kind: str) -> dict[str, Any]:
    model = TASK_CONTRACTS.get(task_kind, FileManifestPatch)
    return model.model_json_schema()


def validate_agent_contract(task_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = TASK_CONTRACTS.get(task_kind, FileManifestPatch)
    try:
        validated = model.model_validate(payload)
    except ValidationError as exc:
        return {
            "ok": False,
            "schema_name": model.__name__,
            "errors": exc.errors(include_url=False),
            "schema": model.model_json_schema(),
        }
    return {
        "ok": True,
        "schema_name": model.__name__,
        "data": validated.model_dump(mode="json"),
        "errors": [],
        "schema": model.model_json_schema(),
    }
