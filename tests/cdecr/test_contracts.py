from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from cdecr.contracts import (
    AccountingBasis,
    AnalystAction,
    AnalystActionFields,
    AnalystActionProjection,
    AssertionState,
    AtomicAction,
    AtomicEvent,
    AtomicSemanticRelation,
    ComparisonBasis,
    EventFamily,
    EventMention,
    EventTime,
    EvidenceSpan,
    ExternalRelationType,
    FinancialMetricFields,
    FinancialMetricProjection,
    GuidanceAction,
    GuidanceFields,
    GuidanceProjection,
    Language,
    MembershipRelation,
    OpenIdentityFields,
    OpenIdentityProfile,
    PackageAction,
    PackageAssignmentRelation,
    PackageFamily,
    PackageKind,
    PackageMergeRelation,
    PackageStatus,
    Participant,
    ParticipantRole,
    Predicate,
    SchemaProjection,
    SourceMessage,
    SourceType,
    TimePrecision,
)


def source_message() -> SourceMessage:
    return SourceMessage(
        message_id="MSG-1",
        source_type=SourceType.NEWS,
        title="Micron raised its outlook",
        text="Micron raised its annual revenue outlook after stronger demand.",
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name="Example Wire",
        url="https://example.test/story",
        ticker_hints=["mu"],
        parent_message_id=None,
        language=Language.EN,
    )


def mention() -> EventMention:
    return EventMention(
        mention_id="MENTION-1",
        message_id="MSG-1",
        evidence_spans=[EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")],
        canonical_proposition="Micron raised its annual revenue outlook.",
        source_claim=None,
        event_family=EventFamily.GUIDANCE_EXPECTATION,
        predicate=Predicate(raw="raised", normalized="raise_guidance"),
        participants=[
            Participant(surface="Micron", entity_id="COMPANY_MU", role=ParticipantRole.SUBJECT)
        ],
        locations=[],
        time=EventTime(
            event_start=date(2026, 6, 25),
            event_end=None,
            precision=TimePrecision.DAY,
            reference_period_id="COMPANY_MU_FY2026",
        ),
        assertion_state=AssertionState.ACTUAL,
        quantities=[],
        open_attributes=[],
        schema_projection=GuidanceProjection(
            fields=GuidanceFields(
                issuer_id="COMPANY_MU",
                period_id="COMPANY_MU_FY2026",
                metric_id="REVENUE",
                action=GuidanceAction.RAISE,
                value_low=42,
                value_high=44,
                unit="USD_BILLION",
            )
        ),
        local_package_hint=None,
    )


def test_source_enums_and_normalization() -> None:
    source = source_message()
    assert source.ticker_hints == ["MU"]
    assert {item.value for item in SourceType} == {
        "NEWS",
        "SOCIAL",
        "ANNOUNCEMENT",
        "FLASH",
        "FILING",
        "OTHER",
    }
    assert {item.value for item in Language} == {"en", "zh", "und"}


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    [
        (
            EventFamily,
            {
                "FINANCIAL_PERFORMANCE",
                "GUIDANCE_EXPECTATION",
                "ANALYST_ACTION",
                "TRANSACTION_CAPITAL",
                "COMMERCIAL_OPERATION",
                "PRODUCTION_SUPPLY",
                "REGULATORY_LEGAL_POLICY",
                "GOVERNANCE_PERSONNEL",
                "PRODUCT_SCIENCE",
                "INCIDENT_GEOPOLITICAL",
                "MARKET_MOVEMENT",
                "OTHER",
            },
        ),
        (
            ParticipantRole,
            {"ACTOR", "SUBJECT", "TARGET", "COUNTERPARTY", "AFFECTED", "AUTHORITY", "OTHER"},
        ),
        (
            TimePrecision,
            {"TIMESTAMP", "DAY", "MONTH", "QUARTER", "YEAR", "INTERVAL", "UNKNOWN"},
        ),
        (
            AssertionState,
            {
                "ACTUAL",
                "ONGOING",
                "PLANNED",
                "EXPECTED",
                "RUMORED",
                "DENIED",
                "HYPOTHETICAL",
                "UNKNOWN",
            },
        ),
        (
            ComparisonBasis,
            {"ABSOLUTE", "YOY", "QOQ", "YTD", "VS_CONSENSUS", "VS_GUIDANCE", "UNKNOWN"},
        ),
        (AccountingBasis, {"GAAP", "NON_GAAP", "STATUTORY", "UNKNOWN"}),
        (
            GuidanceAction,
            {"INITIATE", "RAISE", "LOWER", "REITERATE", "NARROW", "WIDEN", "WITHDRAW", "UNKNOWN"},
        ),
        (
            AnalystAction,
            {
                "UPGRADE",
                "DOWNGRADE",
                "INITIATE",
                "REITERATE",
                "MAINTAIN",
                "RAISE_TARGET",
                "LOWER_TARGET",
                "SUSPEND_COVERAGE",
                "RESUME_COVERAGE",
                "UNKNOWN",
            },
        ),
        (
            AtomicSemanticRelation,
            {"SAME_EVENT", "RELATED_NOT_SAME", "UNRELATED", "UNCERTAIN"},
        ),
        (AtomicAction, {"MERGE", "CREATE_NEW", "CREATE_AND_LINK", "HOLD"}),
        (PackageKind, {"BOUNDED", "EPISODE"}),
        (
            PackageFamily,
            {
                "EARNINGS_DISCLOSURE",
                "COMPANY_DISCLOSURE",
                "ANALYST_REPORT",
                "TRANSACTION",
                "REGULATORY_LEGAL",
                "POLICY",
                "OPERATIONAL_INCIDENT",
                "PRODUCT_SCIENCE",
                "OTHER",
            },
        ),
        (PackageStatus, {"OPEN", "CLOSED", "UNKNOWN"}),
        (
            MembershipRelation,
            {
                "DISCLOSED_IN",
                "COMPONENT_OF",
                "STAGE_OF",
                "UPDATE_OF",
                "CORRECTION_OF",
                "IMPLEMENTATION_OF",
            },
        ),
        (
            ExternalRelationType,
            {
                "CAUSES",
                "MARKET_REACTION_TO",
                "ANALYST_REACTION_TO",
                "CONFIRMS",
                "CONTRADICTS",
                "RELATED_TO",
            },
        ),
        (
            PackageAssignmentRelation,
            {"MEMBER", "EXTERNAL_RELATED", "NOT_RELATED", "UNCERTAIN"},
        ),
        (PackageMergeRelation, {"SAME_PACKAGE", "DIFFERENT_PACKAGE", "UNCERTAIN"}),
        (
            PackageAction,
            {"ADD_TO_PACKAGE", "CREATE_NEW_PACKAGE", "LINK_EXTERNALLY", "MERGE_PACKAGES", "HOLD"},
        ),
    ],
)
def test_all_judgment_and_domain_enums_are_closed(
    enum_type: type[object], expected: set[str]
) -> None:
    assert {item.value for item in enum_type} == expected  # type: ignore[attr-defined]


def test_evidence_span_uses_exact_left_closed_right_open_slice() -> None:
    source = source_message()
    span = EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")
    span.validate_source(source)
    with pytest.raises(ValueError, match="does not match"):
        EvidenceSpan(field="title", start_char=0, end_char=5, text="MicrX").validate_source(source)
    with pytest.raises(ValidationError):
        EvidenceSpan(field="title", start_char=4, end_char=4, text="x")


def test_mention_validates_all_evidence_against_its_source() -> None:
    event_mention = mention()
    event_mention.validate_evidence(source_message())
    wrong_source = source_message().model_copy(update={"message_id": "MSG-2"})
    with pytest.raises(ValueError, match="message_id"):
        event_mention.validate_evidence(wrong_source)


def test_projection_is_a_closed_discriminated_union() -> None:
    adapter = TypeAdapter(SchemaProjection)
    projections = [
        FinancialMetricProjection(
            fields=FinancialMetricFields(
                issuer_id="COMPANY_MU",
                period_id="FY2026_Q3",
                metric_id="NET_INCOME",
                value=1.2,
                unit="USD_BILLION",
                comparison_basis=ComparisonBasis.YOY,
                change_value=10,
                accounting_basis=AccountingBasis.GAAP,
            )
        ),
        GuidanceProjection(
            fields=GuidanceFields(
                issuer_id="COMPANY_MU",
                period_id="FY2026",
                metric_id="CAPEX",
                action=GuidanceAction.RAISE,
                value_low=10,
                value_high=12,
                unit="USD_BILLION",
            )
        ),
        AnalystActionProjection(
            fields=AnalystActionFields(
                institution_id="GOLDMAN_SACHS",
                analyst_id=None,
                company_id="COMPANY_MU",
                action=AnalystAction.RAISE_TARGET,
                rating_from=None,
                rating_to="BUY",
                target_from=120,
                target_to=140,
                currency="USD",
                report_date=date(2026, 6, 25),
            )
        ),
    ]
    for projection in projections:
        restored = adapter.validate_json(projection.model_dump_json())
        assert type(restored) is type(projection)
    with pytest.raises(ValidationError):
        adapter.validate_python({"schema_type": "UNDECLARED", "fields": {}})


def test_open_identity_is_closed_to_documented_fields() -> None:
    fields = OpenIdentityFields(
        normalized_predicate="suspend production",
        principal_participant_ids=["COMPANY_X"],
        event_time=EventTime(precision=TimePrecision.DAY, event_start=date(2026, 6, 25)),
        reference_period_id=None,
        location_or_asset_ids=["FACILITY_X"],
        assertion_state=AssertionState.ACTUAL,
    )
    profile = OpenIdentityProfile(fields=fields)
    payload = profile.model_dump(mode="json")
    payload["fields"]["reason"] = "accidental hard identity"  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs"):
        OpenIdentityProfile.model_validate(payload)


def test_atomic_event_round_trip_and_version_floor() -> None:
    event = AtomicEvent(
        event_id="EVENT-1",
        canonical_proposition="Micron suspended production at Facility X.",
        event_family=EventFamily.PRODUCTION_SUPPLY,
        identity_profile=OpenIdentityProfile(
            fields=OpenIdentityFields(
                normalized_predicate="suspend production",
                principal_participant_ids=["COMPANY_MU"],
                event_time=EventTime(event_start=date(2026, 6, 25), precision=TimePrecision.DAY),
                location_or_asset_ids=["FACILITY_X"],
                assertion_state=AssertionState.ACTUAL,
            )
        ),
        time=EventTime(event_start=date(2026, 6, 25), precision=TimePrecision.DAY),
        assertion_state=AssertionState.ACTUAL,
        mention_ids=["MENTION-1"],
        representative_mention_ids=["MENTION-1"],
        consensus_claims={"status": "suspended"},
        conflict_flags=[],
    )
    assert event.version == 1
    assert AtomicEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        AtomicEvent.model_validate({**event.model_dump(), "version": 0})


@pytest.mark.parametrize(
    "removed_field",
    ["factuality", "modality", "polarity", "sentiment", "direction", "importance", "tradability"],
)
def test_removed_universal_fields_are_rejected(removed_field: str) -> None:
    payload = mention().model_dump(mode="json")
    payload[removed_field] = "SHOULD_NOT_EXIST"
    with pytest.raises(ValidationError, match="Extra inputs"):
        EventMention.model_validate(payload)


def test_predicate_string_and_retired_confidence_are_rejected() -> None:
    payload = mention().model_dump(mode="json")
    payload["predicate"] = "raise guidance"
    with pytest.raises(ValidationError):
        EventMention.model_validate(payload)
    payload = mention().model_dump(mode="json")
    payload["extraction_confidence"] = 0.92
    with pytest.raises(ValidationError):
        EventMention.model_validate(payload)


def test_all_models_reject_extra_fields() -> None:
    payload = source_message().model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        SourceMessage.model_validate(payload)
