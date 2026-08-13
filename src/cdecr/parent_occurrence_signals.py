"""Deterministic Parent Occurrence boundary signals and bounded candidate graph.

This module is intentionally stage-local.  It compiles already persisted facts into
an in-memory identity view; it does not call models, write Registry state, or create
Package identities.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cdecr.contracts import (
    AtomicEvent,
    EventFamily,
    EventMention,
    ParticipantRole,
    SourceMessage,
    SourceType,
)
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    CanonicalFieldRegistryEntry,
    FieldNamespace,
)


class ParentRole(StrEnum):
    DISCLOSURE = "DISCLOSURE"
    MARKET_EPISODE = "MARKET_EPISODE"
    ANALYST_REPORT = "ANALYST_REPORT"
    TRANSACTION_MATTER = "TRANSACTION_MATTER"
    OPERATIONAL_MATTER = "OPERATIONAL_MATTER"
    CONTINUING_MATTER = "CONTINUING_MATTER"
    OTHER = "OTHER"


class MergeGuardStatus(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class ProposalMergeGuardDecision:
    status: MergeGuardStatus
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParentBoundarySignature:
    role: ParentRole
    event_ids: tuple[str, ...] = ()
    issuer_ids: tuple[str, ...] = ()
    period_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    institution_ids: tuple[str, ...] = ()
    object_ids: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()
    market_scope: tuple[str, ...] = ()
    counterparty_ids: tuple[str, ...] = ()
    family_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()

    def stable_payload(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "event_ids": list(self.event_ids),
            "issuer_ids": list(self.issuer_ids),
            "period_ids": list(self.period_ids),
            "artifact_ids": list(self.artifact_ids),
            "institution_ids": list(self.institution_ids),
            "object_ids": list(self.object_ids),
            "metric_ids": list(self.metric_ids),
            "market_scope": list(self.market_scope),
            "counterparty_ids": list(self.counterparty_ids),
            "family_ids": list(self.family_ids),
            "source_ids": list(self.source_ids),
        }


@dataclass(frozen=True)
class BoundaryCompileTelemetry:
    event_count: int
    known_counts: dict[str, int]
    unknown_counts: dict[str, int]


@dataclass(frozen=True)
class CandidateEdge:
    left_ref: str
    right_ref: str
    strength: float
    routes: tuple[str, ...]
    semantic_score: float

    @property
    def key(self) -> tuple[str, str]:
        return (
            (self.left_ref, self.right_ref)
            if self.left_ref <= self.right_ref
            else (self.right_ref, self.left_ref)
        )


@dataclass(frozen=True)
class CandidateGraph:
    edges: tuple[CandidateEdge, ...]
    route_counts: dict[str, int]
    eligible_edge_count: int
    selected_edge_count: int
    truncated_edge_count: int
    semantic_pair_evaluations: int


_ARTIFACT_NAMESPACES = frozenset(
    {
        FieldNamespace.ARTIFACT_FILING,
        FieldNamespace.ARTIFACT_EARNINGS_RELEASE,
        FieldNamespace.ARTIFACT_PRESS_RELEASE,
        FieldNamespace.ARTIFACT_REPORT,
        FieldNamespace.ARTIFACT_AGREEMENT,
    }
)
_OBJECT_NAMESPACES = frozenset(
    {
        FieldNamespace.OBJECT_FACILITY,
        FieldNamespace.OBJECT_PROJECT,
        FieldNamespace.OBJECT_PRODUCT,
        FieldNamespace.OBJECT_ASSET,
        FieldNamespace.OBJECT_TECHNOLOGY,
        FieldNamespace.OBJECT_PROGRAM,
        FieldNamespace.PARTICIPANT_INSTRUMENT,
    }
)


def _ordered(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({value.strip() for value in values if value and value.strip()}))


def _field_values(event: AtomicEvent) -> dict[str, object]:
    return event.identity_profile.fields.model_dump(mode="json")


def _canonical_values(values: Sequence[str], *prefixes: str) -> list[str]:
    return [
        value
        for value in values
        if any(value.strip().upper().startswith(prefix.upper()) for prefix in prefixes)
    ]


def compile_parent_role(
    event: AtomicEvent,
    mentions: Sequence[EventMention] = (),
    *,
    has_independent_analyst_report: bool = False,
) -> ParentRole:
    family = event.event_family
    if family in {EventFamily.FINANCIAL_PERFORMANCE, EventFamily.GUIDANCE_EXPECTATION}:
        return ParentRole.DISCLOSURE
    if family is EventFamily.MARKET_MOVEMENT:
        return ParentRole.MARKET_EPISODE
    if family is EventFamily.ANALYST_ACTION:
        return (
            ParentRole.ANALYST_REPORT
            if has_independent_analyst_report
            else ParentRole.OTHER
        )
    if family is EventFamily.TRANSACTION_CAPITAL:
        return ParentRole.TRANSACTION_MATTER
    if family in {
        EventFamily.COMMERCIAL_OPERATION,
        EventFamily.PRODUCTION_SUPPLY,
        EventFamily.PRODUCT_SCIENCE,
        EventFamily.INCIDENT_GEOPOLITICAL,
    }:
        return ParentRole.OPERATIONAL_MATTER
    if event.assertion_state.value == "ONGOING":
        return ParentRole.CONTINUING_MATTER
    predicates = {item.predicate.normalized for item in mentions}
    if any(token in value for value in predicates for token in ("announce", "report", "release")):
        return ParentRole.DISCLOSURE
    if any(token in value for value in predicates for token in ("acquire", "merge", "agreement")):
        return ParentRole.TRANSACTION_MATTER
    if any(
        participant.role is ParticipantRole.COUNTERPARTY
        for mention in mentions
        for participant in mention.participants
    ):
        return ParentRole.TRANSACTION_MATTER
    return ParentRole.OTHER


def _entry_for(
    link: CanonicalFieldLink,
    entries_by_id: Mapping[str, CanonicalFieldRegistryEntry],
) -> CanonicalFieldRegistryEntry | None:
    return entries_by_id.get(link.registry_id)


def compile_parent_signatures(
    events: Sequence[AtomicEvent],
    *,
    mentions_by_id: Mapping[str, EventMention],
    field_links_by_mention: Mapping[str, Sequence[CanonicalFieldLink]],
    field_entries_by_id: Mapping[str, CanonicalFieldRegistryEntry],
    sources_by_id: Mapping[str, SourceMessage] | None = None,
) -> tuple[dict[str, ParentBoundarySignature], BoundaryCompileTelemetry]:
    """Compile stable signals using one already-loaded stage snapshot."""

    output: dict[str, ParentBoundarySignature] = {}
    dimensions = (
        "issuer",
        "period",
        "artifact",
        "institution",
        "object",
        "metric",
        "market_scope",
        "counterparty",
    )
    known = {key: 0 for key in dimensions}
    for event in sorted(events, key=lambda item: item.event_id):
        raw_fields = _field_values(event)
        issuers: list[str] = []
        periods: list[str] = []
        artifacts: list[str] = []
        institutions: list[str] = []
        objects: list[str] = []
        metrics: list[str] = []
        market: list[str] = []
        counterparties: list[str] = []
        source_ids: list[str] = []
        event_mentions = [
            mention
            for mention_id in event.mention_ids
            if (mention := mentions_by_id.get(mention_id)) is not None
        ]

        for key, raw_value in raw_fields.items():
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            rendered = [str(value) for value in values if value not in (None, "")]
            folded = key.casefold()
            if folded in {"issuer_id", "company_id", "principal_participant_ids"}:
                issuers.extend(_canonical_values(rendered, "COMPANY_", "ENTITY_"))
                institutions.extend(_canonical_values(rendered, "INSTITUTION_"))
            if "period" in folded:
                periods.extend(rendered)
            if folded in {"artifact_id", "report_id", "filing_id"}:
                artifacts.extend(_canonical_values(rendered, "ARTIFACT_", "artifact:"))
            if folded == "institution_id":
                institutions.extend(_canonical_values(rendered, "INSTITUTION_", "institution:"))
            if any(token in folded for token in ("object", "product", "instrument")):
                objects.extend(rendered)
            if "metric" in folded:
                metrics.extend(rendered)
            if "counterparty" in folded:
                counterparties.extend(rendered)
        if event.time.reference_period_id:
            periods.append(event.time.reference_period_id)

        event_date = event.time.event_start
        if event.event_family is EventFamily.MARKET_MOVEMENT and event_date is not None:
            market.append(f"date:{event_date.isoformat()[:10]}")

        for mention in event_mentions:
            source_ids.append(mention.message_id)
            source = (sources_by_id or {}).get(mention.message_id)
            if source is not None:
                # Only explicit first-party artifact source types or an explicit parent thread
                # become artifact identity. A generic NEWS URL is merely source context.
                if source.source_type in {SourceType.FILING, SourceType.ANNOUNCEMENT}:
                    artifacts.append(f"source-artifact:{source.url.strip().casefold()}")
                elif source.parent_message_id:
                    artifacts.append(f"source-parent:{source.parent_message_id}")
            for participant in mention.participants:
                identity = participant.entity_id or ""
                if participant.role is ParticipantRole.COUNTERPARTY:
                    counterparties.extend(
                        _canonical_values([identity], "COMPANY_", "ENTITY_", "INSTITUTION_")
                    )
                elif participant.role in {
                    ParticipantRole.ACTOR,
                    ParticipantRole.SUBJECT,
                    ParticipantRole.TARGET,
                    ParticipantRole.AFFECTED,
                }:
                    issuers.extend(_canonical_values([identity], "COMPANY_", "ENTITY_"))
                    institutions.extend(_canonical_values([identity], "INSTITUTION_"))
            metrics.extend(item.metric_id for item in mention.quantities)
            if mention.time.reference_period_id:
                periods.append(mention.time.reference_period_id)
            projection = mention.schema_projection
            if projection is not None:
                projection_fields = projection.fields.model_dump(mode="json")
                for key, raw_value in projection_fields.items():
                    if raw_value in (None, ""):
                        continue
                    value = str(raw_value)
                    if key in {"issuer_id", "company_id"}:
                        issuers.extend(_canonical_values([value], "COMPANY_", "ENTITY_"))
                    elif key == "period_id":
                        periods.append(value)
                    elif key == "metric_id":
                        metrics.append(value)
                    elif key == "institution_id":
                        institutions.extend(_canonical_values([value], "INSTITUTION_"))
                    elif key == "report_id":
                        artifacts.extend(_canonical_values([value], "ARTIFACT_", "artifact:"))
            for attribute in mention.open_attributes:
                key = attribute.key.casefold()
                value = attribute.value.strip()
                if any(token in key for token in ("object", "product", "instrument")):
                    objects.append(value)
                elif any(token in key for token in ("session", "market", "measure")):
                    market.append(f"{key}:{value}")
                elif "counterparty" in key:
                    counterparties.append(value)

            for link in field_links_by_mention.get(mention.mention_id, ()):
                entry = _entry_for(link, field_entries_by_id)
                namespace = entry.namespace if entry is not None else None
                identity = entry.external_id or entry.id if entry is not None else link.registry_id
                if namespace is FieldNamespace.PARTICIPANT_COMPANY:
                    issuers.append(identity)
                elif namespace is FieldNamespace.PARTICIPANT_INSTITUTION:
                    institutions.append(identity)
                elif namespace is FieldNamespace.FISCAL_PERIOD:
                    periods.append(identity)
                elif namespace is FieldNamespace.METRIC:
                    metrics.append(identity)
                elif namespace in _ARTIFACT_NAMESPACES:
                    artifacts.append(identity)
                elif namespace in _OBJECT_NAMESPACES:
                    objects.append(identity)
                    if namespace is FieldNamespace.PARTICIPANT_INSTRUMENT:
                        market.append(f"instrument:{identity}")
                else:
                    path = link.field_path.casefold()
                    if "metric" in path:
                        metrics.append(identity)
                    elif "period" in path:
                        periods.append(identity)
                    elif any(token in path for token in ("object", "product", "instrument")):
                        objects.append(identity)

        analyst_predicates = {
            mention.predicate.normalized.casefold() for mention in event_mentions
        }
        has_independent_analyst_report = bool(artifacts) or bool(
            institutions
            and any(
                token in predicate
                for predicate in analyst_predicates
                for token in (
                    "price_target",
                    "rating",
                    "upgrade",
                    "downgrade",
                    "initiate",
                    "reiterate",
                )
            )
        )
        signature = ParentBoundarySignature(
            role=compile_parent_role(
                event,
                event_mentions,
                has_independent_analyst_report=has_independent_analyst_report,
            ),
            event_ids=(event.event_id,),
            issuer_ids=_ordered(issuers),
            period_ids=_ordered(periods),
            artifact_ids=_ordered(artifacts),
            institution_ids=_ordered(institutions),
            object_ids=_ordered(objects),
            metric_ids=_ordered(metrics),
            market_scope=_ordered(market),
            counterparty_ids=_ordered(counterparties),
            family_ids=(event.event_family.value,),
            source_ids=_ordered(source_ids),
        )
        output[event.event_id] = signature
        values_by_dimension: dict[str, tuple[str, ...]] = {
            "issuer": signature.issuer_ids,
            "period": signature.period_ids,
            "artifact": signature.artifact_ids,
            "institution": signature.institution_ids,
            "object": signature.object_ids,
            "metric": signature.metric_ids,
            "market_scope": signature.market_scope,
            "counterparty": signature.counterparty_ids,
        }
        for dimension, dimension_values in values_by_dimension.items():
            known[dimension] += int(bool(dimension_values))
    return output, BoundaryCompileTelemetry(
        event_count=len(events),
        known_counts=known,
        unknown_counts={key: len(events) - value for key, value in known.items()},
    )


def aggregate_signatures(
    signatures: Sequence[ParentBoundarySignature],
) -> ParentBoundarySignature:
    if not signatures:
        return ParentBoundarySignature(role=ParentRole.OTHER)
    roles = {item.role for item in signatures if item.role is not ParentRole.OTHER}
    role = next(iter(roles)) if len(roles) == 1 else ParentRole.OTHER
    return ParentBoundarySignature(
        role=role,
        event_ids=_ordered([value for item in signatures for value in item.event_ids]),
        issuer_ids=_ordered([value for item in signatures for value in item.issuer_ids]),
        period_ids=_ordered([value for item in signatures for value in item.period_ids]),
        artifact_ids=_ordered([value for item in signatures for value in item.artifact_ids]),
        institution_ids=_ordered([value for item in signatures for value in item.institution_ids]),
        object_ids=_ordered([value for item in signatures for value in item.object_ids]),
        metric_ids=_ordered([value for item in signatures for value in item.metric_ids]),
        market_scope=_ordered([value for item in signatures for value in item.market_scope]),
        counterparty_ids=_ordered(
            [value for item in signatures for value in item.counterparty_ids]
        ),
        family_ids=_ordered([value for item in signatures for value in item.family_ids]),
        source_ids=_ordered([value for item in signatures for value in item.source_ids]),
    )


def _disjoint_known(left: Sequence[str], right: Sequence[str]) -> bool:
    return bool(left and right and set(left).isdisjoint(right))


def proposal_merge_guard(
    left: ParentBoundarySignature,
    right: ParentBoundarySignature,
) -> ProposalMergeGuardDecision:
    """Return REVIEW only for explicit proposal-level parent-boundary evidence.

    The guard never decides a split.  It routes a newly proposed union through one
    ordinary Resolution repair; missing cues, issuer, family, object, metric, and
    source differences are deliberately non-blocking.
    """

    reasons: list[str] = []
    role_pair = {left.role, right.role}
    if ParentRole.OTHER not in role_pair:
        role_reasons = {
            frozenset({ParentRole.DISCLOSURE, ParentRole.MARKET_EPISODE}),
            frozenset({ParentRole.DISCLOSURE, ParentRole.ANALYST_REPORT}),
            frozenset({ParentRole.DISCLOSURE, ParentRole.TRANSACTION_MATTER}),
            frozenset({ParentRole.TRANSACTION_MATTER, ParentRole.MARKET_EPISODE}),
            frozenset({ParentRole.ANALYST_REPORT, ParentRole.MARKET_EPISODE}),
        }
        if frozenset(role_pair) in role_reasons:
            reasons.append("explicit_parent_role_boundary")
    if _disjoint_known(left.artifact_ids, right.artifact_ids):
        reasons.append("distinct_trusted_artifact")
    if left.role is right.role is ParentRole.ANALYST_REPORT and _disjoint_known(
        left.institution_ids, right.institution_ids
    ):
        reasons.append("distinct_analyst_report")
    if left.role is right.role is ParentRole.MARKET_EPISODE:
        for prefix in ("date:", "session:"):
            left_values = [value for value in left.market_scope if value.startswith(prefix)]
            right_values = [value for value in right.market_scope if value.startswith(prefix)]
            if _disjoint_known(left_values, right_values):
                reasons.append(f"distinct_market_{prefix[:-1]}")
    return ProposalMergeGuardDecision(
        status=MergeGuardStatus.REVIEW if reasons else MergeGuardStatus.PASS,
        reasons=tuple(sorted(set(reasons))),
    )


def parent_specific_bridges(
    left: ParentBoundarySignature,
    right: ParentBoundarySignature,
) -> tuple[str, ...]:
    bridges: list[str] = []
    if set(left.artifact_ids).intersection(right.artifact_ids):
        bridges.append("artifact")
    shared_issuer = bool(set(left.issuer_ids).intersection(right.issuer_ids))
    shared_period = bool(set(left.period_ids).intersection(right.period_ids))
    if (
        left.role is right.role is ParentRole.ANALYST_REPORT
        and shared_issuer
        and set(left.institution_ids).intersection(right.institution_ids)
    ):
        bridges.append("analyst_report")
    if (
        left.role is right.role is ParentRole.TRANSACTION_MATTER
        and shared_issuer
        and (
            set(left.counterparty_ids).intersection(right.counterparty_ids)
            or set(left.object_ids).intersection(right.object_ids)
        )
    ):
        bridges.append("transaction")
    if left.role is right.role is ParentRole.MARKET_EPISODE and set(left.market_scope).intersection(
        right.market_scope
    ):
        bridges.append("market_episode")
    if (
        left.role is right.role is ParentRole.CONTINUING_MATTER
        and shared_issuer
        and set(left.object_ids).intersection(right.object_ids)
    ):
        bridges.append("continuing_matter")
    # issuer-period-role is a high-recall route, not a sufficient bridge.
    if shared_issuer and shared_period and left.role is right.role:
        bridges.append("issuer_period_role_hint")
    return tuple(bridges)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else -1.0


def simhash64(vector: Sequence[float]) -> int:
    """Fixed-seed full-vector SimHash without a heavyweight ANN dependency."""

    bits = 0
    for bit in range(64):
        total = 0.0
        for index, value in enumerate(vector):
            digest = hashlib.blake2b(f"parent-v21:{bit}:{index}".encode(), digest_size=1).digest()[
                0
            ]
            total += value if digest & 1 else -value
        if total >= 0:
            bits |= 1 << bit
    return bits


def simhash_band_keys(value: int, *, multiprobe: bool = True) -> tuple[tuple[int, int], ...]:
    keys: set[tuple[int, int]] = set()
    for band in range(8):
        byte = (value >> (band * 8)) & 0xFF
        keys.add((band, byte))
        if multiprobe:
            for bit in range(8):
                keys.add((band, byte ^ (1 << bit)))
    return tuple(sorted(keys))


def build_candidate_graph(
    refs: Sequence[str],
    *,
    signatures: Mapping[str, ParentBoundarySignature],
    embeddings: Mapping[str, Sequence[float]],
    max_structured_neighbors: int = 16,
    max_semantic_neighbors: int = 24,
    max_edges_per_ref: int = 32,
    semantic_threshold: float = 0.82,
) -> CandidateGraph:
    """Build broad soft-recall candidates; no cue is an identity verdict.

    Retrieval is bounded per route, then de-duplicated, exactly scored, and capped.
    A deterministic SimHash-neighbor fallback gives isolated proposals candidates
    without evaluating the full pair matrix.
    """

    structured_indices: dict[tuple[str, str], list[str]] = defaultdict(list)
    semantic_indices: dict[tuple[int, int], list[str]] = defaultdict(list)
    hash_by_ref: dict[str, int] = {}
    for ref in sorted(refs):
        signature = signatures[ref]
        for value in signature.event_ids:
            structured_indices[("event", value)].append(ref)
        for value in signature.artifact_ids:
            structured_indices[("artifact", value)].append(ref)
        for value in signature.institution_ids:
            structured_indices[("institution", value)].append(ref)
        for value in signature.counterparty_ids:
            structured_indices[("counterparty", value)].append(ref)
        for value in signature.object_ids:
            structured_indices[("object", value)].append(ref)
        for value in signature.market_scope:
            structured_indices[("market", value)].append(ref)
        for value in signature.source_ids:
            structured_indices[("source", value)].append(ref)
        for value in signature.issuer_ids:
            structured_indices[("issuer", value)].append(ref)
        for value in signature.period_ids:
            structured_indices[("period", value)].append(ref)
        for issuer in signature.issuer_ids:
            for family in signature.family_ids:
                structured_indices[("issuer_family", f"{issuer}|{family}")].append(ref)
        for issuer in signature.issuer_ids:
            for period in signature.period_ids:
                structured_indices[("issuer_period", f"{issuer}|{period}")].append(ref)
        for period in signature.period_ids:
            for family in signature.family_ids:
                structured_indices[("period_family", f"{period}|{family}")].append(ref)
        for value in signature.object_ids:
            for family in signature.family_ids:
                structured_indices[("object_family", f"{value}|{family}")].append(ref)
        for metric in signature.metric_ids:
            for period in signature.period_ids:
                structured_indices[("metric_period", f"{metric}|{period}")].append(ref)
        vector = embeddings.get(ref, ())
        if vector:
            hash_value = simhash64(vector)
            hash_by_ref[ref] = hash_value
            for band_key in simhash_band_keys(hash_value, multiprobe=False):
                semantic_indices[band_key].append(ref)

    route_counts: dict[str, int] = defaultdict(int)
    all_edges: dict[tuple[str, str], CandidateEdge] = {}
    semantic_evaluations = 0
    for left_ref in sorted(refs):
        left = signatures[left_ref]
        route_neighbors: dict[str, set[str]] = defaultdict(set)
        left_keys: list[tuple[str, str]] = []
        left_keys.extend(("event", value) for value in left.event_ids)
        left_keys.extend(("artifact", value) for value in left.artifact_ids)
        left_keys.extend(("institution", value) for value in left.institution_ids)
        left_keys.extend(("counterparty", value) for value in left.counterparty_ids)
        left_keys.extend(("object", value) for value in left.object_ids)
        left_keys.extend(("market", value) for value in left.market_scope)
        left_keys.extend(("source", value) for value in left.source_ids)
        left_keys.extend(
            ("issuer_family", f"{issuer}|{family}")
            for issuer in left.issuer_ids
            for family in left.family_ids
        )
        left_keys.extend(
            ("issuer_period", f"{issuer}|{period}")
            for issuer in left.issuer_ids
            for period in left.period_ids
        )
        left_keys.extend(
            ("period_family", f"{period}|{family}")
            for period in left.period_ids
            for family in left.family_ids
        )
        left_keys.extend(
            ("object_family", f"{value}|{family}")
            for value in left.object_ids
            for family in left.family_ids
        )
        left_keys.extend(
            ("metric_period", f"{metric}|{period}")
            for metric in left.metric_ids
            for period in left.period_ids
        )
        for structured_key in left_keys:
            route_neighbors[structured_key[0]].update(
                structured_indices.get(structured_key, ())
            )
        semantic_refs: set[str] = set()
        if left_ref in hash_by_ref:
            for band_key in simhash_band_keys(hash_by_ref[left_ref]):
                semantic_refs.update(semantic_indices.get(band_key, ()))
        semantic_scored: list[tuple[float, str]] = []
        for right_ref in sorted(semantic_refs - {left_ref}):
            score = cosine(embeddings.get(left_ref, ()), embeddings.get(right_ref, ()))
            semantic_evaluations += 1
            if score >= semantic_threshold:
                semantic_scored.append((score, right_ref))
        semantic_scored.sort(key=lambda item: (-item[0], item[1]))
        semantic_allowed = {ref for _, ref in semantic_scored[: max(1, max_semantic_neighbors)]}

        structured_allowed: set[str] = set()
        for route in sorted(route_neighbors):
            candidates = sorted(route_neighbors[route] - {left_ref})
            # Score before truncation so input IDs cannot decide who survives a large bucket.
            candidates.sort(
                key=lambda ref: (
                    -cosine(embeddings.get(left_ref, ()), embeddings.get(ref, ())),
                    ref,
                )
            )
            structured_allowed.update(candidates[: max(1, max_structured_neighbors)])
        candidate_refs = structured_allowed | semantic_allowed
        fallback_routes: dict[str, set[str]] = defaultdict(set)
        structured_fallback: set[str] = set()
        semantic_fallback: set[str] = set()
        if not candidate_refs:
            for issuer in left.issuer_ids:
                fallback_routes["fallback_issuer"].update(
                    structured_indices.get(("issuer", issuer), ())
                )
            for period in left.period_ids:
                fallback_routes["fallback_period"].update(
                    structured_indices.get(("period", period), ())
                )
            fallback_pool = set().union(*(values for values in fallback_routes.values())) - {
                left_ref
            }
            fallback_scored = sorted(
                (
                    cosine(embeddings.get(left_ref, ()), embeddings.get(ref, ())),
                    ref,
                )
                for ref in fallback_pool
            )
            fallback_scored.sort(key=lambda item: (-item[0], item[1]))
            structured_fallback = {ref for _, ref in fallback_scored[:4]}
            candidate_refs.update(structured_fallback)
            semantic_evaluations += len(fallback_scored)
            global_semantic = sorted(
                (
                    cosine(embeddings.get(left_ref, ()), embeddings.get(ref, ())),
                    ref,
                )
                for ref in refs
                if ref != left_ref and ref not in structured_fallback
            )
            global_semantic.sort(key=lambda item: (-item[0], item[1]))
            semantic_fallback = {ref for _, ref in global_semantic[:4]}
            candidate_refs.update(semantic_fallback)
            semantic_evaluations += len(global_semantic)
        per_left: list[CandidateEdge] = []
        for right_ref in sorted(candidate_refs):
            pair = (left_ref, right_ref) if left_ref <= right_ref else (right_ref, left_ref)
            semantic_score = cosine(embeddings.get(left_ref, ()), embeddings.get(right_ref, ()))
            routes = {
                route
                for route, values in route_neighbors.items()
                if right_ref in values
            }
            if right_ref in semantic_allowed:
                routes.add("semantic")
            if right_ref in structured_fallback:
                routes.add("fallback_issuer_time")
            if right_ref in semantic_fallback:
                routes.add("fallback_global_semantic")
            if not routes:
                routes.add("fallback")
            strength = 2.0 * len(routes) + max(0.0, semantic_score)
            edge = CandidateEdge(
                left_ref=pair[0],
                right_ref=pair[1],
                strength=strength,
                routes=tuple(sorted(routes)),
                semantic_score=semantic_score,
            )
            all_edges[pair] = max(all_edges.get(pair, edge), edge, key=lambda item: item.strength)
            per_left.append(edge)
        per_left.sort(key=lambda item: (-item.strength, item.key))
        for edge in per_left[:max_edges_per_ref]:
            for route in edge.routes:
                route_counts[route] += 1

    selected_keys: set[tuple[str, str]] = set()
    edges_by_ref: dict[str, list[CandidateEdge]] = defaultdict(list)
    for edge in all_edges.values():
        edges_by_ref[edge.left_ref].append(edge)
        edges_by_ref[edge.right_ref].append(edge)
    for ref in refs:
        ordered_edges = sorted(
            edges_by_ref.get(ref, ()), key=lambda item: (-item.strength, item.key)
        )
        selected_keys.update(item.key for item in ordered_edges[:max_edges_per_ref])
    selected = tuple(
        sorted(
            (all_edges[key] for key in selected_keys),
            key=lambda item: (-item.strength, item.key),
        )
    )
    return CandidateGraph(
        edges=selected,
        route_counts=dict(sorted(route_counts.items())),
        eligible_edge_count=len(all_edges),
        selected_edge_count=len(selected),
        truncated_edge_count=max(0, len(all_edges) - len(selected)),
        semantic_pair_evaluations=semantic_evaluations,
    )


def weighted_microcomponents(
    refs: Sequence[str],
    edges: Sequence[CandidateEdge],
    *,
    max_size: int = 24,
) -> tuple[list[list[str]], list[CandidateEdge]]:
    """Greedy bounded union; cut weak edges become the R2 bridge ledger."""

    parent = {ref: ref for ref in refs}
    members = {ref: {ref} for ref in refs}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    ledger: list[CandidateEdge] = []
    for edge in sorted(edges, key=lambda item: (-item.strength, item.key)):
        left_root, right_root = find(edge.left_ref), find(edge.right_ref)
        if left_root == right_root:
            continue
        combined = members[left_root] | members[right_root]
        if len(combined) > max_size:
            ledger.append(edge)
            continue
        parent[right_root] = left_root
        members[left_root] = combined
        del members[right_root]
    components = [sorted(value) for value in members.values() if len(value) > 1]
    components.sort(key=lambda value: (value[0], len(value)))
    return components, ledger
