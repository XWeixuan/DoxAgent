"""Parent Occurrence Package V2 bulk-stage facade.

This module intentionally exposes only set-level parent resolution and deterministic projection.
Legacy pair-based Package stages have no entry point here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
    PackageExternalRelation,
    PackageMembership,
    SourceMessage,
)
from cdecr.cross_document_contracts import PackageAssignmentRecord
from cdecr.package_global_clustering import PackageWorkflowV3Service
from cdecr.package_projection import project_frozen_partition, project_frozen_partition_v3
from cdecr.package_v3_contracts import FrozenPackagePartitionV3, PackageWorkflowV3Result
from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.parent_occurrence_contracts import (
    FrozenParentPartition,
)


def resolve_parent_partition(
    *,
    service: ParentOccurrenceService,
    package_service: PackageWorkflowV3Service,
    events: Sequence[AtomicEvent],
    mentions: Sequence[EventMention] | None,
    sources: Sequence[SourceMessage] | None,
    existing_packages: Sequence[EventPackage],
    models: Any,
    run_id: str,
    persistence_scope_id: str | None = None,
) -> PackageWorkflowV3Result:
    """Run retained document induction, then V3 global Package clustering."""

    pool = service.build_parent_occurrence_pool(
        events=events,
        mentions=mentions,
        sources=sources,
        existing_packages=existing_packages,
        models=models,
        run_id=run_id,
        persistence_scope_id=persistence_scope_id,
    )
    if pool.status != "FINALIZED":
        return PackageWorkflowV3Result(
            status="PARTIAL_PACKAGE_REGISTRY",
            failures=[],
            telemetry=pool.telemetry or {},
        )
    result = package_service.run(
        events=events,
        proposals=pool.proposals,
        external_links=pool.external_links,
        models=models,
        run_id=run_id,
        registry_scope_id=persistence_scope_id or run_id,
    )
    return result.model_copy(
        update={"telemetry": {**(pool.telemetry or {}), **result.telemetry}}
    )


def project_parent_partition(
    partition: FrozenParentPartition | FrozenPackagePartitionV3,
    *,
    events: Sequence[AtomicEvent],
    existing_packages: Sequence[EventPackage],
    run_id: str,
) -> tuple[
    list[EventPackage],
    list[PackageMembership],
    list[PackageAssignmentRecord],
    list[PackageExternalRelation],
    list[tuple[str, str]],
]:
    """Project one frozen partition; this function performs no semantic decisions."""

    if isinstance(partition, FrozenPackagePartitionV3):
        return project_frozen_partition_v3(
            partition,
            events=events,
            existing_packages=existing_packages,
            run_id=run_id,
        )
    return project_frozen_partition(
        partition,
        events=events,
        existing_packages=existing_packages,
        run_id=run_id,
    )
