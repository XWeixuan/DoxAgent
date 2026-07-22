from __future__ import annotations

import json
from datetime import UTC, date, datetime

from cdecr.contracts import (
    AccountingBasis,
    AssertionState,
    ComparisonBasis,
    EventFamily,
    EventMention,
    EventTime,
    EvidenceSpan,
    FinancialMetricFields,
    FinancialMetricProjection,
    Participant,
    ParticipantRole,
    Predicate,
    Quantity,
    TimePrecision,
)
from cdecr.normalization import NormalizationEngine
from cdecr.ports import EmbeddingResult, StructuredModelRequest, StructuredModelResult
from cdecr.single_document_contracts import NormalizationMethod


class Embeddings:
    def __init__(self, *, ambiguous: bool = False) -> None:
        self.ambiguous = ambiguous

    def embed(self, texts: list[str]) -> EmbeddingResult:
        vectors = [[1.0, 0.0]]
        for index in range(len(texts) - 1):
            if self.ambiguous and index < 2:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([1.0, 0.0] if index == 0 else [0.0, 1.0])
        return EmbeddingResult(
            model="text-embedding-v4",
            dimensions=2,
            vectors=vectors,
            latency_ms=1,
        )


class SelectionModel:
    def __init__(self, *, invent: bool = False) -> None:
        self.invent = invent

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        items = json.loads(request.user_prompt)
        selections = []
        for item in items:
            canonical = "INVENTED_ID" if self.invent else item["candidate_ids"][0]
            selections.append({"field_path": item["field_path"], "canonical_id": canonical})
        return StructuredModelResult(
            model="deepseek-v4-flash",
            payload={"selections": selections},
            latency_ms=1,
        )


def mention(
    *,
    participant: str = "Micron",
    metric: str = "sales",
    projection_issuer: str = "Micron",
) -> EventMention:
    return EventMention(
        mention_id="MENTION-1",
        message_id="MSG-1",
        evidence_spans=[EvidenceSpan(field="text", start_char=0, end_char=6, text="Micron")],
        canonical_proposition="Micron reported sales of $4.5B.",
        source_claim=None,
        event_family=EventFamily.FINANCIAL_PERFORMANCE,
        predicate=Predicate(raw="reported", normalized="report_financial_metric"),
        participants=[
            Participant(
                surface=participant,
                entity_id=participant,
                role=ParticipantRole.SUBJECT,
            )
        ],
        locations=[],
        time=EventTime(
            event_start=date(2026, 6, 25),
            precision=TimePrecision.DAY,
            reference_period_id="FY2026 Q3",
        ),
        assertion_state=AssertionState.ACTUAL,
        quantities=[Quantity(metric_id=metric, value=4.5, unit="$", raw_text="$4.5B")],
        open_attributes=[],
        schema_projection=FinancialMetricProjection(
            fields=FinancialMetricFields(
                issuer_id=projection_issuer,
                period_id="FY2026 Q3",
                metric_id=metric,
                value=4.5,
                unit="$",
                comparison_basis=ComparisonBasis.ABSOLUTE,
                accounting_basis=AccountingBasis.GAAP,
            )
        ),
    )


def test_exact_entity_period_metric_quantity_and_projection_normalization() -> None:
    normalized, decisions = NormalizationEngine().normalize(mention(), ticker_hints=["MU"])
    assert normalized.participants[0].entity_id == "COMPANY_MU"
    assert normalized.time.reference_period_id == "COMPANY_MU_FY2026_Q3"
    assert normalized.quantities[0].metric_id == "REVENUE"
    assert normalized.quantities[0].value == 4_500_000_000
    assert normalized.quantities[0].unit == "USD"
    assert isinstance(normalized.schema_projection, FinancialMetricProjection)
    assert normalized.schema_projection.fields.issuer_id == "COMPANY_MU"
    assert normalized.schema_projection.fields.metric_id == "REVENUE"
    assert all(decision.method is NormalizationMethod.M0_EXACT for decision in decisions)


def test_m1_requires_threshold_and_runner_up_margin() -> None:
    accepted, decisions = NormalizationEngine(embedding_client=Embeddings()).normalize(
        mention(participant="Mikron Teknology", projection_issuer="Micron"),
        ticker_hints=["MU"],
    )
    assert accepted.participants[0].entity_id == "COMPANY_MU"
    participant_decision = next(
        item for item in decisions if item.field_path == "participants.0.entity_id"
    )
    assert participant_decision.method is NormalizationMethod.M1_EMBEDDING

    rejected, rejected_decisions = NormalizationEngine(
        embedding_client=Embeddings(ambiguous=True)
    ).normalize(
        mention(participant="Unknown Holdings", projection_issuer="Micron"),
        ticker_hints=["MU"],
    )
    assert rejected.participants[0].entity_id is None
    rejected_decision = next(
        item for item in rejected_decisions if item.field_path == "participants.0.entity_id"
    )
    assert rejected_decision.method is NormalizationMethod.UNRESOLVED


def test_m2_is_candidate_constrained_and_never_accepts_invented_id() -> None:
    selected, decisions = NormalizationEngine(
        embedding_client=Embeddings(ambiguous=True),
        fallback_client=SelectionModel(),
    ).normalize(
        mention(participant="Unknown Holdings", projection_issuer="Micron"),
        ticker_hints=["MU"],
    )
    assert selected.participants[0].entity_id is not None
    decision = next(item for item in decisions if item.field_path == "participants.0.entity_id")
    assert decision.method is NormalizationMethod.M2_CONSTRAINED

    invented, invented_decisions = NormalizationEngine(
        embedding_client=Embeddings(ambiguous=True),
        fallback_client=SelectionModel(invent=True),
    ).normalize(
        mention(participant="Unknown Holdings", projection_issuer="Micron"),
        ticker_hints=["MU"],
    )
    assert invented.participants[0].entity_id is None
    invented_decision = next(
        item for item in invented_decisions if item.field_path == "participants.0.entity_id"
    )
    assert invented_decision.method is NormalizationMethod.UNRESOLVED


def test_unknown_metric_is_preserved_as_unknown_and_bad_projection_is_removed() -> None:
    normalized, decisions = NormalizationEngine().normalize(
        mention(metric="mystery ratio", projection_issuer="Unlisted Entity"),
        ticker_hints=["MU"],
    )
    assert normalized.quantities[0].metric_id == "UNKNOWN_METRIC"
    assert normalized.schema_projection is None
    projection = next(item for item in decisions if item.field_path == "schema_projection")
    assert projection.method is NormalizationMethod.UNRESOLVED


def test_blank_entity_and_explicit_unknown_metric_avoid_embedding_or_m2() -> None:
    class NoCallEmbeddings:
        def embed(self, texts: list[str]) -> EmbeddingResult:
            raise AssertionError(f"blank values must not be embedded: {len(texts)}")

    class NoCallModel:
        def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
            raise AssertionError("blank values have no candidate IDs for M2")

    value = mention(participant="   ").model_copy(
        update={
            "quantities": [
                Quantity(metric_id="unknown_metric", value=1, unit="USD", raw_text="1 USD")
            ],
            "schema_projection": None,
        }
    )
    normalized, decisions = NormalizationEngine(
        embedding_client=NoCallEmbeddings(),
        fallback_client=NoCallModel(),
    ).normalize(value, ticker_hints=[])
    assert normalized.participants[0].entity_id is None
    assert normalized.quantities[0].metric_id == "UNKNOWN_METRIC"
    assert {item.unresolved_reason for item in decisions if item.unresolved_reason} == {
        "entity text is blank",
    }


def test_contract_source_timestamp_is_timezone_aware() -> None:
    assert datetime(2026, 6, 25, tzinfo=UTC).utcoffset() is not None


def test_quantity_normalization_does_not_double_scale() -> None:
    value = mention().model_copy(
        update={
            "quantities": [
                Quantity(
                    metric_id="revenue",
                    value=4_500_000_000,
                    unit="USD",
                    raw_text="$4.5B",
                )
            ]
        }
    )
    normalized, _ = NormalizationEngine().normalize(value, ticker_hints=["MU"])
    assert normalized.quantities[0].value == 4_500_000_000
