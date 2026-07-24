"""Deterministic N11-N13 Package profile, view, embedding, and boundary helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime

from cdecr.contracts import (
    AtomicEvent,
    EventFamily,
    EventPackage,
    PackageBoundaryAction,
    PackageQualityState,
)
from cdecr.coreference_rules import (
    core_entity_ids_from_profile,
    merge_package_ranges,
    reference_period_from_profile,
    singleton_package,
)
from cdecr.cross_document_contracts import (
    PackageAnchorView,
    PackageBoundaryFinding,
    PackageCandidate,
    PackageDecisionView,
    PackageRepresentativeMember,
    PackageRetrievalSignals,
    PackageSeed,
)
from cdecr.ports import CDECRRegistry

PACKAGE_PROFILE_COMPILER_VERSION = "package-profile-compiler-v1"
PACKAGE_ASSIGNMENT_POLICY_VERSION = "package-assignment-policy-v2"
PACKAGE_BOUNDARY_POLICY_VERSION = "package-boundary-policy-v1"


def package_identity_text(package: EventPackage) -> str:
    """Return a stable identity representation without retrieval-only scores."""

    payload = {
        "package_kind": package.package_kind.value,
        "package_family": package.package_family.value,
        "package_anchor_ids": sorted(package.package_anchor_ids),
        "anchor_artifact_id": package.anchor_artifact_id,
        "anchor_entities": sorted(package.anchor_entities),
        "anchor_period_id": package.anchor_period_id,
        "time_range": package.time_range.model_dump(mode="json"),
        "lifecycle_state": package.lifecycle_state,
        "status": package.status.value,
        "quality_state": package.quality_state.value,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def package_retrieval_text(
    package: EventPackage,
    representative_members: Sequence[AtomicEvent],
) -> str:
    """Return the exact UTF-8 text whose hash identifies a Package embedding."""

    payload = {
        "identity": json.loads(package_identity_text(package)),
        "canonical_title": package.canonical_title,
        "canonical_summary": package.canonical_summary,
        "representative_member_propositions": [
            {
                "event_id": event.event_id,
                "event_family": event.event_family.value,
                "canonical_proposition": event.canonical_proposition,
            }
            for event in representative_members[:5]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def package_retrieval_hash(
    package: EventPackage,
    representative_members: Sequence[AtomicEvent],
) -> str:
    return hashlib.sha256(
        package_retrieval_text(package, representative_members).encode("utf-8")
    ).hexdigest()


def representative_package_members(
    events: Sequence[AtomicEvent],
    *,
    limit: int = 5,
) -> list[AtomicEvent]:
    """Choose stable representatives while preferring distinct identities."""

    selected: list[AtomicEvent] = []
    seen_identities: set[str] = set()
    ordered = sorted(events, key=lambda item: item.event_id)
    for event in ordered:
        identity_key = json.dumps(
            event.identity_profile.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity_key in seen_identities:
            continue
        selected.append(event)
        seen_identities.add(identity_key)
        if len(selected) == limit:
            return selected
    for event in ordered:
        if event in selected:
            continue
        selected.append(event)
        if len(selected) == limit:
            break
    return selected


class PackageProfileCompiler:
    """Compile a complete Package version from active, immutable Atomic Events."""

    def compile_singleton(self, event: AtomicEvent, seed: PackageSeed) -> EventPackage:
        return self.compile(singleton_package(event, seed), [event], seed=seed, force_version=1)

    def compile(
        self,
        root: EventPackage,
        member_events: Sequence[AtomicEvent],
        *,
        seed: PackageSeed | None = None,
        force_version: int | None = None,
    ) -> EventPackage:
        if not member_events:
            raise ValueError("Package profile requires at least one active member")
        events = sorted(
            {event.event_id: event for event in member_events}.values(),
            key=lambda item: item.event_id,
        )
        entity_ids = set(root.anchor_entities)
        periods: set[str] = set()
        time_range = root.time_range
        for event in events:
            entity_ids.update(core_entity_ids_from_profile(event.identity_profile))
            period = reference_period_from_profile(event.identity_profile)
            if period:
                periods.add(period)
            event_range = root.time_range.model_copy(
                update={"start": event.time.event_start, "end": event.time.event_end}
            )
            time_range = merge_package_ranges(time_range, event_range)
        anchor_ids = set(root.package_anchor_ids)
        artifacts = {value for value in (root.anchor_artifact_id,) if value}
        if seed is not None:
            entity_ids.update(seed.anchor_entities)
            anchor_ids.update(seed.package_anchor_ids)
            artifacts.update(seed.artifact_candidate_ids)
            if seed.anchor_artifact_id:
                artifacts.add(seed.anchor_artifact_id)
            if seed.anchor_period_id:
                periods.add(seed.anchor_period_id)
        artifact = next(iter(artifacts)) if len(artifacts) == 1 else None
        period = next(iter(periods)) if len(periods) == 1 else None
        propositions = [
            event.canonical_proposition
            for event in representative_package_members(events)
        ]
        summary = " ".join(propositions[:3]).strip()
        title = root.canonical_title or propositions[0]
        candidate = root.model_copy(
            update={
                "canonical_title": title,
                "anchor_entities": sorted(entity_ids),
                "package_anchor_ids": sorted(anchor_ids),
                "anchor_artifact_id": artifact,
                "anchor_period_id": period,
                "time_range": time_range,
                "member_event_ids": [event.event_id for event in events],
                "canonical_summary": summary,
            }
        )
        changed = candidate.model_dump(exclude={"version"}) != root.model_dump(exclude={"version"})
        version = force_version if force_version is not None else root.version + int(changed)
        return candidate.model_copy(update={"version": version})


def build_package_decision_view(
    registry: CDECRRegistry,
    candidate: PackageCandidate,
) -> PackageDecisionView:
    package = candidate.package
    anchors: list[PackageAnchorView] = []
    for canonical_id in package.package_anchor_ids:
        entry = registry.resolve_field_registry_entry(canonical_id)
        if entry is None:
            continue
        anchors.append(
            PackageAnchorView(
                canonical_id=entry.id,
                external_id=entry.external_id,
                trust="KB_EXTERNAL" if entry.external_id else "PROVISIONAL",
                canonical_text=entry.canonical_text,
            )
        )
    events = [
        event
        for event_id in package.member_event_ids
        if (event := registry.get_current_atomic_event(event_id)) is not None
    ]
    representatives = [
        PackageRepresentativeMember(
            event_id=event.event_id,
            canonical_proposition=event.canonical_proposition,
            event_family=event.event_family.value,
            identity_profile=event.identity_profile.model_dump(mode="json"),
            time=event.time.model_dump(mode="json"),
            assertion_state=event.assertion_state.value,
        )
        for event in representative_package_members(events)
    ]
    return PackageDecisionView(
        package=package,
        package_anchors=anchors,
        representative_members=representatives,
        retrieval_signals=PackageRetrievalSignals(
            routes=candidate.recall_routes,
            embedding_similarity=candidate.embedding_similarity,
        ),
    )


def canonical_package_sort_key(
    package: EventPackage,
    *,
    trusted_anchor_count: int,
    source_count: int,
) -> tuple[int, int, int, int, int, str]:
    """Lower tuple wins; Package ID is only the stable final tiebreaker."""

    artifact_period_completeness = int(package.anchor_artifact_id is not None) + int(
        package.anchor_period_id is not None
    )
    stable_profile = int(bool(package.canonical_summary.strip())) + int(
        bool(package.canonical_title.strip())
    )
    return (
        -trusted_anchor_count,
        -artifact_period_completeness,
        -stable_profile,
        -len(package.member_event_ids),
        -source_count,
        package.package_id,
    )


class PackageBoundaryGate:
    """Conservative deterministic guard for overexpanded or conflicted Packages."""

    def evaluate(
        self,
        package: EventPackage,
        member_events: Sequence[AtomicEvent],
    ) -> PackageBoundaryFinding:
        reasons: list[str] = []
        actions: list[PackageBoundaryAction] = []
        families = {event.event_family for event in member_events}
        if len(package.member_event_ids) > 25:
            reasons.append("MEMBER_COUNT_ABNORMAL_GROWTH")
        if len(package.package_anchor_ids) > 3:
            reasons.append("TOO_MANY_CANONICAL_ANCHORS")
        if package.anchor_artifact_id is None and len(package.package_anchor_ids) > 1:
            reasons.append("CONFLICTING_OR_UNRESOLVED_ARTIFACT_ANCHORS")
        reaction_families = {EventFamily.MARKET_MOVEMENT, EventFamily.ANALYST_ACTION}
        if families.intersection(reaction_families) and families.difference(reaction_families):
            reasons.append("REACTION_EVENT_INSIDE_PACKAGE_BOUNDARY")
            actions.append(PackageBoundaryAction.REMOVE_MEMBER)
        if package.package_kind.value == "BOUNDED" and len(families) > 4:
            reasons.append("BOUNDED_IDENTITY_HETEROGENEITY")
        span_days = _span_days(package.time_range.start, package.time_range.end)
        if package.package_kind.value == "BOUNDED" and span_days is not None and span_days > 120:
            reasons.append("BOUNDED_TIME_SPAN_EXCEEDED")
        if len(reasons) >= 2:
            actions.append(PackageBoundaryAction.CREATE_SPLIT_PACKAGE)
        if reasons:
            actions.extend(
                [
                    PackageBoundaryAction.FREEZE_PACKAGE,
                    PackageBoundaryAction.REBUILD_PROFILE,
                    PackageBoundaryAction.REBUILD_EMBEDDING,
                ]
            )
        quality_state = (
            PackageQualityState.QUARANTINED
            if len(reasons) >= 3
            else PackageQualityState.FROZEN
            if reasons
            else PackageQualityState.ACTIVE
        )
        return PackageBoundaryFinding(
            package_id=package.package_id,
            quality_state=quality_state,
            reasons=reasons,
            actions=list(dict.fromkeys(actions)),
        )


def _span_days(start: datetime | date | None, end: datetime | date | None) -> int | None:
    if start is None or end is None:
        return None
    start_date = start.date() if isinstance(start, datetime) else start
    end_date = end.date() if isinstance(end, datetime) else end
    return abs((end_date - start_date).days)
