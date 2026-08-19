"""Deterministic projection of one frozen parent partition into Package state."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence

from cdecr.contracts import (
    AtomicEvent,
    EventPackage,
    MembershipRelation,
    PackageExternalRelation,
    PackageFamily,
    PackageKind,
    PackageMembership,
    PackageQualityState,
    PackageStatus,
    PackageTimeRange,
)
from cdecr.cross_document_contracts import PackageAssignmentRecord
from cdecr.package_v3_contracts import FrozenPackageClusterV3, FrozenPackagePartitionV3
from cdecr.parent_occurrence_contracts import FrozenParentGroup, FrozenParentPartition

PACKAGE_ASSIGNMENT_POLICY_VERSION = "parent-occurrence-v2.0r"
PACKAGE_ASSIGNMENT_POLICY_VERSION_V3 = "package-global-registry-v3"


def _derived_family(event: AtomicEvent) -> PackageFamily:
    value = event.event_family.value
    if value in {"FINANCIAL_PERFORMANCE", "GUIDANCE_EXPECTATION"}:
        return PackageFamily.EARNINGS_DISCLOSURE
    if value == "ANALYST_ACTION":
        return PackageFamily.ANALYST_REPORT
    if value == "TRANSACTION_CAPITAL":
        return PackageFamily.TRANSACTION
    if value == "REGULATORY_LEGAL_POLICY":
        return PackageFamily.REGULATORY_LEGAL
    if value == "INCIDENT_GEOPOLITICAL":
        return PackageFamily.OPERATIONAL_INCIDENT
    if value == "PRODUCT_SCIENCE":
        return PackageFamily.PRODUCT_SCIENCE
    return PackageFamily.OTHER


def _stable_id(prefix: str, value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _event_entities(event: AtomicEvent) -> set[str]:
    values: set[str] = set()
    fields = event.identity_profile.fields
    raw = fields.model_dump(mode="json")
    for key, value in raw.items():
        if not (key.endswith("_id") or key.endswith("_ids")):
            continue
        if isinstance(value, str) and value:
            values.add(value)
        elif isinstance(value, list):
            values.update(str(item) for item in value if item)
    return values


def _time_range(events: Sequence[AtomicEvent]) -> PackageTimeRange:
    starts = [value for item in events if (value := item.time.event_start) is not None]
    ends = [value for item in events if (value := item.time.event_end) is not None]
    start = min(starts, key=lambda value: value.isoformat()) if starts else None
    end = max(ends, key=lambda value: value.isoformat()) if ends else None
    return PackageTimeRange(start=start, end=end)


def _package_for_group(
    group: FrozenParentGroup,
    *,
    partition_hash: str,
    events_by_id: Mapping[str, AtomicEvent],
    existing_by_id: Mapping[str, EventPackage],
) -> EventPackage:
    existing_ids = sorted(value for value in group.existing_package_ids if value in existing_by_id)
    base = existing_by_id[existing_ids[0]] if existing_ids else None
    package_id = (
        base.package_id
        if base is not None
        else _stable_id("package", {"partition": partition_hash, "group": group.group_id})
    )
    events = [events_by_id[event_id] for event_id in group.event_ids]
    package_family = Counter(_derived_family(event) for event in events).most_common(1)[0][0]
    entities = sorted({value for event in events for value in _event_entities(event)})
    summary = "; ".join(dict.fromkeys(item.canonical_proposition for item in events))[:2000]
    return EventPackage(
        package_id=package_id,
        package_kind=(
            PackageKind.EPISODE if group.scope == "CONTINUING_MATTER" else PackageKind.BOUNDED
        ),
        package_family=package_family,
        canonical_title=group.canonical_label,
        anchor_entities=entities,
        parent_scope=group.scope,
        supporting_proposal_ids=sorted(group.proposal_ids),
        supporting_document_refs=sorted(group.document_refs),
        partition_hash=partition_hash,
        time_range=_time_range(events),
        lifecycle_state=None,
        member_event_ids=sorted(group.event_ids),
        canonical_summary=summary or group.canonical_label,
        status=PackageStatus.UNKNOWN,
        quality_state=PackageQualityState.ACTIVE,
        version=(base.version + 1 if base is not None else 1),
    )


def project_frozen_partition(
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
    """Build the only membership mutation plan for a finalized partition."""

    events_by_id = {item.event_id: item for item in events}
    existing_by_id = {item.package_id: item for item in existing_packages}
    expected = set(events_by_id)
    actual = {event_id for group in partition.groups for event_id in group.event_ids}
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"frozen parent coverage mismatch missing={missing} unknown={unknown}")

    packages: list[EventPackage] = []
    memberships: list[PackageMembership] = []
    assignments: list[PackageAssignmentRecord] = []
    external_relations: list[PackageExternalRelation] = []
    redirects: list[tuple[str, str]] = []
    package_by_group: dict[str, EventPackage] = {}
    for group in sorted(partition.groups, key=lambda item: item.group_id):
        package = _package_for_group(
            group,
            partition_hash=partition.partition_hash,
            events_by_id=events_by_id,
            existing_by_id=existing_by_id,
        )
        packages.append(package)
        package_by_group[group.group_id] = package
        for old_id in sorted(group.existing_package_ids):
            if old_id != package.package_id:
                redirects.append((old_id, package.package_id))
        for event_id in sorted(group.event_ids):
            relation = group.membership_by_event.get(event_id, MembershipRelation.COMPONENT_OF)
            membership = PackageMembership(
                membership_id=_stable_id(
                    "membership-v2",
                    {
                        "event": event_id,
                        "package": package.package_id,
                        "partition": partition.partition_hash,
                    },
                ),
                event_id=event_id,
                package_id=package.package_id,
                relation=relation,
            )
            memberships.append(membership)
            assignments.append(
                PackageAssignmentRecord(
                    assignment_id=_stable_id(
                        "package-assignment-v2",
                        {
                            "run": run_id,
                            "event": event_id,
                            "partition": partition.partition_hash,
                        },
                    ),
                    run_id=run_id,
                    event_id=event_id,
                    resulting_package_id=package.package_id,
                    membership_relation=relation,
                    supporting_proposal_ids=sorted(group.proposal_ids),
                    partition_hash=partition.partition_hash,
                    reason="PARENT_OCCURRENCE_V2_0R_FROZEN_PARTITION",
                )
            )
    for link in partition.external_links:
        target = package_by_group[link.target_group_id]
        external_relations.append(
            PackageExternalRelation(
                relation_id=_stable_id(
                    "package-external-v2",
                    {
                        "source": link.source_event_id,
                        "target": target.package_id,
                        "relation": link.relation.value,
                        "partition": partition.partition_hash,
                    },
                ),
                source_event_id=link.source_event_id,
                target_package_id=target.package_id,
                relation=link.relation,
            )
        )
    return packages, memberships, assignments, external_relations, redirects


def _package_for_v3_group(
    group: FrozenPackageClusterV3,
    *,
    registry_scope_id: str,
    partition_hash: str,
    events_by_id: Mapping[str, AtomicEvent],
    existing_by_id: Mapping[str, EventPackage],
    affected: bool,
) -> EventPackage:
    package_id = _stable_id(
        "package-v3", {"scope": registry_scope_id, "mcp_id": group.mcp_id}
    )
    base = existing_by_id.get(package_id)
    if base is not None and not affected:
        return base
    events = [events_by_id[event_id] for event_id in group.atomic_event_ids]
    package_family = Counter(_derived_family(event) for event in events).most_common(1)[0][0]
    entities = sorted({value for event in events for value in _event_entities(event)})
    return EventPackage(
        package_id=package_id,
        package_kind=(
            PackageKind.EPISODE if group.scope == "CONTINUING_MATTER" else PackageKind.BOUNDED
        ),
        package_family=package_family,
        canonical_title=group.canonical,
        anchor_entities=entities,
        parent_scope=group.scope,
        supporting_proposal_ids=sorted(group.proposal_ids),
        supporting_document_refs=sorted(group.document_refs),
        partition_hash=partition_hash,
        time_range=_time_range(events),
        lifecycle_state=None,
        member_event_ids=sorted(group.atomic_event_ids),
        canonical_summary=group.compressed_description,
        status=PackageStatus.UNKNOWN,
        quality_state=PackageQualityState.ACTIVE,
        version=(base.version + 1 if base is not None else 1),
    )


def project_frozen_partition_v3(
    partition: FrozenPackagePartitionV3,
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
    """Project a finalized MCP Registry without adding Package identity decisions."""

    events_by_id = {item.event_id: item for item in events}
    expected = set(events_by_id)
    actual = {event_id for group in partition.groups for event_id in group.atomic_event_ids}
    if expected != actual or sum(len(item.atomic_event_ids) for item in partition.groups) != len(
        actual
    ):
        raise ValueError(
            "Package V3 partition must assign every active Atomic to exactly one Package"
        )
    existing_by_id = {item.package_id: item for item in existing_packages}
    affected_mcp_ids = set(partition.affected_mcp_ids)
    package_by_mcp: dict[str, EventPackage] = {}
    packages: list[EventPackage] = []
    memberships: list[PackageMembership] = []
    assignments: list[PackageAssignmentRecord] = []
    for group in sorted(partition.groups, key=lambda item: item.mcp_id):
        package = _package_for_v3_group(
            group,
            registry_scope_id=partition.registry_scope_id,
            partition_hash=partition.partition_hash,
            events_by_id=events_by_id,
            existing_by_id=existing_by_id,
            affected=(group.mcp_id in affected_mcp_ids),
        )
        package_by_mcp[group.mcp_id] = package
        packages.append(package)
        for event_id in sorted(group.atomic_event_ids):
            relation = group.membership_by_event.get(
                event_id, MembershipRelation.COMPONENT_OF
            )
            membership = PackageMembership(
                membership_id=_stable_id(
                    "membership-v3",
                    {
                        "event": event_id,
                        "package": package.package_id,
                        "partition": partition.partition_hash,
                    },
                ),
                event_id=event_id,
                package_id=package.package_id,
                relation=relation,
            )
            memberships.append(membership)
            assignments.append(
                PackageAssignmentRecord(
                    assignment_id=_stable_id(
                        "package-assignment-v3",
                        {
                            "run": run_id,
                            "event": event_id,
                            "partition": partition.partition_hash,
                        },
                    ),
                    run_id=run_id,
                    event_id=event_id,
                    resulting_package_id=package.package_id,
                    membership_relation=relation,
                    supporting_proposal_ids=sorted(group.proposal_ids),
                    partition_hash=partition.partition_hash,
                    reason="PACKAGE_GLOBAL_REGISTRY_V3_FROZEN_PARTITION",
                )
            )
    external_relations = [
        PackageExternalRelation(
            relation_id=_stable_id(
                "package-external-v3",
                {
                    "source": item.source_event_id,
                    "target": package_by_mcp[item.target_mcp_id].package_id,
                    "relation": item.relation.value,
                    "partition": partition.partition_hash,
                },
            ),
            source_event_id=item.source_event_id,
            target_package_id=package_by_mcp[item.target_mcp_id].package_id,
            relation=item.relation,
        )
        for item in partition.external_links
        if item.target_mcp_id in package_by_mcp
    ]
    redirects = [
        (
            _stable_id(
                "package-v3",
                {"scope": partition.registry_scope_id, "mcp_id": source_mcp_id},
            ),
            _stable_id(
                "package-v3",
                {"scope": partition.registry_scope_id, "mcp_id": target_mcp_id},
            ),
        )
        for source_mcp_id, target_mcp_id in sorted(partition.mcp_redirects.items())
    ]
    return packages, memberships, assignments, external_relations, redirects
