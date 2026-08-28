"""Frozen business contracts for the Canonical Event Library foundation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

_EVENT_ID = re.compile(r"^E[1-9]\d*$")
_TEMP_EVENT_ID = re.compile(r"^T[1-9]\d*$")
_FACT_ID = re.compile(r"^F[1-9]\d*$")
_TEMP_FACT_ID = re.compile(r"^TF[1-9]\d*$")
_DELTA_ID = re.compile(r"^D[1-9]\d*$")
_RUNTIME_HINT_ID = re.compile(r"^R[1-9]\d*$")
EVENT_LIBRARY_CONTRACT_VERSION = "event-library-foundation-v1"
EVENT_LIBRARY_WIRE_VERSION = "event-library-maintenance-v3"
FROZEN_RUNTIME_TIME_WIRE_VERSION = "frozen-runtime-time-v2"
REFERENCE_REVIEW_POLICY_VERSION = "reference-review-policy-v2"


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


class CanonicalEventType(StrEnum):
    EARNINGS_RELEASE = "EARNINGS_RELEASE"
    GUIDANCE_UPDATE = "GUIDANCE_UPDATE"
    ANALYST_ACTION = "ANALYST_ACTION"
    ANALYST_RESPONSE_EPISODE = "ANALYST_RESPONSE_EPISODE"
    INVESTOR_EVENT = "INVESTOR_EVENT"
    PRODUCT_ANNOUNCEMENT = "PRODUCT_ANNOUNCEMENT"
    PRODUCT_MILESTONE = "PRODUCT_MILESTONE"
    CAPACITY_OR_CAPEX = "CAPACITY_OR_CAPEX"
    SUPPLY_DEMAND_UPDATE = "SUPPLY_DEMAND_UPDATE"
    MATERIAL_CONTRACT = "MATERIAL_CONTRACT"
    FINANCING = "FINANCING"
    CAPITAL_RETURN = "CAPITAL_RETURN"
    M_AND_A = "M_AND_A"
    REGULATORY_ACTION = "REGULATORY_ACTION"
    LITIGATION_MILESTONE = "LITIGATION_MILESTONE"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    MARKET_EPISODE = "MARKET_EPISODE"
    OTHER_CORPORATE_EVENT = "OTHER_CORPORATE_EVENT"


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
    AGE_REVIEW_DUE_30D = "AGE_REVIEW_DUE_30D"
    # Historical schedules remain readable; new scheduling never emits this value.
    LEGACY_EXPIRED_30D = "EXPIRED_30D"
    INCLUDED_RECHECK_7D = "INCLUDED_RECHECK_7D"
    TIME_UNRESOLVED = "TIME_UNRESOLVED"


class OccurrenceDateCandidateSource(StrEnum):
    PROPOSITION_EVIDENCE = "PROPOSITION_EVIDENCE"
    OFFICIAL_RELEASE_DATE = "OFFICIAL_RELEASE_DATE"
    RUNTIME_CONFIRMED_OCCURRENCE = "RUNTIME_CONFIRMED_OCCURRENCE"
    SOURCE_PUBLISHED_AT = "SOURCE_PUBLISHED_AT"
    FOCUSED_WEB_SEARCH = "FOCUSED_WEB_SEARCH"


class OccurrenceDateCandidate(StrictModel):
    candidate_date: date
    source_kind: OccurrenceDateCandidateSource
    source_id: str = Field(min_length=1)
    source_message_id: str | None = None
    runtime_package_id: str | None = None
    evidence: str | None = None


class DateSemanticRole(StrEnum):
    EVENT_OCCURRENCE = "EVENT_OCCURRENCE"
    FACT_OCCURRENCE = "FACT_OCCURRENCE"
    SUBJECT_TIME = "SUBJECT_TIME"


class DateResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    GENUINELY_PERIOD_WIDE = "GENUINELY_PERIOD_WIDE"
    CONFLICTING = "CONFLICTING"
    UNRESOLVED = "UNRESOLVED"


class ReferenceViewBasis(StrEnum):
    CURRENT_BASELINE = "CURRENT_BASELINE"
    OPEN_OR_EVOLVING_MATTER = "OPEN_OR_EVOLVING_MATTER"
    LATEST_CONTROLLING_UPDATE = "LATEST_CONTROLLING_UPDATE"
    STILL_EFFECTIVE_FORWARD_ITEM = "STILL_EFFECTIVE_FORWARD_ITEM"
    RECENT_UNABSORBED_UPDATE = "RECENT_UNABSORBED_UPDATE"
    SUPERSEDED = "SUPERSEDED"
    COMPLETED_AND_ABSORBED = "COMPLETED_AND_ABSORBED"
    SUBJECT_HORIZON_PASSED = "SUBJECT_HORIZON_PASSED"
    ROUTINE_OR_REDUNDANT = "ROUTINE_OR_REDUNDANT"
    NO_CURRENT_DOWNSTREAM_UTILITY = "NO_CURRENT_DOWNSTREAM_UTILITY"

    @property
    def includes(self) -> bool:
        return self in {
            self.CURRENT_BASELINE,
            self.OPEN_OR_EVOLVING_MATTER,
            self.LATEST_CONTROLLING_UPDATE,
            self.STILL_EFFECTIVE_FORWARD_ITEM,
            self.RECENT_UNABSORBED_UPDATE,
        }


class TimeReferenceRepairReason(StrEnum):
    LEGACY_FACT_OCCURRENCE_MISSING = "LEGACY_FACT_OCCURRENCE_MISSING"
    BROAD_EVENT_FACT_SAME = "BROAD_EVENT_FACT_SAME"
    FUTURE_OCCURRENCE_AFTER_AS_OF = "FUTURE_OCCURRENCE_AFTER_AS_OF"
    REFERENCE_BASIS_MISSING = "REFERENCE_BASIS_MISSING"
    SUPERSESSION_CONTEXT = "SUPERSESSION_CONTEXT"


class TimeReferenceRepairWorkItem(StrictModel):
    event_id: str
    fact_ids: list[str] = Field(default_factory=list)
    reasons: list[TimeReferenceRepairReason]

    @field_validator("event_id")
    @classmethod
    def stable_event_only(cls, value: str) -> str:
        if not _EVENT_ID.fullmatch(value):
            raise ValueError("repair worklist may target Published E# IDs only")
        return value


class CanonicalFact(StrictModel):
    fact_id: str = Field(description="Stable F# or same-Bundle temporary TF# semantic identity.")
    proposition: str = Field(
        min_length=1,
        description=(
            "One minimal independently useful proposition produced or changed by the Event."
        ),
    )
    assertion_state: CanonicalAssertionState = Field(
        description="Business state of the proposition, not article tone."
    )
    subject_time: CanonicalSubjectTimeMarker | str | None = Field(
        default=None,
        description=(
            "Object period or SAME when it equals Event occurrence time; null when not applicable."
        ),
    )
    fact_occurred_at: str | None = Field(
        default=None,
        description=(
            "Time this Fact was stated, disclosed, confirmed, or formed. Missing only on "
            "legacy Published rows."
        ),
    )
    fact_occurrence_time_precision: OccurrenceTimePrecision | None = Field(
        default=None,
        description="Precision for fact_occurred_at; new Revision Bundle Facts require DAY.",
    )

    @field_validator("fact_id")
    @classmethod
    def valid_fact_id(cls, value: str) -> str:
        if not (_FACT_ID.fullmatch(value) or _TEMP_FACT_ID.fullmatch(value)):
            raise ValueError("fact_id must be a stable F# or temporary TF# ID")
        return value

    @model_validator(mode="after")
    def paired_occurrence_fields(self) -> CanonicalFact:
        if (self.fact_occurred_at is None) != (self.fact_occurrence_time_precision is None):
            raise ValueError("Fact occurrence value and precision must be supplied together")
        return self


class CanonicalFactRevision(CanonicalFact):
    consumes_delta_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Exactly the D# inputs whose propositions are admitted into this Fact revision."
        ),
    )

    @field_validator("consumes_delta_ids")
    @classmethod
    def valid_delta_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("consumes_delta_ids must be unique")
        if any(not _DELTA_ID.fullmatch(item) for item in value):
            raise ValueError("consumes_delta_ids must contain D# IDs")
        return value

    def published(self) -> CanonicalFact:
        return CanonicalFact.model_validate(self.model_dump(exclude={"consumes_delta_ids"}))


class CanonicalEvent(StrictModel):
    event_id: str = Field(description="Stable E# or same-Bundle temporary T# occurrence identity.")
    ticker: str = Field(
        min_length=1, description="Uppercase security ticker scoped by the per-ticker Registry."
    )
    title: str = Field(
        min_length=1, description="Actor, action and distinguishing object of this occurrence."
    )
    event_type: str = Field(min_length=1, description="Frozen uppercase canonical Event type.")
    occurred_at: str = Field(
        min_length=1,
        description=(
            "Action or information-release time in the form required by occurrence_time_precision."
        ),
    )
    occurrence_time_precision: OccurrenceTimePrecision = Field(
        description="Precision that determines the exact occurred_at wire form."
    )
    status: CanonicalObjectStatus = Field(
        default=CanonicalObjectStatus.ACTIVE, description="Lifecycle state after this revision."
    )
    canonical_summary: str = Field(
        min_length=1,
        description="Compact account of what occurred and its principal business result.",
    )
    known_event_summary: str = Field(
        min_length=1,
        description="Recognition-oriented date/actor/action/stage/figure summary for W1 matching.",
    )
    is_important: bool = Field(
        description="Durable decision relevance independent from Reference-view inclusion."
    )
    include_in_reference_view: bool = Field(
        description="Current usefulness to D2/D3, independent from Library admission."
    )
    related_event_ids: list[str] = Field(
        default_factory=list, description="Non-replacing related milestone Event IDs."
    )
    supersedes_event_id: str | None = Field(
        default=None,
        description="Earlier Event whose information state this occurrence supersedes.",
    )
    derived_from_event_ids: list[str] = Field(
        default_factory=list,
        description="Earlier Events from which this occurrence directly derives.",
    )
    facts: Sequence[CanonicalFact] = Field(
        min_length=1, description="Complete active Fact membership for this Event revision."
    )
    price_analysis: dict[str, Any] | None = Field(
        default=None,
        description="Read-preserved downstream price analysis; new O2 Events must use null.",
    )

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
    # Published legacy rows may retain an older label, but every new O2 wire
    # revision is constrained to the frozen first-version vocabulary.
    event_type: CanonicalEventType = Field(
        description="Frozen first-version uppercase canonical Event type for every new O2 revision."
    )
    facts: Sequence[CanonicalFactRevision] = Field(min_length=1)

    def published(self) -> CanonicalEvent:
        payload = self.model_dump(exclude={"facts"})
        payload["facts"] = [item.published().model_dump(mode="json") for item in self.facts]
        return CanonicalEvent.model_validate(payload)


class EventRetirement(StrictModel):
    event_id: str = Field(description="Base E# or same-Bundle T# being made non-active.")
    redirect_to_event_id: str = Field(
        description="Resolvable active successor Event after this Bundle is applied."
    )
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
    delta_id: str = Field(
        description="One unconsumed D# that still requires exactly one disposition."
    )
    resolution: DeltaResolution = Field(
        description="Formal residual wire field; disposition is not valid new output."
    )
    target_event_id: str | None = Field(
        default=None,
        description="Stable or same-Bundle Event target, required only for DUPLICATE_FACT.",
    )
    target_fact_id: str | None = Field(
        default=None,
        description="Stable or same-Bundle Fact target, required only for DUPLICATE_FACT.",
    )

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


class DateResolutionLedgerEntry(StrictModel):
    delta_id: str | None = None
    runtime_atomic_id: str | None = None
    runtime_package_id: str | None = None
    source_message_id: str | None = None
    candidates: list[OccurrenceDateCandidate] = Field(default_factory=list)
    selected_date: date | None = None
    selected_precision: OccurrenceTimePrecision | None = None
    semantic_role: DateSemanticRole
    status: DateResolutionStatus
    event_id: str | None = None
    fact_id: str | None = None
    subject_time: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def valid_resolution(self) -> DateResolutionLedgerEntry:
        if self.delta_id is not None and not _DELTA_ID.fullmatch(self.delta_id):
            raise ValueError("date ledger delta_id must be a D# ID")
        if self.event_id is not None and not (
            _EVENT_ID.fullmatch(self.event_id) or _TEMP_EVENT_ID.fullmatch(self.event_id)
        ):
            raise ValueError("date ledger event_id must be an E# or T# ID")
        if self.fact_id is not None and not (
            _FACT_ID.fullmatch(self.fact_id) or _TEMP_FACT_ID.fullmatch(self.fact_id)
        ):
            raise ValueError("date ledger fact_id must be an F# or TF# ID")
        if self.status is DateResolutionStatus.RESOLVED and self.selected_date is None:
            raise ValueError("RESOLVED date ledger entries require selected_date")
        if self.status is DateResolutionStatus.GENUINELY_PERIOD_WIDE:
            if self.semantic_role is not DateSemanticRole.EVENT_OCCURRENCE:
                raise ValueError("only Event occurrence may be genuinely period-wide")
            if self.event_id is None:
                raise ValueError("period-wide date ledger entries require event_id")
        if self.semantic_role is DateSemanticRole.FACT_OCCURRENCE and self.fact_id is None:
            raise ValueError("Fact occurrence ledger entries require fact_id")
        return self


class ReferenceViewDecisionLedgerEntry(StrictModel):
    event_id: str
    is_important: bool
    include_in_reference_view: bool
    reference_view_basis: ReferenceViewBasis
    note: str = Field(min_length=1)
    review_reason: ReferenceReviewReason
    as_of: datetime

    @field_validator("event_id")
    @classmethod
    def valid_event_identity(cls, value: str) -> str:
        if not (_EVENT_ID.fullmatch(value) or _TEMP_EVENT_ID.fullmatch(value)):
            raise ValueError("reference ledger event_id must be an E# or T# ID")
        return value

    @field_validator("as_of")
    @classmethod
    def frozen_clock_is_zoned(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference ledger as_of must include a timezone")
        return value

    @model_validator(mode="after")
    def basis_matches_flag(self) -> ReferenceViewDecisionLedgerEntry:
        if self.reference_view_basis.includes != self.include_in_reference_view:
            raise ValueError("reference_view_basis direction conflicts with inclusion flag")
        return self


class CanonicalRevisionBundle(StrictModel):
    contract_version: Literal[
        "event-library-foundation-v1", "event-library-maintenance-v3"
    ] = "event-library-foundation-v1"
    run_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    base_library_version: int = Field(ge=0)
    # Review-only runs intentionally carry no Delta and must not mint an empty V+1.
    delta_batch_ids: list[str] = Field(default_factory=list)
    event_revisions: list[CanonicalEventRevision] = Field(default_factory=list)
    event_retirements: list[EventRetirement] = Field(default_factory=list)
    residual_delta_resolutions: list[ResidualDeltaResolution] = Field(default_factory=list)
    reference_review_decisions: list[ReferenceReviewDecision] = Field(default_factory=list)
    date_resolution_ledger: list[DateResolutionLedgerEntry] = Field(default_factory=list)
    reference_view_decision_ledger: list[ReferenceViewDecisionLedgerEntry] = Field(
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
        date_keys = [
            (item.semantic_role, item.event_id, item.fact_id, item.delta_id)
            for item in self.date_resolution_ledger
        ]
        if len(date_keys) != len(set(date_keys)):
            raise ValueError("date_resolution_ledger entries must have unique target identities")
        reference_ids = [item.event_id for item in self.reference_view_decision_ledger]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("reference_view_decision_ledger must contain one row per Event")
        return self


class CanonicalRevisionBundleManifest(StrictModel):
    """Workspace wire manifest; Event revisions remain in separate JSON files."""

    contract_version: Literal[
        "event-library-foundation-v1", "event-library-maintenance-v3"
    ] = "event-library-foundation-v1"
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


class CandidateMapEntry(StrictModel):
    delta_ids: list[str] = Field(
        description="Assigned D# IDs represented once by this occurrence candidate."
    )
    same_occurrence_event_ids: list[str] = Field(
        default_factory=list,
        description=(
            "High-recall stable Event candidates for the same occurrence; not a final match."
        ),
    )
    related_event_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Stable Event candidates that may be related milestones rather than "
            "the same occurrence."
        ),
    )
    detail_event_ids: list[str] = Field(
        default_factory=list,
        description="Exact stable Event Details the following stage is allowed to open.",
    )


class CandidateMap(RootModel[dict[str, CandidateMapEntry]]):
    """Incremental high-recall occurrence map keyed by an analyst-readable candidate key."""

    @model_validator(mode="after")
    def valid_ids(self) -> CandidateMap:
        for key, entry in self.root.items():
            if not key.strip():
                raise ValueError("Candidate Map keys must not be empty")
            if any(not _DELTA_ID.fullmatch(item) for item in entry.delta_ids):
                raise ValueError("Candidate Map delta_ids must contain D# IDs")
            event_ids = (
                entry.same_occurrence_event_ids + entry.related_event_ids + entry.detail_event_ids
            )
            if any(not _EVENT_ID.fullmatch(item) for item in event_ids):
                raise ValueError("Candidate Map may contain stable E# IDs only")
        return self


class SurveyDeltaCatalog(RootModel[dict[str, str]]):
    """Initialization Survey assignment of each D# to one occurrence or KEEP_PENDING_* key."""

    @model_validator(mode="after")
    def valid_catalog(self) -> SurveyDeltaCatalog:
        if any(not _DELTA_ID.fullmatch(item) for item in self.root):
            raise ValueError("Survey Delta Catalog keys must be D# IDs")
        if any(not value.strip() for value in self.root.values()):
            raise ValueError("Survey Delta Catalog values must not be empty")
        return self


class WaveIndexEntry(StrictModel):
    candidate_key: str = Field(description="Survey or wave-local occurrence candidate key.")
    assigned_delta_ids: list[str] = Field(description="D# IDs accounted for by this entry.")
    draft_paths: list[str] = Field(
        default_factory=list,
        description="Workspace-relative complete Canonical Event Revision draft paths.",
    )
    unresolved_recommendation: str | None = Field(
        default=None,
        description=(
            "A concise cross-wave boundary or residual-resolution question for reconciliation."
        ),
    )


class WaveIndex(StrictModel):
    entries: list[WaveIndexEntry] = Field(
        description="Complete accounting of the wave's assigned Delta IDs."
    )

    @model_validator(mode="after")
    def valid_delta_ids(self) -> WaveIndex:
        ids = [item for entry in self.entries for item in entry.assigned_delta_ids]
        if len(ids) != len(set(ids)) or any(not _DELTA_ID.fullmatch(item) for item in ids):
            raise ValueError("Wave Index must contain unique D# IDs")
        return self


class RuntimePackageSnapshot(StrictModel):
    runtime_package_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    title: str = Field(min_length=1)
    member_runtime_atomic_ids: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    occurrence_date_candidates: list[OccurrenceDateCandidate] = Field(default_factory=list)


class FrozenRuntimeAtomic(StrictModel):
    runtime_atomic_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    proposition: str = Field(min_length=1)
    # Kept for frozen V1 readability. New snapshots put the old CDECR time meaning
    # in subject_time and never reinterpret this value as occurrence.
    time: str | None = None
    subject_time: str | None = None
    occurrence_date_candidates: list[OccurrenceDateCandidate] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    assertion_state: CanonicalAssertionState
    entities: list[str] = Field(default_factory=list)
    runtime_package_ids: list[str] = Field(default_factory=list)


class FrozenRuntimeSnapshot(StrictModel):
    contract_version: Literal[
        "event-library-foundation-v1", "frozen-runtime-time-v2"
    ] = "event-library-foundation-v1"
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
    time: str | None = None
    subject_time: str | None = None
    occurrence_date_candidates: list[OccurrenceDateCandidate] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
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
    time_anchors: list[str] = Field(
        default_factory=list, description="Legacy raw CDECR time values; never occurrence dates."
    )
    subject_time_anchors: list[str] = Field(default_factory=list)
    entity_anchors: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    occurrence_date_candidates: list[OccurrenceDateCandidate] = Field(default_factory=list)

    @field_validator("runtime_hint_id")
    @classmethod
    def package_hint_id(cls, value: str) -> str:
        if not _RUNTIME_HINT_ID.fullmatch(value):
            raise ValueError("runtime_hint_id must be an R# ID")
        return value

    @field_validator("member_delta_ids")
    @classmethod
    def package_delta_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not _DELTA_ID.fullmatch(item) for item in value):
            raise ValueError("member_delta_ids must contain unique D# IDs")
        return value


class ReferenceReviewCandidate(StrictModel):
    event_id: str
    event_type: str
    occurred_at: str
    occurrence_time_precision: OccurrenceTimePrecision
    occurrence_anchor: date | None = None
    title: str
    canonical_summary: str
    known_event_summary: str
    facts: list[CanonicalFact] = Field(default_factory=list)
    subject_horizons: list[str] = Field(default_factory=list)
    is_important: bool
    include_in_reference_view: bool
    prior_reference_view_basis: ReferenceViewBasis | None = None
    related_event_ids: list[str] = Field(default_factory=list)
    supersedes_event_id: str | None = None
    supersedes_event_ids: list[str] = Field(default_factory=list)
    superseded_by_event_ids: list[str] = Field(default_factory=list)
    frozen_as_of: datetime
    last_reviewed_at: datetime | None = None
    next_review_at: datetime | None = None
    review_mode: ReferenceReviewMode
    candidate_reason: ReferenceReviewReason


class ReferenceReviewDecision(StrictModel):
    event_id: str = Field(description="Reviewed stable or same-Bundle Event ID.")
    reviewed_at: datetime = Field(
        description="Frozen as_of clock supplied by deterministic orchestration."
    )
    review_mode: ReferenceReviewMode = Field(
        description="Deterministic explicit or implicit review classification."
    )
    candidate_reason: ReferenceReviewReason = Field(
        description="Deterministic reason this Event entered review."
    )
    changed: bool = Field(
        default=False, description="Deterministic comparison of prior and selected flags."
    )
    include_in_reference_view: bool = Field(
        description="Model-selected current Reference-view usefulness."
    )
    is_important: bool | None = Field(
        default=None,
        description="Independent importance judgment; required by maintenance-v3 Bundles.",
    )
    reference_view_basis: ReferenceViewBasis | None = Field(
        default=None,
        description="Auditable current-state basis; required by maintenance-v3 Bundles.",
    )
    next_review_at: datetime | None = Field(
        default=None, description="Deterministic 10/30/7 policy schedule after this judgment."
    )
    note: str | None = Field(
        default=None, description="Optional concise semantic rationale owned by the model."
    )


class DeltaBatch(StrictModel):
    contract_version: Literal[
        "event-library-foundation-v1",
        "event-library-maintenance-v2",
        "event-library-maintenance-v3",
    ] = "event-library-maintenance-v3"
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
            item.runtime_hint_id: set(item.member_delta_ids) for item in self.runtime_packages
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
    contract_version: Literal[
        "event-library-maintenance-v2", "event-library-maintenance-v3"
    ] = "event-library-maintenance-v3"
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
    upstream_d1_artifact_manifest_path: str | None = None
    upstream_d1_report_paths: dict[str, str] = Field(default_factory=dict)
    canonical_event_schema_path: str
    revision_bundle_schema_path: str
    schema_index_path: str = "schemas/schema_index.json"
    minimal_bundle_example_path: str = "examples/bundle"
    date_resolution_ledger_schema_path: str = "schemas/date_resolution_ledger.schema.json"
    reference_view_decision_ledger_schema_path: str = (
        "schemas/reference_view_decision_ledger.schema.json"
    )


class MUGoldExpectation(StrictModel):
    expected_event_count: int = Field(ge=0)
    expected_fact_count: int = Field(ge=0)
    expected_pending_delta_ids: list[str] = Field(default_factory=list)
    expected_dropped_delta_ids: list[str] = Field(default_factory=list)
    expected_occurrence_delta_groups: list[list[str]] = Field(default_factory=list)
    current_state_critical_event_ids: list[str] = Field(default_factory=list)
    expected_reference_event_ids: list[str] = Field(default_factory=list)
    expected_excluded_event_ids: list[str] = Field(default_factory=list)
    superseded_event_ids: list[str] = Field(default_factory=list)
    current_baseline_event_ids: list[str] = Field(default_factory=list)
    d2_current_state_required_event_ids: list[str] = Field(default_factory=list)
    d3_maintenance_change_event_ids: list[str] = Field(default_factory=list)


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
