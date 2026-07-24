"""Runtime canonical identity evidence and deterministic Atomic embedding inputs."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from enum import StrEnum

from cdecr.contracts import AtomicEvent, EventMention, ParticipantRole, StrictModel
from cdecr.field_coreference_contracts import (
    ATOMIC_OBJECT_FIELD_NAMESPACES,
    PARTICIPANT_FIELD_NAMESPACES,
    FieldNamespace,
)
from cdecr.ports import CDECRRegistry


class IdentityTrustLevel(StrEnum):
    EXTERNAL = "EXTERNAL"
    CANONICAL = "CANONICAL"
    LEGACY = "LEGACY"
    SURFACE = "SURFACE"
    UNRESOLVED = "UNRESOLVED"


class IdentityComparison(StrEnum):
    SAME = "SAME"
    DIFFERENT = "DIFFERENT"
    UNKNOWN = "UNKNOWN"


class ResolvedIdentityEvidence(StrictModel):
    field_path: str
    role: ParticipantRole | None = None
    namespace: FieldNamespace | None = None
    external_id: str | None = None
    canonical_registry_id: str | None = None
    legacy_entity_id: str | None = None
    normalized_surface: str | None = None
    trust_level: IdentityTrustLevel

    @property
    def canonical_value(self) -> str | None:
        return (
            self.external_id
            or self.canonical_registry_id
            or self.legacy_entity_id
            or self.normalized_surface
        )


class CanonicalIdentityView(StrictModel):
    canonical_participants_by_role: dict[str, list[str]]
    canonical_locations: list[str]
    canonical_named_objects: list[str]


def normalized_identity_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    return " ".join(re.sub(r"[^\w\s]", " ", normalized).split())


def resolved_identity_evidence(
    registry: CDECRRegistry, mention: EventMention
) -> list[ResolvedIdentityEvidence]:
    evidence: list[ResolvedIdentityEvidence] = []
    for index, participant in enumerate(mention.participants):
        evidence.append(
            _evidence(
                registry,
                mention,
                field_path=f"participants[{index}]",
                role=participant.role,
                legacy_entity_id=participant.entity_id,
                raw_surface=participant.surface,
            )
        )
    for index, location in enumerate(mention.locations):
        evidence.append(
            _evidence(
                registry,
                mention,
                field_path=f"locations[{index}]",
                raw_surface=location,
            )
        )
    for index, attribute in enumerate(mention.open_attributes):
        evidence.append(
            _evidence(
                registry,
                mention,
                field_path=f"open_attributes[{index}].value",
                raw_surface=attribute.value,
            )
        )
    return evidence


def compare_identity_evidence(
    left: ResolvedIdentityEvidence | None,
    right: ResolvedIdentityEvidence | None,
) -> IdentityComparison:
    if left is None or right is None:
        return IdentityComparison.UNKNOWN
    if left.external_id is not None and right.external_id is not None:
        return (
            IdentityComparison.SAME
            if left.external_id == right.external_id
            else IdentityComparison.DIFFERENT
        )
    if (
        left.canonical_registry_id is not None
        and left.canonical_registry_id == right.canonical_registry_id
    ):
        return IdentityComparison.SAME
    if left.external_id is not None or right.external_id is not None:
        return IdentityComparison.UNKNOWN
    if left.canonical_registry_id is not None or right.canonical_registry_id is not None:
        return IdentityComparison.UNKNOWN
    if (
        left.legacy_entity_id is not None
        and left.legacy_entity_id == right.legacy_entity_id
    ):
        return IdentityComparison.SAME
    if (
        left.normalized_surface
        and left.normalized_surface == right.normalized_surface
    ):
        return IdentityComparison.SAME
    return IdentityComparison.UNKNOWN


def compare_identity_groups(
    left: Sequence[ResolvedIdentityEvidence],
    right: Sequence[ResolvedIdentityEvidence],
) -> IdentityComparison:
    if not left or not right:
        return IdentityComparison.UNKNOWN
    comparisons = [
        compare_identity_evidence(left_item, right_item)
        for left_item in left
        for right_item in right
    ]
    if IdentityComparison.SAME in comparisons:
        return IdentityComparison.SAME
    if (
        all(item.external_id is not None for item in left)
        and all(item.external_id is not None for item in right)
        and comparisons
        and all(item is IdentityComparison.DIFFERENT for item in comparisons)
    ):
        return IdentityComparison.DIFFERENT
    return IdentityComparison.UNKNOWN


def canonical_identity_view(
    registry: CDECRRegistry, mentions: Sequence[EventMention]
) -> CanonicalIdentityView:
    by_role: dict[str, set[str]] = {}
    locations: set[str] = set()
    named_objects: set[str] = set()
    for mention in mentions:
        for item in resolved_identity_evidence(registry, mention):
            value = item.canonical_value
            if value is None:
                continue
            if item.namespace in PARTICIPANT_FIELD_NAMESPACES or item.role is not None:
                role = (item.role or ParticipantRole.OTHER).value
                by_role.setdefault(role, set()).add(value)
            elif item.namespace is FieldNamespace.PLACE or item.field_path.startswith("locations["):
                locations.add(value)
            elif item.namespace in ATOMIC_OBJECT_FIELD_NAMESPACES:
                named_objects.add(value)
    return CanonicalIdentityView(
        canonical_participants_by_role={
            role: sorted(values) for role, values in sorted(by_role.items())
        },
        canonical_locations=sorted(locations),
        canonical_named_objects=sorted(named_objects),
    )


def atomic_identity_text(
    event: AtomicEvent, representative_mentions: Sequence[EventMention]
) -> str:
    propositions = sorted(
        {
            mention.canonical_proposition
            for mention in representative_mentions
            if mention.mention_id in event.representative_mention_ids
        }
    )[:3]
    payload = {
        "event_version": event.version,
        "event_family": event.event_family.value,
        "identity_profile": event.identity_profile.model_dump(mode="json"),
        "time": event.time.model_dump(mode="json"),
        "assertion_state": event.assertion_state.value,
        "representative_propositions": propositions,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _evidence(
    registry: CDECRRegistry,
    mention: EventMention,
    *,
    field_path: str,
    raw_surface: str,
    role: ParticipantRole | None = None,
    legacy_entity_id: str | None = None,
) -> ResolvedIdentityEvidence:
    link = registry.get_field_link(mention.mention_id, field_path)
    root = (
        registry.resolve_field_registry_entry(link.registry_id)
        if link is not None
        else None
    )
    normalized = normalized_identity_surface(raw_surface) or None
    if root is not None and root.external_id is not None:
        trust = IdentityTrustLevel.EXTERNAL
    elif root is not None:
        trust = IdentityTrustLevel.CANONICAL
    elif legacy_entity_id is not None:
        trust = IdentityTrustLevel.LEGACY
    elif normalized is not None:
        trust = IdentityTrustLevel.SURFACE
    else:
        trust = IdentityTrustLevel.UNRESOLVED
    return ResolvedIdentityEvidence(
        field_path=field_path,
        role=role,
        namespace=None if root is None else root.namespace,
        external_id=None if root is None else root.external_id,
        canonical_registry_id=None if root is None else root.id,
        legacy_entity_id=legacy_entity_id,
        normalized_surface=normalized,
        trust_level=trust,
    )
