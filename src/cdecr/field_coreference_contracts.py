"""Strict contracts for typed cross-document field coreference."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from cdecr.contracts import NonEmptyString, StrictModel


class FieldNamespace(StrEnum):
    PARTICIPANT_COMPANY = "participant.company"
    PARTICIPANT_INSTITUTION = "participant.institution"
    PARTICIPANT_PERSON = "participant.person"
    PARTICIPANT_INSTRUMENT = "participant.instrument"
    PARTICIPANT_AUTHORITY = "participant.authority"
    PARTICIPANT_UNKNOWN = "participant.unknown"
    PLACE = "place"
    OBJECT_FACILITY = "object.facility"
    OBJECT_PROJECT = "object.project"
    OBJECT_PRODUCT = "object.product"
    OBJECT_ASSET = "object.asset"
    OBJECT_TECHNOLOGY = "object.technology"
    OBJECT_PROGRAM = "object.program"
    CONCEPT_PREDICATE = "concept.predicate"
    CONCEPT_ACCOUNTING_BASIS = "concept.accounting_basis"
    CONCEPT_COMPARISON_BASIS = "concept.comparison_basis"
    CONCEPT_GUIDANCE_ACTION = "concept.guidance_action"
    CONCEPT_ANALYST_ACTION = "concept.analyst_action"
    CONCEPT_LIFECYCLE_STAGE = "concept.lifecycle_stage"
    CONCEPT_RATING = "concept.rating"
    METRIC = "metric"
    FISCAL_PERIOD = "fiscal_period"
    ARTIFACT_FILING = "artifact.filing"
    ARTIFACT_EARNINGS_RELEASE = "artifact.earnings_release"
    ARTIFACT_PRESS_RELEASE = "artifact.press_release"
    ARTIFACT_REPORT = "artifact.report"
    ARTIFACT_AGREEMENT = "artifact.agreement"
    PACKAGE_ANCHOR = "package_anchor"


PARTICIPANT_FIELD_NAMESPACES = frozenset(
    {
        FieldNamespace.PARTICIPANT_COMPANY,
        FieldNamespace.PARTICIPANT_INSTITUTION,
        FieldNamespace.PARTICIPANT_PERSON,
        FieldNamespace.PARTICIPANT_INSTRUMENT,
        FieldNamespace.PARTICIPANT_AUTHORITY,
    }
)

ATOMIC_OBJECT_FIELD_NAMESPACES = frozenset(
    {
        FieldNamespace.OBJECT_FACILITY,
        FieldNamespace.OBJECT_PROJECT,
        FieldNamespace.OBJECT_PRODUCT,
        FieldNamespace.OBJECT_ASSET,
        FieldNamespace.OBJECT_TECHNOLOGY,
        FieldNamespace.OBJECT_PROGRAM,
    }
)

ATOMIC_FIELD_RECALL_NAMESPACES = frozenset({FieldNamespace.PLACE, *ATOMIC_OBJECT_FIELD_NAMESPACES})


class FieldLinkMethod(StrEnum):
    EXTERNAL_LINKING = "EXTERNAL_LINKING"
    INTERNAL_COREFERENCE = "INTERNAL_COREFERENCE"
    UNRESOLVED_CANONICALIZED = "UNRESOLVED_CANONICALIZED"


class FieldDecision(StrEnum):
    LINK = "LINK"
    NEW = "NEW"
    UNRESOLVED = "UNRESOLVED"


class CanonicalFieldRegistryEntry(StrictModel):
    id: NonEmptyString
    namespace: FieldNamespace
    canonical_text: NonEmptyString
    aliases: list[NonEmptyString]
    external_id: str | None = None
    redirect_to: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> CanonicalFieldRegistryEntry:
        if self.redirect_to == self.id:
            raise ValueError("field registry entry cannot redirect to itself")
        folded = [alias.casefold() for alias in self.aliases]
        if len(folded) != len(set(folded)):
            raise ValueError("field registry aliases must be unique")
        return self


class CanonicalFieldLink(StrictModel):
    mention_id: NonEmptyString
    field_path: NonEmptyString
    registry_id: NonEmptyString
    method: FieldLinkMethod


class FieldCoreferenceHints(StrictModel):
    source_ticker: str | None = None
    issuer_id: str | None = None
    participant_role: str | None = None
    attribute_key: str | None = None
    published_date: str | None = None
    attempted_kb_type: str | None = None
    source_fingerprint: str | None = None
    evidence_group_hash: str | None = None
    parent_identity_key: str | None = None


class FieldCoreferenceInput(StrictModel):
    namespace: FieldNamespace
    raw_value: NonEmptyString
    local_context: NonEmptyString
    hints: FieldCoreferenceHints = Field(default_factory=FieldCoreferenceHints)


class FieldCoreferenceCandidate(StrictModel):
    canonical_id: NonEmptyString
    aliases: list[NonEmptyString] = Field(min_length=1)
    hard_dimensions: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    namespace: FieldNamespace | None = None


class FieldCoreferenceModelOutput(StrictModel):
    decision: FieldDecision
    canonical_id: str | None = None
    target_namespace: FieldNamespace | None = None

    @model_validator(mode="after")
    def validate_target(self) -> FieldCoreferenceModelOutput:
        if self.decision is FieldDecision.LINK and not self.canonical_id:
            raise ValueError("LINK requires canonical_id")
        if self.decision is not FieldDecision.LINK and self.canonical_id is not None:
            raise ValueError("NEW and UNRESOLVED must not include canonical_id")
        if self.target_namespace is not None and self.decision is not FieldDecision.NEW:
            raise ValueError("target_namespace is allowed only for NEW")
        return self


class FieldBatchWireDecision(StrictModel):
    """Request-local wire item; semantic constraints are validated per task locally."""

    task_id: NonEmptyString
    decision: FieldDecision
    canonical_id: str | None = None
    target_namespace: str | None = None


class FieldBatchWireOutput(StrictModel):
    decisions: list[FieldBatchWireDecision]


class FieldCoreferenceResult(StrictModel):
    canonical_id: str | None = None
    external_id: str | None = None
    resolution_method: FieldLinkMethod | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> FieldCoreferenceResult:
        if (self.canonical_id is None) != (self.resolution_method is None):
            raise ValueError("canonical_id and resolution_method must be set together")
        if self.external_id is not None and self.canonical_id is None:
            raise ValueError("external_id requires canonical_id")
        return self
