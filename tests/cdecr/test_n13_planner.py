from __future__ import annotations

from datetime import date

from cdecr.cross_document_contracts import RecallRoute
from cdecr.n13_planner import N13PackageCard, build_indexed_n13_plan


def _card(package_id: str, *, anchor: str, vector: tuple[float, ...]) -> N13PackageCard:
    return N13PackageCard(
        package_id=package_id,
        member_event_ids=frozenset({f"event:{package_id}"}),
        artifact_ids=frozenset(),
        anchor_ids=frozenset({anchor}),
        entity_ids=frozenset({"COMPANY_MU"}),
        issuer_ids=frozenset({"COMPANY_MU"}),
        institution_ids=frozenset(),
        instrument_ids=frozenset(),
        object_ids=frozenset(),
        market_sessions=frozenset(),
        market_measures=frozenset(),
        member_identity_hashes=frozenset(),
        source_ids=frozenset(),
        parent_blocks=(),
        event_families=frozenset({"FINANCIAL_DISCLOSURE"}),
        package_kind="BOUNDED",
        package_family="EARNINGS_DISCLOSURE",
        anchor_period_id="FY2026-Q3",
        lifecycle_state=None,
        time_start=date(2026, 6, 30),
        time_end=date(2026, 6, 30),
        vector=vector,
        anchor_conflict=False,
    )


def test_indexed_n13_planner_deduplicates_pairs_and_preserves_strong_route() -> None:
    cards = [
        _card("P1", anchor="A1", vector=(1.0, 0.0)),
        _card("P2", anchor="A1", vector=(0.9, 0.1)),
        _card("P3", anchor="A3", vector=(0.0, 1.0)),
    ]
    plan = build_indexed_n13_plan(cards, touched_package_ids=["P1", "P2"])
    pairs = {(pair.left_package_id, pair.right_package_id): pair for pair in plan.pairs}
    assert ("P1", "P2") in pairs
    assert RecallRoute.PACKAGE_ANCHOR in pairs[("P1", "P2")].routes
    assert len(pairs) == len(plan.pairs)
    assert plan.planner_version == "indexed_v2"
