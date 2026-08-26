"""Frozen business contracts for the Canonical Event Library foundation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EVENT_ID = re.compile(r"^E[1-9]\d*$")
_TEMP_EVENT_ID = re.compile(r"^T[1-9]\d*$")
_FACT_ID = re.compile(r"^F[1-9]\d*$")
_TEMP_FACT_ID = re.compile(r"^TF[1-9]\d*$")
_DELTA_ID = re.compile(r"^D[1-9]\d*$")
_RUNTIME_HINT_ID = re.compile(r"^R[1-9]\d*$")
EVENT_LIBRARY_CONTRACT_VERSION = "event-library-foundation-v1"
EVENT_LIBRARY_WIRE_VERSION = "event-library-maintenance-v2"


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OccurrenceTimePrecision(StrEnum):
    TIMESTAMP = "TIMESTAMP"
    DAY = "DAY"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"
    INTERVAL = "INTERVAL"
    UNKNOWN = "UNKNOWN"


class CanonicalAssertionState(StrEnum):
    ACTUAL = "ACTUAL"
    GUIDANCE = "GUIDANCE"
    FORECAST = "FORECAST"
    PLAN = "PLAN"
    RUMOR = "RUMOR"
    DENIAL = "DENIAL"
    SCHEDULED = "SCHEDULED"
    ONGOING = "ONGOING"
    PLANNED = "PLANNED"
    EXPECTED = "EXPECTED"
    RUMORED = "RUMORED"
    DENIED = "DENIED"
    HYPOTHETICAL = "HYPOTHETICAL"
    UNKNOWN = "UNKNOWN"


class CanonicalSubjectTimeMarker(StrEnum):
    """Reserved subject-time markers alongside free-form object periods."""

    SAME = "SAME"


class CanonicalObjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPPRESSED = "SUPPRESSED"
    MERGED = "MERGED"


class DeltaResolution(StrEnum):
    DUPLICATE_FACT = "DUPLICATE_FACT"
    KEEP_PENDING = "KEEP_PENDING"
    DROP_INVALID = "DROP_INVALID"


class DeltaBatchStatus(StrEnum):
    PENDING = "PENDING"
    PARTIAL_PUBLISHED = "PARTIAL_PUBLISHED"
    PUBLISHED = "PUBLISHED"
    FINALIZED_NOOP = "FINALIZED_NOOP"


class LibraryVersionStatus(StrEnum):
    WORKING = "WORKING"
    PUBLISHED = "PUBLISHED"


class ReferenceReviewMode(StrEnum):
    IMPLICIT = "IMPLICIT"
    EXPLICIT = "EXPLICIT"


class ReferenceReviewReason(StrEnum):
    NEW_OR_MODIFIED = "NEW_OR_MODIFIED"
    SUPERSEDED_TARGET = "SUPERSEDED_TARGET"
    PERIODIC_10D = "PERIODIC_10D"
    EXPIRED_30D = "EXPIRED_30D"
    INCLUDED_RECHECK_7D = "INCLUDED_RECHECK_7D"
    TIME_UNRESOLVED = "TIME_UNRESOLVED"


class CanonicalFact(StrictModel):
    fact_id: str
    proposition: str = Field(min_length=1)
    assertion_state: CanonicalAssertionState
    subject_time: CanonicalSubjectTimeMarker | str | None = None

    @field_validator("fact_id")
    @classmethod
    def valid_fact_id(cls, value: str) -> str:
        if not (_FACT_ID.fullmatch(value) or _TEMP_FACT_ID.fullmatch(value)):
            raise ValueError("fact_id must be a stable F# or temporary TF# ID")
        return value

class CanonicalFactRevision(CanonicalFact):
    consumes_delta_ids: list[str] = Field(default_factory=list)

    @field_validator("consumes_delta_ids")
    @classmethod
    def valid_delta_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("consumes_delta_ids must be unique")
        if any(not _DELTA_ID.fullmatch(item) for item in value):
            raise ValueError("consumes_delta_ids must contain D# IDs")
        return value

    def published(self) -> CanonicalFact:
        return CanonicalFact.model_validate(
            self.model_dump(exclude={"consumes_delta_ids"})
        )


class CanonicalEvent(StrictModel):
    event_id: str
    ticker: str = Field(min_length=1)
    title: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)
    occurrence_time_precision: OccurrenceTimePrecision
    status: CanonicalObjectStatus = CanonicalObjectStatus.ACTIVE
    canonical_summary: str = Field(min_length=1)
    known_event_summary: str = Field(min_length=1)
    is_important: bool
    include_in_reference_view: bool
    related_event_ids: list[str] = Field(default_factory=list)
    supersedes_event_id: str | None = None
    derived_from_event_ids: list[str] = Field(default_factory=list)
    facts: Sequence[CanonicalFact] = Field(min_length=1)
    price_analysis: dict[str, Any] | None = None

    @field_validator("event_id")
    @classmethod
    def valid_event_id(cls, value: str) -> str:
        if not (_EVENT_ID.fullmatch(value) or _TEMP_EVENT_ID.fullmatch(value)):
            raise ValueError("event_id must be a stable E# or temporary T# ID")
        return value

    @field_validator("ticker")
    @classmethod
    def normalized_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker must not be empty")
        return normalized

    @field_validator("related_event_ids", "derived_from_event_ids")
    @classmethod
    def unique_event_refs(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("event references must be unique")
        if any(not (_EVENT_ID.fullmatch(item) or _TEMP_EVENT_ID.fullmatch(item)) for item in value):
            raise ValueError("event references must contain E# or T# IDs")
        return value

    @model_validator(mode="after")
    def validate_membership(self) -> CanonicalEvent:
        fact_ids = [item.fact_id for item in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("facts must have unique fact_id values")
        refs = set(self.related_event_ids) | set(self.derived_from_event_ids)
        if self.supersedes_event_id is not None:
            refs.add(self.supersedes_event_id)
        if self.event_id in refs:
            raise ValueError("an Event cannot relate to itself")
        return self


class CanonicalEventRevision(CanonicalEvent):
    facts: Sequence[CanonicalFactRevision] = Field(min_length=1)

    def published(self) -> CanonicalEvent:
        payload = self.model_dump(exclude={"facts"})
        payload["facts"] = [item.published().model_dump(mode="json") for item in self.facts]
        return CanonicalEvent.model_validate(payload)


class EventRetirement(StrictModel):
    event_id: str
    redirect_to_event_id: str
    reason: Literal[
        "MERGED_DUPLICATE_OCCURRENCE",
        "SUPPRESSED_INVALID_OCCURRENCE",
        "SPLIT_TO_SUCCESSOR",
    ]

    @field_validator("event_id", "redirect_to_event_id")
    @classmethod
    def stable_or_temp_event_id(cls, value: str) -> str:
        if not (_EVENT_ID.fullmatch(value) or _TEMP_EVENT_ID.fullmatch(value)):
            raise ValueError("retirement IDs must contain E# or T# IDs")
        return value

    @model_validator(mode="after")
    def no_self_redirect(self) -> EventRetirement:
        if self.event_id == self.redirect_to_event_id:
            raise ValueError("event retirement cannot redirect to itself")
        return self


class ResidualDeltaResolution(StrictModel):
    delta_id: str
    resolution: DeltaResolution
    target_event_id: str | None = None
    target_fact_id: str | None = None

    @field_validator("delta_id")
    @classmethod
    def valid_delta_id(cls, value: str) -> str:
        if not _DELTA_ID.fullmatch(value):
            raise ValueError("delta_id must be a D# ID")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> ResidualDeltaResolution:
        if self.resolution is DeltaResolution.DUPLICATE_FACT:
            if self.target_event_id is None or self.target_fact_id is None:
                raise ValueError("DUPLICATE_FACT requires target_event_id and target_fact_id")
        elif self.target_event_id is not None or self.target_fact_id is not None:
            raise ValueError("KEEP_PENDING and DROP_INVALID must not carry targets")
        return self


class CanonicalRevisionBundle(StrictModel):
    contract_version: Literal["event-library-foundation-v1"] = (
        "event-library-foundation-v1"
    )
    run_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    base_library_version: int = Field(ge=0)
    # Review-only runs intentionally carry no Delta and must not mint an empty V+1.
    delta_batch_ids: list[str] = Field(default_factory=list)
    event_revisions: list[CanonicalEventRevision] = Field(default_factory=list)
    event_retirements: list[EventRetirement] = Field(default_factory=list)
    residual_delta_resolutions: list[ResidualDeltaResolution] = Field(default_factory=list)
    reference_review_decisions: list[ReferenceReviewDecision] = Field(
        default_factory=list
    )

    @field_validator("ticker")
    @classmethod
    def bundle_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def unique_bundle_ids(self) -> CanonicalRevisionBundle:
        if len(self.delta_batch_ids) != len(set(self.delta_batch_ids)):
            raise ValueError("delta_batch_ids must be unique")
        event_ids = [item.event_id for item in self.event_revisions]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event_revisions must have unique event_id values")
        retired = [item.event_id for item in self.event_retirements]
        if len(retired) != len(set(retired)):
            raise ValueError("event_retirements must have unique event_id values")
        return self


class CanonicalRevisionBundleManifest(StrictModel):
    """Workspace wire manifest; Event revisions remain in separate JSON files."""

    contract_version: Literal["event-library-foundation-v1"] = (
        "event-library-foundation-v1"
    )
    run_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    base_library_version: int = Field(ge=0)
    delta_batch_ids: list[str] = Field(default_factory=list)
    event_revisions: list[str] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def manifest_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("event_revisions")
    @classmethod
    def safe_event_paths(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("event revision paths must be unique")
        for item in value:
            path = item.replace("\\", "/")
            if not path.startswith("events/") or path.startswith("/") or ".." in path.split("/"):
                raise ValueError("event revision paths must stay under events/")
            if not path.endswith(".json"):
                raise ValueError("event revision paths must be JSON files")
        return value


class RuntimePackageSnapshot(StrictModel):
    runtime_package_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    title: str = Field(min_length=1)
    member_runtime_atomic_ids: list[str] = Field(default_factory=list)


class FrozenRuntimeAtomic(StrictModel):
    runtime_atomic_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    proposition: str = Field(min_length=1)
    time: str = Field(min_length=1)
    assertion_state: CanonicalAssertionState
    entities: list[str] = Field(default_factory=list)
    runtime_package_ids: list[str] = Field(default_factory=list)


class FrozenRuntimeSnapshot(StrictModel):
    contract_version: Literal["event-library-foundation-v1"] = (
        "event-library-foundation-v1"
    )
    snapshot_id: str = Field(min_length=1)
    runtime_scope: str = Field(min_length=1)
    epoch_id: str = Field(min_length=1)
    epoch_status: Literal["FINALIZED"] = "FINALIZED"
    market: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    as_of: datetime
    atomics: list[FrozenRuntimeAtomic]
    packages: list[RuntimePackageSnapshot] = Field(default_factory=list)

    @field_validator("market", "ticker")
    @classmethod
    def upper_scope_part(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def unique_runtime_ids(self) -> FrozenRuntimeSnapshot:
        atomic_ids = [item.runtime_atomic_id for item in self.atomics]
        package_ids = [item.runtime_package_id for item in self.packages]
        if len(atomic_ids) != len(set(atomic_ids)):
            raise ValueError("runtime snapshot atomics must be unique")
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("runtime snapshot packages must be unique")
        known = set(atomic_ids)
        for package in self.packages:
            if not set(package.member_runtime_atomic_ids).issubset(known):
                raise ValueError("runtime package contains an unknown Atomic ID")
        return self


class DeltaItem(StrictModel):
    delta_id: str
    runtime_atomic_id: str
    runtime_atomic_version: int = Field(ge=1)
    runtime_signature: str
    proposition: str = Field(min_length=1)
    time: str = Field(min_length=1)
    assertion_state: CanonicalAssertionState
    entities: list[str] = Field(default_factory=list)
    runtime_hint_ids: list[str] = Field(default_factory=list)
    target_suggestion_ids: list[str] = Field(default_factory=list)

    @field_validator("delta_id")
    @classmethod
    def delta_short_id(cls, value: str) -> str:
        if not _DELTA_ID.fullmatch(value):
            raise ValueError("delta_id must be a D# ID")
        return value

    @field_validator("runtime_hint_ids")
    @classmethod
    def hint_short_ids(cls, value: list[str]) -> list[str]:
        if any(not _RUNTIME_HINT_ID.fullmatch(item) for item in value):
            raise ValueError("runtime_hint_ids must contain R# IDs")
        return value


class RuntimeHint(StrictModel):
    runtime_hint_id: str
    title: str = Field(min_length=1)

    @field_validator("runtime_hint_id")
    @classmethod
    def hint_id(cls, value: str) -> str:
        if not _RUNTIME_HINT_ID.fullmatch(value):
            raise ValueError("runtime_hint_id must be an R# ID")
        return value


class RuntimePackageDelta(StrictModel):
    """Package context over Atomic Delta members; never a disposition unit."""

    runtime_hint_id: str
    title: str = Field(min_length=1)
    runtime_package_version: int = Field(ge=1)
    member_delta_ids: list[str] = Field(min_length=1)
    time_anchors: list[str] = Field(default_factory=list)
    entity_anchors: list[str] = Field(default_factory=list)

    @field_validator("runtime_hint_id")
    @classmethod
    def package_hint_id(cls, value: str) -> str:
        if not _RUNTIME_HINT_ID.fullmatch(value):
            raise ValueError("runtime_hint_id must be an R# ID")
        return value

    @field_validator("member_delta_ids")
    @classmethod
    def package_delta_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            not _DELTA_ID.fullmatch(item) for item in value
        ):
            raise ValueError("member_delta_ids must contain unique D# IDs")
        return value


class ReferenceReviewCandidate(StrictModel):
    event_id: str
    occurred_at: str
    occurrence_anchor: date | None = None
    title: str
    known_event_summary: str
    is_important: bool
    include_in_reference_view: bool
    related_event_ids: list[str] = Field(default_factory=list)
    supersedes_event_id: str | None = None
    last_reviewed_at: datetime | None = None
    next_review_at: datetime | None = None
    review_mode: ReferenceReviewMode
    candidate_reason: ReferenceReviewReason


class ReferenceReviewDecision(StrictModel):
    event_id: str
    reviewed_at: datetime
    review_mode: ReferenceReviewMode
    candidate_reason: ReferenceReviewReason
    changed: bool = False
    include_in_reference_view: bool
    next_review_at: datetime | None = None
    note: str | None = None


class DeltaBatch(StrictModel):
    contract_version: Literal[
        "event-library-foundation-v1", "event-library-maintenance-v2"
    ] = "event-library-maintenance-v2"
    batch_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    runtime_scope: str = Field(min_length=1)
    source_snapshot_id: str = Field(min_length=1)
    source_epoch_id: str = Field(min_length=1)
    base_library_version: int = Field(ge=0)
    status: DeltaBatchStatus = DeltaBatchStatus.PENDING
    items: list[DeltaItem]
    runtime_hints: list[RuntimeHint] = Field(default_factory=list)
    runtime_packages: list[RuntimePackageDelta] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def unique_short_ids(self) -> DeltaBatch:
        delta_ids = [item.delta_id for item in self.items]
        hint_ids = [item.runtime_hint_id for item in self.runtime_hints]
        package_hint_ids = [item.runtime_hint_id for item in self.runtime_packages]
        if len(delta_ids) != len(set(delta_ids)):
            raise ValueError("Delta item IDs must be unique within a batch")
        if len(hint_ids) != len(set(hint_ids)):
            raise ValueError("Runtime hint IDs must be unique within a batch")
        if len(package_hint_ids) != len(set(package_hint_ids)):
            raise ValueError("Runtime package hint IDs must be unique within a batch")
        known_delta_ids = set(delta_ids)
        package_members = {
            item.runtime_hint_id: set(item.member_delta_ids)
            for item in self.runtime_packages
        }
        if any(not members.issubset(known_delta_ids) for members in package_members.values()):
            raise ValueError("Runtime package contains an unknown Delta ID")
        for item in self.items:
            for hint_id in item.runtime_hint_ids:
                if hint_id in package_members and item.delta_id not in package_members[hint_id]:
                    raise ValueError("Delta/Runtime Package membership is not bidirectional")
        for hint_id, members in package_members.items():
            for delta_id in members:
                delta = next(item for item in self.items if item.delta_id == delta_id)
                if hint_id not in delta.runtime_hint_ids:
                    raise ValueError("Runtime Package/Delta membership is not bidirectional")
        return self


class FrozenViewManifest(StrictModel):
    contract_version: Literal["event-library-maintenance-v2"] = "event-library-maintenance-v2"
    frozen_view_id: str
    run_id: str
    mode: Literal["INITIALIZE", "INCREMENTAL"]
    ticker: str
    as_of: datetime
    base_library_version: int = Field(ge=0)
    delta_batch_ids: list[str]
    published_event_count: int = Field(ge=0)
    pending_delta_count: int = Field(ge=0)
    known_event_index_contract_version: str = "known-event-index-v2"
    known_event_index_path: str
    event_details_path: str
    pending_atomics_path: str
    runtime_hints_path: str
    runtime_packages_path: str = "delta/runtime_packages.json"
    package_index_path: str = "delta/package_index.md"
    reference_review_candidates_path: str
    upstream_context_manifest_path: str | None = None
    canonical_event_schema_path: str
    revision_bundle_schema_path: str


class MUGoldExpectation(StrictModel):
    expected_event_count: int = Field(ge=0)
    expected_fact_count: int = Field(ge=0)
    expected_pending_delta_ids: list[str] = Field(default_factory=list)
    expected_dropped_delta_ids: list[str] = Field(default_factory=list)
    expected_occurrence_delta_groups: list[list[str]] = Field(default_factory=list)


class MUGoldContract(StrictModel):
    contract_version: Literal["mu-event-library-gold-v1"] = "mu-event-library-gold-v1"
    runtime_snapshot_path: str
    revision_bundle_path: str
    expectation: MUGoldExpectation


class PublicationResult(StrictModel):
    ticker: str
    base_library_version: int
    published_library_version: int
    delta_batch_ids: list[str]
    batch_status: DeltaBatchStatus
    event_id_map: dict[str, str] = Field(default_factory=dict)
    fact_id_map: dict[str, str] = Field(default_factory=dict)
    applied_event_count: int = Field(ge=0)
    pending_delta_count: int = Field(ge=0)
    dropped_delta_count: int = Field(ge=0)
    duplicate_delta_count: int = Field(ge=0)
    published_at: datetime = Field(default_factory=utc_now)
