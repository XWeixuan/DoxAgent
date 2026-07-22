from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

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
    Language,
    LocalPackageHint,
    MembershipRelation,
    Participant,
    ParticipantRole,
    Predicate,
    SourceMessage,
    SourceType,
    TimePrecision,
)
from cdecr.cross_document import CrossDocumentEngine
from cdecr.cross_document_contracts import CrossDocumentStatus
from cdecr.ports import EmbeddingResult, StructuredModelRequest, StructuredModelResult
from cdecr.registry import SQLiteCDECRRegistry


class FakeEmbedding:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls += 1
        return EmbeddingResult(
            model="text-embedding-v4",
            dimensions=4,
            vectors=[[1.0, 0.0, 0.0, 0.0] for _ in texts],
            input_tokens=len(texts),
            latency_ms=1,
        )


class FakeStructured:
    def __init__(self, *, invalid_first: bool = False, always_invalid: bool = False) -> None:
        self.calls: list[StructuredModelRequest] = []
        self.invalid_first = invalid_first
        self.always_invalid = always_invalid

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.calls.append(request)
        if self.always_invalid or (self.invalid_first and len(self.calls) == 1):
            payload: dict[str, object] = {"bad": True}
        elif request.system_prompt.startswith("Repair"):
            original = json.loads(request.user_prompt)["original_request"]
            payload = self._atomic_payload(json.loads(original))
        elif "Mention-to-Cluster" in request.system_prompt:
            payload = self._atomic_payload(json.loads(request.user_prompt))
        elif "Atomic-to-Package" in request.system_prompt:
            body = json.loads(request.user_prompt)
            payload = {
                "decisions": [
                    {
                        "event_id": event_id,
                        "candidate_package_id": item["package"]["package_id"],
                        "relation": "NOT_RELATED",
                        "membership_relation": None,
                        "external_relation": None,
                    }
                    for event_id, values in body["candidates"].items()
                    for item in values
                ]
            }
        elif "Package-to-Package" in request.system_prompt:
            body = json.loads(request.user_prompt)
            payload = {
                "decisions": [
                    {
                        "source_package_id": item["source"]["package_id"],
                        "target_package_id": item["target"]["package_id"],
                        "relation": "DIFFERENT_PACKAGE",
                    }
                    for item in body["pairs"]
                ]
            }
        else:  # pragma: no cover - protects prompt routing
            raise AssertionError("unexpected prompt")
        return StructuredModelResult(model="fake", payload=payload, latency_ms=1)

    @staticmethod
    def _atomic_payload(body: dict[str, object]) -> dict[str, object]:
        candidates = body["candidates"]
        assert isinstance(candidates, dict)
        return {
            "decisions": [
                {
                    "mention_id": mention_id,
                    "candidate_event_id": item["event"]["event_id"],
                    "relation": "SAME_EVENT",
                    "claim_conflict": False,
                    "identity_conflicts": [],
                }
                for mention_id, values in candidates.items()
                for item in values
            ]
        }


def source(message_id: str) -> SourceMessage:
    return SourceMessage(
        message_id=message_id,
        source_type=SourceType.NEWS,
        title="Micron earnings update",
        text="Micron reported quarterly results with stronger demand.",
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name=f"Wire-{message_id}",
        url=f"https://example.test/{message_id}",
        ticker_hints=["MU"],
        language=Language.EN,
    )


def metric_mention(
    message_id: str,
    *,
    metric: str = "REVENUE",
    period: str = "FY2026-Q4",
) -> EventMention:
    return EventMention(
        mention_id=f"MENTION-{message_id}-{metric}-{period}",
        message_id=message_id,
        evidence_spans=[EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")],
        canonical_proposition=f"Micron reported {metric} for {period}.",
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
        assertion_state=AssertionState.ACTUAL,
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
                accounting_basis=AccountingBasis.GAAP,
            )
        ),
        local_package_hint=LocalPackageHint(
            anchor=f"MU {period} earnings",
            relation_to_anchor=MembershipRelation.DISCLOSED_IN,
        ),
    )


def market_mention(message_id: str) -> EventMention:
    return EventMention(
        mention_id=f"MENTION-{message_id}-MARKET",
        message_id=message_id,
        evidence_spans=[EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")],
        canonical_proposition="Micron shares rose after the earnings release.",
        source_claim=None,
        event_family=EventFamily.MARKET_MOVEMENT,
        predicate=Predicate(raw="rose", normalized="share_price_rise"),
        participants=[
            Participant(surface="Micron", entity_id="COMPANY_MU", role=ParticipantRole.SUBJECT)
        ],
        locations=[],
        time=EventTime(event_start=date(2026, 6, 25), precision=TimePrecision.DAY),
        assertion_state=AssertionState.ACTUAL,
        quantities=[],
        open_attributes=[],
    )


@pytest.fixture
def registry(tmp_path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(tmp_path / "engine.sqlite3")
    value.initialize()
    return value


def add(registry: SQLiteCDECRRegistry, message: SourceMessage, mention: EventMention) -> None:
    registry.save_source(message, fingerprint=(message.message_id[-1] * 64))
    registry.save_mention(mention)


def engine(
    registry: SQLiteCDECRRegistry,
    *,
    m2: FakeStructured | None = None,
    m3: FakeStructured | None = None,
) -> tuple[CrossDocumentEngine, FakeEmbedding, FakeStructured, FakeStructured]:
    embedding = FakeEmbedding()
    m2 = m2 or FakeStructured()
    m3 = m3 or FakeStructured()
    return (
        CrossDocumentEngine(
            registry=registry,
            embedding_client=embedding,
            m2_client=m2,
            m3_client=m3,
        ),
        embedding,
        m2,
        m3,
    )


def test_cold_start_incremental_merge_package_and_idempotency(
    registry: SQLiteCDECRRegistry,
) -> None:
    first_source = source("MSG-1")
    first_mention = metric_mention("MSG-1")
    add(registry, first_source, first_mention)
    processor, embedding, m2, _ = engine(registry)
    first = processor.process("MSG-1")
    assert first.status is CrossDocumentStatus.SUCCEEDED
    assert len(first.atomic_events) == 1
    assert len(first.packages) == 1
    assert m2.calls == []

    second_source = source("MSG-2")
    second_mention = metric_mention("MSG-2")
    add(registry, second_source, second_mention)
    second = processor.process("MSG-2")
    assert second.status is CrossDocumentStatus.SUCCEEDED
    assert second.atomic_events[0].event_id == first.atomic_events[0].event_id
    assert set(second.atomic_events[0].mention_ids) == {
        first_mention.mention_id,
        second_mention.mention_id,
    }
    assert len(registry.list_current_atomic_events()) == 1
    assert len(registry.list_current_packages()) == 1
    model_calls_before = registry.count_model_calls()
    embedding_calls_before = embedding.calls
    reused = processor.process("MSG-2")
    assert reused.reused
    assert registry.count_model_calls() == model_calls_before
    assert embedding.calls == embedding_calls_before


def test_same_batch_mentions_use_temporary_atomic_candidates(
    registry: SQLiteCDECRRegistry,
) -> None:
    message = source("MSG-1")
    first = metric_mention("MSG-1").model_copy(update={"mention_id": "MENTION-A"})
    second = metric_mention("MSG-1").model_copy(update={"mention_id": "MENTION-B"})
    registry.save_source(message, fingerprint="1" * 64)
    registry.save_mention(first)
    registry.save_mention(second)
    processor, _, m2, _ = engine(registry)
    result = processor.process(message.message_id)
    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert len(registry.list_current_atomic_events()) == 1
    assert set(registry.list_current_atomic_events()[0].mention_ids) == {
        first.mention_id,
        second.mention_id,
    }
    atomic_calls = [call for call in m2.calls if "Mention-to-Cluster" in call.system_prompt]
    assert len(atomic_calls) == 1


def test_hard_identity_splits_metrics_but_same_earnings_package(
    registry: SQLiteCDECRRegistry,
) -> None:
    processor, _, m2, _ = engine(registry)
    add(registry, source("MSG-1"), metric_mention("MSG-1", metric="REVENUE"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", metric="EPS_GAAP"))
    result = processor.process("MSG-2")
    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert len(registry.list_current_atomic_events()) == 2
    packages = registry.list_current_packages()
    assert len(packages) == 1
    assert len(packages[0].member_event_ids) == 2
    assert all("Mention-to-Cluster" not in call.system_prompt for call in m2.calls)
    assert result.candidate_counts["atomic_hard_blocked"] >= 1
    assert result.atomic_assignments[0].hard_conflicts


def test_different_period_splits_atomic_and_package(registry: SQLiteCDECRRegistry) -> None:
    processor, _, _, _ = engine(registry)
    add(registry, source("MSG-1"), metric_mention("MSG-1", period="FY2026-Q4"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", period="FY2027-Q1"))
    result = processor.process("MSG-2")
    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert len(registry.list_current_atomic_events()) == 2
    assert len(registry.list_current_packages()) == 2


def test_market_reaction_is_external_not_package_member(
    registry: SQLiteCDECRRegistry,
) -> None:
    processor, _, _, _ = engine(registry)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    first = processor.process("MSG-1")
    earnings = first.packages[0]
    add(registry, source("MSG-2"), market_mention("MSG-2"))
    reaction = processor.process("MSG-2")
    assert reaction.status is CrossDocumentStatus.SUCCEEDED
    assert len(registry.list_current_packages()) == 1
    current = registry.get_current_package(earnings.package_id)
    assert current is not None
    assert len(current.member_event_ids) == 1
    relations = registry.list_package_external_relations()
    assert len(relations) == 1
    assert relations[0].relation.value == "MARKET_REACTION_TO"
    assert reaction.package_assignments[0].action.value == "LINK_EXTERNALLY"


def test_invalid_structured_output_gets_one_repair(registry: SQLiteCDECRRegistry) -> None:
    m2 = FakeStructured(invalid_first=True)
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))
    result = processor.process("MSG-2")
    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert len(m2.calls) == 2
    assert m2.calls[1].system_prompt.startswith("Repair")


def test_persistent_invalid_output_fails_document_without_fake_result(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = FakeStructured(always_invalid=True)
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))
    result = processor.process("MSG-2")
    assert result.status is CrossDocumentStatus.FAILED
    assert result.error_code == "structured_output_invalid"
    assert len(m2.calls) == 2
