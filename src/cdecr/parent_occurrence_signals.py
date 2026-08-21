"""Deterministic Parent Occurrence boundary signals for document-local induction.

This module is intentionally stage-local.  It compiles already persisted facts into
an in-memory identity view; it does not call models, write Registry state, or create
Package identities.
"""

from __future__ import annotations

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
