from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    lowered = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    parts = [part for part in lowered.split("-") if part]
    return "-".join(parts) or "project"


@dataclass
class Finding:
    code: str
    severity: str
    category: str
    message: str
    evidence: list[str] = field(default_factory=list)
    owner: str = "chief"
    repair_role: str = "chief"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RequirementAtom:
    id: str
    text: str
    priority: str
    category: str
    source_section: str = ""
    owner_role: str = "product-analyst"
    acceptance_refs: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AcceptanceContract:
    id: str
    requirement_id: str
    statement: str
    validation_method: str
    required_evidence: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArchitectureDecision:
    id: str
    title: str
    decision: str
    rationale: str
    impacted_subsystems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Subsystem:
    id: str
    name: str
    owner_role: str
    path_hints: list[str]
    requirement_ids: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorkPackage:
    id: str
    title: str
    owner_role: str
    subsystem_id: str
    requirement_ids: list[str]
    dependencies: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    status: str = "ready"
    parallel_group: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentRun:
    id: str
    run_id: str
    work_package_id: str
    role: str
    status: str
    started_at: str
    finished_at: str = ""
    summary: str = ""
    model_calls: int = 0
    tool_calls: int = 0
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["findings"] = [item.to_dict() for item in self.findings]
        return payload


@dataclass
class PatchSet:
    id: str
    work_package_id: str
    role: str
    status: str
    files_changed: list[str] = field(default_factory=list)
    diff_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationRun:
    id: str
    run_id: str
    gate: str
    status: str
    score: int
    findings: list[Finding] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["findings"] = [item.to_dict() for item in self.findings]
        return payload


@dataclass
class RepairTask:
    id: str
    project_id: str
    run_id: str
    finding_code: str
    assigned_role: str
    status: str
    reason: str
    created_at: str = field(default_factory=utc_now)
    resolved_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReleaseCandidate:
    id: str
    project_id: str
    run_id: str
    status: str
    decision: str
    gate_score: int
    blockers: list[Finding] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["blockers"] = [item.to_dict() for item in self.blockers]
        return payload


@dataclass
class Project:
    id: str
    name: str
    title: str
    description: str
    project_path: str
    status: str = "draft"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Run:
    id: str
    project_id: str
    status: str
    chief_summary: str
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)
