from __future__ import annotations

from datetime import date

from cdecr.atomic_identity_sidecar import (
    AtomicIdentityAdapterKind,
    AtomicRecallRankBand,
    IdentityAxis,
    compare_atomic_identity_sidecars,
    compile_atomic_identity_sidecar,
    dedupe_ranked_candidate_roots,
    metric_family,
    rank_atomic_candidates,
)
from cdecr.contracts import (
    AccountingBasis,
    AssertionState,
    AtomicEvent,
    ComparisonBasis,
    EventFamily,
    EventMention,
    EventTime,
    FinancialMetricIdentityFields,
    FinancialMetricIdentityProfile,
    Predicate,
    TimePrecision,
)
from cdecr.cross_document_contracts import AtomicCandidate, RecallRoute


def mention(mention_id: str, *, metric: str = "REVENUE") -> EventMention:
    return EventMention(
        mention_id=mention_id,
        message_id=f"MSG-{mention_id}",
        canonical_proposition=f"Company reported {metric}.",
        event_family=EventFamily.FINANCIAL_PERFORMANCE,
        predicate=Predicate(raw="reported", normalized="report_financial_metric"),
        participants=[],
        locations=[],
        time=EventTime(
            event_start=date(2026, 6, 25),
            precision=TimePrecision.DAY,
            reference_period_id="FY2026Q3",
        ),
        assertion_state=AssertionState.ACTUAL,
        quantities=[],
        open_attributes=[],
    )


def profile(metric: str) -> FinancialMetricIdentityProfile:
    return FinancialMetricIdentityProfile(
        fields=FinancialMetricIdentityFields(
            issuer_id="COMPANY_MU",
            period_id="FY2026Q3",
            metric_id=metric,
            comparison_basis=ComparisonBasis.ABSOLUTE,
            accounting_basis=AccountingBasis.GAAP,
        )
    )


def candidate(event_id: str, metric: str, score: float) -> AtomicCandidate:
    identity = profile(metric)
    return AtomicCandidate(
        event=AtomicEvent(
            event_id=event_id,
            canonical_proposition=metric,
            event_family=EventFamily.FINANCIAL_PERFORMANCE,
            identity_profile=identity,
            time=EventTime(
                event_start=date(2026, 6, 25),
                precision=TimePrecision.DAY,
                reference_period_id="FY2026Q3",
            ),
            assertion_state=AssertionState.ACTUAL,
            mention_ids=[f"M-{event_id}"],
            representative_mention_ids=[f"M-{event_id}"],
            consensus_claims={},
            conflict_flags=[],
            version=1,
        ),
        recall_routes=[RecallRoute.FIELD_ID],
        recall_score=score,
        hard_conflicts=[],
    )


def test_financial_sidecar_uses_metric_family_not_raw_metric_id() -> None:
    value = compile_atomic_identity_sidecar(mention("1"), profile("US_GAAP_NET_INCOME"))

    assert value.adapter_kind is AtomicIdentityAdapterKind.FINANCIAL_GUIDANCE
    assert "metric_family:profit" in value.facet
    assert "metric:US_GAAP_NET_INCOME" in value.facet
    assert value.applicable_axes == [
        IdentityAxis.REFERENT,
        IdentityAxis.OCCURRENCE,
        IdentityAxis.FACET,
    ]
    assert metric_family("GAAP_PROFIT") == "profit"


def test_missing_candidate_axis_is_ambiguous_not_conflict() -> None:
    incoming = compile_atomic_identity_sidecar(mention("1"), profile("REVENUE"))
    candidate_sidecar = incoming.model_copy(
        update={
            "facet": [],
            "applicable_axes": [IdentityAxis.REFERENT, IdentityAxis.OCCURRENCE],
        }
    )

    comparison = compare_atomic_identity_sidecars(incoming, candidate_sidecar)

    assert comparison.matched_axes == [IdentityAxis.REFERENT, IdentityAxis.OCCURRENCE]
    assert comparison.ambiguous_axes == [IdentityAxis.FACET]
    assert not comparison.conflicted_axes


def test_ranker_prefers_full_identity_and_dedupes_canonical_roots() -> None:
    incoming = compile_atomic_identity_sidecar(mention("IN"), profile("REVENUE"))
    revenue = candidate("atomic:revenue", "REVENUE", 0.4)
    duplicate = candidate("atomic:revenue-old", "REVENUE", 0.9)
    eps = candidate("atomic:eps", "EPS_GAAP", 0.95)
    sidecars = {
        item.event.event_id: compile_atomic_identity_sidecar(
            mention(item.event.event_id, metric=item.event.canonical_proposition),
            item.event.identity_profile,
        )
        for item in (revenue, duplicate, eps)
    }
    ranked = rank_atomic_candidates(
        candidates=[eps, duplicate, revenue],
        incoming_sidecar=incoming,
        candidate_sidecars=sidecars,
        candidate_roots={
            "atomic:revenue": "atomic:revenue",
            "atomic:revenue-old": "atomic:revenue",
            "atomic:eps": "atomic:eps",
        },
        raw_embedding_similarities={
            "atomic:revenue": 0.7,
            "atomic:revenue-old": 0.8,
            "atomic:eps": 0.99,
        },
    )

    assert ranked[0].rank_band is AtomicRecallRankBand.FULL_IDENTITY
    assert ranked[0].candidate.event.event_id == "atomic:revenue-old"
    assert ranked[-1].candidate.event.event_id == "atomic:eps"
    assert [item.candidate_root_id for item in dedupe_ranked_candidate_roots(ranked)] == [
        "atomic:revenue",
        "atomic:eps",
    ]
