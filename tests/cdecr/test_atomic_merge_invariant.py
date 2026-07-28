from __future__ import annotations

from cdecr.atomic_identity_contracts import (
    AtomicIdentityAdapterKind,
    AtomicIdentitySidecar,
    IdentityAxis,
)
from cdecr.atomic_merge_invariant import (
    AtomicMergeInvariantResult,
    AtomicMergeInvariantRule,
    evaluate_atomic_merge_invariant,
    evaluate_atomic_merge_invariant_gates,
    first_unlocked_atomic_candidate,
)


def sidecar(
    *,
    adapter: AtomicIdentityAdapterKind = AtomicIdentityAdapterKind.FINANCIAL_GUIDANCE,
    referent: list[str] | None = None,
    occurrence: list[str] | None = None,
    facet: list[str] | None = None,
) -> AtomicIdentitySidecar:
    referent = referent or []
    occurrence = occurrence or []
    facet = facet or []
    return AtomicIdentitySidecar(
        adapter_kind=adapter,
        referent=referent,
        occurrence=occurrence,
        facet=facet,
        applicable_axes=[
            axis
            for axis, values in (
                (IdentityAxis.REFERENT, referent),
                (IdentityAxis.OCCURRENCE, occurrence),
                (IdentityAxis.FACET, facet),
            )
            if values
        ],
        signature_hash="x",
    )


def test_metric_family_conflict_is_shadow_until_rule_is_enabled() -> None:
    incoming = sidecar(
        referent=["issuer:MU"],
        facet=["metric:revenue", "basis:GAAP"],
    )
    candidate = sidecar(
        referent=["issuer:MU"],
        facet=["metric:eps", "basis:GAAP"],
    )

    shadow = evaluate_atomic_merge_invariant(incoming, candidate)
    enforced = evaluate_atomic_merge_invariant(
        incoming,
        candidate,
        enforced_rules=frozenset({AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY}),
    )

    assert shadow.result is AtomicMergeInvariantResult.SEMANTIC_REVIEW
    assert enforced.result is AtomicMergeInvariantResult.LOCKED_OUT


def test_same_metric_different_claim_value_is_allowed() -> None:
    incoming = sidecar(
        referent=["issuer:MU"],
        occurrence=["period:FY2026Q3", "state:ACTUAL"],
        facet=["metric:capex", "basis:GAAP"],
    )
    candidate = sidecar(
        referent=["issuer:MU"],
        occurrence=["period:FY2026Q3", "state:ACTUAL"],
        facet=["metric:capex", "basis:GAAP"],
    )

    result = evaluate_atomic_merge_invariant(
        incoming,
        candidate,
        enforced_rules=frozenset(AtomicMergeInvariantRule),
    )

    assert result.result is AtomicMergeInvariantResult.ALLOW
    assert not result.triggered_rules


def test_explicit_market_sessions_and_measures_are_independent_rules() -> None:
    incoming = sidecar(
        adapter=AtomicIdentityAdapterKind.MARKET_MOVEMENT,
        referent=["participant:MU"],
        occurrence=["date:2026-06-25", "session:pre_market"],
        facet=["measure:price_move"],
    )
    candidate = sidecar(
        adapter=AtomicIdentityAdapterKind.MARKET_MOVEMENT,
        referent=["participant:MU"],
        occurrence=["date:2026-06-25", "session:regular"],
        facet=["measure:trading_volume"],
    )

    result = evaluate_atomic_merge_invariant(incoming, candidate)

    assert result.triggered_rules == [
        AtomicMergeInvariantRule.MARKET_MEASURE,
        AtomicMergeInvariantRule.MARKET_SESSION,
    ]


def test_first_unlocked_candidate_preserves_n9_order() -> None:
    locked = evaluate_atomic_merge_invariant(
        sidecar(facet=["metric:revenue"]),
        sidecar(facet=["metric:eps"]),
        enforced_rules=frozenset({AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY}),
    )
    allowed = evaluate_atomic_merge_invariant(
        sidecar(facet=["metric:revenue"]),
        sidecar(facet=["metric:revenue"]),
        enforced_rules=frozenset({AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY}),
    )

    assert first_unlocked_atomic_candidate(
        [("n9-target", locked), ("alternate", allowed)]
    ) == "alternate"


def test_invariant_gate_is_per_rule_and_requires_zero_correct_merge_blocks() -> None:
    metrics = evaluate_atomic_merge_invariant_gates(
        [
            (AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY, False),
            (AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY, False),
            (AtomicMergeInvariantRule.MARKET_SESSION, False),
            (AtomicMergeInvariantRule.MARKET_SESSION, True),
        ],
        minimum_block_precision=0.99,
    )

    assert metrics["PRIMARY_METRIC_FAMILY"]["eligible_for_enforcement"] is True
    assert metrics["MARKET_SESSION"]["eligible_for_enforcement"] is False
    assert metrics["MARKET_SESSION"]["correct_merges_blocked"] == 1
