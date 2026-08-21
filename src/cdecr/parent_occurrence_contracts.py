"""Contracts for the set-based Parent Occurrence Package V2 stage."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from cdecr.contracts import ExternalRelationType, MembershipRelation, PackageFamily, StrictModel

ParentScope = Literal["PARENT_OCCURRENCE", "CONTINUING_MATTER"]


class AtomicDocumentSlice(StrictModel):
    """One Atomic as evidenced by one source document."""

    slice_id: str
    atomic_ref: str
    event_id: str
    document_ref: str
    document_fingerprint: str
    proposition: str
    event_family: str
    time: dict[str, object]
    period_id: str | None = None
    participants: list[str] = Field(default_factory=list)
    object_cues: list[str] = Field(default_factory=list)
    artifact_cues: list[str] = Field(default_factory=list)
    metric_cues: list[str] = Field(default_factory=list)
    institution_cues: list[str] = Field(default_factory=list)
    counterparty_cues: list[str] = Field(default_factory=list)
    market_scope: list[str] = Field(default_factory=list)
    parent_role: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=4)


class ParentContextBlock(StrictModel):
    block_ref: str
    text: str = Field(min_length=1)


class ParentInductionDocument(StrictModel):
    task_id: str
    document_ref: str
    title: str
    published_at: str
    source_name: str
    document_context: list[ParentContextBlock] = Field(min_length=1)
    atomics: list[AtomicDocumentSlice] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_refs_resolve(self) -> ParentInductionDocument:
        allowed = {item.block_ref for item in self.document_context}
        refs = {ref for atomic in self.atomics for ref in atomic.evidence_refs}
        if not refs.issubset(allowed):
            raise ValueError("Atomic evidence refs must resolve to document context blocks")
        return self


class ParentMembershipDecision(StrictModel):
    atomic_ref: str = Field(description="Copy an input Atomic ref.")
    membership_relation: MembershipRelation = Field(
        description="How the Atomic belongs to the parent."
    )


class ParentExternalLinkDecision(StrictModel):
    source_atomic_ref: str = Field(description="Atomic that has the external relation.")
    target_local_group_id: str = Field(description="Target parent group in this task.")
    relation: ExternalRelationType = Field(
        description="Relation from the Atomic to the target parent."
    )


class ParentInductionGroup(StrictModel):
    local_group_id: str = Field(description="Group ID unique within this task.")
    scope: ParentScope = Field(description="Bounded parent occurrence or continuing matter.")
    label: str = Field(description="Short distinguishing parent description.")
    members: list[ParentMembershipDecision] = Field(
        min_length=1,
        description="Atomics assigned to this parent.",
    )
    external_links: list[ParentExternalLinkDecision] = Field(
        default_factory=list,
        description="Supported relations to another parent; may be empty.",
    )

    @model_validator(mode="after")
    def unique_members(self) -> ParentInductionGroup:
        refs = [item.atomic_ref for item in self.members]
        if len(refs) != len(set(refs)):
            raise ValueError("group member refs must be unique")
        return self


class ParentInductionDecision(StrictModel):
    task_id: str = Field(description="Copy the input task ID.")
    groups: list[ParentInductionGroup] = Field(
        min_length=1,
        description="Complete partition of the input refs.",
    )

    @model_validator(mode="after")
    def unique_groups(self) -> ParentInductionDecision:
        ids = [item.local_group_id for item in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("local group IDs must be unique")
        return self


class ParentInductionBatch(StrictModel):
    decisions: list[ParentInductionDecision] = Field(
        min_length=1,
        description="One result for each input document task.",
    )

    @model_validator(mode="after")
    def unique_tasks(self) -> ParentInductionBatch:
        task_ids = [item.task_id for item in self.decisions]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("induction task IDs must be unique")
        return self


class ParentExternalLinkProposal(StrictModel):
    source_event_id: str
    target_proposal_id: str
    relation: ExternalRelationType
    document_ref: str


class ParentProposalCard(StrictModel):
    proposal_ref: str
    proposal_id: str
    supporting_proposal_ids: list[str] = Field(min_length=1)
    scope: ParentScope
    package_family: PackageFamily
    label: str
    atomic_refs: list[str] = Field(min_length=1)
    event_ids: list[str] = Field(min_length=1)
    membership_by_event: dict[str, MembershipRelation]
    document_refs: list[str] = Field(min_length=1)
    existing_package_ids: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    period_ids: list[str] = Field(default_factory=list)
    parent_role: str = "OTHER"
    object_cues: list[str] = Field(default_factory=list)
    artifact_cues: list[str] = Field(default_factory=list)
    metric_cues: list[str] = Field(default_factory=list)
    institution_cues: list[str] = Field(default_factory=list)
    counterparty_cues: list[str] = Field(default_factory=list)
    market_scope: list[str] = Field(default_factory=list)
    representative_facts: list[str] = Field(default_factory=list, max_length=6)
    embedding: list[float] = Field(default_factory=list, exclude=True)


class CanonicalParentPrototype(StrictModel):
    parent_ref: str
    package_id: str
    scope: ParentScope
    package_family: PackageFamily
    canonical_label: str
    event_ids: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    period_ids: list[str] = Field(default_factory=list)
    parent_role: str = "OTHER"
    object_cues: list[str] = Field(default_factory=list)
    artifact_cues: list[str] = Field(default_factory=list)
    institution_cues: list[str] = Field(default_factory=list)
    counterparty_cues: list[str] = Field(default_factory=list)
    market_scope: list[str] = Field(default_factory=list)
    representative_facts: list[str] = Field(default_factory=list, max_length=6)
    embedding: list[float] = Field(default_factory=list, exclude=True)


class ParentResolutionGroup(StrictModel):
    resolution_group_id: str = Field(description="Group ID unique within this response.")
    proposal_refs: list[str] = Field(
        min_length=1,
        description="Proposals that identify this parent.",
    )
    existing_parent_refs: list[str] = Field(
        default_factory=list,
        description="Prototypes that identify this parent.",
    )
    canonical_label: str = Field(description="Short distinguishing parent description.")

    @model_validator(mode="after")
    def unique_refs(self) -> ParentResolutionGroup:
        if len(self.proposal_refs) != len(set(self.proposal_refs)):
            raise ValueError("proposal refs must be unique within a group")
        if len(self.existing_parent_refs) != len(set(self.existing_parent_refs)):
            raise ValueError("prototype refs must be unique within a group")
        return self


class ParentResolutionBatch(StrictModel):
    groups: list[ParentResolutionGroup] = Field(
        min_length=1,
        description="Complete partition of the input refs.",
    )

    @model_validator(mode="after")
    def unique_groups(self) -> ParentResolutionBatch:
        ids = [item.resolution_group_id for item in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("resolution group IDs must be unique")
        proposals = [ref for item in self.groups for ref in item.proposal_refs]
        parents = [ref for item in self.groups for ref in item.existing_parent_refs]
        if len(proposals) != len(set(proposals)):
            raise ValueError("proposal refs must occur exactly once")
        if len(parents) != len(set(parents)):
            raise ValueError("prototype refs may occur at most once")
        return self


class FrozenParentGroup(StrictModel):
    group_id: str
    scope: ParentScope
    package_family: PackageFamily
    canonical_label: str
    proposal_ids: list[str] = Field(min_length=1)
    existing_package_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(min_length=1)
    membership_by_event: dict[str, MembershipRelation]
    document_refs: list[str] = Field(default_factory=list)


class FrozenParentExternalLink(StrictModel):
    source_event_id: str
    target_group_id: str
    relation: ExternalRelationType
    supporting_document_refs: list[str] = Field(default_factory=list)


class FrozenParentPartition(StrictModel):
    partition_hash: str
    snapshot_hash: str
    status: Literal["FINALIZED"] = "FINALIZED"
    groups: list[FrozenParentGroup] = Field(min_length=1)
    external_links: list[FrozenParentExternalLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_event_partition(self) -> FrozenParentPartition:
        events = [event_id for group in self.groups for event_id in group.event_ids]
        if len(events) != len(set(events)):
            raise ValueError("each Atomic must occur in exactly one frozen parent")
        group_ids = {group.group_id for group in self.groups}
        event_ids = set(events)
        for link in self.external_links:
            if link.source_event_id not in event_ids:
                raise ValueError("external link source must belong to the partition")
            if link.target_group_id not in group_ids:
                raise ValueError("external link target must be a frozen parent group")
        return self


class ParentStageFailure(StrictModel):
    stage: Literal["PARENT_INDUCE", "PARENT_RESOLVE_R1", "PARENT_RESOLVE_R2", "PARENT_RECONCILE"]
    task_id: str
    error_code: str


class ParentOccurrenceStageResult(StrictModel):
    status: Literal["FINALIZED", "PARTIAL_PARENT_RESOLUTION"]
    partition: FrozenParentPartition | None = None
    failures: list[ParentStageFailure] = Field(default_factory=list)
    telemetry: dict[str, object] = Field(default_factory=dict)
