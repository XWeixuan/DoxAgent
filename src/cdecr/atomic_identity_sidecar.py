"""Deterministic three-axis Atomic identity sidecars and conservative N7 ranking."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from enum import IntEnum

from pydantic import Field

from cdecr.atomic_identity_contracts import (
    ATOMIC_IDENTITY_SIDECAR_VERSION,
    AtomicIdentityAdapterKind,
    AtomicIdentitySidecar,
    IdentityAxis,
    IdentityAxisVerdict,
)
from cdecr.contracts import (
    AnalystActionIdentityProfile,
    EventFamily,
    EventMention,
    FinancialMetricIdentityProfile,
    GuidanceIdentityProfile,
    IdentityProfile,
    OpenIdentityProfile,
    QuantityRole,
    StrictModel,
)
from cdecr.cross_document_contracts import AtomicCandidate, RecallRoute
from cdecr.ports import CDECRRegistry, DecisionAuditRecord

ATOMIC_RECALL_RANKER_VERSION = "atomic-recall-ranker-v2-shadow"
_UNSET_METRIC = object()


class AtomicRecallRankBand(IntEnum):
    FULL_IDENTITY = 0
    PARTIAL_IDENTITY = 1
    SEMANTIC_CORROBORATED = 2
    BROAD_RECALL = 3
    AUDIT_TAIL = 4


class AtomicAxisComparison(StrictModel):
    matched_axes: list[IdentityAxis] = Field(default_factory=list)
    conflicted_axes: list[IdentityAxis] = Field(default_factory=list)
    ambiguous_axes: list[IdentityAxis] = Field(default_factory=list)


class AtomicRankedCandidate(StrictModel):
    candidate: AtomicCandidate
    candidate_root_id: str
    adapter_kind: AtomicIdentityAdapterKind
    rank_band: AtomicRecallRankBand
    matched_axes: list[IdentityAxis] = Field(default_factory=list)
    conflicted_axes: list[IdentityAxis] = Field(default_factory=list)
    ambiguous_axes: list[IdentityAxis] = Field(default_factory=list)
    independent_evidence_groups: int = Field(ge=0)
    raw_embedding_similarity: float | None = None
    sidecar_signature_hash: str


def compile_atomic_identity_sidecar(
    mention: EventMention,
    profile: IdentityProfile,
    *,
    primary_metric_id: str | None | object = _UNSET_METRIC,
    principal_company_ids: Sequence[str] = (),
) -> AtomicIdentitySidecar:
    adapter = _adapter_kind(mention, profile)
    referent: list[str] = []
    occurrence: list[str] = []
    facet: list[str] = []
    resolved_primary_metric_id = (
        profile.fields.metric_id
        if primary_metric_id is _UNSET_METRIC
        and isinstance(profile, (FinancialMetricIdentityProfile, GuidanceIdentityProfile))
        else primary_metric_id
        if isinstance(primary_metric_id, str)
        else None
    )

    if isinstance(profile, FinancialMetricIdentityProfile):
        referent = [f"issuer:{profile.fields.issuer_id}"]
        occurrence = [
            f"state:{mention.assertion_state.value}",
            f"period:{profile.fields.period_id}",
        ]
        facet = [
            *_trusted_metric_facets(resolved_primary_metric_id),
            f"basis:{profile.fields.accounting_basis.value}",
            f"comparison:{profile.fields.comparison_basis.value}",
        ]
    elif isinstance(profile, GuidanceIdentityProfile):
        referent = [f"issuer:{profile.fields.issuer_id}"]
        occurrence = [
            f"state:{mention.assertion_state.value}",
            f"period:{profile.fields.period_id}",
            f"guidance_action:{profile.fields.action.value}",
        ]
        facet = _trusted_metric_facets(resolved_primary_metric_id)
    elif isinstance(profile, AnalystActionIdentityProfile):
        referent = [
            f"institution:{profile.fields.institution_id}",
            f"company:{profile.fields.company_id}",
        ]
        occurrence = [
            f"action:{profile.fields.action.value}",
            *(
                [f"report:{profile.fields.report_id}"]
                if profile.fields.report_id
                else [f"date:{profile.fields.report_date.isoformat()}"]
                if profile.fields.report_date
                else []
            ),
        ]
        facet = [_analyst_facet(profile)]
    elif isinstance(profile, OpenIdentityProfile):
        referent = [
            f"participant:{value}"
            for value in profile.fields.principal_participant_ids
        ]
        occurrence = _open_occurrence(mention, profile)
        facet = _open_facets(
            mention,
            adapter,
            primary_metric_id=resolved_primary_metric_id,
        )

    referent.extend(f"company:{value}" for value in principal_company_ids)

    return _sidecar(
        adapter_kind=adapter,
        referent=referent,
        occurrence=occurrence,
        facet=facet,
    )


def combine_atomic_identity_sidecars(
    sidecars: Iterable[AtomicIdentitySidecar],
) -> AtomicIdentitySidecar:
    values = list(sidecars)
    if not values:
        return _sidecar(adapter_kind=AtomicIdentityAdapterKind.GENERIC_OPEN)
    adapter = (
        values[0].adapter_kind
        if all(item.adapter_kind is values[0].adapter_kind for item in values)
        else AtomicIdentityAdapterKind.GENERIC_OPEN
    )
    return _sidecar(
        adapter_kind=adapter,
        referent=(value for item in values for value in item.referent),
        occurrence=(value for item in values for value in item.occurrence),
        facet=(value for item in values for value in item.facet),
    )


def compare_atomic_identity_sidecars(
    incoming: AtomicIdentitySidecar,
    candidate: AtomicIdentitySidecar,
) -> AtomicAxisComparison:
    matched: list[IdentityAxis] = []
    conflicted: list[IdentityAxis] = []
    ambiguous: list[IdentityAxis] = []
    for axis in incoming.applicable_axes:
        incoming_values = incoming.values(axis)
        candidate_values = candidate.values(axis)
        if not candidate_values:
            ambiguous.append(axis)
        else:
            incoming_by_key = _values_by_key(incoming_values)
            candidate_by_key = _values_by_key(candidate_values)
            shared_keys = set(incoming_by_key).intersection(candidate_by_key)
            if any(
                not incoming_by_key[key].intersection(candidate_by_key[key])
                for key in shared_keys
            ):
                conflicted.append(axis)
            elif set(incoming_by_key).issubset(candidate_by_key):
                matched.append(axis)
            else:
                ambiguous.append(axis)
    return AtomicAxisComparison(
        matched_axes=matched,
        conflicted_axes=conflicted,
        ambiguous_axes=ambiguous,
    )


def deterministic_axis_verdicts(
    incoming: AtomicIdentitySidecar,
    candidate: AtomicIdentitySidecar,
) -> dict[IdentityAxis, IdentityAxisVerdict]:
    comparison = compare_atomic_identity_sidecars(incoming, candidate)
    return {
        **{axis: IdentityAxisVerdict.MATCH for axis in comparison.matched_axes},
        **{axis: IdentityAxisVerdict.CONFLICT for axis in comparison.conflicted_axes},
        **{axis: IdentityAxisVerdict.AMBIGUOUS for axis in comparison.ambiguous_axes},
    }


def rank_atomic_candidates(
    *,
    candidates: Sequence[AtomicCandidate],
    incoming_sidecar: AtomicIdentitySidecar,
    candidate_sidecars: dict[str, AtomicIdentitySidecar],
    candidate_roots: dict[str, str],
    raw_embedding_similarities: dict[str, float],
) -> list[AtomicRankedCandidate]:
    ranked: list[AtomicRankedCandidate] = []
    for candidate in candidates:
        event_id = candidate.event.event_id
        sidecar = candidate_sidecars[event_id]
        comparison = compare_atomic_identity_sidecars(incoming_sidecar, sidecar)
        raw_cosine = raw_embedding_similarities.get(event_id)
        band = _rank_band(
            incoming=incoming_sidecar,
            comparison=comparison,
            raw_cosine=raw_cosine,
            recall_routes=candidate.recall_routes,
        )
        ranked.append(
            AtomicRankedCandidate(
                candidate=candidate,
                candidate_root_id=candidate_roots[event_id],
                adapter_kind=sidecar.adapter_kind,
                rank_band=band,
                matched_axes=comparison.matched_axes,
                conflicted_axes=comparison.conflicted_axes,
                ambiguous_axes=comparison.ambiguous_axes,
                independent_evidence_groups=_independent_evidence_groups(
                    candidate.recall_routes
                ),
                raw_embedding_similarity=raw_cosine,
                sidecar_signature_hash=sidecar.signature_hash,
            )
        )
    ranked.sort(
        key=lambda item: (
            item.rank_band,
            -len(item.matched_axes),
            -item.independent_evidence_groups,
            -(item.raw_embedding_similarity if item.raw_embedding_similarity is not None else -1.0),
            item.candidate_root_id,
            item.candidate.event.event_id,
        )
    )
    return ranked


def dedupe_ranked_candidate_roots(
    ranked: Sequence[AtomicRankedCandidate],
) -> list[AtomicRankedCandidate]:
    output: list[AtomicRankedCandidate] = []
    seen: set[str] = set()
    for item in ranked:
        if item.candidate_root_id in seen:
            continue
        seen.add(item.candidate_root_id)
        output.append(item)
    return output


def append_atomic_ranker_shadow_audit(
    *,
    registry: CDECRRegistry,
    run_id: str,
    mention_id: str,
    legacy_ranked: Sequence[AtomicCandidate],
    shadow_ranked: Sequence[AtomicRankedCandidate],
    top_k: int,
    audit_sink: Callable[[DecisionAuditRecord], object] | None = None,
) -> None:
    legacy_top = [item.event.event_id for item in legacy_ranked[:top_k]]
    deduped_shadow = dedupe_ranked_candidate_roots(shadow_ranked)
    shadow_top = [item.candidate.event.event_id for item in deduped_shadow[:top_k]]
    shadow_by_event = {
        item.candidate.event.event_id: item for item in shadow_ranked
    }
    (audit_sink or registry.append_decision_audit)(
        DecisionAuditRecord(
            audit_id=f"n7-ranker-shadow:{run_id}:{mention_id}",
            run_id=run_id,
            decision_type="ATOMIC_N7_RANKER_SHADOW",
            subject_id=mention_id,
            payload={
                "mention_id": mention_id,
                "ranker_version": ATOMIC_RECALL_RANKER_VERSION,
                "mode": "shadow_not_sent",
                "legacy_top_k": legacy_top,
                "shadow_top_k": shadow_top,
                "top_k_overlap": len(set(legacy_top).intersection(shadow_top)),
                "legacy_duplicate_root_count": (
                    len(legacy_top)
                    - len(
                        {
                            item.candidate_root_id
                            for event_id in legacy_top
                            if (item := shadow_by_event.get(event_id)) is not None
                        }
                    )
                ),
                "shadow_duplicate_root_count": (
                    len(shadow_top)
                    - len(
                        {
                            item.candidate_root_id
                            for event_id in shadow_top
                            if (item := shadow_by_event.get(event_id)) is not None
                        }
                    )
                ),
                "candidates": [
                    {
                        "candidate_root_id": item.candidate_root_id,
                        "original_candidate_event_id": item.candidate.event.event_id,
                        "shadow_rank": rank,
                        "rank_band": item.rank_band.name,
                        "matched_axes": [axis.value for axis in item.matched_axes],
                        "conflicted_axes": [axis.value for axis in item.conflicted_axes],
                        "ambiguous_axes": [axis.value for axis in item.ambiguous_axes],
                        "independent_evidence_groups": item.independent_evidence_groups,
                        "raw_embedding_similarity": item.raw_embedding_similarity,
                        "recall_routes": [
                            route.value for route in item.candidate.recall_routes
                        ],
                        "adapter_kind": item.adapter_kind.value,
                        "sidecar_signature_hash": item.sidecar_signature_hash,
                        "decision_eligible": not item.candidate.hard_conflicts,
                    }
                    for rank, item in enumerate(shadow_ranked, start=1)
                ],
            },
        )
    )


def metric_family(metric_id: str) -> str:
    tokens = set(re.findall(r"[a-z0-9]+", metric_id.casefold()))
    joined = "_".join(sorted(tokens))
    if "eps" in tokens or {"earnings", "per", "share"} <= tokens:
        return "eps"
    if {"net", "income"} <= tokens or "profit" in tokens or "earnings" in tokens:
        return "profit"
    if "revenue" in tokens or "sales" in tokens:
        return "revenue"
    if {"gross", "margin"} <= tokens:
        return "gross_margin"
    if "fcf" in tokens or {"free", "cash", "flow"} <= tokens:
        return "free_cash_flow"
    if "capex" in tokens or {"capital", "expenditure"} <= tokens:
        return "capex"
    if "volume" in tokens:
        return "trading_volume"
    if "market" in tokens and "cap" in tokens:
        return "market_cap"
    if "price" in tokens or "close" in tokens:
        return "price"
    return joined or metric_id.casefold()


def _adapter_kind(
    mention: EventMention,
    profile: IdentityProfile,
) -> AtomicIdentityAdapterKind:
    if isinstance(profile, (FinancialMetricIdentityProfile, GuidanceIdentityProfile)):
        return AtomicIdentityAdapterKind.FINANCIAL_GUIDANCE
    if mention.event_family is EventFamily.MARKET_MOVEMENT:
        return AtomicIdentityAdapterKind.MARKET_MOVEMENT
    if isinstance(profile, AnalystActionIdentityProfile) or mention.event_family in {
        EventFamily.TRANSACTION_CAPITAL,
        EventFamily.COMMERCIAL_OPERATION,
        EventFamily.REGULATORY_LEGAL_POLICY,
    }:
        return AtomicIdentityAdapterKind.ACTION_ARTIFACT
    if mention.event_family is EventFamily.GUIDANCE_EXPECTATION:
        return AtomicIdentityAdapterKind.OUTLOOK_STATE
    return AtomicIdentityAdapterKind.GENERIC_OPEN


def _open_occurrence(
    mention: EventMention,
    profile: OpenIdentityProfile,
) -> list[str]:
    values = [f"state:{mention.assertion_state.value}"]
    if profile.fields.reference_period_id:
        values.append(f"period:{profile.fields.reference_period_id}")
    elif profile.fields.event_time.event_start is not None:
        values.append(f"date:{profile.fields.event_time.event_start.isoformat()}")
    predicate = profile.fields.normalized_predicate
    if predicate:
        values.append(f"action:{_action_family(predicate)}")
    session = _session(mention)
    if session:
        values.append(f"session:{session}")
    return values


def _open_facets(
    mention: EventMention,
    adapter: AtomicIdentityAdapterKind,
    *,
    primary_metric_id: str | None = None,
) -> list[str]:
    raw_metric = next(
        (
            quantity.metric_id
            for quantity in mention.quantities
            if quantity.role is QuantityRole.PRIMARY
        ),
        None,
    )
    metric_id = primary_metric_id or raw_metric
    metric = metric_family(metric_id) if metric_id is not None else None
    trusted_metric_facets = _trusted_metric_facets(primary_metric_id)
    if adapter is AtomicIdentityAdapterKind.MARKET_MOVEMENT:
        return [
            f"measure:{metric or _market_measure(mention.predicate.normalized)}",
            *trusted_metric_facets,
        ]
    if adapter is AtomicIdentityAdapterKind.ACTION_ARTIFACT:
        return [
            f"artifact_facet:{_artifact_facet(mention)}",
            *trusted_metric_facets,
        ]
    if adapter is AtomicIdentityAdapterKind.OUTLOOK_STATE:
        return [
            f"outlook_topic:{metric or _action_family(mention.predicate.normalized)}",
            *trusted_metric_facets,
        ]
    return trusted_metric_facets


def _trusted_metric_facets(primary_metric_id: str | None) -> list[str]:
    if primary_metric_id is None:
        return []
    return [
        f"metric_family:{metric_family(primary_metric_id)}",
        f"metric:{primary_metric_id}",
    ]


def _rank_band(
    *,
    incoming: AtomicIdentitySidecar,
    comparison: AtomicAxisComparison,
    raw_cosine: float | None,
    recall_routes: Sequence[RecallRoute],
) -> AtomicRecallRankBand:
    if incoming.adapter_kind is AtomicIdentityAdapterKind.GENERIC_OPEN:
        return AtomicRecallRankBand.BROAD_RECALL
    if (
        incoming.applicable_axes
        and not comparison.conflicted_axes
        and not comparison.ambiguous_axes
        and set(comparison.matched_axes) == set(incoming.applicable_axes)
    ):
        return AtomicRecallRankBand.FULL_IDENTITY
    if comparison.matched_axes and not comparison.conflicted_axes:
        return AtomicRecallRankBand.PARTIAL_IDENTITY
    if raw_cosine is not None and raw_cosine >= 0.82:
        return AtomicRecallRankBand.SEMANTIC_CORROBORATED
    if recall_routes:
        return AtomicRecallRankBand.BROAD_RECALL
    return AtomicRecallRankBand.AUDIT_TAIL


def _independent_evidence_groups(routes: Sequence[RecallRoute]) -> int:
    route_set = set(routes)
    groups = (
        {
            RecallRoute.CORE_ENTITY,
            RecallRoute.SCHEMA_IDENTITY,
            RecallRoute.FIELD_ID,
        },
        {RecallRoute.TIME_WINDOW},
        {RecallRoute.PROPOSITION_EMBEDDING},
        {RecallRoute.EVENT_FAMILY},
        {RecallRoute.SOURCE_FINGERPRINT},
    )
    return sum(bool(route_set.intersection(group)) for group in groups)


def _sidecar(
    *,
    adapter_kind: AtomicIdentityAdapterKind,
    referent: Iterable[str] = (),
    occurrence: Iterable[str] = (),
    facet: Iterable[str] = (),
) -> AtomicIdentitySidecar:
    referent_values = sorted(set(referent))
    occurrence_values = sorted(set(occurrence))
    facet_values = sorted(set(facet))
    applicable = [
        axis
        for axis, values in (
            (IdentityAxis.REFERENT, referent_values),
            (IdentityAxis.OCCURRENCE, occurrence_values),
            (IdentityAxis.FACET, facet_values),
        )
        if values
    ]
    encoded = json.dumps(
        {
            "adapter_kind": adapter_kind.value,
            "referent": referent_values,
            "occurrence": occurrence_values,
            "facet": facet_values,
            "compiler_version": ATOMIC_IDENTITY_SIDECAR_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return AtomicIdentitySidecar(
        adapter_kind=adapter_kind,
        referent=referent_values,
        occurrence=occurrence_values,
        facet=facet_values,
        applicable_axes=applicable,
        compiler_version=ATOMIC_IDENTITY_SIDECAR_VERSION,
        signature_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )


def _values_by_key(values: Iterable[str]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = {}
    for value in values:
        key = value.partition(":")[0]
        output.setdefault(key, set()).add(value)
    return output


def _action_family(value: str) -> str:
    tokens = set(value.casefold().split("_"))
    for family, aliases in (
        ("sign", {"sign", "signed", "execute", "executed"}),
        ("announce", {"announce", "announced", "unveil", "launch"}),
        ("commit", {"commit", "committed", "pledge"}),
        ("renew", {"renew", "renewed", "extend"}),
        ("raise", {"raise", "raised", "increase"}),
        ("lower", {"lower", "lowered", "decrease"}),
        ("reiterate", {"reiterate", "maintain", "affirm"}),
    ):
        if tokens.intersection(aliases):
            return family
    return value.casefold()


def _artifact_facet(mention: EventMention) -> str:
    text = " ".join(
        [
            mention.predicate.normalized,
            mention.canonical_proposition,
            *(attribute.key for attribute in mention.open_attributes),
        ]
    ).casefold()
    for facet, needles in (
        ("future_revenue", ("future revenue", "backlog", "revenue potential")),
        ("commitment", ("commitment", "committed", "purchase obligation")),
        ("terms", ("terms", "duration", "value", "consideration")),
        ("signing", ("sign", "agreement", "contract")),
        ("rating", ("rating", "upgrade", "downgrade")),
        ("target", ("price target", "target price")),
    ):
        if any(needle in text for needle in needles):
            return facet
    return _action_family(mention.predicate.normalized)


def _analyst_facet(profile: AnalystActionIdentityProfile) -> str:
    return (
        "target"
        if "TARGET" in profile.fields.action.value
        else "rating"
        if profile.fields.action.value not in {"UNKNOWN", "MAINTAIN", "REITERATE"}
        else "outlook"
    )


def _session(mention: EventMention) -> str | None:
    text = f"{mention.predicate.normalized} {mention.canonical_proposition}".casefold()
    for session, needles in (
        ("pre_market", ("pre-market", "premarket", "before the open")),
        ("after_hours", ("after-hours", "after hours", "post-market")),
        ("regular", ("regular session", "during trading", "at the close")),
        ("early", ("early trading", "early session")),
        ("open", ("market open", "at the open", "opening trade")),
    ):
        if any(needle in text for needle in needles):
            return session
    return None


def _market_measure(predicate: str) -> str:
    value = predicate.casefold()
    if "volume" in value:
        return "trading_volume"
    if "market_cap" in value or "capitalization" in value:
        return "market_cap"
    if "index" in value:
        return "index_level"
    if "price" in value or "share" in value or "stock" in value:
        return "price_move"
    return value
