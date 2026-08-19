"""Contracts for Package Workflow V3 global clustering and rolling registry."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from cdecr.contracts import ExternalRelationType, MembershipRelation, StrictModel
from cdecr.parent_occurrence_contracts import ParentScope


class PackageV3OccurrenceInput(StrictModel):
    occurrence_id: str
    parent_occurrence: str = Field(min_length=1)


class PackageV3InitialCluster(StrictModel):
    mcp_id: str
    canonical: str = Field(default="", examples=["Micron fiscal Q3 2026 earnings release"])
    occurrence_ids: list[str] = Field(min_length=1)


class PackageV3InitialClusteringOutput(StrictModel):
    clusters: list[PackageV3InitialCluster] = Field(min_length=1)


class PackageV3RegistryCard(StrictModel):
    mcp_id: str
    canonical: str = Field(min_length=1)
    description: str = Field(min_length=1)


class PackageV3ExistingAssignment(StrictModel):
    mcp_id: str
    occurrence_ids: list[str] = Field(min_length=1)


class PackageV3NewMCP(StrictModel):
    canonical: str = Field(default="", examples=["Apple June 2026 device price increase"])
    occurrence_ids: list[str] = Field(min_length=1)


class PackageV3Merge(StrictModel):
    keep_mcp_id: str
    merge_mcp_ids: list[str] = Field(min_length=1)


class PackageV3RollingClusteringOutput(StrictModel):
    existing_assignments: list[PackageV3ExistingAssignment]
    new_mcps: list[PackageV3NewMCP]
    merges: list[PackageV3Merge]


class PackageV3DescriptionOccurrence(StrictModel):
    occurrence_id: str
    parent_occurrence: str = Field(min_length=1)


class PackageV3DescriptionInput(StrictModel):
    mcp_id: str
    canonical: str = Field(min_length=1)
    parent_occurrences: list[PackageV3DescriptionOccurrence] = Field(min_length=1)


class PackageV3Description(StrictModel):
    mcp_id: str
    compressed_description: str = Field(min_length=1)


class PackageV3DescriptionOutput(StrictModel):
    descriptions: list[PackageV3Description] = Field(min_length=1)


class PackageParentOccurrenceV3(StrictModel):
    registry_scope_id: str
    occurrence_id: str
    occurrence_business_key: str
    source_proposal_id: str
    parent_occurrence: str = Field(min_length=1)
    atomic_event_ids: list[str] = Field(min_length=1)
    membership_by_event: dict[str, MembershipRelation]
    document_refs: list[str] = Field(min_length=1)
    document_fingerprint: str
    published_at: str
    scope: ParentScope
    external_links: list[dict[str, object]] = Field(default_factory=list)
    source_payload_hash: str

    @model_validator(mode="after")
    def membership_covers_atomic_events(self) -> PackageParentOccurrenceV3:
        if set(self.membership_by_event) != set(self.atomic_event_ids):
            raise ValueError("occurrence membership must cover every Atomic exactly once")
        return self


class RollingMCPStateV3(StrictModel):
    mcp_id: str
    canonical: str = Field(min_length=1)
    compressed_description: str = Field(min_length=1)
    occurrence_ids: list[str] = Field(min_length=1)
    status: Literal["ACTIVE", "MERGED"] = "ACTIVE"
    redirect_to: str | None = None
    created_registry_version: int = Field(ge=1)
    updated_registry_version: int = Field(ge=1)


class FrozenRollingRegistryV3(StrictModel):
    registry_scope_id: str
    registry_version: int = Field(ge=1)
    registry_hash: str
    status: Literal["FINALIZED"] = "FINALIZED"
    mcps: list[RollingMCPStateV3]


class FrozenPackageExternalLinkV3(StrictModel):
    source_event_id: str
    target_mcp_id: str
    relation: ExternalRelationType
    supporting_document_refs: list[str] = Field(default_factory=list)


class FrozenPackageClusterV3(StrictModel):
    mcp_id: str
    canonical: str
    compressed_description: str
    occurrence_ids: list[str]
    atomic_event_ids: list[str]
    membership_by_event: dict[str, MembershipRelation]
    document_refs: list[str]
    proposal_ids: list[str]
    scope: ParentScope


class FrozenPackagePartitionV3(StrictModel):
    registry_scope_id: str
    registry_version: int = Field(ge=1)
    registry_hash: str
    partition_hash: str
    groups: list[FrozenPackageClusterV3]
    external_links: list[FrozenPackageExternalLinkV3] = Field(default_factory=list)
    affected_mcp_ids: list[str] = Field(default_factory=list)
    mcp_redirects: dict[str, str] = Field(default_factory=dict)


class PackageWorkflowV3Failure(StrictModel):
    stage: str
    batch_id: str
    error_code: str


class PackageWorkflowV3Result(StrictModel):
    status: Literal["FINALIZED", "PARTIAL_PACKAGE_REGISTRY"]
    partition: FrozenPackagePartitionV3 | None = None
    failures: list[PackageWorkflowV3Failure] = Field(default_factory=list)
    telemetry: dict[str, object] = Field(default_factory=dict)
