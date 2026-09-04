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

DOCUMENT3_SCHEMA_VERSION: Final[Literal["document3.v2.2"]] = "document3.v2.2"
RUNTIME_POLICY_PROJECTION_VERSION: Final[Literal["document3.runtime_projection.v4"]] = (
    "document3.runtime_projection.v4"
)
RUNTIME_POLICY_CONSUMER_CONTRACT: Final[Literal["persistent-runtime.v2.or-policy.v1"]] = (
    "persistent-runtime.v2.or-policy.v1"
)


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


class ValidationSeverity(StrEnum):
    """Runtime disposition for deterministic findings.

    Record/file defects are recoverable by default.  ``FATAL`` is reserved for
    global integrity failures such as frozen-input mutation or stale publication
    state; it must never be selected by an Agent-authored severity flag.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    RECOVERABLE = "RECOVERABLE"
    FATAL = "FATAL"


class ValidationScope(StrEnum):
    RECORD = "RECORD"
    FILE = "FILE"
    STAGE = "STAGE"
    GLOBAL = "GLOBAL"


class CalibrationSourceKind(StrEnum):
    D2 = "D2"
    REFERENCE_VIEW = "REFERENCE_VIEW"
    WEB = "WEB"
    DATA_MCP = "DATA_MCP"


class PathStatus(StrEnum):
    PENDING = "PENDING"
    COMPILED = "COMPILED"
    UNRESOLVED = "UNRESOLVED"


class TriggerDisposition(StrEnum):
    TRIGGER_READY = "TRIGGER_READY"
    TRIGGER_UNRESOLVED = "TRIGGER_UNRESOLVED"


class TriggerCalibrationStageStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


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
    reference_state: str = Field(
        min_length=1,
        description=(
            "The condition-specific reality anchor confirmed at the policy cutoff; "
            "not a Unit/Shell summary or a restatement of the criterion."
        ),
    )
    trigger_boundary: str = Field(
        min_length=1,
        description=(
            "The minimum observable surprise beyond the market-expectation baseline "
            "that independently creates the Policy's directional revision."
        ),
    )


class ActivationCondition(ContractModel):
    condition_id: str = Field(min_length=1)
    criterion: str = Field(
        min_length=1,
        description=(
            "A stable natural-business description of the future reality change monitored "
            "by Runtime; it excludes the current reference value and point-in-time market "
            "calibration."
        ),
    )
    calibration: Calibration


class Policy(ContractModel):
    """One-time decision boundary activated by any one complete Condition (fixed OR)."""

    policy_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_refs: list[PolicySourceRef] = Field(min_length=1)
    decision: PolicyDecision
    match_scope: str = Field(min_length=1)
    activation_conditions: list[ActivationCondition] = Field(
        min_length=1,
        description=(
            "Independently sufficient natural occurrences combined with fixed OR semantics; "
            "one Condition confirmed by the same message activates the Policy."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_safe_legacy_shape(cls, value: Any) -> Any:
        """Drop retired prose/mode fields without changing historical ALL semantics."""

        if not isinstance(value, dict):
            return value
        payload = dict(value)
        legacy_mode = str(payload.pop("activation_mode", "ANY")).upper()
        conditions = payload.get("activation_conditions")
        condition_count = len(conditions) if isinstance(conditions, list) else 0
        if legacy_mode == "ALL" and condition_count > 1:
            raise ValueError(
                "multi-condition legacy ALL Policy cannot be migrated to fixed OR semantics"
            )
        payload.pop("activation_summary", None)
        if isinstance(conditions, list):
            migrated_conditions: list[Any] = []
            for condition in conditions:
                if not isinstance(condition, dict):
                    migrated_conditions.append(condition)
                    continue
                migrated = dict(condition)
                calibration = migrated.get("calibration")
                if isinstance(calibration, dict):
                    migrated_calibration = dict(calibration)
                    migrated_calibration.pop("qualifying_evidence", None)
                    migrated["calibration"] = migrated_calibration
                migrated_conditions.append(migrated)
            payload["activation_conditions"] = migrated_conditions
        return payload

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
    schema_version: Literal["document3.v2.2"] = DOCUMENT3_SCHEMA_VERSION
    ticker: str
    policy_set_version: int = Field(ge=1)
    publication_state: PublicationState = PublicationState.COMPLETE
    document2_ref: Document2Ref
    event_library_ref: EventLibraryRef | None = None
    policies: list[Policy] = Field(default_factory=list)
    published_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def migrate_safe_legacy_version(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if payload.get("schema_version") in {"document3.v2", "document3.v2.1"}:
            payload["schema_version"] = DOCUMENT3_SCHEMA_VERSION
        return payload

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


class TriggerCalibrationRunResult(ContractModel):
    """Small Node-A response; durable research remains in workspace artifacts."""

    status: Literal["COMPLETED", "FAILED"]
    processed_gap_count: int = Field(default=0, ge=0)
    processed_path_count: int = Field(default=0, ge=0)
    unprocessed_path_count: int = Field(default=0, ge=0)


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


class TriggerCalibrationRecord(ContractModel):
    """Strict Stage-A research record keyed to one immutable D2-derived path."""

    shell_id: str = Field(min_length=1)
    expectation_id: str = Field(min_length=1)
    gap_id: str = Field(min_length=1)
    path_id: str = Field(min_length=1)
    trigger_bearing_actor: str = Field(min_length=1)
    trigger_bearing_object: str = Field(min_length=1)
    current_state: str = Field(min_length=1)
    candidate_trigger: str = Field(min_length=1)
    trade_sufficiency: str = Field(min_length=1)
    minimality: str = Field(min_length=1)
    disclosure_route: str = Field(min_length=1)
    judgeability: str = Field(min_length=1)
    source_basis: list[str] = Field(min_length=1)
    disposition: TriggerDisposition
    unresolved_reason: str | None = None

    @field_validator("source_basis")
    @classmethod
    def source_basis_is_unique(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("source_basis must contain at least one source reference")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("source_basis entries must be unique")
        return cleaned

    @model_validator(mode="after")
    def unresolved_disposition_has_reason(self) -> TriggerCalibrationRecord:
        if self.disposition is TriggerDisposition.TRIGGER_UNRESOLVED and not self.unresolved_reason:
            raise ValueError("TRIGGER_UNRESOLVED record requires unresolved_reason")
        return self


class TriggerPathDisposition(ContractModel):
    shell_id: str = Field(min_length=1)
    expectation_id: str = Field(min_length=1)
    gap_id: str = Field(min_length=1)
    path_id: str = Field(min_length=1)
    disposition: TriggerDisposition
    unresolved_reason: str | None = None

    @model_validator(mode="after")
    def unresolved_disposition_has_reason(self) -> TriggerPathDisposition:
        if self.disposition is TriggerDisposition.TRIGGER_UNRESOLVED and not self.unresolved_reason:
            raise ValueError("TRIGGER_UNRESOLVED disposition requires unresolved_reason")
        return self


class TriggerCalibrationState(ContractModel):
    stage_status: TriggerCalibrationStageStatus = TriggerCalibrationStageStatus.IN_PROGRESS
    completed_shell_ids: list[str] = Field(default_factory=list)
    current_shell_id: str | None = None
    path_dispositions: list[TriggerPathDisposition] = Field(default_factory=list)
    unprocessed_path_count: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("completed_shell_ids")
    @classmethod
    def completed_shell_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("completed_shell_ids must be unique")
        return value

    @field_validator("path_dispositions")
    @classmethod
    def disposition_path_ids_are_unique(
        cls, value: list[TriggerPathDisposition]
    ) -> list[TriggerPathDisposition]:
        path_ids = [item.path_id for item in value]
        if len(path_ids) != len(set(path_ids)):
            raise ValueError("path_dispositions must contain one row per path_id")
        return value

    @model_validator(mode="after")
    def completed_stage_is_closed(self) -> TriggerCalibrationState:
        if self.stage_status is TriggerCalibrationStageStatus.COMPLETED:
            if self.current_shell_id is not None:
                raise ValueError("completed Trigger Calibration cannot have current_shell_id")
            if self.unprocessed_path_count:
                raise ValueError("completed Trigger Calibration cannot have unprocessed paths")
        return self


class WaveState(AgentModel):
    completed_shell_ids: list[str] = Field(default_factory=list)
    current_shell_id: str | None = None
    completed_path_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class FrozenInputFile(ContractModel):
    relative_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Document3InputManifest(ContractModel):
    schema_version: Literal["document3.input_manifest.v1"] = "document3.input_manifest.v1"
    files: list[FrozenInputFile] = Field(min_length=4)

    @field_validator("files")
    @classmethod
    def paths_are_unique(cls, value: list[FrozenInputFile]) -> list[FrozenInputFile]:
        paths = [item.relative_path for item in value]
        if len(paths) != len(set(paths)):
            raise ValueError("input manifest paths must be unique")
        return value


class Document3InitializeTask(ContractModel):
    mode: Literal["O3_INITIALIZE"] = "O3_INITIALIZE"
    ticker: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    cutoff_at: datetime
    document2_ref: Document2Ref
    requested_event_library_version: int | None = Field(default=None, ge=1)
    event_library_ref: EventLibraryRef | None = None
    failed_shells: list[FailedShellCoverage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    provenance_warnings: list[str] = Field(default_factory=list)
    workflow_warnings: list[str] = Field(default_factory=list)
    semantic_warnings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def warnings_are_deduplicated_and_classified(self) -> CoverageMap:
        self.provenance_warnings = list(dict.fromkeys(self.provenance_warnings))
        provenance = set(self.provenance_warnings)
        self.semantic_warnings = [
            item
            for item in dict.fromkeys(self.semantic_warnings)
            if item not in provenance
        ]
        classified_preworkflow = provenance | set(self.semantic_warnings)
        self.workflow_warnings = [
            item
            for item in dict.fromkeys(self.workflow_warnings)
            if item not in classified_preworkflow
        ]
        classified = {
            *self.provenance_warnings,
            *self.workflow_warnings,
            *self.semantic_warnings,
        }
        legacy = [item for item in self.warnings if item not in classified]
        self.workflow_warnings.extend(item for item in legacy if item not in self.workflow_warnings)
        self.warnings = list(
            dict.fromkeys(
                [
                    *self.provenance_warnings,
                    *self.workflow_warnings,
                    *self.semantic_warnings,
                ]
            )
        )
        return self


class SemanticDiagnosticFinding(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    affected_ids: list[str] = Field(default_factory=list)


class SemanticDiagnostics(ContractModel):
    schema_version: Literal["document3.semantic_diagnostics.v1"] = (
        "document3.semantic_diagnostics.v1"
    )
    generated_at: datetime = Field(default_factory=utc_now)
    condition_count: int = Field(ge=0)
    path_count: int = Field(ge=0)
    criterion_equals_trigger_boundary_count: int = Field(ge=0)
    criterion_equals_trigger_boundary_ratio: float = Field(ge=0, le=1)
    duplicate_reference_state_count: int = Field(ge=0)
    duplicate_reference_state_ratio: float = Field(ge=0, le=1)
    d2_possible_occurrence_exact_copy_count: int = Field(ge=0)
    d2_possible_occurrence_exact_copy_ratio: float = Field(ge=0, le=1)
    d2_boundary_sufficient_true_count: int = Field(ge=0)
    d2_boundary_sufficient_true_ratio: float = Field(ge=0, le=1)
    unresolved_path_count: int = Field(ge=0)
    hidden_or_condition_ids: list[str] = Field(default_factory=list)
    unanchored_degree_condition_ids: list[str] = Field(default_factory=list)
    or_group_sizes: dict[str, int] = Field(default_factory=dict)
    max_or_group_size: int = Field(ge=0)
    semantic_batch_generation_detected: bool = False
    semantic_batch_generation_evidence: list[str] = Field(default_factory=list)
    requires_explanation: bool = False
    findings: list[SemanticDiagnosticFinding] = Field(default_factory=list)


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
    diagnostics_reviewed: bool = False
    diagnostics_explanation: str = ""

    @model_validator(mode="after")
    def counters_match_issues(self) -> ReviewResult:
        self.issue_count = len(self.issues)
        self.blocking_issue_count = sum(item.blocking for item in self.issues)
        return self


class ValidationFinding(ContractModel):
    code: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.WARNING
    scope: ValidationScope = ValidationScope.RECORD
    recovery_action: str | None = None
    affected_ids: list[str] = Field(default_factory=list)
    # Compatibility field for already persisted reports. New code derives hard
    # blocking from ``severity == FATAL`` and never from Agent-authored content.
    blocking: bool = False


class ValidationReport(ContractModel):
    valid: bool
    publication_state: PublicationState
    findings: list[ValidationFinding] = Field(default_factory=list)

    @property
    def blocking_findings(self) -> list[ValidationFinding]:
        return [
            finding
            for finding in self.findings
            if finding.blocking or finding.severity is ValidationSeverity.FATAL
        ]


class RuntimePolicyRecord(ContractModel):
    """Compact W2 projection; its enclosing v4 contract defines fixed OR semantics."""

    policy_id: str = Field(min_length=1)
    match_scope: str = Field(min_length=1)
    activation_revision: str = Field(pattern=r"^ar_[0-9a-f]{24}$")
    condition_ids: list[str] = Field(min_length=1)
    criterion: list[str] = Field(min_length=1)

    @field_validator("criterion")
    @classmethod
    def criterion_is_unique(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("criterion must contain at least one condition")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("criterion entries must be unique within a policy")
        return cleaned

    @model_validator(mode="after")
    def condition_ids_align_with_criteria(self) -> RuntimePolicyRecord:
        if len(self.condition_ids) != len(self.criterion):
            raise ValueError("condition_ids and criterion must have the same length")
        if len(self.condition_ids) != len(set(self.condition_ids)):
            raise ValueError("condition_ids must be unique within a projected policy")
        return self


class RuntimePolicyProjection(ContractModel):
    schema_version: Literal["document3.runtime_projection.v4"] = RUNTIME_POLICY_PROJECTION_VERSION
    consumer_contract: Literal["persistent-runtime.v2.or-policy.v1"] = (
        RUNTIME_POLICY_CONSUMER_CONTRACT
    )
    ticker: str
    policy_set_version: int = Field(ge=1)
    policy_set_published_at: datetime
    policies: list[RuntimePolicyRecord] = Field(default_factory=list)

    @field_validator("policies")
    @classmethod
    def projected_policy_ids_are_unique(
        cls, value: list[RuntimePolicyRecord]
    ) -> list[RuntimePolicyRecord]:
        ids = [item.policy_id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Runtime projection must contain one row per policy")
        return value


class PolicyDetailSnapshot(ContractModel):
    """Version-pinned canonical Policy details requested by Runtime W2."""

    ticker: str
    policy_set_version: int = Field(ge=1)
    requested_policy_ids: list[str]
    policies: list[Policy] = Field(default_factory=list)
    missing_policy_ids: list[str] = Field(default_factory=list)


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
