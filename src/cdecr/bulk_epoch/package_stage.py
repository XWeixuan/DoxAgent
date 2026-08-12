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
from cdecr.package_projection import project_frozen_partition
from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.parent_occurrence_contracts import (
    FrozenParentPartition,
    ParentOccurrenceStageResult,
)


def resolve_parent_partition(
    *,
    service: ParentOccurrenceService,
    events: Sequence[AtomicEvent],
    mentions: Sequence[EventMention] | None,
    sources: Sequence[SourceMessage] | None,
    existing_packages: Sequence[EventPackage],
    models: Any,
    run_id: str,
    persistence_scope_id: str | None = None,
) -> ParentOccurrenceStageResult:
    """Run the one shared INDUCE -> R1 -> R2 -> RECONCILE resolver."""

    return service.run(
        events=events,
        mentions=mentions,
        sources=sources,
        existing_packages=existing_packages,
        models=models,
        run_id=run_id,
        persistence_scope_id=persistence_scope_id,
    )


def project_parent_partition(
    partition: FrozenParentPartition,
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

    return project_frozen_partition(
        partition,
        events=events,
        existing_packages=existing_packages,
        run_id=run_id,
    )
