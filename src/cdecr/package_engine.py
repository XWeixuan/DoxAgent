"""Deterministic N11-N13 Package profile, view, embedding, and boundary helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal

from cdecr.bulk_epoch.stage_runtime import StageReadSnapshot
from cdecr.contracts import (
    AnalystActionProjection,
    AtomicEvent,
    EventFamily,
    EventPackage,
    FinancialMetricProjection,
    GuidanceProjection,
    PackageBoundaryAction,
    PackageFamily,
    PackageQualityState,
)
from cdecr.coreference_rules import (
    core_entity_ids_from_profile,
    merge_package_ranges,
    reference_period_from_profile,
    singleton_package,
)
from cdecr.cross_document_contracts import (
    AtomicSurfaceEvidence,
    PackageAnchorView,
    PackageBoundaryFinding,
    PackageCandidate,
    PackageDecisionView,
    PackageRepresentativeMember,
    PackageRetrievalSignals,
    PackageSeed,
    SurfaceCanonicalEvidence,
    SurfaceParticipantEvidence,
)
from cdecr.field_coreference_contracts import (
    ATOMIC_OBJECT_FIELD_NAMESPACES,
    PARTICIPANT_FIELD_NAMESPACES,
    FieldNamespace,
)
from cdecr.ports import CDECRRegistry

PACKAGE_PROFILE_COMPILER_VERSION = "package-profile-compiler-v3-anchor-aggregate"
PACKAGE_ASSIGNMENT_POLICY_VERSION = "package-assignment-policy-v4"
PACKAGE_BOUNDARY_POLICY_VERSION = "package-boundary-policy-v2"
N12_PACKAGE_CARD_VERSION = "n12-package-card-v2-task-aware"

_ARTIFACT_NAMESPACES = {
    FieldNamespace.PACKAGE_ANCHOR,
    FieldNamespace.ARTIFACT_FILING,
    FieldNamespace.ARTIFACT_EARNINGS_RELEASE,
    FieldNamespace.ARTIFACT_PRESS_RELEASE,
    FieldNamespace.ARTIFACT_REPORT,
    FieldNamespace.ARTIFACT_AGREEMENT,
}


def package_identity_text(package: EventPackage) -> str:
    """Return a stable identity representation without retrieval-only scores."""

    payload = {
        "package_kind": package.package_kind.value,
        "package_family": package.package_family.value,
        "package_anchor_ids": sorted(package.package_anchor_ids),
        "primary_anchor_id": package.primary_anchor_id,
        "anchor_conflict": package.anchor_conflict,
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
    preferred_events: Sequence[AtomicEvent] = (),
) -> list[AtomicEvent]:
    """Choose stable representatives while preferring distinct identities."""

    selected: list[AtomicEvent] = []
    seen_identities: set[str] = set()

    def affinity(event: AtomicEvent) -> int:
        score = 0
        event_entities = set(core_entity_ids_from_profile(event.identity_profile))
        event_period = reference_period_from_profile(event.identity_profile)
        event_identity = event.identity_profile.model_dump(mode="json")
        for preferred in preferred_events:
            preferred_score = 0
            if event.event_family is preferred.event_family:
                preferred_score += 4
            if event.identity_profile.schema_type == preferred.identity_profile.schema_type:
                preferred_score += 2
            if event_identity == preferred.identity_profile.model_dump(mode="json"):
                preferred_score += 8
            if event_entities.intersection(
                core_entity_ids_from_profile(preferred.identity_profile)
            ):
                preferred_score += 2
            preferred_period = reference_period_from_profile(preferred.identity_profile)
            if event_period and preferred_period and event_period == preferred_period:
                preferred_score += 3
            score = max(score, preferred_score)
        return score

    ordered = sorted(events, key=lambda item: (-affinity(item), item.event_id))
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


def canonical_package_members(
    registry: CDECRRegistry,
    package: EventPackage,
) -> list[AtomicEvent]:
    """Resolve redirects and return each logical Atomic member exactly once."""

    members: dict[str, AtomicEvent] = {}
    for event_id in package.member_event_ids:
        event = registry.get_current_atomic_event(event_id)
        if event is not None:
            members[event.event_id] = event
    return [members[event_id] for event_id in sorted(members)]


def package_is_n13_repairable(package: EventPackage) -> bool:
    return package.quality_state in {
        PackageQualityState.ACTIVE,
        PackageQualityState.FROZEN,
    }


class PackageProfileCompiler:
    """Compile a complete Package version from active, immutable Atomic Events."""

    def __init__(self, registry: CDECRRegistry | None = None) -> None:
        self.registry = registry

    def compile_singleton(
        self,
        event: AtomicEvent,
        seed: PackageSeed,
        *,
        read_snapshot: StageReadSnapshot | None = None,
    ) -> EventPackage:
        return self.compile(
            singleton_package(event, seed),
            [event],
            seed=seed,
            force_version=1,
            read_snapshot=read_snapshot,
        )

    def compile(
        self,
        root: EventPackage,
        member_events: Sequence[AtomicEvent],
        *,
        seed: PackageSeed | None = None,
        force_version: int | None = None,
        read_snapshot: StageReadSnapshot | None = None,
    ) -> EventPackage:
        if not member_events:
            raise ValueError("Package profile requires at least one active member")
        events = sorted(
            {event.event_id: event for event in member_events}.values(),
            key=lambda item: item.event_id,
        )
        entity_ids: set[str] = set()
        periods: set[str] = set()
        anchor_ids: set[str] = set()
        artifacts: set[str] = set()
        time_range = root.time_range.model_copy(
            update={"start": events[0].time.event_start, "end": events[0].time.event_end}
        )
        for event in events:
            entity_ids.update(core_entity_ids_from_profile(event.identity_profile))
            period = reference_period_from_profile(event.identity_profile)
            if period and self.registry is None:
                periods.add(period)
            event_range = root.time_range.model_copy(
                update={"start": event.time.event_start, "end": event.time.event_end}
            )
            time_range = merge_package_ranges(time_range, event_range)
            if self.registry is not None or read_snapshot is not None:
                event_periods: set[str] = set()
                for mention_id in event.mention_ids:
                    mention = (
                        read_snapshot.mentions_by_id.get(mention_id)
                        if read_snapshot is not None
                        else self.registry.get_mention(mention_id)  # type: ignore[union-attr]
                    )
                    if mention is None:
                        continue
                    links = (
                        read_snapshot.field_links_by_mention.get(mention_id, ())
                        if read_snapshot is not None
                        else self.registry.list_field_links_for_mention(mention_id)  # type: ignore[union-attr]
                    )
                    for link in links:
                        entry = (
                            read_snapshot.field_entries_by_id.get(link.registry_id)
                            if read_snapshot is not None
                            else self.registry.resolve_field_registry_entry(link.registry_id)  # type: ignore[union-attr]
                        )
                        if entry is None:
                            continue
                        if entry.namespace in PARTICIPANT_FIELD_NAMESPACES:
                            entity_ids.add(entry.external_id or entry.id)
                        elif entry.namespace is FieldNamespace.FISCAL_PERIOD:
                            event_periods.add(entry.external_id or entry.id)
                        elif entry.namespace in _ARTIFACT_NAMESPACES:
                            anchor_ids.add(entry.id)
                            if entry.external_id:
                                artifacts.add(entry.external_id)
                if event_periods:
                    periods.update(event_periods)
                elif period:
                    periods.add(period)
        if seed is not None:
            # The seed is derived from the same current members. It is only a
            # fallback when a caller does not expose registry links directly.
            if self.registry is None:
                entity_ids.update(seed.anchor_entities)
                anchor_ids.update(seed.package_anchor_ids)
                artifacts.update(seed.artifact_candidate_ids)
                if seed.anchor_artifact_id:
                    artifacts.add(seed.anchor_artifact_id)
                if seed.anchor_period_id:
                    periods.add(seed.anchor_period_id)
        artifact = next(iter(artifacts)) if len(artifacts) == 1 else None
        anchor_identity_groups: dict[str, list[str]] = {}
        for anchor_id in sorted(anchor_ids):
            entry = (
                next(
                        (
                            entry
                            for entry in read_snapshot.field_entries_by_id.values()
                            if entry.id == anchor_id
                        ),
                        None,
                    )
                    if read_snapshot is not None
                    else self.registry.resolve_field_registry_entry(anchor_id)
                if self.registry is not None
                else None
            )
            identity = (
                f"external:{entry.external_id}"
                if entry is not None and entry.external_id
                else f"canonical:{entry.id if entry is not None else anchor_id}"
            )
            anchor_identity_groups.setdefault(identity, []).append(anchor_id)
        primary_anchor_id = (
            sorted(next(iter(anchor_identity_groups.values())))[0]
            if len(anchor_identity_groups) == 1
            else None
        )
        anchor_conflict = len(anchor_identity_groups) > 1
        period = next(iter(periods)) if len(periods) == 1 else None
        propositions = [
            event.canonical_proposition for event in representative_package_members(events)
        ]
        summary = " ".join(propositions[:3]).strip()
        title = propositions[0]
        candidate = root.model_copy(
            update={
                "canonical_title": title,
                "anchor_entities": sorted(entity_ids),
                "package_anchor_ids": sorted(anchor_ids),
                "primary_anchor_id": primary_anchor_id,
                "anchor_conflict": anchor_conflict,
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
    *,
    source_short_ids: dict[str, str] | None = None,
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
    events = canonical_package_members(registry, package)
    representatives = []
    for event in representative_package_members(events):
        surface_evidence, source_ids = atomic_surface_evidence(
            registry,
            event,
            source_short_ids=source_short_ids,
        )
        representatives.append(
            PackageRepresentativeMember(
                event_id=event.event_id,
                canonical_proposition=event.canonical_proposition,
                event_family=event.event_family.value,
                identity_profile=event.identity_profile.model_dump(mode="json"),
                time=event.time.model_dump(mode="json"),
                assertion_state=event.assertion_state.value,
                surface_evidence=surface_evidence,
                source_ids=source_ids,
            )
        )
    return PackageDecisionView(
        package=package,
        package_anchors=anchors,
        representative_members=representatives,
        retrieval_signals=PackageRetrievalSignals(
            routes=candidate.recall_routes,
            embedding_similarity=candidate.embedding_similarity,
        ),
    )


def build_n12_package_card(
    registry: CDECRRegistry,
    package: EventPackage,
    *,
    source_short_ids: dict[str, str] | None = None,
    representative_limit: int = 3,
    preferred_anchor_ids: Sequence[str] = (),
    preferred_events: Sequence[AtomicEvent] = (),
) -> dict[str, object]:
    """Build a bounded N12 identity card whose size does not track Package size."""

    members = canonical_package_members(registry, package)
    anchors: list[dict[str, object]] = []
    preferred_anchor_set = set(preferred_anchor_ids)
    ordered_anchor_ids = sorted(
        package.package_anchor_ids,
        key=lambda value: (
            value not in preferred_anchor_set,
            value != package.primary_anchor_id,
            value,
        ),
    )
    for canonical_id in ordered_anchor_ids:
        entry = registry.resolve_field_registry_entry(canonical_id)
        if entry is None:
            continue
        anchors.append(
            {
                "id": entry.external_id or entry.id,
                "text": entry.canonical_text,
                "trust": "KB" if entry.external_id else "PROVISIONAL",
            }
        )
    # A conflicted Package can accumulate many provisional aliases. Preserve
    # the conflict and total count while bounding the model-visible set.
    visible_anchors = anchors[:4]
    representatives: list[dict[str, object]] = []
    for event in representative_package_members(
        members,
        limit=representative_limit,
        preferred_events=preferred_events,
    ):
        surface_evidence, source_ids = atomic_surface_evidence(
            registry,
            event,
            source_short_ids=source_short_ids,
        )
        representative: dict[str, object] = {
            "proposition": event.canonical_proposition,
            "family": event.event_family.value,
            "identity": event.identity_profile.model_dump(mode="json", exclude_none=True),
        }
        time_payload = event.time.model_dump(mode="json", exclude_none=True)
        if time_payload:
            representative["time"] = time_payload
        if event.assertion_state.value != "ACTUAL":
            representative["assertion"] = event.assertion_state.value
        evidence_payload = surface_evidence.model_dump(mode="json", exclude_none=True)
        if evidence_payload:
            representative["surface_evidence"] = evidence_payload
        if source_ids:
            representative["sources"] = source_ids
        representatives.append(representative)

    card: dict[str, object] = {
        "card_version": N12_PACKAGE_CARD_VERSION,
        "kind": package.package_kind.value,
        "family": package.package_family.value,
        "label": package.canonical_summary or package.canonical_title,
        "member_count": len(members),
        "representatives": representatives,
    }
    if package.anchor_entities:
        card["entities"] = sorted(set(package.anchor_entities))
    if visible_anchors:
        card["anchors"] = visible_anchors
        card["anchor_count"] = len(anchors)
    if package.primary_anchor_id:
        primary = registry.resolve_field_registry_entry(package.primary_anchor_id)
        card["primary_anchor"] = (
            package.primary_anchor_id
            if primary is None
            else (primary.external_id or primary.id)
        )
    if package.anchor_conflict:
        card["anchor_conflict"] = True
    if package.anchor_artifact_id:
        card["artifact"] = package.anchor_artifact_id
    if package.anchor_period_id:
        card["period"] = package.anchor_period_id
    time_range = package.time_range.model_dump(mode="json", exclude_none=True)
    if time_range and not package.anchor_period_id:
        card["time_range"] = time_range
    if package.lifecycle_state:
        card["lifecycle"] = package.lifecycle_state
    if package.quality_state is not PackageQualityState.ACTIVE:
        card["quality"] = package.quality_state.value
    return card


def build_n12_event_card(
    registry: CDECRRegistry,
    event: AtomicEvent,
    *,
    source_short_ids: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build the compact incoming-Atomic definition shared by N12 tasks."""

    surface_evidence, source_ids = atomic_surface_evidence(
        registry,
        event,
        source_short_ids=source_short_ids,
    )
    card: dict[str, object] = {
        "proposition": event.canonical_proposition,
        "family": event.event_family.value,
        "identity": event.identity_profile.model_dump(mode="json", exclude_none=True),
        "mention_count": len(event.mention_ids),
    }
    time_payload = event.time.model_dump(mode="json", exclude_none=True)
    if time_payload:
        card["time"] = time_payload
    if event.assertion_state.value != "ACTUAL":
        card["assertion"] = event.assertion_state.value
    evidence_payload = surface_evidence.model_dump(mode="json", exclude_none=True)
    if evidence_payload:
        card["surface_evidence"] = evidence_payload
    if source_ids:
        card["sources"] = source_ids
    return card


def build_slim_package_view(
    registry: CDECRRegistry,
    package: EventPackage,
    *,
    source_short_ids: dict[str, str] | None = None,
    representative_limit: int = 3,
) -> dict[str, object]:
    """Build the N13 dictionary view without repeated container/profile fields."""

    events = [
        event
        for event_id in package.member_event_ids
        if (event := registry.get_current_atomic_event(event_id)) is not None
    ]
    representatives: list[dict[str, object]] = []
    for index, event in enumerate(
        representative_package_members(events, limit=representative_limit), start=1
    ):
        surface_evidence, source_ids = atomic_surface_evidence(
            registry,
            event,
            source_short_ids=source_short_ids,
        )
        surface_payload = surface_evidence.model_dump(mode="json", exclude_none=True)
        # Compact refs carry canonical identity.  Preserve the raw surfaces in
        # the evidence view but do not repeat canonical IDs there.
        for value in surface_payload.values():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, dict):
                    item.pop("canonical_ids", None)
        identity_refs: dict[str, object] = {}
        entity_refs = sorted(core_entity_ids_from_profile(event.identity_profile))
        if entity_refs:
            identity_refs["entity_refs"] = entity_refs
        period_ref = reference_period_from_profile(event.identity_profile)
        if period_ref:
            identity_refs["period_ref"] = period_ref
        if surface_evidence.artifacts and surface_evidence.artifacts.canonical_ids:
            identity_refs["artifact_refs"] = surface_evidence.artifacts.canonical_ids
        if (
            surface_evidence.object_locations
            and surface_evidence.object_locations.canonical_ids
        ):
            identity_refs["object_refs"] = (
                surface_evidence.object_locations.canonical_ids
            )
        representative: dict[str, object] = {
            "id": f"e{index}",
            "proposition": event.canonical_proposition,
            "family": event.event_family.value,
        }
        if identity_refs:
            representative["identity_refs"] = identity_refs
        time_payload = event.time.model_dump(mode="json", exclude_none=True)
        if time_payload:
            representative["time"] = time_payload
        if event.assertion_state.value != "ACTUAL":
            representative["assertion"] = event.assertion_state.value
        if surface_payload:
            representative["surface_evidence"] = surface_payload
        if source_ids:
            representative["sources"] = source_ids
        representatives.append(representative)

    payload: dict[str, object] = {
        "kind": package.package_kind.value,
        "family": package.package_family.value,
        "container_label": package.canonical_summary or package.canonical_title,
        "member_count": len(package.member_event_ids),
        "representatives": representatives,
    }
    if package.anchor_entities:
        payload["entities"] = package.anchor_entities
    if package.anchor_artifact_id:
        payload["artifact"] = package.anchor_artifact_id
    if package.anchor_period_id:
        payload["period"] = package.anchor_period_id
    time_range = package.time_range.model_dump(mode="json", exclude_none=True)
    if time_range and not package.anchor_period_id:
        payload["time_range"] = time_range
    if package.lifecycle_state:
        payload["lifecycle"] = package.lifecycle_state
    if package.quality_state is not PackageQualityState.ACTIVE:
        payload["quality"] = package.quality_state.value
    return payload


def request_local_source_ids(
    registry: CDECRRegistry,
    events: Sequence[AtomicEvent],
) -> dict[str, str]:
    """Build deterministic request-local source IDs without exposing metadata."""

    message_ids: list[str] = []
    seen: set[str] = set()
    for event in sorted(events, key=lambda item: item.event_id):
        for mention_id in event.representative_mention_ids:
            mention = registry.get_mention(mention_id)
            if mention is None or mention.message_id in seen:
                continue
            seen.add(mention.message_id)
            message_ids.append(mention.message_id)
    return {message_id: f"s{index}" for index, message_id in enumerate(message_ids, start=1)}


def atomic_surface_evidence(
    registry: CDECRRegistry,
    event: AtomicEvent,
    *,
    source_short_ids: dict[str, str] | None = None,
) -> tuple[AtomicSurfaceEvidence, list[str]]:
    """Project compact P0 raw surfaces plus resolved canonical identities."""

    participant_values: dict[str, tuple[list[str], list[str]]] = {}
    artifact_surfaces: list[str] = []
    artifact_ids: list[str] = []
    period_surfaces: list[str] = []
    period_ids: list[str] = []
    object_surfaces: list[str] = []
    object_ids: list[str] = []
    source_ids: list[str] = []

    def add(target: list[str], value: str | None) -> None:
        if value and value not in target and len(target) < 3:
            target.append(value)

    def canonical_id(mention_id: str, field_path: str) -> tuple[FieldNamespace | None, str | None]:
        link = registry.get_field_link(mention_id, field_path)
        root = registry.resolve_field_registry_entry(link.registry_id) if link is not None else None
        return (
            None if root is None else root.namespace,
            None if root is None else (root.external_id or root.id),
        )

    for mention_id in event.representative_mention_ids:
        mention = registry.get_mention(mention_id)
        if mention is None:
            continue
        if source_short_ids is not None:
            add(source_ids, source_short_ids.get(mention.message_id))
        for index, participant in enumerate(mention.participants):
            role = participant.role.value
            surfaces, canonical_ids = participant_values.setdefault(role, ([], []))
            add(surfaces, participant.surface)
            _, resolved = canonical_id(mention_id, f"participants[{index}]")
            add(canonical_ids, resolved or participant.entity_id)
        projection = mention.schema_projection
        projection_participants: list[tuple[str, str, str]] = []
        if isinstance(projection, (FinancialMetricProjection, GuidanceProjection)):
            projection_participants.append(
                ("ISSUER", "schema_projection.fields.issuer_id", projection.fields.issuer_id)
            )
            add(period_surfaces, projection.fields.period_id)
            _, resolved = canonical_id(mention_id, "schema_projection.fields.period_id")
            add(period_ids, resolved)
        elif isinstance(projection, AnalystActionProjection):
            projection_participants.extend(
                [
                    (
                        "INSTITUTION",
                        "schema_projection.fields.institution_id",
                        projection.fields.institution_id,
                    ),
                    (
                        "COMPANY",
                        "schema_projection.fields.company_id",
                        projection.fields.company_id,
                    ),
                ]
            )
        for role, field_path, raw in projection_participants:
            surfaces, canonical_ids = participant_values.setdefault(role, ([], []))
            add(surfaces, raw)
            _, resolved = canonical_id(mention_id, field_path)
            add(canonical_ids, resolved)
        if mention.time.reference_period_id:
            add(period_surfaces, mention.time.reference_period_id)
            _, resolved = canonical_id(mention_id, "time.reference_period_id")
            add(period_ids, resolved)
        if mention.local_package_hint is not None:
            add(artifact_surfaces, mention.local_package_hint.anchor)
            _, resolved = canonical_id(mention_id, "local_package_hint.anchor")
            add(artifact_ids, resolved)
        for index, location in enumerate(mention.locations):
            add(object_surfaces, location)
            _, resolved = canonical_id(mention_id, f"locations[{index}]")
            add(object_ids, resolved)
        for index, attribute in enumerate(mention.open_attributes):
            field_path = f"open_attributes[{index}].value"
            namespace, resolved = canonical_id(mention_id, field_path)
            if namespace in _ARTIFACT_NAMESPACES:
                add(artifact_surfaces, attribute.value)
                add(artifact_ids, resolved)
            elif namespace is FieldNamespace.FISCAL_PERIOD:
                add(period_surfaces, attribute.value)
                add(period_ids, resolved)
            elif namespace in ATOMIC_OBJECT_FIELD_NAMESPACES or namespace is FieldNamespace.PLACE:
                add(object_surfaces, attribute.value)
                add(object_ids, resolved)

    participants = [
        SurfaceParticipantEvidence(
            role=role,
            surfaces=surfaces,
            canonical_ids=canonical_ids,
        )
        for role, (surfaces, canonical_ids) in sorted(participant_values.items())
        if surfaces or canonical_ids
    ]

    def category(surfaces: list[str], canonical_ids: list[str]) -> SurfaceCanonicalEvidence | None:
        if not surfaces and not canonical_ids:
            return None
        return SurfaceCanonicalEvidence(
            surfaces=surfaces,
            canonical_ids=canonical_ids,
        )

    return (
        AtomicSurfaceEvidence(
            participants=participants or None,
            artifacts=category(artifact_surfaces, artifact_ids),
            periods=category(period_surfaces, period_ids),
            object_locations=category(object_surfaces, object_ids),
        ),
        source_ids,
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
        warnings: list[str] = []
        review_reasons: list[str] = []
        blocking_reasons: list[str] = []
        actions: list[PackageBoundaryAction] = []
        families = {event.event_family for event in member_events}
        if not member_events or not package.member_event_ids:
            blocking_reasons.append("EMPTY_PACKAGE")
        if len(package.member_event_ids) > 25:
            warnings.append("PACKAGE_MEMBER_COUNT_WARNING")
        trusted_artifacts: set[str] = set()
        provisional_anchors: set[str] = set()
        for anchor_id in package.package_anchor_ids:
            entry = self.registry.resolve_field_registry_entry(anchor_id) if self.registry else None
            if entry is None:
                provisional_anchors.add(anchor_id)
            elif entry.external_id:
                trusted_artifacts.add(entry.external_id)
            else:
                provisional_anchors.add(entry.id)
        if package.anchor_artifact_id:
            trusted_artifacts.add(package.anchor_artifact_id)
        if len(trusted_artifacts) > 1:
            blocking_reasons.append("CONFLICTING_TRUSTED_PARENT_ARTIFACTS")
        elif len(provisional_anchors) > 1:
            review_reasons.append("MULTIPLE_PROVISIONAL_PACKAGE_ANCHORS")
        non_market = families.difference({EventFamily.MARKET_MOVEMENT})
        if EventFamily.MARKET_MOVEMENT in families and non_market:
            review_reasons.append("MARKET_REACTION_REQUIRES_N12_REEVALUATION")
            if package.quality_state is PackageQualityState.ACTIVE:
                actions.append(PackageBoundaryAction.REMOVE_MEMBER)
        non_analyst = families.difference({EventFamily.ANALYST_ACTION})
        if (
            EventFamily.ANALYST_ACTION in families
            and non_analyst
            and package.package_family is not PackageFamily.ANALYST_REPORT
        ):
            review_reasons.append("ANALYST_REACTION_REQUIRES_N12_REEVALUATION")
            if package.quality_state is PackageQualityState.ACTIVE:
                actions.append(PackageBoundaryAction.REMOVE_MEMBER)
        span_days = _span_days(package.time_range.start, package.time_range.end)
        if package.package_kind.value == "BOUNDED" and span_days is not None and span_days > 120:
            warnings.append("PACKAGE_TIME_SPAN_WARNING")
        if blocking_reasons or review_reasons:
            actions.extend(
                [
                    PackageBoundaryAction.REBUILD_PROFILE,
                    PackageBoundaryAction.REBUILD_EMBEDDING,
                ]
            )
        severity: Literal["WARNING", "REVIEW_REQUIRED", "BLOCKING_CONFLICT"] | None
        if blocking_reasons:
            severity = "BLOCKING_CONFLICT"
            quality_state = PackageQualityState.QUARANTINED
        elif review_reasons:
            severity = "REVIEW_REQUIRED"
            quality_state = PackageQualityState.ACTIVE
        elif warnings:
            severity = "WARNING"
            quality_state = PackageQualityState.ACTIVE
        else:
            severity = None
            quality_state = PackageQualityState.ACTIVE
        return PackageBoundaryFinding(
            package_id=package.package_id,
            quality_state=quality_state,
            severity=severity,
            reasons=[*blocking_reasons, *review_reasons, *warnings],
            actions=list(dict.fromkeys(actions)),
        )

    def __init__(self, registry: CDECRRegistry | None = None) -> None:
        self.registry = registry


def _span_days(start: datetime | date | None, end: datetime | date | None) -> int | None:
    if start is None or end is None:
        return None
    start_date = start.date() if isinstance(start, datetime) else start
    end_date = end.date() if isinstance(end, datetime) else end
    return abs((end_date - start_date).days)
