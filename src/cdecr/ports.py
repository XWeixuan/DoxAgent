"""Application ports used by CDECR business code."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import Field, field_validator

from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
    ExternalEventRelation,
    PackageExternalRelation,
    PackageExternalRelationCandidate,
    PackageMembership,
    PackageMembershipDecision,
    SourceMessage,
    StrictModel,
)
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    CanonicalFieldRegistryEntry,
    FieldNamespace,
)
from cdecr.model_boundary import (
    normalize_json_prompt,
    normalize_temporal_values,
    relax_temporal_schema,
)
from cdecr.single_document_contracts import (
    DreamCandidate,
    GrounderOutput,
    JudgeDecisionRecord,
    NormalizationDecision,
    PreprocessedDocument,
    PreprocessingResult,
    SingleDocumentResult,
)

if TYPE_CHECKING:
    from cdecr.cross_document_contracts import (
        AtomicAssignmentRecord,
        CrossDocumentResult,
        PackageAssignmentRecord,
        PackageMergePlan,
        PackagePairEvaluation,
        PackagePairMergeDecision,
    )


class SourceQuery(StrictModel):
    market: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    start_at: datetime
    end_at: datetime
    limit: int = Field(ge=1, le=1000)
    min_text_chars: int = Field(default=1, ge=1)


class RejectedSource(StrictModel):
    source_row_id: str
    reason_codes: list[str]


class SourceRecord(StrictModel):
    source_row_id: str
    market: str
    ticker: str
    document_fingerprint: str
    message: SourceMessage


class SourceReadBatch(StrictModel):
    query: SourceQuery
    accepted: list[SourceRecord]
    rejected: list[RejectedSource]
    raw_count: int


class EmbeddingResult(StrictModel):
    model: str
    dimensions: int
    vectors: list[list[float]]
    input_tokens: int | None = None
    latency_ms: int = Field(ge=0)
    request_id: str | None = None


class StructuredModelRequest(StrictModel):
    system_prompt: str
    user_prompt: str
    json_schema: dict[str, object]
    output_mode: Literal["json_object"] = "json_object"

    @field_validator("user_prompt")
    @classmethod
    def normalize_model_time_input(cls, value: str) -> str:
        return normalize_json_prompt(value)

    @field_validator("json_schema", mode="before")
    @classmethod
    def relax_model_time_schema(cls, value: object) -> object:
        return relax_temporal_schema(value)


class StructuredModelResult(StrictModel):
    model: str
    payload: dict[str, object]
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = Field(ge=0)
    request_id: str | None = None

    @field_validator("payload", mode="before")
    @classmethod
    def normalize_model_time_output(cls, value: object) -> object:
        return normalize_temporal_values(value)


class DecisionAuditRecord(StrictModel):
    audit_id: str
    run_id: str | None = None
    decision_type: str
    subject_id: str
    payload: dict[str, Any]


class SourceMessageReader(Protocol):
    def read(self, query: SourceQuery) -> SourceReadBatch: ...


class EmbeddingClient(Protocol):
    def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...


class StructuredModelClient(Protocol):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult: ...


class EntityNormalizer(Protocol):
    def normalize_entity(
        self, surface: str, *, ticker_hints: Sequence[str], mention_id: str, field_path: str
    ) -> tuple[str | None, NormalizationDecision]: ...


class TimePeriodNormalizer(Protocol):
    def normalize_period(
        self, raw_period: str | None, *, company_id: str | None, mention_id: str, field_path: str
    ) -> tuple[str | None, NormalizationDecision | None]: ...


class MetricNormalizer(Protocol):
    def normalize_metric(
        self, raw_metric: str | None, *, mention_id: str, field_path: str
    ) -> tuple[str, NormalizationDecision]: ...


class QuantityNormalizer(Protocol):
    def normalize_quantity(
        self, raw_text: str, value: int | float, unit: str, *, mention_id: str, field_path: str
    ) -> tuple[int | float, str, NormalizationDecision]: ...


class CDECRRegistry(Protocol):
    def initialize(self) -> None: ...

    def save_source(self, source: SourceMessage, *, fingerprint: str) -> bool: ...

    def save_mention(self, mention: EventMention) -> bool: ...

    def save_atomic_event(self, event: AtomicEvent) -> bool: ...

    def save_package(self, package: EventPackage) -> bool: ...

    def save_membership(self, membership: PackageMembership) -> bool: ...

    def save_membership_decision(self, decision: PackageMembershipDecision) -> bool: ...

    def save_external_relation(self, relation: ExternalEventRelation) -> bool: ...

    def list_external_relations(self) -> list[ExternalEventRelation]: ...

    def save_package_external_relation(self, relation: PackageExternalRelation) -> bool: ...

    def list_package_external_relations(
        self, *, source_event_id: str | None = None
    ) -> list[PackageExternalRelation]: ...

    def save_package_external_relation_candidate(
        self, candidate: PackageExternalRelationCandidate
    ) -> bool: ...

    def list_package_external_relation_candidates(
        self, *, source_event_id: str | None = None
    ) -> list[PackageExternalRelationCandidate]: ...

    def get_source(self, message_id: str) -> SourceMessage | None: ...

    def list_all_sources(self, *, limit: int = 10000) -> list[SourceMessage]: ...

    def get_source_fingerprint(self, message_id: str) -> str | None: ...

    def get_mention(self, mention_id: str) -> EventMention | None: ...

    def list_all_mentions(self, *, limit: int = 100000) -> list[EventMention]: ...

    def list_mentions_for_message(self, message_id: str) -> list[EventMention]: ...

    def get_current_atomic_event(self, event_id: str) -> AtomicEvent | None: ...

    def list_current_atomic_events(self, *, limit: int = 1000) -> list[AtomicEvent]: ...

    def get_atomic_event_for_mention(self, mention_id: str) -> AtomicEvent | None: ...

    def get_current_package(self, package_id: str) -> EventPackage | None: ...

    def resolve_package_root(self, package_id: str, *, max_depth: int = 32) -> str | None: ...

    def list_current_packages(self, *, limit: int = 1000) -> list[EventPackage]: ...

    def list_packages_for_event(self, event_id: str) -> list[EventPackage]: ...

    def list_memberships_for_package(self, package_id: str) -> list[PackageMembership]: ...

    def list_membership_decisions(
        self, *, event_id: str | None = None
    ) -> list[PackageMembershipDecision]: ...

    def recall_atomic_event_ids(
        self,
        *,
        entity_ids: Sequence[str],
        event_family: str,
        normalized_predicate: str,
        schema_type: str,
        reference_period_id: str | None,
        event_start: str | None,
        event_end: str | None,
        source_fingerprint: str | None,
        field_ids: Sequence[tuple[FieldNamespace, str]] = (),
        per_route_limit: int = 20,
    ) -> dict[str, set[str]]: ...

    def recall_package_ids(
        self,
        *,
        package_kind: str,
        package_family: str,
        anchor_entities: Sequence[str],
        local_anchor_hint: str | None = None,
        anchor_artifact_id: str | None,
        anchor_period_id: str | None,
        time_start: str | None,
        time_end: str | None,
        package_anchor_ids: Sequence[str] = (),
        per_route_limit: int = 20,
    ) -> dict[str, set[str]]: ...

    def list_sources(
        self,
        *,
        market: str | None = None,
        ticker: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 100,
    ) -> list[SourceMessage]: ...

    def list_preprocessed_documents(
        self, *, exclude_message_id: str | None = None
    ) -> list[PreprocessedDocument]: ...

    def get_completed_document_result(self, processing_key: str) -> SingleDocumentResult | None: ...

    def get_latest_completed_document_result_for_message(
        self, message_id: str
    ) -> SingleDocumentResult | None: ...

    def start_document_run(
        self,
        *,
        run_id: str,
        processing_key: str,
        message_id: str,
        pipeline_version: str,
        prompt_version: str,
        catalog_version: str,
        model_config: dict[str, Any],
    ) -> bool: ...

    def save_preprocessing_result(self, run_id: str, result: PreprocessingResult) -> bool: ...

    def save_dream_candidates(self, run_id: str, candidates: Sequence[DreamCandidate]) -> int: ...

    def get_latest_dream_candidates_for_processing_key(
        self, processing_key: str
    ) -> list[DreamCandidate]: ...

    def save_grounder_batch(
        self,
        *,
        run_id: str,
        processing_key: str,
        batch_key: str,
        output: GrounderOutput,
    ) -> bool: ...

    def get_grounder_batch(
        self, *, processing_key: str, batch_key: str
    ) -> GrounderOutput | None: ...

    def save_judge_decisions(
        self, run_id: str, decisions: Sequence[JudgeDecisionRecord]
    ) -> int: ...

    def save_normalization_decisions(
        self, run_id: str, decisions: Sequence[NormalizationDecision]
    ) -> int: ...

    def save_embedding(
        self,
        *,
        owner_kind: str,
        owner_id: str,
        model: str,
        input_hash: str,
        vector: Sequence[float],
        embedding_id: str | None = None,
    ) -> bool: ...

    def list_latest_embeddings(
        self, *, owner_kind: str, model: str, limit: int = 10000
    ) -> list[Any]: ...

    def get_embedding(
        self, *, owner_kind: str, owner_id: str, model: str, input_hash: str
    ) -> Any | None: ...

    def create_field_registry_entry(self, entry: CanonicalFieldRegistryEntry) -> bool: ...

    def get_field_registry_entry(self, registry_id: str) -> CanonicalFieldRegistryEntry | None: ...

    def list_field_registry_entries(
        self, *, namespace: FieldNamespace | None = None, limit: int = 10000
    ) -> list[CanonicalFieldRegistryEntry]: ...

    def find_field_registry_by_external_id(
        self, *, namespace: FieldNamespace, external_id: str
    ) -> CanonicalFieldRegistryEntry | None: ...

    def update_field_registry_aliases(self, registry_id: str, aliases: Sequence[str]) -> bool: ...

    def set_field_registry_external_id(self, registry_id: str, external_id: str) -> bool: ...

    def save_field_redirect(self, source_id: str, target_id: str) -> bool: ...

    def resolve_field_registry_entry(
        self, registry_id: str, *, max_depth: int = 16
    ) -> CanonicalFieldRegistryEntry | None: ...

    def save_field_link(self, link: CanonicalFieldLink) -> bool: ...

    def get_field_link(self, mention_id: str, field_path: str) -> CanonicalFieldLink | None: ...

    def list_field_links_for_mention(self, mention_id: str) -> list[CanonicalFieldLink]: ...

    def start_cross_document_run(
        self,
        *,
        run_id: str,
        processing_key: str,
        message_id: str,
        engine_version: str,
        prompt_version: str,
        model_config: dict[str, Any],
    ) -> bool: ...

    def start_cross_document_trace(
        self,
        *,
        trace_id: str,
        message_id: str,
        engine_version: str,
        prompt_version: str,
        model_config: dict[str, Any],
    ) -> bool: ...

    def finish_cross_document_trace(
        self, trace_id: str, *, status: Literal["REUSED", "FAILED"]
    ) -> None: ...

    def get_completed_cross_document_result(
        self, processing_key: str
    ) -> CrossDocumentResult | None: ...

    def complete_cross_document_run(self, result: CrossDocumentResult) -> bool: ...

    def fail_cross_document_run(self, run_id: str, *, error_code: str) -> None: ...

    def save_atomic_assignment(self, record: AtomicAssignmentRecord) -> bool: ...

    def get_latest_atomic_assignment_for_mention(
        self, mention_id: str
    ) -> AtomicAssignmentRecord | None: ...

    def save_package_assignment(self, record: PackageAssignmentRecord) -> bool: ...

    def get_latest_package_assignment_for_event(
        self, event_id: str
    ) -> PackageAssignmentRecord | None: ...

    def save_package_merge_decision(
        self, *, decision_id: str, run_id: str, decision: PackagePairMergeDecision
    ) -> bool: ...

    def save_package_pair_evaluation(self, evaluation: PackagePairEvaluation) -> bool: ...

    def get_package_pair_evaluation(
        self,
        *,
        left_package_id: str,
        right_package_id: str,
        left_profile_hash: str,
        right_profile_hash: str,
    ) -> PackagePairEvaluation | None: ...

    def save_atomic_redirect(
        self, *, source_event_id: str, target_event_id: str, run_id: str, reason: str
    ) -> bool: ...

    def resolve_atomic_event_root(self, event_id: str, *, max_depth: int = 32) -> str: ...

    def save_package_redirect(
        self, *, source_package_id: str, target_package_id: str, run_id: str, reason: str
    ) -> bool: ...

    def apply_package_merge_plan(
        self,
        *,
        plan: PackageMergePlan,
        decisions: Sequence[PackagePairMergeDecision],
        merged_package: EventPackage,
        run_id: str,
        embedding_model: str,
        embedding_input_hash: str,
        embedding_vector: Sequence[float],
    ) -> EventPackage: ...

    def record_model_call(
        self,
        *,
        model_call_id: str,
        run_id: str | None,
        tier: str,
        model: str,
        status: Literal["SUCCEEDED", "FAILED"],
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        error_code: str | None,
        metadata: dict[str, Any],
        stage: str | None = None,
        prompt_version: str | None = None,
        schema_hash: str | None = None,
        input_hash: str | None = None,
    ) -> bool: ...

    def append_decision_audit(self, record: DecisionAuditRecord) -> bool: ...

    def rebuild_derived_state(self) -> dict[str, int]: ...

    def complete_document_run(self, result: SingleDocumentResult) -> int: ...

    def fail_document_run(self, run_id: str, *, error_code: str) -> None: ...
