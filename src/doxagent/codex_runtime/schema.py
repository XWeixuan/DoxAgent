"""Versioned contracts for the additive Codex Document 1 workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CODEX_D1_WORKFLOW_VERSION: Final[Literal["codex_d1_v2"]] = "codex_d1_v2"
CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION: Final[Literal["codex_global_research_v1"]] = (
    "codex_global_research_v1"
)
CODEX_MARKET_SITUATION_WORKFLOW_VERSION: Final[Literal["codex_market_situation_v1"]] = (
    "codex_market_situation_v1"
)
CodexWorkflowVersion: TypeAlias = Literal[
    "codex_d1_v2",
    "codex_global_research_v1",
    "codex_market_situation_v1",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexD1Node(StrEnum):
    PROGRAM_COLLECTION = "program_collection"
    C4_PRE_SCAN = "c4_pre_scan"
    C1 = "c1"
    C2 = "c2"
    C3 = "c3"
    O4_B = "o4_b"
    AGENT_NORMALIZATION = "agent_normalization"
    C4_ENRICHMENT = "c4_enrichment"
    C4_FINALIZATION = "c4_finalization"
    O4_A = "o4_a"
    C5 = "c5"
    O4 = "o4"
    ASSEMBLE = "assemble"
    PUBLISH = "publish"


CodexResearchNode = CodexD1Node


class ResearchLane(StrEnum):
    LEGACY_DOCUMENT1 = "legacy_document1"
    GLOBAL_RESEARCH = "global_research"
    MARKET_SITUATION_RESEARCH = "market_situation_research"


class CodexAgentRole(StrEnum):
    C1 = "c1_researcher"
    C2 = "c2_researcher"
    C3 = "c3_researcher"
    C4 = "c4_researcher"
    C5 = "c5_researcher"
    O4 = "o4_researcher"


_LANE_BY_WORKFLOW: dict[str, ResearchLane] = {
    CODEX_D1_WORKFLOW_VERSION: ResearchLane.LEGACY_DOCUMENT1,
    CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION: ResearchLane.GLOBAL_RESEARCH,
    CODEX_MARKET_SITUATION_WORKFLOW_VERSION: ResearchLane.MARKET_SITUATION_RESEARCH,
}


def lane_for_workflow(workflow_version: str) -> ResearchLane:
    try:
        return _LANE_BY_WORKFLOW[workflow_version]
    except KeyError as exc:
        raise ValueError(f"unsupported Codex workflow version: {workflow_version}") from exc


def _validate_workflow_lane(workflow_version: str, research_lane: ResearchLane) -> None:
    expected = lane_for_workflow(workflow_version)
    if research_lane is not expected:
        raise ValueError(f"workflow {workflow_version} requires research_lane={expected.value}")


class AttemptStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArtifactKind(StrEnum):
    CONTEXT = "context"
    REPORT = "report"
    STRUCTURED_COMPLETION = "structured_completion"
    MANIFEST = "manifest"
    AUDIT = "audit"
    BUNDLE = "bundle"


class ArtifactRef(StrictModel):
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    artifact_id: str
    run_id: str
    node: CodexD1Node
    attempt_id: str
    kind: ArtifactKind
    relative_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    content_type: str
    published: bool = False
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def metadata_stays_small(self) -> ArtifactRef:
        _validate_workflow_lane(self.workflow_version, self.research_lane)
        if len(self.model_dump_json().encode("utf-8")) > 4096:
            raise ValueError("artifact metadata exceeds 4 KiB")
        return self


class ThreadRecord(StrictModel):
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    ticker: str
    run_id: str
    agent_role: CodexAgentRole
    thread_id: str
    model: str
    model_provider: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def metadata_stays_small(self) -> ThreadRecord:
        _validate_workflow_lane(self.workflow_version, self.research_lane)
        if len(self.model_dump_json().encode("utf-8")) > 4096:
            raise ValueError("thread metadata exceeds 4 KiB")
        return self


class NodeAttempt(StrictModel):
    attempt_id: str
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    cutoff_at: datetime = Field(default_factory=utc_now)
    ticker: str
    run_id: str
    node: CodexD1Node
    status: AttemptStatus = AttemptStatus.PENDING
    attempt_number: int = Field(default=1, ge=1)
    thread_id: str | None = None
    input_sha256: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("error_message")
    @classmethod
    def error_message_stays_small(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 4096:
            raise ValueError("attempt error_message exceeds 4 KiB")
        return value

    @model_validator(mode="after")
    def attempt_stays_small(self) -> NodeAttempt:
        _validate_workflow_lane(self.workflow_version, self.research_lane)
        if len(self.model_dump_json().encode("utf-8")) > 8192:
            raise ValueError("attempt metadata exceeds 8 KiB")
        return self


class WorkflowCheckpoint(StrictModel):
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    ticker: str
    run_id: str
    completed_nodes: list[CodexD1Node] = Field(default_factory=list)
    current_nodes: list[CodexD1Node] = Field(default_factory=list)
    failed_nodes: list[CodexD1Node] = Field(default_factory=list)
    cancelled: bool = False
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("completed_nodes", "current_nodes", "failed_nodes")
    @classmethod
    def node_arrays_are_bounded(cls, value: list[CodexD1Node]) -> list[CodexD1Node]:
        if len(value) > 64:
            raise ValueError("checkpoint node arrays are limited to 64 items")
        return value

    @model_validator(mode="after")
    def checkpoint_stays_small(self) -> WorkflowCheckpoint:
        _validate_workflow_lane(self.workflow_version, self.research_lane)
        if len(self.model_dump_json().encode("utf-8")) > 16384:
            raise ValueError("checkpoint exceeds 16 KiB")
        return self


class StructuredCompletion(StrictModel):
    node: CodexD1Node
    status: Literal["completed", "partial", "failed"]
    report_path: str | None = None
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    observation_candidates_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentObservationCandidate(StrictModel):
    metric_key: str
    meaning: str | None = None
    value: str | float | int | bool
    unit: str | None = None
    as_of: str | None = None
    source_aliases: list[str] = Field(default_factory=list)
    method: str
    confidence: Literal["high", "medium", "low"] = "medium"


class NormalizedAgentObservation(AgentObservationCandidate):
    node: CodexD1Node
    attempt_id: str
    artifact_id: str
    governed_metric: bool
    freeform_metric: bool
    source_role: Literal["AGENT"] = "AGENT"


class EntityRelation(StrictModel):
    relation_subject: str = Field(alias="关系主体")
    relation_object: str = Field(alias="关系对象")
    relation_type: str = Field(alias="关系类型")
    relation_description: str = Field(alias="关系说明")
    related_business_or_product: str = Field(alias="关联业务或产品")


class FutureNode(StrictModel):
    time: str = Field(alias="时间")
    future_event: str = Field(alias="未来事项")
    relationship_to_target: str = Field(alias="与目标公司的关系")
    source: str = Field(alias="来源")
    source_published_at: str = Field(alias="来源发布日期")


class SourceRecord(StrictModel):
    source_id: str
    run_id: str
    attempt_id: str
    alias: str
    url: str
    source: str | None = None
    note: str | None = None
    title: str | None = None
    captured_text: str | None = None
    source_type: Literal["web", "tool_observation"] = "web"
    tool_name: str | None = None
    tool_call_id: str | None = None
    block_id: str | None = None
    source_locator: str | None = None
    source_coordinates: Any | None = None
    content_hash: str | None = None
    provider: str | None = None
    method_version: str | None = None
    promoted_from_attempt_id: str | None = None
    captured_at: datetime = Field(default_factory=utc_now)
    warning: str | None = None


class CitationEntry(StrictModel):
    alias: str
    source_id: str | None = None
    url: str | None = None
    title: str | None = None
    attempt_id: str | None = None
    anchor: str | None = None
    block_id: str | None = None
    source_locator: str | None = None
    content_hash: str | None = None
    resolved: bool
    warning: str | None = None


class CitationManifest(StrictModel):
    run_id: str
    artifact_id: str
    entries: list[CitationEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class WorkflowEvent(StrictModel):
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    event_id: str
    run_id: str
    event_type: str
    sequence: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("payload")
    @classmethod
    def payload_stays_small(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")) > 16384:
            raise ValueError("workflow event payload exceeds 16 KiB")
        return value

    @model_validator(mode="after")
    def workflow_matches_lane(self) -> WorkflowEvent:
        _validate_workflow_lane(self.workflow_version, self.research_lane)
        return self


class CodexRunSummary(StrictModel):
    run_id: str
    ticker: str
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    status: Literal["queued", "running", "failed", "cancelled", "published"]
    current_node: str | None = None
    completed_node_count: int = Field(default=0, ge=0)
    failed_node_count: int = Field(default=0, ge=0)
    latest_event_sequence: int = Field(default=-1, ge=-1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def workflow_matches_lane(self) -> CodexRunSummary:
        _validate_workflow_lane(self.workflow_version, self.research_lane)
        return self


class PublishedDocument(StrictModel):
    artifact_id: str
    run_id: str
    artifact_kind: Literal["report", "bundle", "manifest"]
    sha256: str
    size_bytes: int = Field(ge=0)
    content_type: str
    content_text: str | None = None
    storage_path: str | None = None
    published_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def exactly_one_content_location(self) -> PublishedDocument:
        if (self.content_text is None) == (self.storage_path is None):
            raise ValueError("published document requires exactly one content location")
        if self.content_text is not None:
            actual = len(self.content_text.encode("utf-8"))
            if actual != self.size_bytes:
                raise ValueError("published document size_bytes does not match content_text")
            if actual > 2 * 1024 * 1024:
                raise ValueError("published document content exceeds 2 MiB")
        return self


class Document1HandoffV1(StrictModel):
    """Frozen D2-facing boundary; D2 is deliberately not implemented here."""

    schema_version: Literal["document1-handoff-v1"] = "document1-handoff-v1"
    run_id: str
    ticker: str
    document1_artifact_id: str
    citation_manifest_artifact_id: str | None = None
    published_at: datetime


class Document1V2Bundle(StrictModel):
    workflow_version: Literal["codex_d1_v2"] = CODEX_D1_WORKFLOW_VERSION
    run_id: str
    ticker: str
    status: Literal["draft", "published", "failed"]
    reports: dict[str, ArtifactRef] = Field(default_factory=dict)
    entity_relations: list[EntityRelation] = Field(default_factory=list)
    future_nodes: list[FutureNode] = Field(default_factory=list)
    citation_manifest: CitationManifest | None = None
    handoff: Document1HandoffV1 | None = None
    created_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def published_requires_handoff(self) -> Document1V2Bundle:
        if self.status == "published" and (self.handoff is None or self.published_at is None):
            raise ValueError("published bundles require published_at and handoff")
        return self


class GlobalResearchHandoffV1(StrictModel):
    schema_version: Literal["global-research-handoff-v1"] = "global-research-handoff-v1"
    run_id: str
    ticker: str
    document_artifact_id: str
    citation_manifest_artifact_id: str | None = None
    published_at: datetime


class MarketSituationHandoffV1(StrictModel):
    schema_version: Literal["market-situation-handoff-v1"] = "market-situation-handoff-v1"
    run_id: str
    ticker: str
    document_artifact_id: str
    citation_manifest_artifact_id: str | None = None
    published_at: datetime


class GlobalResearchBundle(StrictModel):
    workflow_version: Literal["codex_global_research_v1"] = CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION
    research_lane: Literal[ResearchLane.GLOBAL_RESEARCH] = ResearchLane.GLOBAL_RESEARCH
    run_id: str
    ticker: str
    status: Literal["draft", "published", "failed"]
    reports: dict[str, ArtifactRef] = Field(default_factory=dict)
    entity_relations: list[EntityRelation] = Field(default_factory=list)
    future_nodes: list[FutureNode] = Field(default_factory=list)
    citation_manifest: CitationManifest | None = None
    handoff: GlobalResearchHandoffV1 | None = None
    created_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def published_requires_handoff(self) -> GlobalResearchBundle:
        if self.status == "published" and (self.handoff is None or self.published_at is None):
            raise ValueError("published global research bundles require handoff")
        return self


class MarketSituationBundle(StrictModel):
    workflow_version: Literal["codex_market_situation_v1"] = CODEX_MARKET_SITUATION_WORKFLOW_VERSION
    research_lane: Literal[ResearchLane.MARKET_SITUATION_RESEARCH] = (
        ResearchLane.MARKET_SITUATION_RESEARCH
    )
    run_id: str
    ticker: str
    status: Literal["draft", "published", "failed"]
    reports: dict[str, ArtifactRef] = Field(default_factory=dict)
    citation_manifest: CitationManifest | None = None
    handoff: MarketSituationHandoffV1 | None = None
    created_at: datetime = Field(default_factory=utc_now)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def published_requires_handoff(self) -> MarketSituationBundle:
        if self.status == "published" and (self.handoff is None or self.published_at is None):
            raise ValueError("published market situation bundles require handoff")
        return self


ResearchBundle: TypeAlias = Document1V2Bundle | GlobalResearchBundle | MarketSituationBundle
