"""Contracts for the Codex Document2 expectation workflow."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    ArtifactRef,
    ResearchLane,
    utc_now,
)


class AgentModel(BaseModel):
    """Agent-authored payloads accept harmless extra fields and keep core shape typed."""

    model_config = ConfigDict(extra="ignore")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterValueType(StrEnum):
    NUMBER = "NUMBER"
    RANGE = "RANGE"
    TIME = "TIME"
    STAGE = "STAGE"
    DIRECTION = "DIRECTION"
    EVIDENCE = "EVIDENCE"


class SourceRole(StrEnum):
    ACTUAL = "ACTUAL"
    MANAGEMENT = "MANAGEMENT"
    SELL_SIDE = "SELL_SIDE"
    INDUSTRY_CHAIN = "INDUSTRY_CHAIN"
    MARKET_IMPLIED = "MARKET_IMPLIED"


class ValidityState(StrEnum):
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    DISPUTED = "DISPUTED"
    RETRACTED = "RETRACTED"


class StructuralRole(StrEnum):
    REQUIRED = "REQUIRED"
    BLOCKER = "BLOCKER"
    MODIFIER = "MODIFIER"


class NumberValue(AgentModel):
    number: float
    unit: str


class RangeValue(AgentModel):
    lower: float
    upper: float
    unit: str


class TimeValue(AgentModel):
    point: str | None = None
    start: str | None = None
    end: str | None = None
    precision: str


class StageValue(AgentModel):
    stage: str


class DirectionValue(AgentModel):
    direction: str


class EvidenceValue(AgentModel):
    stance: str
    strength: str


StateValueData = NumberValue | RangeValue | TimeValue | StageValue | DirectionValue | EvidenceValue


class StateParameter(AgentModel):
    parameter_id: str
    definition: str
    value_type: ParameterValueType


class StateValue(AgentModel):
    state_value_id: str
    parameter_id: str
    source_role: SourceRole
    value: StateValueData
    previous_value: StateValueData | None = None
    time_scope: str
    as_of: str
    citation: list[str] = Field(default_factory=list)
    validity_state: ValidityState = ValidityState.CURRENT


class ExpectationState(AgentModel):
    parameters: list[StateParameter] = Field(default_factory=list)
    values: list[StateValue] = Field(default_factory=list)


class Observability(AgentModel):
    match_condition: str


class RealizationFactor(AgentModel):
    factor_id: str
    condition: str
    structural_role: StructuralRole
    current_status: str
    impact: str
    citation: list[str] = Field(default_factory=list)
    observability: Observability


class PotentialGap(AgentModel):
    gap_id: str
    possible_occurrence: str
    derivation: str
    citation: list[str] = Field(default_factory=list)
    expected_revision: str
    recognition_criteria: str | None = None


class ExpectationUnit(AgentModel):
    expectation_id: str
    proposition: str
    horizon: str
    state: ExpectationState = Field(default_factory=ExpectationState)
    realization_factors: list[RealizationFactor] = Field(default_factory=list)
    potential_gaps: list[PotentialGap] = Field(default_factory=list)


class ExpectationShell(AgentModel):
    shell_id: str
    core_question: str
    boundary_rule: str
    units: list[ExpectationUnit] = Field(default_factory=list)


class ExpectationUnitSeed(AgentModel):
    expectation_id: str
    proposition: str
    horizon: str


class ExpectationShellSeed(AgentModel):
    shell_id: str
    core_question: str
    boundary_rule: str
    units: list[ExpectationUnitSeed] = Field(default_factory=list)


class CandidateUnit(AgentModel):
    candidate_id: str
    candidate: str
    reason: str
    references: list[str] = Field(default_factory=list)


class CandidateDiscoveryResult(AgentModel):
    candidates: list[CandidateUnit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProvisionalCandidateUnit(AgentModel):
    candidate_ref: str
    candidate_id: str
    candidate: str


class ProvisionalShellDraft(AgentModel):
    shell_temp_id: str
    core_question: str
    boundary_reasoning: str
    candidate_units: list[ProvisionalCandidateUnit] = Field(default_factory=list)


class UnassignedCandidate(AgentModel):
    candidate_ref: str
    candidate_id: str
    candidate: str
    reason: str


class ShellSynthesisResult(AgentModel):
    provisional_shells: list[ProvisionalShellDraft] = Field(default_factory=list)
    unassigned_candidates: list[UnassignedCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DomainReviewFeedback(AgentModel):
    feedback_id: str
    target: str
    issue: str
    reasoning: str
    references: list[str] = Field(default_factory=list)
    recommendation: str


class DomainReviewResult(AgentModel):
    reviewer_role: Literal["C1", "C3", "C5"]
    overall_assessment: str
    targeted_feedback: list[DomainReviewFeedback] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ShellFinalizationResult(AgentModel):
    shells: list[ExpectationShellSeed] = Field(default_factory=list)
    finalization_note: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CandidateUnitV21(AgentModel):
    name: str
    scope: str
    why_material: str
    ref: list[str] = Field(default_factory=list)


class CandidateDiscoveryResultV21(AgentModel):
    candidates: list[CandidateUnitV21] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProvisionalCandidateUnitV21(CandidateUnitV21):
    candidate_ref: str


class ProvisionalShellDraftV21(AgentModel):
    shell_temp_id: str
    name: str
    scope: str
    boundary: str
    ref: list[str] = Field(default_factory=list)
    candidate_units: list[ProvisionalCandidateUnitV21] = Field(default_factory=list)


class UnassignedCandidateV21(ProvisionalCandidateUnitV21):
    reason: str


class ShellSynthesisResultV21(AgentModel):
    provisional_shells: list[ProvisionalShellDraftV21] = Field(default_factory=list)
    unassigned_candidates: list[UnassignedCandidateV21] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DomainReviewFeedbackV21(AgentModel):
    feedback_id: str
    target: str
    issue: str
    reasoning: str
    recommendation: str
    ref: list[str] = Field(default_factory=list)


class DomainReviewResultV21(AgentModel):
    reviewer_role: Literal["C1", "C3", "C5"]
    overall_assessment: str
    targeted_feedback: list[DomainReviewFeedbackV21] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExpectationUnitSeedV21(AgentModel):
    name: str
    scope: str
    horizon: str
    ref: list[str] = Field(default_factory=list)


class ExpectationShellSeedV21(AgentModel):
    name: str
    scope: str
    boundary: str
    ref: list[str] = Field(default_factory=list)
    units: list[ExpectationUnitSeedV21] = Field(default_factory=list)


class ShellFinalizationResultV21(AgentModel):
    shells: list[ExpectationShellSeedV21] = Field(default_factory=list)
    finalization_note: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class StateParameterV21(AgentModel):
    name: str
    definition: str
    value_type: ParameterValueType
    ref: list[str] = Field(default_factory=list)


class StateValueV21(AgentModel):
    name: str
    parameter: str
    source_role: SourceRole
    value: StateValueData
    previous_value: StateValueData | None = None
    time_scope: str
    as_of: str
    validity_state: ValidityState = ValidityState.CURRENT
    ref: list[str] = Field(default_factory=list)


class ExpectationStateV21(AgentModel):
    parameters: list[StateParameterV21] = Field(default_factory=list)
    values: list[StateValueV21] = Field(default_factory=list)


class ExpectationBaselineV21(AgentModel):
    name: str
    baseline: str
    ordinary_progress: str
    open_frontier: str
    time_scope: str
    ref: list[str] = Field(default_factory=list)


class RealizationFactorV21(AgentModel):
    name: str
    mechanism: str
    current_status: str
    materiality_context: str
    scope_boundary: str
    ref: list[str] = Field(default_factory=list)


class PossibilityV21(AgentModel):
    name: str
    implication: str


class PotentialGapV21(AgentModel):
    name: str
    why_live: str
    revision_logic: str
    possibility_space: list[PossibilityV21] = Field(default_factory=list)
    ref: list[str] = Field(default_factory=list)


class ExpectationUnitV21(ExpectationUnitSeedV21):
    state: ExpectationStateV21 = Field(default_factory=ExpectationStateV21)
    expectation_baseline: list[ExpectationBaselineV21] = Field(default_factory=list)
    realization_factors: list[RealizationFactorV21] = Field(default_factory=list)
    potential_gaps: list[PotentialGapV21] = Field(default_factory=list)


class ExpectationShellV21(ExpectationShellSeedV21):
    units: list[ExpectationUnitV21] = Field(default_factory=list)


class DiscoveryCandidateV21(AgentModel):
    name: str
    change_hypothesis: str
    relevance: str
    live_basis: str
    ref: list[str] = Field(default_factory=list)


class DiscoveryUnitV21(AgentModel):
    name: str
    candidates: list[DiscoveryCandidateV21] = Field(default_factory=list)


class OpenDiscoveryScanV21(AgentModel):
    shell: str
    units: list[DiscoveryUnitV21] = Field(default_factory=list)


class DiscoverySelectionV21(AgentModel):
    unit: str
    candidate: str
    decision: Literal["DEEPEN", "MERGE", "PARK"]
    merge_into: str | None = None
    reason: str
    research_focus: str
    ref: list[str] = Field(default_factory=list)


class OpenDiscoverySelectionV21(AgentModel):
    shell: str
    selections: list[DiscoverySelectionV21] = Field(default_factory=list)


class OpenDiscoveryCompletionV21(AgentModel):
    scan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection: OpenDiscoverySelectionV21


class OpenDiscoveryCheckpointV21(ContractModel):
    schema_version: Literal["d2-open-discovery-checkpoint-v1"] = "d2-open-discovery-checkpoint-v1"
    discovery_contract_version: Literal["single-v1"] = "single-v1"
    workspace_run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    shell: str
    research_cutoff_at: datetime
    seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    producer_attempt_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    scan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scan: OpenDiscoveryScanV21


class OpenDiscoveryResultV21(ContractModel):
    checkpoint: OpenDiscoveryCheckpointV21
    selection: OpenDiscoverySelectionV21


class LateAdditionV21(AgentModel):
    unit: str
    name: str
    discovered_during: str
    change_hypothesis: str
    reason: str
    ref: list[str] = Field(default_factory=list)


class DiscoveryResolutionV21(AgentModel):
    unit: str
    candidate: str
    resolution: str
    destination: str | None = None
    reason: str


class ShellResearchTurnResultV21(AgentModel):
    canonical_shell: ExpectationShellV21
    late_additions: list[LateAdditionV21] = Field(default_factory=list)
    open_discovery_resolution: list[DiscoveryResolutionV21] = Field(default_factory=list)


class InputAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class InputManifestEntry(ContractModel):
    status: InputAvailability
    artifact_ids: list[str] = Field(default_factory=list)
    workspace_paths: list[str] = Field(default_factory=list)
    source_run_id: str | None = None
    as_of: datetime | None = None
    warning: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Document2InputManifest(ContractModel):
    global_research: InputManifestEntry
    narrative_research: InputManifestEntry
    event_library: InputManifestEntry


class ShellResearchStage(StrEnum):
    PENDING = "PENDING"
    OPEN_DISCOVERY = "OPEN_DISCOVERY"
    # Historical split-v1 checkpoint values, never active stages for new runs.
    DISCOVERY_SCAN = "DISCOVERY_SCAN"
    DISCOVERY_SELECTION = "DISCOVERY_SELECTION"
    STATE = "STATE"
    REALIZATION = "REALIZATION"
    GAPS = "GAPS"
    FINALIZATION = "FINALIZATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ShellOutcome(ContractModel):
    shell_id: str
    status: Literal["completed", "failed"]
    artifact_id: str | None = None
    failed_stage: ShellResearchStage | None = None
    failure_kind: Literal["SYSTEM", "TRANSIENT", "FORMAT", "SHELL"] | None = None
    error_code: str | None = None
    error: str | None = None
    seed: ExpectationShellSeed | ExpectationShellSeedV21 | None = None


class ShellOutcomeV21(ContractModel):
    shell: str
    status: Literal["completed", "failed"]
    artifact_id: str | None = None
    failed_stage: ShellResearchStage | None = None
    failure_kind: Literal["SYSTEM", "TRANSIENT", "FORMAT", "SHELL"] | None = None
    error_code: str | None = None
    error: str | None = None
    seed: ExpectationShellSeedV21 | None = None


class Document2Document(ContractModel):
    schema_version: Literal["document2.v2"] = "document2.v2"
    workflow_version: Literal["codex_document2_v1"] = CODEX_DOCUMENT2_WORKFLOW_VERSION
    document2_run_id: str
    ticker: str
    as_of: datetime
    source_global_run_id: str
    input_manifest: Document2InputManifest
    shells: list[ExpectationShell] = Field(default_factory=list)
    shell_outcomes: list[ShellOutcome] = Field(default_factory=list)


class Document2DocumentV21(ContractModel):
    schema_version: Literal["document2.v2.1"] = "document2.v2.1"
    workflow_version: Literal["codex_document2_v1"] = CODEX_DOCUMENT2_WORKFLOW_VERSION
    document2_run_id: str
    ticker: str
    as_of: datetime
    source_global_run_id: str
    input_manifest: Document2InputManifest
    shells: list[ExpectationShellV21] = Field(default_factory=list)
    shell_outcomes: list[ShellOutcomeV21] = Field(default_factory=list)


class CitationResolutionState(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    INVALID = "INVALID"


class Document2CitationEntry(ContractModel):
    alias: str
    status: CitationResolutionState
    origin_run_id: str | None = None
    origin_attempt_id: str | None = None
    origin_alias: str | None = None
    source_id: str | None = None
    url: str | None = None
    title: str | None = None
    warning: str | None = None


class Document2CitationManifest(ContractModel):
    schema_version: Literal["document2-citation-manifest-v1"] = "document2-citation-manifest-v1"
    run_id: str
    artifact_id: str
    entries: list[Document2CitationEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class CitationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class ShellRunState(ContractModel):
    shell_id: str
    workspace_run_id: str
    thread_id: str | None = None
    stage: ShellResearchStage = ShellResearchStage.PENDING
    canonical_path: str | None = None
    canonical_sha256: str | None = None
    stage_outputs: dict[str, ArtifactRef] = Field(default_factory=dict)
    discovery_scan_ref: ArtifactRef | None = None
    discovery_selection_ref: ArtifactRef | None = None
    late_additions_ref: ArtifactRef | None = None
    discovery_resolution_ref: ArtifactRef | None = None
    snapshot_paths: list[str] = Field(default_factory=list)
    event_library_injected: bool = False
    error: str | None = None


class Document2Checkpoint(ContractModel):
    discovery_contract_version: Literal["split-v1", "single-v1"] = "split-v1"
    document_schema_version: Literal["document2.v2", "document2.v2.1"] = "document2.v2"
    schema_version: Literal["document2-checkpoint-v1"] = "document2-checkpoint-v1"
    run_id: str
    source_global_run_id: str
    o0_workspace_run_id: str
    o0_thread_ids: dict[str, str] = Field(default_factory=dict)
    completed_stages: list[str] = Field(default_factory=list)
    stage_artifacts: dict[str, str] = Field(default_factory=dict)
    attempt_workspaces: dict[str, str] = Field(default_factory=dict)
    final_shell_seed_path: str | None = None
    shell_runs: dict[str, ShellRunState] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class Document2HandoffV1(ContractModel):
    schema_version: Literal["document2-handoff-v1"] = "document2-handoff-v1"
    run_id: str
    ticker: str
    source_global_run_id: str
    document2_artifact_id: str
    citation_manifest_artifact_id: str | None = None
    publication_state: Literal["COMPLETE", "PARTIAL"]
    citation_status: CitationStatus
    published_at: datetime


class Document2Bundle(ContractModel):
    workflow_version: Literal["codex_document2_v1"] = CODEX_DOCUMENT2_WORKFLOW_VERSION
    research_lane: Literal[ResearchLane.DOCUMENT2] = ResearchLane.DOCUMENT2
    run_id: str
    ticker: str
    source_global_run_id: str
    status: Literal["draft", "published", "failed"]
    publication_state: Literal["COMPLETE", "PARTIAL"] | None = None
    citation_status: CitationStatus = CitationStatus.UNAVAILABLE
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    shell_outcomes: list[ShellOutcome] = Field(default_factory=list)
    checkpoint: Document2Checkpoint | None = None
    handoff: Document2HandoffV1 | None = None
    current: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None


class Document2RunRequest(ContractModel):
    document_schema_version: Literal["document2.v2", "document2.v2.1"] = "document2.v2"
    workflow_version: Literal["codex_document2_v1"] = CODEX_DOCUMENT2_WORKFLOW_VERSION
    research_lane: Literal[ResearchLane.DOCUMENT2] = ResearchLane.DOCUMENT2
    run_id: str
    source_global_run_id: str
    ticker: str | None = None
    as_of: datetime | None = None
    initialization_id: str | None = None
    force_new: bool = False
    reuse_published_partial: bool = False


class StartDocument2Request(ContractModel):
    source_global_run_id: str
    run_id: str | None = None
    as_of: datetime | None = None
    force_new: bool = False


def strict_json_schema(value: Any) -> Any:
    """Convert a Pydantic schema to the closed shape required by Responses."""

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


CANDIDATE_DISCOVERY_SCHEMA = strict_json_schema(CandidateDiscoveryResult.model_json_schema())
SHELL_SYNTHESIS_SCHEMA = strict_json_schema(ShellSynthesisResult.model_json_schema())
DOMAIN_REVIEW_SCHEMA = strict_json_schema(DomainReviewResult.model_json_schema())
SHELL_FINALIZATION_SCHEMA = strict_json_schema(ShellFinalizationResult.model_json_schema())
EXPECTATION_SHELL_SCHEMA = strict_json_schema(ExpectationShell.model_json_schema())
