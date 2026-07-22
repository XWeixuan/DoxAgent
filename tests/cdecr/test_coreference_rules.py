from __future__ import annotations

from datetime import UTC, date, datetime

from cdecr.contracts import (
    AccountingBasis,
    AnalystAction,
    AnalystActionFields,
    AnalystActionProjection,
    AssertionState,
    ComparisonBasis,
    EventFamily,
    EventMention,
    EventTime,
    EvidenceSpan,
    FinancialMetricFields,
    FinancialMetricProjection,
    LocalPackageHint,
    MembershipRelation,
    Participant,
    ParticipantRole,
    Predicate,
    TimePrecision,
)
from cdecr.coreference_rules import (
    bounded_package_exact_match,
    hard_cannot_link,
    package_seed_for_event,
    singleton_atomic_event,
    singleton_package,
)
from cdecr.cross_document_contracts import HardConflictCode


def metric_mention(
    *,
    mention_id: str = "M1",
    period: str = "FY2026-Q4",
    metric: str = "REVENUE",
    accounting: AccountingBasis = AccountingBasis.GAAP,
    assertion: AssertionState = AssertionState.ACTUAL,
) -> EventMention:
    return EventMention(
        mention_id=mention_id,
        message_id=f"MSG-{mention_id}",
        evidence_spans=[EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")],
        canonical_proposition="Micron reported quarterly revenue.",
        source_claim=None,
        event_family=EventFamily.FINANCIAL_PERFORMANCE,
        predicate=Predicate(raw="reported", normalized="report_financial_metric"),
        participants=[
            Participant(surface="Micron", entity_id="COMPANY_MU", role=ParticipantRole.SUBJECT)
        ],
        locations=[],
        time=EventTime(
            event_start=date(2026, 6, 25),
            precision=TimePrecision.DAY,
            reference_period_id=period,
        ),
        assertion_state=assertion,
        quantities=[],
        open_attributes=[],
        schema_projection=FinancialMetricProjection(
            fields=FinancialMetricFields(
                issuer_id="COMPANY_MU",
                period_id=period,
                metric_id=metric,
                value=9.3,
                unit="USD_B",
                comparison_basis=ComparisonBasis.ABSOLUTE,
                accounting_basis=accounting,
            )
        ),
        local_package_hint=LocalPackageHint(
            anchor="MU FY2026-Q4 earnings",
            relation_to_anchor=MembershipRelation.DISCLOSED_IN,
        ),
    )


def analyst_mention(*, mention_id: str, institution: str, target: float) -> EventMention:
    return EventMention(
        mention_id=mention_id,
        message_id=f"MSG-{mention_id}",
        evidence_spans=[EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")],
        canonical_proposition=f"{institution} raised its Micron target to {target}.",
        source_claim=institution,
        event_family=EventFamily.ANALYST_ACTION,
        predicate=Predicate(raw="raised", normalized="raise_target_price"),
        participants=[
            Participant(surface="Micron", entity_id="COMPANY_MU", role=ParticipantRole.SUBJECT)
        ],
        locations=[],
        time=EventTime(event_start=date(2026, 6, 25), precision=TimePrecision.DAY),
        assertion_state=AssertionState.ACTUAL,
        quantities=[],
        open_attributes=[],
        schema_projection=AnalystActionProjection(
            fields=AnalystActionFields(
                institution_id=institution,
                company_id="COMPANY_MU",
                action=AnalystAction.RAISE_TARGET,
                target_to=target,
                currency="USD",
                report_date=date(2026, 6, 25),
            )
        ),
    )


def test_financial_hard_identity_prevents_false_merge() -> None:
    event = singleton_atomic_event(metric_mention())
    period_conflicts = hard_cannot_link(metric_mention(mention_id="M2", period="FY2027-Q1"), event)
    metric_conflicts = hard_cannot_link(metric_mention(mention_id="M3", metric="EPS_GAAP"), event)
    basis_conflicts = hard_cannot_link(
        metric_mention(mention_id="M4", accounting=AccountingBasis.NON_GAAP), event
    )
    assert HardConflictCode.REFERENCE_PERIOD in period_conflicts
    assert HardConflictCode.METRIC in metric_conflicts
    assert HardConflictCode.ACCOUNTING_BASIS in basis_conflicts


def test_analyst_institution_is_identity_but_target_value_is_not() -> None:
    event = singleton_atomic_event(
        analyst_mention(mention_id="A1", institution="JPMORGAN", target=150)
    )
    same_institution = hard_cannot_link(
        analyst_mention(mention_id="A2", institution="JPMORGAN", target=160), event
    )
    other_institution = hard_cannot_link(
        analyst_mention(mention_id="A3", institution="GOLDMAN_SACHS", target=150), event
    )
    assert same_institution == []
    assert HardConflictCode.ANALYST_INSTITUTION in other_institution


def test_same_earnings_period_groups_distinct_metrics_but_periods_do_not() -> None:
    revenue = singleton_atomic_event(metric_mention())
    revenue_seed = package_seed_for_event(revenue, [metric_mention()])
    package = singleton_package(revenue, revenue_seed)
    eps = singleton_atomic_event(metric_mention(mention_id="M2", metric="EPS_GAAP"))
    next_period = singleton_atomic_event(
        metric_mention(mention_id="M3", period="FY2027-Q1", metric="EPS_GAAP")
    )
    assert bounded_package_exact_match(package_seed_for_event(eps, []), package)
    assert not bounded_package_exact_match(package_seed_for_event(next_period, []), package)


def test_contract_fixture_timestamps_are_timezone_aware() -> None:
    assert datetime(2026, 6, 25, tzinfo=UTC).utcoffset() is not None
