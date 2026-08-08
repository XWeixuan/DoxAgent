"""Contracts for Document 1 horizontal indicator collection.

These models deliberately live outside the ReAct observation-memory contracts.  A
collection observation is a governed data-domain record; it is not a prompt-memory
block and must not be persisted through the ReAct observation store by accident.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from doxagent.models import NonEmptyStr

HORIZONTAL_COLLECTION_MANIFEST_ARTIFACT_KIND = "horizontal_collection_manifest"
HORIZONTAL_COLLECTION_MANIFEST_SCHEMA_VERSION = "1.0"


class HorizontalCollectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricRequirement(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class SourceRole(StrEnum):
    ACTUAL = "ACTUAL"
    MANAGEMENT = "MANAGEMENT"
    SELL_SIDE = "SELL_SIDE"
    INDUSTRY_CHAIN = "INDUSTRY_CHAIN"
    MARKET_IMPLIED = "MARKET_IMPLIED"


class CollectionMode(StrEnum):
    PROGRAM = "PROGRAM"
    AGENT = "AGENT"
    UNAVAILABLE = "UNAVAILABLE"


class CollectionTargetStatus(StrEnum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class OutputPolicy(StrEnum):
    STATE_VALUE = "STATE_VALUE"
    OBSERVATION_ONLY = "OBSERVATION_ONLY"


class MetricValueType(StrEnum):
    NUMBER = "NUMBER"
    RANGE = "RANGE"
    SERIES = "SERIES"
    DIRECTION = "DIRECTION"
    STAGE = "STAGE"
    OBJECT = "OBJECT"


class EntityScope(StrEnum):
    ISSUER = "ISSUER"
    US_MACRO = "US_MACRO"
    INDUSTRY = "INDUSTRY"
    SECURITY = "SECURITY"


class ResolverStatus(StrEnum):
    RESOLVED = "RESOLVED"
    CANDIDATE = "CANDIDATE"
    UNRESOLVED = "UNRESOLVED"


class ObjectType(StrEnum):
    EVENT = "EVENT"
    METRIC = "METRIC"
    EVIDENCE = "EVIDENCE"
    FILING = "FILING"
    ANALYST_ESTIMATE = "ANALYST_ESTIMATE"
    STATE_VALUE = "STATE_VALUE"
    REALIZATION_FACTOR = "REALIZATION_FACTOR"


class ProviderCapabilityStatus(StrEnum):
    DOCUMENTED = "DOCUMENTED"
    ENTITLED = "ENTITLED"
    IMPLEMENTED = "IMPLEMENTED"
    CONTRACT_TESTED = "CONTRACT_TESTED"
    PRODUCTION_READY = "PRODUCTION_READY"
    BLOCKED = "BLOCKED"


class ObjectRef(HorizontalCollectionModel):
    object_type: ObjectType
    provider_specific_id: NonEmptyStr | None = None
    source_locator: NonEmptyStr | None = None
    canonical_object_id_candidate: NonEmptyStr | None = None
    resolver_status: ResolverStatus

    @model_validator(mode="after")
    def require_a_locator(self) -> ObjectRef:
        if not any(
            (
                self.provider_specific_id,
                self.source_locator,
                self.canonical_object_id_candidate,
            )
        ):
            raise ValueError("ObjectRef requires a provider id, source locator, or candidate id.")
        return self


class MetricDefinition(HorizontalCollectionModel):
    metric_id: NonEmptyStr
    standard_name: NonEmptyStr
    definition: NonEmptyStr
    requirement: MetricRequirement
    value_type: MetricValueType
    default_unit: NonEmptyStr | None = None
    default_time_scope: NonEmptyStr
    aliases: tuple[NonEmptyStr, ...] = ()


class StateParameterIdentity(HorizontalCollectionModel):
    """Entity-scoped identity for one canonical metric parameter."""

    entity_id: NonEmptyStr
    metric_id: NonEmptyStr

    @property
    def parameter_id(self) -> str:
        return f"param_{self.entity_id.lower()}_{self.metric_id}"


class StateValueCurrentKey(HorizontalCollectionModel):
    """Uniqueness boundary for the CURRENT value of a parameter slot."""

    entity_id: NonEmptyStr
    metric_id: NonEmptyStr
    source_role: SourceRole
    time_scope: NonEmptyStr


class CollectionTargetDefinition(HorizontalCollectionModel):
    collection_target_id: NonEmptyStr
    metric_id: NonEmptyStr | None = None
    candidate_metric_ids: tuple[NonEmptyStr, ...] = ()
    requirement: MetricRequirement
    source_role: SourceRole
    time_scope: NonEmptyStr
    entity_scope: EntityScope
    collection_mode: CollectionMode
    provider: NonEmptyStr | None = None
    tool_name: NonEmptyStr | None = None
    method_id: NonEmptyStr | None = None
    output_policy: OutputPolicy
    capability_status: ProviderCapabilityStatus

    @model_validator(mode="after")
    def validate_route(self) -> CollectionTargetDefinition:
        if bool(self.metric_id) == bool(self.candidate_metric_ids):
            raise ValueError(
                "A target must define one metric_id or candidate_metric_ids, but not both."
            )
        if self.collection_mode is CollectionMode.PROGRAM:
            if not self.provider or not self.tool_name:
                raise ValueError("PROGRAM targets require provider and tool_name.")
        elif self.tool_name is not None:
            raise ValueError("AGENT/UNAVAILABLE targets must not route directly to a tool.")
        if (
            self.capability_status is ProviderCapabilityStatus.PRODUCTION_READY
            and self.collection_mode is not CollectionMode.PROGRAM
        ):
            raise ValueError("Only PROGRAM targets can be PRODUCTION_READY.")
        return self


class CollectionObservation(HorizontalCollectionModel):
    collection_target_id: NonEmptyStr
    item_key: NonEmptyStr | None = None
    value: Any
    as_of: datetime
    source_refs: tuple[ObjectRef, ...]
    unit: NonEmptyStr | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    quality_flags: tuple[NonEmptyStr, ...] = ()
    method_id: NonEmptyStr | None = None
    method_version: NonEmptyStr | None = None
    input_refs: tuple[ObjectRef, ...] = ()

    @model_validator(mode="after")
    def require_source_refs(self) -> CollectionObservation:
        if not self.source_refs:
            raise ValueError("CollectionObservation requires at least one source_ref.")
        if bool(self.method_id) != bool(self.method_version):
            raise ValueError("method_id and method_version must be supplied together.")
        return self


class ProviderAttempt(HorizontalCollectionModel):
    provider: NonEmptyStr
    tool_name: NonEmptyStr
    status: CollectionTargetStatus
    started_at: datetime
    finished_at: datetime
    error_code: NonEmptyStr | None = None
    message: NonEmptyStr | None = None


class HorizontalCollectionTargetResult(HorizontalCollectionModel):
    collection_target_id: NonEmptyStr
    status: CollectionTargetStatus
    requested_items: int = Field(default=1, ge=0)
    succeeded_items: int = Field(default=0, ge=0)
    unavailable_items: int = Field(default=0, ge=0)
    failed_items: int = Field(default=0, ge=0)
    provider_attempts: tuple[ProviderAttempt, ...] = ()
    output_refs: tuple[ObjectRef, ...] = ()
    reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_counts_and_outputs(self) -> HorizontalCollectionTargetResult:
        accounted = self.succeeded_items + self.unavailable_items + self.failed_items
        if accounted > self.requested_items:
            raise ValueError("Manifest item counts exceed requested_items.")
        if (
            self.status
            not in {
                CollectionTargetStatus.FILLED,
                CollectionTargetStatus.PARTIAL,
            }
            and self.output_refs
        ):
            raise ValueError("Only FILLED/PARTIAL target results may contain output_refs.")
        if self.status is CollectionTargetStatus.FILLED and (
            self.requested_items == 0 or self.succeeded_items != self.requested_items
        ):
            raise ValueError("FILLED requires every requested item to succeed.")
        return self


class HorizontalCollectionManifest(HorizontalCollectionModel):
    artifact_kind: str = HORIZONTAL_COLLECTION_MANIFEST_ARTIFACT_KIND
    schema_version: str = HORIZONTAL_COLLECTION_MANIFEST_SCHEMA_VERSION
    run_id: NonEmptyStr
    ticker: NonEmptyStr
    metric_registry_version: NonEmptyStr
    target_registry_version: NonEmptyStr
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    target_results: tuple[HorizontalCollectionTargetResult, ...]

    @model_validator(mode="after")
    def require_unique_targets(self) -> HorizontalCollectionManifest:
        ids = [item.collection_target_id for item in self.target_results]
        if len(ids) != len(set(ids)):
            raise ValueError("Manifest target_results must be unique by collection_target_id.")
        return self


class HorizontalCollectionArtifactEnvelope(HorizontalCollectionModel):
    """Persistence envelope for a separate run-audit artifact.

    The envelope is stored through the run repository as an audit artifact, not as a
    GlobalResearch/ExpectationUnit document and not as a ReAct observation block.
    """

    artifact_kind: str = HORIZONTAL_COLLECTION_MANIFEST_ARTIFACT_KIND
    run_id: NonEmptyStr
    payload: HorizontalCollectionManifest
