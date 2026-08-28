"""Independent V2 contracts for D3/O3 monitoring execution policies."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    ArtifactRef,
    ResearchLane,
    utc_now,
)

DOCUMENT3_SCHEMA_VERSION: Final[Literal["document3.v2"]] = "document3.v2"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentModel(BaseModel):
    """Agent work files ignore harmless annotations; canonical output stays strict."""

    model_config = ConfigDict(extra="ignore")


class PolicyDecision(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class PublicationState(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class CalibrationSourceKind(StrEnum):
    D2 = "D2"
    REFERENCE_VIEW = "REFERENCE_VIEW"
    WEB = "WEB"
    DATA_MCP = "DATA_MCP"


class PathStatus(StrEnum):
    PENDING = "PENDING"
    COMPILED = "COMPILED"
    UNRESOLVED = "UNRESOLVED"


class O3RunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    REVIEW_BLOCKED = "REVIEW_BLOCKED"
    DEGRADED = "DEGRADED"
    NOOP = "NOOP"


class Document2Ref(ContractModel):
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: datetime
    publication_state: PublicationState


class EventLibraryRef(ContractModel):
    contract_version: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: datetime


class PolicySourceRef(ContractModel):
    shell_id: str = Field(min_length=1)
    expectation_id: str = Field(min_length=1)
    gap_id: str = Field(min_length=1)


class Calibration(ContractModel):
    reference_state: str = Field(min_length=1)
    trigger_boundary: str = Field(min_length=1)
    qualifying_evidence: str = Field(min_length=1)


class ActivationCondition(ContractModel):
    condition_id: str = Field(min_length=1)
    criterion: str = Field(min_length=1)
    calibration: Calibration


class Policy(ContractModel):
    policy_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_refs: list[PolicySourceRef] = Field(min_length=1)
    decision: PolicyDecision
    match_scope: str = Field(min_length=1)
    activation_conditions: list[ActivationCondition] = Field(min_length=1)
    activation_summary: str = Field(min_length=1)

    @field_validator("source_refs")
    @classmethod
    def source_refs_are_unique(cls, value: list[PolicySourceRef]) -> list[PolicySourceRef]:
        keys = [(item.shell_id, item.expectation_id, item.gap_id) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("source_refs must be unique within a policy")
        return value

    @field_validator("activation_conditions")
    @classmethod
    def condition_ids_are_unique(
        cls, value: list[ActivationCondition]
    ) -> list[ActivationCondition]:
        ids = [item.condition_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("condition_id must be unique within a policy")
        return value


class PolicySet(ContractModel):
    schema_version: Literal["document3.v2"] = DOCUMENT3_SCHEMA_VERSION
    ticker: str
    policy_set_version: int = Field(ge=1)
    publication_state: PublicationState = PublicationState.COMPLETE
    document2_ref: Document2Ref
    event_library_ref: EventLibraryRef | None = None
    policies: list[Policy] = Field(default_factory=list)
    published_at: datetime = Field(default_factory=utc_now)

    @field_validator("policies")
    @classmethod
    def policy_ids_are_unique(cls, value: list[Policy]) -> list[Policy]:
        ids = [item.policy_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("policy_id must be unique in a policy set")
        return value


class PolicyPatchSet(ContractModel):
    base_policy_set_version: int = Field(ge=1)
    event_library_ref: EventLibraryRef
    upsert_policies: list[Policy] = Field(default_factory=list)
    retire_policy_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def operations_do_not_conflict(self) -> PolicyPatchSet:
        upsert_ids = [item.policy_id for item in self.upsert_policies]
        if len(upsert_ids) != len(set(upsert_ids)):
            raise ValueError("upsert policy ids must be unique")
        if len(self.retire_policy_ids) != len(set(self.retire_policy_ids)):
            raise ValueError("retire policy ids must be unique")
        overlap = set(upsert_ids).intersection(self.retire_policy_ids)
        if overlap:
            raise ValueError(f"policy cannot be upserted and retired: {sorted(overlap)}")
        return self

    @property
    def is_empty(self) -> bool:
        return not self.upsert_policies and not self.retire_policy_ids


class O3RunResult(ContractModel):
    status: O3RunStatus
    processed_gap_count: int = Field(default=0, ge=0)
    policy_count: int = Field(default=0, ge=0)
    unresolved_path_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    policy_set_version: int | None = Field(default=None, ge=1)


class WorklistEntry(AgentModel):
    shell_id: str
    expectation_id: str
    gap_id: str
    path_id: str
    direction: PolicyDecision
    path_summary: str
    d2_boundary_sufficient: bool
    missing_calibration: str = ""
    status: PathStatus = PathStatus.PENDING
    policy_ids: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = None


class CalibrationLogEntry(AgentModel):
    path_id: str
    calibration_need: str
    source_kind: CalibrationSourceKind
    finding: str
    resolved: bool


class WaveState(AgentModel):
    completed_shell_ids: list[str] = Field(default_factory=list)
    current_shell_id: str | None = None
    completed_path_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class MaintenanceCandidate(AgentModel):
    policy_id: str
    reason: str


class CoveragePath(ContractModel):
    path_id: str
    direction: PolicyDecision
    status: PathStatus
    policy_ids: list[str] = Field(default_factory=list)
    unresolved_reason: str | None = None


class CoverageGap(ContractModel):
    shell_id: str
    expectation_id: str
    gap_id: str
    paths: list[CoveragePath] = Field(default_factory=list)


class FailedShellCoverage(ContractModel):
    shell_id: str
    reason: str


class CoverageMap(ContractModel):
    ticker: str
    gaps: list[CoverageGap] = Field(default_factory=list)
    failed_shells: list[FailedShellCoverage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReviewIssue(ContractModel):
    code: str
    message: str
    affected_policy_ids: list[str] = Field(default_factory=list)
    requires_research: bool = False
    blocking: bool = False


class ReviewResult(AgentModel):
    status: Literal["PASSED", "REVIEW_BLOCKED"]
    issue_count: int = Field(ge=0)
    blocking_issue_count: int = Field(ge=0)
    issues: list[ReviewIssue] = Field(default_factory=list)


class ValidationFinding(ContractModel):
    code: str
    message: str
    blocking: bool = False


class ValidationReport(ContractModel):
    valid: bool
    publication_state: PublicationState
    findings: list[ValidationFinding] = Field(default_factory=list)

    @property
    def blocking_findings(self) -> list[ValidationFinding]:
        return [finding for finding in self.findings if finding.blocking]


class RuntimeConditionProjection(ContractModel):
    policy_id: str
    title: str
    decision: PolicyDecision
    match_scope: str
    condition_id: str
    criterion: str
    activation_summary: str


class RuntimePolicyProjection(ContractModel):
    schema_version: Literal["document3.runtime_projection.v1"] = "document3.runtime_projection.v1"
    ticker: str
    policy_set_version: int = Field(ge=1)
    policy_set_published_at: datetime
    conditions: list[RuntimeConditionProjection] = Field(default_factory=list)


class PolicySetVersionMetadata(ContractModel):
    """Compact history row; intentionally excludes canonical policy JSON."""

    ticker: str
    policy_set_version: int = Field(ge=1)
    is_current: bool
    publication_state: PublicationState
    policy_count: int = Field(ge=0)
    published_at: datetime


class Document3Handoff(ContractModel):
    workflow_version: Literal["codex_document3_v1"] = CODEX_DOCUMENT3_WORKFLOW_VERSION
    ticker: str
    run_id: str
    policy_set_version: int = Field(ge=1)
    publication_state: PublicationState
    published_artifact: ArtifactRef
    coverage_artifact: ArtifactRef
    runtime_projection_artifact: ArtifactRef
    published_at: datetime = Field(default_factory=utc_now)


class Document3Bundle(ContractModel):
    workflow_version: Literal["codex_document3_v1"] = CODEX_DOCUMENT3_WORKFLOW_VERSION
    research_lane: Literal[ResearchLane.DOCUMENT3] = ResearchLane.DOCUMENT3
    ticker: str
    run_id: str
    status: Literal["draft", "published", "failed"]
    handoff: Document3Handoff | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def published_bundle_has_handoff(self) -> Document3Bundle:
        if self.status == "published" and (self.handoff is None or self.published_at is None):
            raise ValueError("published D3 bundle requires handoff and published_at")
        return self


class Document3InitializeRequest(ContractModel):
    ticker: str
    document2_run_id: str
    event_library_version: int | None = Field(default=None, ge=1)
    run_id: str | None = None


class Document3MaintainRequest(ContractModel):
    ticker: str
    event_library_version: int | None = Field(default=None, ge=1)
    run_id: str | None = None


def strict_json_schema(value: Any) -> Any:
    """Close a Pydantic schema for Responses structured output."""

    if isinstance(value, list):
        return [strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    strict = {key: strict_json_schema(item) for key, item in value.items() if key != "default"}
    properties = strict.get("properties")
    if isinstance(properties, dict):
        strict["additionalProperties"] = False
        strict["required"] = list(properties)
    return strict
