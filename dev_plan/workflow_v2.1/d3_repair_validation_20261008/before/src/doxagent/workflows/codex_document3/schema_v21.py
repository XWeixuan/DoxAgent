"""V21 work contracts; deliberately independent of the active V2 contract."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schema import Document2Ref, EventLibraryRef

Text = Annotated[str, Field(min_length=1)]


class WorkModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Lead(WorkModel):
    name: Text
    lead: Text
    ref: list[Text] = Field(default_factory=list)


class Topic(WorkModel):
    name: Text
    owner: Text
    brief: Text
    ref: list[Text] = Field(default_factory=list)


class NotSelected(WorkModel):
    ref: Text
    reason: Text


class Agenda(WorkModel):
    topics: list[Topic]
    waves: list[list[Text]]
    not_selected: list[NotSelected] = Field(default_factory=list)


class ResearchResult(WorkModel):
    topic: Text
    policies: list[Text]
    notes: str
    ref: list[Text] = Field(default_factory=list)


class Relation(WorkModel):
    policies: list[Text]
    proposal: Text


class Review(WorkModel):
    relations: list[Relation]
    research_requests: list[Topic]
    coverage_notes: str


class Replacement(WorkModel):
    before: list[Text]
    after: list[Text]
    reason: Text


class Coverage(WorkModel):
    topic: Text
    policies: list[Text]
    note: str


class RemainingGap(WorkModel):
    name: Text
    reason: Text


class Consolidation(WorkModel):
    replacements: list[Replacement]
    coverage: list[Coverage]
    remaining_gaps: list[RemainingGap]
    summary: str
    publish_empty: bool = False


class Calibration(WorkModel):
    reference_state: Text
    trigger_boundary: Text


class ConditionDraftV3(WorkModel):
    condition_id: Text | None = None
    decision: Literal["LONG", "SHORT"]
    trigger_layer: Literal["OUTER", "MIDDLE", "INNER"]
    criterion: Text
    calibration: Calibration


class PolicyDraftV3(WorkModel):
    policy_id: Text | None = None
    title: Text
    ref: list[Text] = Field(default_factory=list)
    transmission: Text
    match_scope: Text
    activation_conditions: list[ConditionDraftV3] = Field(min_length=1)


class ConditionV3(ConditionDraftV3):
    condition_id: Text


class PolicyV3(PolicyDraftV3):
    policy_id: Text
    activation_conditions: list[ConditionV3] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_conditions(self):
        ids = [c.condition_id for c in self.activation_conditions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Condition ID")
        return self


class PolicySetV3(WorkModel):
    schema_version: Literal["document3.v3"] = "document3.v3"
    workflow_version: Literal["document3.v2.1"] = "document3.v2.1"
    ticker: Text
    policy_set_version: int = Field(ge=1)
    as_of: datetime
    publication_state: Literal["COMPLETE", "PARTIAL"]
    document2_ref: Document2Ref | None = None
    event_library_ref: EventLibraryRef | None = None
    policies: list[PolicyV3]
    published_at: datetime

    @field_validator("as_of", "published_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware datetime required")
        return value

    @model_validator(mode="after")
    def unique_policies(self):
        ids = [p.policy_id for p in self.policies]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Policy ID")
        return self


class MaintenancePatchV3(WorkModel):
    base_policy_set_version: int = Field(ge=1)
    event_library_ref: EventLibraryRef | None = None
    upsert_policies: list[object]  # local validation preserves unrelated upserts
    retire_policy_ids: list[Text]
    remaining_gaps: list[RemainingGap] = Field(default_factory=list)
    summary: str = ""


class TechnicalReceipt(WorkModel):
    completed: bool
    notes: str = ""


class MaintenancePatchContractV3(MaintenancePatchV3):
    upsert_policies: list[PolicyDraftV3]


class StagedHandoffV21(WorkModel):
    staged: Literal[True] = True
    policy_set_version: int
    files: dict[str, str]
    hashes: dict[str, str]
    release_manifest_path: str


class RunResultV21(WorkModel):
    orchestration_version: Literal["v2.1"] = "v2.1"
    run_id: str
    ticker: str
    mode: Literal["initialize", "maintain"]
    status: Literal["COMPLETE", "PARTIAL", "NOOP", "DEGRADED"]
    staged: Literal[True] = True
    handoff: StagedHandoffV21 | None = None
    policy_set_version: int | None = None
    missing: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def version_from_handoff(self):
        if self.handoff:
            self.policy_set_version = self.handoff.policy_set_version
        return self


WORK_SCHEMAS = {
    "lead": Lead,
    "agenda": Agenda,
    "result": ResearchResult,
    "review": Review,
    "policy": PolicyDraftV3,
    "consolidation": Consolidation,
    "patch": MaintenancePatchContractV3,
}
