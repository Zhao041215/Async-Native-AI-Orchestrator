from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator


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


class ArchitectureSurface(ContractModel):
    architecture_summary: str = ""
    technology_choices: list[Any] = Field(default_factory=list)
    design_principles: list[Any] = Field(default_factory=list)
    primary_risks: list[Any] = Field(default_factory=list)
    scale_notes: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_surface_content(self) -> "ArchitectureSurface":
        if not str(self.architecture_summary or "").strip():
            raise ValueError("architecture summary must not be empty")
        if not self.technology_choices:
            raise ValueError("technology choices must not be empty")
        return self


class ArchitectureStructure(ContractModel):
    project_layout: ProjectLayout
    module_boundaries: list[Any] = Field(default_factory=list)
    integration_contracts: list[Any] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    implementation_notes: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_structure_content(self) -> "ArchitectureStructure":
        if not self.module_boundaries and not self.integration_contracts and not self.validation_commands:
            raise ValueError("architecture structure must include boundaries, contracts, or validation commands")
        return self


class ArchitectureLayout(ContractModel):
    project_layout: ProjectLayout
    validation_commands: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_layout_content(self) -> "ArchitectureLayout":
        if not self.project_layout.directories:
            raise ValueError("architecture layout must include concrete directories")
        if not (self.validation_commands or self.project_layout.validation_commands):
            raise ValueError("architecture layout must include validation commands")
        return self


class ArchitectureContracts(ContractModel):
    module_boundaries: list[Any] = Field(default_factory=list)
    integration_contracts: list[Any] = Field(default_factory=list)
    implementation_notes: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_contract_content(self) -> "ArchitectureContracts":
        if not self.module_boundaries and not self.integration_contracts:
            raise ValueError("architecture contracts must include module boundaries or integration contracts")
        return self


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


class PackageScopeItem(ContractModel):
    package_key: str
    role: str
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


class PackageScopePlanning(ContractModel):
    packages: list[PackageScopeItem]

    @model_validator(mode="after")
    def _require_package_scope(self) -> "PackageScopePlanning":
        if not self.packages:
            raise ValueError("package scope planning must include project-specific packages")
        return self


class PackageWaveAssignment(ContractModel):
    package_key: str
    wave_key: str
    depends_on: list[str] = Field(default_factory=list)


class PackageWavePlanning(ContractModel):
    waves: list[WaveContract]
    assignments: list[PackageWaveAssignment]

    @model_validator(mode="after")
    def _require_wave_assignments(self) -> "PackageWavePlanning":
        if not self.waves:
            raise ValueError("package wave planning must include waves")
        if not self.assignments:
            raise ValueError("package wave planning must include assignments")
        return self


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
    "architecture_surface": ArchitectureSurface,
    "architecture_layout": ArchitectureLayout,
    "architecture_contracts": ArchitectureContracts,
    "architecture_structure": ArchitectureStructure,
    "architecture_design": ArchitectureDesign,
    "package_scope_planning": PackageScopePlanning,
    "package_wave_planning": PackageWavePlanning,
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
