from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cdecr.atomic_identity import atomic_identity_text
from cdecr.canonical_field_resolution import CanonicalFieldResolutionEngine
from cdecr.contracts import (
    AccountingBasis,
    AssertionState,
    AtomicAction,
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
    PackageQualityState,
    Participant,
    ParticipantRole,
    Predicate,
    Quantity,
    QuantityRole,
    SourceMessage,
    SourceType,
    TimePrecision,
)
from cdecr.coreference_rules import merge_event_times, singleton_atomic_event
from cdecr.cross_document import CrossDocumentEngine
from cdecr.cross_document_contracts import CrossDocumentStatus
from cdecr.field_coreference_contracts import CanonicalFieldRegistryEntry
from cdecr.ports import EmbeddingResult, StructuredModelRequest, StructuredModelResult
from cdecr.registry import SQLiteCDECRRegistry


class FakeEmbedding:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls += 1
        return EmbeddingResult(
            model="qwen3.7-text-embedding",
            dimensions=4,
            vectors=[[1.0, 0.0, 0.0, 0.0] for _ in texts],
            input_tokens=len(texts),
            latency_ms=1,
        )


def test_bulk_epoch_orders_inputs_and_defers_n13_until_final_document() -> None:
    class RegistryStub:
        def __init__(self) -> None:
            self.sources = {
                "late": SourceMessage(
                    message_id="late",
                    source_type=SourceType.NEWS,
                    title="late",
                    text="late",
                    published_at=datetime(2026, 7, 2, tzinfo=UTC),
                    source_name="example",
                    url="https://example.com/late",
                    ticker_hints=["MU"],
                    language=Language.EN,
                ),
                "early": SourceMessage(
                    message_id="early",
                    source_type=SourceType.NEWS,
                    title="early",
                    text="early",
                    published_at=datetime(2026, 7, 1, tzinfo=UTC),
                    source_name="example",
                    url="https://example.com/early",
                    ticker_hints=["MU"],
                    language=Language.EN,
                ),
            }

        def get_source(self, message_id: str) -> SourceMessage | None:
            return self.sources.get(message_id)

    engine = object.__new__(CrossDocumentEngine)
    engine.registry = RegistryStub()
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_process(message_id: str, **kwargs: object) -> str:
        calls.append((message_id, kwargs))
        return message_id

    engine.process = fake_process  # type: ignore[method-assign]
    assert engine.process_batch(["late", "early"], execution_mode="BULK_EPOCH") == [
        "early",
        "late",
    ]
    assert calls[0][1]["defer_package_merge"] is True
    assert calls[1][1]["defer_package_merge"] is False
    assert calls[0][1]["bulk_epoch_id"] == calls[1][1]["bulk_epoch_id"]


class FakeStructured:
    def __init__(
        self,
        *,
        invalid_first: bool = False,
        always_invalid: bool = False,
        atomic_relation: str | None = None,
        package_merge_relation: str = "DIFFERENT_PACKAGE",
        malformed_derived_fields: bool = False,
    ) -> None:
        self.calls: list[StructuredModelRequest] = []
        self.invalid_first = invalid_first
        self.always_invalid = always_invalid
        self.atomic_relation = atomic_relation
        self.package_merge_relation = package_merge_relation
        self.malformed_derived_fields = malformed_derived_fields
        self.invalid_emitted = False

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.calls.append(request)
        is_field_request = "typed field value" in request.system_prompt
        if is_field_request:
            payload: dict[str, object] = {"decision": "NEW"}
        elif self.always_invalid or (self.invalid_first and not self.invalid_emitted):
            self.invalid_emitted = True
            payload = {"bad": True}
        elif request.system_prompt.startswith("Repair"):
            original = json.loads(request.user_prompt)["original_request"]
            payload = self._payload_for_body(json.loads(original))
        elif "Atomic Event Assignment Adjudicator" in request.system_prompt:
            payload = self._atomic_payload(json.loads(request.user_prompt))
        elif "Atomic-to-Package" in request.system_prompt:
            payload = self._package_payload(json.loads(request.user_prompt))
        elif (
            "Package-to-Package" in request.system_prompt
            or "Package coreference review model" in request.system_prompt
        ):
            payload = self._package_merge_payload(json.loads(request.user_prompt))
        else:  # pragma: no cover - protects prompt routing
            raise AssertionError("unexpected prompt")
        return StructuredModelResult(model="fake", payload=payload, latency_ms=1)

    def _payload_for_body(self, body: dict[str, object]) -> dict[str, object]:
        if "tasks" in body:
            return self._atomic_payload(body)
        if "candidates" in body:
            return self._package_payload(body)
        if "pairs" in body:
            return self._package_merge_payload(body)
        raise AssertionError("unexpected repair body")

    def _package_payload(self, body: dict[str, object]) -> dict[str, object]:
        events = body["events"]
        if "tasks" in body:
            tasks = body["tasks"]
            assert isinstance(tasks, list)
            seeds = {
                task["event_id"]: task["seed"]
                for task in tasks
                if isinstance(task, dict)
            }
            candidates = {
                task["event_id"]: task["candidates"]
                for task in tasks
                if isinstance(task, dict)
            }
        else:
            seeds = body["seeds"]
            candidates = body["candidates"]
        packages = body.get("packages", {})
        assert isinstance(events, dict)
        assert isinstance(seeds, dict)
        assert isinstance(candidates, dict)
        assert isinstance(packages, dict)
        decisions: list[dict[str, object]] = []
        for event_id, values in candidates.items():
            assert isinstance(event_id, str)
            assert isinstance(values, list)
            event = events[event_id]
            seed = seeds[event_id]
            assert isinstance(event, dict)
            assert isinstance(seed, dict)
            assessments: list[dict[str, object]] = []
            members: list[str] = []
            for value in values:
                assert isinstance(value, dict)
                if "package_id" in value:
                    package_id = value["package_id"]
                    package_view = packages[package_id]
                    assert isinstance(package_view, dict)
                    package = package_view.get("package", package_view)
                else:
                    package = value["package"]
                    assert isinstance(package, dict)
                    package_id = package["package_id"]
                assert isinstance(package, dict)
                assert isinstance(package_id, str)
                if event.get("event_family", event.get("family")) == "MARKET_MOVEMENT":
                    relation = "EXTERNAL_RELATED"
                    membership = None
                    external = "MARKET_REACTION_TO"
                elif package.get("anchor_period_id", package.get("period")) == seed.get(
                    "anchor_period_id"
                ):
                    relation = "MEMBER"
                    membership = "DISCLOSED_IN"
                    external = None
                    members.append(package_id)
                else:
                    relation = "NOT_RELATED"
                    membership = None
                    external = None
                assessments.append(
                    {
                        "candidate_package_id": package_id,
                        "relation": relation,
                        "membership_relation": membership,
                        "external_relation": external,
                        "reason": f"fake-{relation.casefold()}",
                    }
                )
            decisions.append(
                {
                    "event_id": event_id,
                    "candidate_assessments": assessments,
                    "ranked_member_package_ids": members,
                    "selected_member_package_id": members[0] if members else None,
                    "selection_reason": "fake-canonical-target" if members else None,
                }
            )
        return {"decisions": decisions}

    def _package_merge_payload(self, body: dict[str, object]) -> dict[str, object]:
        pairs = body["pairs"]
        assert isinstance(pairs, list)

        def package_id(value: object) -> object:
            assert isinstance(value, dict)
            package = value.get("package", value)
            assert isinstance(package, dict)
            return package["package_id"]

        return {
            "decisions": [
                {
                    **(
                        {"pair_id": item["id"]}
                        if "id" in item
                        else {
                            "source_package_id": (
                                item["source_package_id"]
                                if "source_package_id" in item
                                else package_id(item["source"])
                            ),
                            "target_package_id": (
                                item["target_package_id"]
                                if "target_package_id" in item
                                else package_id(item["target"])
                            ),
                        }
                    ),
                    "relation": self.package_merge_relation,
                    "reason": f"fake-{self.package_merge_relation.casefold()}",
                }
                for item in pairs
            ]
        }

    def _atomic_payload(self, body: dict[str, object]) -> dict[str, object]:
        tasks = body["tasks"]
        atoms = body.get("atoms", {})
        assert isinstance(tasks, list)
        assert isinstance(atoms, dict)
        decisions: list[dict[str, object]] = []
        for task in tasks:
            assert isinstance(task, dict)
            incoming = task["incoming"]
            candidates = task["candidates"]
            assert isinstance(incoming, dict)
            assert isinstance(candidates, list)
            expanded_candidates: list[dict[str, object]] = []
            for candidate in candidates:
                assert isinstance(candidate, dict)
                if atoms:
                    atom = atoms[candidate["event_id"]]
                    assert isinstance(atom, dict)
                    expanded_candidates.append(atom)
                else:
                    expanded_candidates.append(candidate)
            assessments = [
                {
                    "candidate_event_id": candidate["event_id"],
                    "relation": (
                        self.atomic_relation
                        or (
                            "SAME_EVENT"
                            if candidate["identity_profile"] == incoming["identity_profile"]
                            else "UNRELATED"
                        )
                    ),
                    "axis_assessments": [
                        {
                            "axis": axis,
                            "verdict": (
                                "MATCH"
                                if candidate["identity_profile"]
                                == incoming["identity_profile"]
                                else "CONFLICT"
                            ),
                        }
                        for axis in incoming.get("identity_axes", {})
                    ],
                    "claim_conflict": False,
                    "identity_differences": (
                        []
                        if candidate["identity_profile"] == incoming["identity_profile"]
                        else ["identity_profile"]
                    ),
                }
                for candidate in expanded_candidates
            ]
            same = [
                item["candidate_event_id"]
                for item in assessments
                if item["relation"] == "SAME_EVENT"
            ]
            related = [
                item["candidate_event_id"]
                for item in assessments
                if item["relation"] == "RELATED_NOT_SAME"
            ]
            decisions.append(
                {
                    "mention_id": incoming["mention_id"],
                    "action": "MERGE" if same else "CREATE_NEW",
                    "merge_target_event_id": (
                        f"{same[0]}-copy-error"
                        if same and self.malformed_derived_fields
                        else (same[0] if same else None)
                    ),
                    "candidate_assessments": assessments,
                    "related_candidate_event_ids": (
                        ["not-an-input-id"] if self.malformed_derived_fields else related
                    ),
                    "possible_duplicate_atomic_ids": (
                        ["not-an-input-id"] if self.malformed_derived_fields else same[1:]
                    ),
                }
            )
        return {"decisions": decisions}


class AtomicCoverageDrift(FakeStructured):
    def __init__(self, *, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self.injected = False

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if (
            self.injected
            or request.system_prompt.startswith("Repair")
            or "Atomic Event Assignment Adjudicator" not in request.system_prompt
        ):
            return result
        self.injected = True
        decisions = result.payload["decisions"]
        assert isinstance(decisions, list) and decisions
        assessments = decisions[0]["candidate_assessments"]
        assert isinstance(assessments, list) and assessments
        if self.mode == "extra_duplicate":
            duplicate = json.loads(json.dumps(assessments[0]))
            extra = json.loads(json.dumps(assessments[0]))
            extra["candidate_event_id"] = "a999"
            assessments.extend([duplicate, extra])
        elif self.mode == "missing":
            decisions[0]["candidate_assessments"] = []
            decisions[0]["action"] = "CREATE_NEW"
            decisions[0]["merge_target_event_id"] = None
            decisions[0]["related_candidate_event_ids"] = []
            decisions[0]["possible_duplicate_atomic_ids"] = []
        elif self.mode == "missing_axis":
            axis_assessments = assessments[0]["axis_assessments"]
            assert isinstance(axis_assessments, list) and axis_assessments
            assessments[0]["axis_assessments"] = axis_assessments[:-1]
        else:  # pragma: no cover - test helper guard
            raise AssertionError(self.mode)
        return result


class PackageDerivedFieldDrift(FakeStructured):
    def _package_payload(self, body: dict[str, object]) -> dict[str, object]:
        payload = super()._package_payload(body)
        decisions = payload["decisions"]
        assert isinstance(decisions, list)
        for decision in decisions:
            assert isinstance(decision, dict)
            assessments = decision["candidate_assessments"]
            assert isinstance(assessments, list)
            if any(
                isinstance(item, dict) and item.get("relation") == "MEMBER" for item in assessments
            ):
                decision["ranked_member_package_ids"] = []
                decision["selected_member_package_id"] = None
                decision["selection_reason"] = None
        return payload


class PackageCoverageDrift(FakeStructured):
    def _package_payload(self, body: dict[str, object]) -> dict[str, object]:
        payload = super()._package_payload(body)
        decisions = payload["decisions"]
        assert isinstance(decisions, list) and decisions
        decisions[0]["candidate_assessments"] = []
        decisions[0]["ranked_member_package_ids"] = []
        decisions[0]["selected_member_package_id"] = None
        decisions[0]["selection_reason"] = None
        return payload


class PackageMergeCoverageDrift(FakeStructured):
    def _package_merge_payload(self, body: dict[str, object]) -> dict[str, object]:
        del body
        return {"decisions": []}


class PackageAndMergeCoverageDrift(PackageCoverageDrift):
    def _package_merge_payload(self, body: dict[str, object]) -> dict[str, object]:
        del body
        return {"decisions": []}


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
        quantities=[
            Quantity(
                metric_id=metric,
                value=9.3,
                unit="USD_B",
                raw_text=f"{metric} was 9.3 billion",
                role=QuantityRole.PRIMARY,
            )
        ],
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


def test_merge_event_times_normalizes_mixed_timezone_awareness() -> None:
    merged = merge_event_times(
        EventTime(
            event_start=datetime(2026, 7, 22, 12, tzinfo=UTC),
            event_end=datetime(2026, 7, 22, 12, tzinfo=UTC),
            precision=TimePrecision.TIMESTAMP,
        ),
        EventTime(
            event_start=datetime(2026, 7, 22, 13),
            event_end=datetime(2026, 7, 22, 14),
            precision=TimePrecision.TIMESTAMP,
        ),
    )

    assert merged.event_start == datetime(2026, 7, 22, 12)
    assert merged.event_end == datetime(2026, 7, 22, 14)


def engine(
    registry: SQLiteCDECRRegistry,
    *,
    m2: FakeStructured | None = None,
    m3: FakeStructured | None = None,
    hard_cannot_link_mode: str = "shadow",
    n12_wire_protocol: str = "shadow",
    n13_wire_protocol: str = "shadow",
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
            hard_cannot_link_mode=hard_cannot_link_mode,
            n12_wire_protocol=n12_wire_protocol,
            n13_wire_protocol=n13_wire_protocol,
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
    assert not any(
        "Atomic Event Assignment Adjudicator" in call.system_prompt for call in m2.calls
    )

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


def test_completed_result_is_reused_before_field_coreference(
    registry: SQLiteCDECRRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor, embedding, _, _ = engine(registry)
    first = processor.process("MSG-1")
    assert first.status is CrossDocumentStatus.SUCCEEDED
    model_calls_before = registry.count_model_calls()
    embedding_calls_before = embedding.calls

    def unexpected_resolution(*args: object, **kwargs: object) -> object:
        raise AssertionError("completed replay must not enter N5.5")

    monkeypatch.setattr(
        CanonicalFieldResolutionEngine,
        "resolve_document",
        unexpected_resolution,
    )

    reused = processor.process("MSG-1")

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
    atomic_calls = [
        call for call in m2.calls if "Atomic Event Assignment Adjudicator" in call.system_prompt
    ]
    assert len(atomic_calls) == 1


def test_same_batch_distinct_atomics_share_provisional_parent_context_package(
    registry: SQLiteCDECRRegistry,
) -> None:
    message = source("MSG-1")
    revenue = metric_mention("MSG-1", metric="REVENUE").model_copy(
        update={"mention_id": "MENTION-REVENUE"}
    )
    eps = metric_mention("MSG-1", metric="EPS_GAAP").model_copy(
        update={"mention_id": "MENTION-EPS"}
    )
    registry.save_source(message, fingerprint="1" * 64)
    registry.save_mention(revenue)
    registry.save_mention(eps)
    processor, _, _, _ = engine(registry)

    result = processor.process(message.message_id)

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert len(registry.list_current_atomic_events()) == 2
    packages = registry.list_current_packages()
    assert len(packages) == 1
    assert len(packages[0].member_event_ids) == 2
    with sqlite3.connect(registry.path) as connection:
        audit = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'PACKAGE_PARENT_CONTEXT_GROUP'
            """,
            (result.run_id,),
        ).fetchone()
    assert audit is not None
    assert json.loads(audit[0])["parent_context_id"].startswith("parent-context:")


def test_n9_keeps_three_mentions_per_request_and_uses_only_short_ids(
    registry: SQLiteCDECRRegistry,
) -> None:
    processor, _, m2, _ = engine(registry)
    add(registry, source("MSG-0"), metric_mention("MSG-0"))
    processor.process("MSG-0")
    registry.save_source(source("MSG-1"), fingerprint="1" * 64)
    for index in range(3):
        registry.save_mention(
            metric_mention("MSG-1").model_copy(update={"mention_id": f"MENTION-BATCH-{index}"})
        )

    result = processor.process("MSG-1")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    atomic_calls = [
        call for call in m2.calls if "Atomic Event Assignment Adjudicator" in call.system_prompt
    ]
    assert len(atomic_calls) == 1
    payload = json.loads(atomic_calls[0].user_prompt)
    assert [task["incoming"]["mention_id"] for task in payload["tasks"]] == [
        "m1",
        "m2",
        "m3",
    ]
    assert all(
        candidate["event_id"].startswith(f"m{task_index}c")
        for task_index, task in enumerate(payload["tasks"], start=1)
        for candidate in task["candidates"]
    )
    assert all(
        not candidate["event_id"].startswith(("atomic:", "provisional:"))
        for task in payload["tasks"]
        for candidate in task["candidates"]
    )
    assert all(
        "recall_routes" not in candidate and "recall_score" not in candidate
        for task in payload["tasks"]
        for candidate in task["candidates"]
    )
    assert all(
        task["incoming"]["identity_axes"]
        and all(candidate["identity_axes"] for candidate in task["candidates"])
        for task in payload["tasks"]
    )
    with sqlite3.connect(registry.path) as connection:
        shadow_payloads = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload_json FROM decision_audits "
                "WHERE decision_type = 'WIRE_PAYLOAD_SHADOW'"
            )
        ]
    assert shadow_payloads
    assert all(item["mode"] == "shadow_not_sent" for item in shadow_payloads)
    assert any(item["estimated_savings_bytes"] > 0 for item in shadow_payloads)


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
    assert any("Atomic Event Assignment Adjudicator" in call.system_prompt for call in m2.calls)
    assert result.candidate_counts["atomic_hard_conflict_observed"] >= 1
    assert not result.atomic_assignments[0].hard_conflicts


def test_n12_normalizes_redundant_member_selection_fields(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = PackageDerivedFieldDrift()
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1", metric="REVENUE"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", metric="EPS_GAAP"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert result.package_assignments[0].selected_member_package_id is not None
    with sqlite3.connect(registry.path) as connection:
        audit = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'PACKAGE_N12_NORMALIZATION'
            """,
            (result.run_id,),
        ).fetchone()
    assert audit is not None
    normalizations = json.loads(audit[0])["normalizations"]
    assert any(item["kind"] == "MEMBER_RANKING_REBUILT" for item in normalizations)
    assert any(item["kind"] == "SELECTED_MEMBER_ALIGNED" for item in normalizations)


def test_legacy_enforce_request_is_shadow_only_for_atomic_candidate(
    registry: SQLiteCDECRRegistry,
) -> None:
    processor, _, m2, _ = engine(registry, hard_cannot_link_mode="enforce")
    add(registry, source("MSG-1"), metric_mention("MSG-1", metric="REVENUE"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", metric="EPS_GAAP"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert not result.atomic_assignments[0].hard_conflicts
    assert any("Atomic Event Assignment Adjudicator" in call.system_prompt for call in m2.calls)


def test_atomic_embedding_tracks_exact_current_identity_text(
    registry: SQLiteCDECRRegistry,
) -> None:
    processor, _, _, _ = engine(registry)
    first_mention = metric_mention("MSG-1")
    add(registry, source("MSG-1"), first_mention)
    first = processor.process("MSG-1")
    first_event = first.atomic_events[0]
    first_text = atomic_identity_text(first_event, [first_mention])
    first_hash = hashlib.sha256(first_text.encode("utf-8")).hexdigest()
    assert (
        registry.get_embedding(
            owner_kind="atomic_event",
            owner_id=first_event.event_id,
            model="qwen3.7-text-embedding",
            input_hash=first_hash,
        )
        is not None
    )
    second_mention = metric_mention("MSG-2")
    add(registry, source("MSG-2"), second_mention)
    second = processor.process("MSG-2")
    current = second.atomic_events[0]
    representatives = [
        item
        for mention_id in current.representative_mention_ids
        if (item := registry.get_mention(mention_id)) is not None
    ]
    current_text = atomic_identity_text(current, representatives)
    current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()

    assert current.version == 2
    assert current_hash != first_hash
    assert (
        registry.get_embedding(
            owner_kind="atomic_event",
            owner_id=current.event_id,
            model="qwen3.7-text-embedding",
            input_hash=current_hash,
        )
        is not None
    )


def test_n9_related_candidate_is_audited_without_formal_relation(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = FakeStructured(atomic_relation="RELATED_NOT_SAME")
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    first = processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))

    second = processor.process("MSG-2")

    assert len(registry.list_current_atomic_events()) == 2
    assert second.atomic_assignments[0].related_candidate_event_ids == [
        first.atomic_events[0].event_id
    ]
    assert registry.list_external_relations() == []


def test_n9_persistent_uncertainty_escalates_to_m3_then_creates_new(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = FakeStructured(atomic_relation="UNCERTAIN")
    m3 = FakeStructured(atomic_relation="UNCERTAIN")
    processor, _, _, used_m3 = engine(registry, m2=m2, m3=m3)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))

    second = processor.process("MSG-2")

    assert second.atomic_assignments[0].action.value == "CREATE_NEW"
    assert len(registry.list_current_atomic_events()) == 2
    assert any(
        "Atomic Event Assignment Adjudicator" in call.system_prompt for call in used_m3.calls
    )


def test_multiple_same_candidates_absorb_only_singleton_duplicate(
    registry: SQLiteCDECRRegistry,
) -> None:
    first = metric_mention("MSG-1")
    add(registry, source("MSG-1"), first)
    processor, _, _, m3 = engine(registry)
    first_result = processor.process("MSG-1")
    second = metric_mention("MSG-2")
    add(registry, source("MSG-2"), second)
    duplicate_event = singleton_atomic_event(
            second,
            identity_profile=first_result.atomic_events[0].identity_profile,
        ).model_copy(update={"event_id": "atomic:zzzz-singleton"})
    registry.save_atomic_event(
        duplicate_event
    )
    third = metric_mention("MSG-3")
    add(registry, source("MSG-3"), third)

    result = processor.process("MSG-3")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert result.atomic_assignments[0].possible_duplicate_atomic_ids == []
    assert len(registry.list_current_atomic_events()) == 1
    assert len(registry.list_current_atomic_events()[0].mention_ids) == 3
    assert any(
        "Atomic Event Assignment Adjudicator" in call.system_prompt for call in m3.calls
    )


def test_identity_root_change_requires_derived_rebuild_before_reassignment(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = market_mention("MSG-1")
    add(registry, source("MSG-1"), mention)
    processor, _, _, _ = engine(registry)
    first = processor.process("MSG-1")
    original_profile = first.atomic_events[0].identity_profile
    link = registry.get_field_link(mention.mention_id, "predicate.normalized")
    assert link is not None
    root = registry.resolve_field_registry_entry(link.registry_id)
    assert root is not None
    registry.create_field_registry_entry(
        CanonicalFieldRegistryEntry(
            id="FIELD:redirect-target",
            namespace=root.namespace,
            canonical_text="share price increase replacement",
            aliases=["share price increase replacement"],
        )
    )
    registry.save_field_redirect(root.id, "FIELD:redirect-target")

    failed = processor.process("MSG-1")

    assert failed.status is CrossDocumentStatus.FAILED
    assert failed.error_code == "DERIVED_STATE_REBUILD_REQUIRED"
    current = registry.list_current_atomic_events()
    assert len(current) == 1
    assert current[0].identity_profile == original_profile
    assert current[0].mention_ids == [mention.mention_id]

    registry.rebuild_derived_state()
    rebuilt = processor.process("MSG-1")

    assert rebuilt.status is CrossDocumentStatus.SUCCEEDED
    assert len(registry.list_current_atomic_events()) == 1


def test_different_period_splits_atomic_and_package(registry: SQLiteCDECRRegistry) -> None:
    processor, _, _, _ = engine(registry)
    add(registry, source("MSG-1"), metric_mention("MSG-1", period="FY2026-Q4"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", period="FY2027-Q1"))
    result = processor.process("MSG-2")
    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert len(registry.list_current_atomic_events()) == 2
    assert len(registry.list_current_packages()) == 2
    assert result.candidate_counts["atomic_hard_conflict_observed"] == 1
    assert result.candidate_counts["package_hard_blocked"] == 0


def test_n12_payload_includes_raw_surface_and_request_local_source_ids(
    registry: SQLiteCDECRRegistry,
) -> None:
    processor, _, m2, _ = engine(registry)
    add(registry, source("MSG-1"), metric_mention("MSG-1", period="FY2026-Q4"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", period="FY2027-Q1"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    request = next(
        call for call in m2.calls if "Atomic-to-Package assignment" in call.system_prompt
    )
    payload = json.loads(request.user_prompt)
    event_id, incoming = next(iter(payload["events"].items()))
    candidate = payload["candidates"][event_id][0]
    assert candidate["package"]["package_id"].startswith("p")
    member = candidate["representative_members"][0]
    assert "Micron" in {
        surface
        for participant in incoming["surface_evidence"]["participants"]
        for surface in participant["surfaces"]
    }
    incoming_source = incoming["source_ids"][0]
    assert "Micron" in {
        surface
        for participant in member["surface_evidence"]["participants"]
        for surface in participant["surfaces"]
    }
    member_source = member["source_ids"][0]
    assert {incoming_source, member_source} == {"s1", "s2"}
    assert "MSG-1" not in request.user_prompt
    assert "MSG-2" not in request.user_prompt


def test_n12_on_uses_shared_package_dictionary_and_bounded_cards(
    registry: SQLiteCDECRRegistry,
) -> None:
    processor, _, m2, _ = engine(registry, n12_wire_protocol="on")
    add(registry, source("MSG-1"), metric_mention("MSG-1", period="FY2026-Q4"))
    processor.process("MSG-1")
    registry.save_source(source("MSG-2"), fingerprint="2" * 64)
    registry.save_mention(metric_mention("MSG-2", metric="EPS_GAAP"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    request = next(
        call for call in m2.calls if "Atomic-to-Package assignment" in call.system_prompt
    )
    payload = json.loads(request.user_prompt)
    assert set(payload) == {"events", "packages", "tasks"}
    assert len(payload["packages"]) == 1
    task = payload["tasks"][0]
    assert task["event_id"] in payload["events"]
    assert task["candidates"][0]["package_id"] in payload["packages"]
    card = payload["packages"][task["candidates"][0]["package_id"]]
    assert card["member_count"] == 1
    assert "member_event_ids" not in json.dumps(card)
    assert "consensus_claims" not in request.user_prompt


def test_n12_invalid_task_degrades_to_new_package_without_batch_repair(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = PackageCoverageDrift()
    processor, _, _, _ = engine(registry, m2=m2, n12_wire_protocol="on")
    add(registry, source("MSG-1"), metric_mention("MSG-1", period="FY2026-Q4"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", period="FY2027-Q1"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert not any(summary.stage == "package_assignment_repair" for summary in result.model_calls)
    assert any(
        summary.stage == "package_assignment_item_repair" for summary in result.model_calls
    )
    assert result.package_assignments[0].action.value == "CREATE_NEW_PACKAGE"


def test_frozen_package_enters_n13_and_can_return_active(
    registry: SQLiteCDECRRegistry,
) -> None:
    m3 = FakeStructured(package_merge_relation="SAME_PACKAGE")
    processor, _, _, _ = engine(registry, m3=m3)
    add(
        registry,
        source("MSG-1"),
        metric_mention("MSG-1", metric="REVENUE", period="FY2026-Q4"),
    )
    first = processor.process("MSG-1")
    frozen = first.packages[0].model_copy(
        update={
            "quality_state": PackageQualityState.FROZEN,
            "version": first.packages[0].version + 1,
        }
    )
    registry.save_package(frozen)
    add(
        registry,
        source("MSG-2"),
        metric_mention("MSG-2", metric="EPS_GAAP", period="FY2026-Q4"),
    )

    second = processor.process("MSG-2")

    assert second.status is CrossDocumentStatus.SUCCEEDED
    roots = {
        registry.resolve_package_root(package.package_id)
        for package in registry.list_current_packages()
    }
    assert len(roots) == 1
    root_id = next(iter(roots))
    assert root_id is not None
    root = registry.get_current_package(root_id)
    assert root is not None
    assert root.quality_state is PackageQualityState.ACTIVE
    assert len(root.member_event_ids) == 2
    assert any("Package coreference review model" in call.system_prompt for call in m3.calls)


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
    assert len(registry.list_current_packages()) == 2
    current = registry.get_current_package(earnings.package_id)
    assert current is not None
    assert len(current.member_event_ids) == 1
    assert registry.list_package_external_relations() == []
    candidates = registry.list_package_external_relation_candidates()
    assert len(candidates) == 1
    assert candidates[0].relation.value == "MARKET_REACTION_TO"
    assert reaction.package_assignments[0].action.value == "CREATE_NEW_PACKAGE"


def test_n13_external_relation_is_advisory_and_boundary_repair_keeps_separate(
    registry: SQLiteCDECRRegistry,
) -> None:
    m3 = FakeStructured(package_merge_relation="SAME_PACKAGE")
    processor, _, _, _ = engine(registry, m3=m3)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), market_mention("MSG-2"))

    reaction = processor.process("MSG-2")

    assert reaction.status is CrossDocumentStatus.SUCCEEDED
    packages = registry.list_current_packages()
    assert len(packages) == 2
    family_sets = [
        {
            registry.get_current_atomic_event(event_id).event_family
            for event_id in package.member_event_ids
        }
        for package in packages
    ]
    assert all(
        not (
            EventFamily.MARKET_MOVEMENT in families
            and EventFamily.FINANCIAL_PERFORMANCE in families
        )
        for families in family_sets
    )
    with sqlite3.connect(registry.path) as connection:
        contexts = [
            json.loads(row[0])["invocation_context"]
            for row in connection.execute(
                "SELECT payload_json FROM decision_audits "
                "WHERE decision_type = 'WIRE_PAYLOAD_SHADOW' "
                "AND run_id = ?",
                (reaction.run_id,),
            )
        ]
        weak_same_not_applied = connection.execute(
            "SELECT COUNT(*) FROM decision_audits "
            "WHERE decision_type = 'SAME_PACKAGE_NOT_APPLIED_WEAK_BOUNDARY' "
            "AND run_id = ?",
            (reaction.run_id,),
        ).fetchone()[0]
    assert any(item["operation"] == "normal_assignment" for item in contexts)
    assert not any(item["operation"] == "reaction_member_repair" for item in contexts)
    assert weak_same_not_applied >= 1
    with sqlite3.connect(registry.path) as connection:
        shadow_count = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ?
              AND decision_type = 'PACKAGE_EXTERNAL_RELATION_GUARD_SHADOW'
            """,
            (reaction.run_id,),
        ).fetchone()[0]
    assert shadow_count == 1


def test_n13_on_fails_closed_until_full_business_gate(
    registry: SQLiteCDECRRegistry,
) -> None:
    with pytest.raises(ValueError, match="full business gate"):
        engine(registry, n13_wire_protocol="on")


def test_n13_uses_pair_ids_and_records_smaller_pair_inline_shadow(
    registry: SQLiteCDECRRegistry,
) -> None:
    m3 = PackageCoverageDrift(package_merge_relation="DIFFERENT_PACKAGE")
    processor, _, _, _ = engine(
        registry, m2=m3, m3=m3, n13_wire_protocol="shadow"
    )
    add(registry, source("MSG-1"), metric_mention("MSG-1", metric="REVENUE"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", metric="GROSS_MARGIN"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    request = next(
        call for call in m3.calls if "Package coreference review model" in call.system_prompt
    )
    request_payload = json.loads(request.user_prompt)
    assert set(request_payload) == {"batch_index", "batch_count", "pairs"}
    assert all(pair["pair_id"].startswith("r") for pair in request_payload["pairs"])
    assert request.json_schema["title"] == "PackageMergeWireDecisionBatch"
    with sqlite3.connect(registry.path) as connection:
        audit = json.loads(
            connection.execute(
                """
                SELECT payload_json
                FROM decision_audits
                WHERE decision_type = 'WIRE_PAYLOAD_SHADOW'
                  AND subject_id LIKE 'package_merge:%'
                """
            ).fetchone()[0]
        )
    assert audit["optimized_payload_bytes"] < audit["baseline_payload_bytes"]
    assert audit["wire_ref_count"] == 0


def test_n13_canary_uses_dictionary_slim_view_and_persists_pair_profile(
    registry: SQLiteCDECRRegistry,
) -> None:
    m3 = PackageCoverageDrift(package_merge_relation="DIFFERENT_PACKAGE")
    processor, _, _, _ = engine(
        registry, m2=m3, m3=m3, n13_wire_protocol="canary"
    )
    add(registry, source("MSG-1"), metric_mention("MSG-1", metric="REVENUE"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", metric="GROSS_MARGIN"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    request = next(
        call for call in m3.calls if "Package coreference review model" in call.system_prompt
    )
    payload = json.loads(request.user_prompt)
    assert set(payload) == {"batch_index", "batch_count", "packages", "pairs"}
    assert all(
        set(pair) == {"id", "left", "right", "routes", "similarity"} for pair in payload["pairs"]
    )
    assert all(pair["left"] in payload["packages"] for pair in payload["pairs"])
    assert all(pair["right"] in payload["packages"] for pair in payload["pairs"])
    assert all("identity_profile" not in json.dumps(view) for view in payload["packages"].values())
    with sqlite3.connect(registry.path) as connection:
        profile = json.loads(
            connection.execute(
                """
                SELECT payload_json FROM decision_audits
                WHERE run_id = ? AND decision_type = 'PACKAGE_N13_PAYLOAD_PROFILE'
                """,
                (result.run_id,),
            ).fetchone()[0]
        )
        pair_count = connection.execute(
            "SELECT COUNT(*) FROM package_pair_evaluations WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0]
    assert profile["optimized_bytes"] < profile["baseline_bytes"]
    assert profile["field_bytes"]["packages"] > 0
    assert pair_count >= 1


def test_n13_missing_pair_degrades_to_no_merge_without_batch_repair(
    registry: SQLiteCDECRRegistry,
) -> None:
    m3 = PackageAndMergeCoverageDrift()
    processor, _, _, _ = engine(registry, m2=m3, m3=m3)
    add(registry, source("MSG-1"), metric_mention("MSG-1", metric="REVENUE"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2", metric="GROSS_MARGIN"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert not any(summary.stage == "package_merge_repair" for summary in result.model_calls)
    with sqlite3.connect(registry.path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ? AND decision_type = 'PACKAGE_N13_DEGRADED'
            """,
            (result.run_id,),
        ).fetchone()[0]
    assert count >= 1


def test_invalid_structured_output_gets_one_repair(registry: SQLiteCDECRRegistry) -> None:
    m2 = FakeStructured(invalid_first=True)
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))
    result = processor.process("MSG-2")
    assert result.status is CrossDocumentStatus.SUCCEEDED
    atomic_calls = [
        call
        for call in m2.calls
        if "Atomic Event Assignment Adjudicator" in call.system_prompt
        or call.system_prompt.startswith("Repair")
    ]
    assert len(atomic_calls) == 2
    assert atomic_calls[1].system_prompt.startswith("Repair")


def test_persistent_invalid_cross_document_output_degrades_to_separate_results(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = FakeStructured(always_invalid=True)
    m3 = FakeStructured(always_invalid=True)
    processor, _, _, _ = engine(registry, m2=m2, m3=m3)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))
    result = processor.process("MSG-2")
    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert result.atomic_assignments[0].action is AtomicAction.CREATE_NEW
    assert result.atomic_assignments[0].possible_duplicate_atomic_ids == []
    atomic_calls = [
        call
        for call in m2.calls
        if "Atomic Event Assignment Adjudicator" in call.system_prompt
        or call.system_prompt.startswith("Repair")
    ]
    assert len(atomic_calls) >= 2
    with sqlite3.connect(registry.path) as connection:
        degraded_types = {
            row[0]
            for row in connection.execute(
                """
            SELECT decision_type FROM decision_audits
            WHERE run_id = ? AND decision_type IN (
                'ATOMIC_N9_BATCH_DEGRADED',
                'PACKAGE_N12_BATCH_DEGRADED',
                'PACKAGE_N13_BATCH_DEGRADED'
            )
            """,
                (result.run_id,),
            )
        }
    assert degraded_types == {
        "ATOMIC_N9_BATCH_DEGRADED",
        "PACKAGE_N12_BATCH_DEGRADED",
        "PACKAGE_N13_BATCH_DEGRADED",
    }


def test_n9_normalizes_redundant_lists_and_unique_same_target_copy_error(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = FakeStructured(malformed_derived_fields=True)
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assignment = result.atomic_assignments[0]
    assert assignment.action is AtomicAction.MERGE
    assert assignment.related_candidate_event_ids == []
    assert assignment.possible_duplicate_atomic_ids == []
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'ATOMIC_N9_VALIDATION'
            ORDER BY created_at
            LIMIT 1
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    audit = json.loads(payload[0])
    assert audit["model_call_id"]
    assert audit["tasks"]["m1"]["invalid_target"] == "m1c1-copy-error"
    assert any(
        item["kind"] == "UNIQUE_SAME_EVENT_TARGET_RESTORED" for item in audit["normalizations"]
    )


def test_n9_safely_drops_extra_and_identical_duplicate_assessments(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = AtomicCoverageDrift(mode="extra_duplicate")
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'ATOMIC_N9_VALIDATION'
              AND json_extract(payload_json, '$.attempt') = 'initial'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    audit = json.loads(payload[0])
    assert audit["tasks"]["m1"]["extra"] == ["a999"]
    assert audit["tasks"]["m1"]["duplicates"] == ["m1c1"]
    assert any(
        item["kind"] == "IDENTICAL_ASSESSMENT_DEDUPLICATED" for item in audit["normalizations"]
    )


def test_n9_missing_assessment_degrades_only_task_without_repair(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = AtomicCoverageDrift(mode="missing")
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert not any(summary.stage == "atomic_coreference_repair" for summary in result.model_calls)
    assert result.atomic_assignments[0].action is AtomicAction.CREATE_NEW
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'ATOMIC_N9_VALIDATION'
              AND json_extract(payload_json, '$.attempt') = 'initial'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    audit = json.loads(payload[0])
    assert audit["tasks"]["m1"]["missing"] == ["m1c1"]


def test_n9_missing_identity_axis_is_filled_without_task_degradation(
    registry: SQLiteCDECRRegistry,
) -> None:
    m2 = AtomicCoverageDrift(mode="missing_axis")
    processor, _, _, _ = engine(registry, m2=m2)
    add(registry, source("MSG-1"), metric_mention("MSG-1"))
    processor.process("MSG-1")
    add(registry, source("MSG-2"), metric_mention("MSG-2"))

    result = processor.process("MSG-2")

    assert result.status is CrossDocumentStatus.SUCCEEDED
    assert not any(summary.stage == "atomic_coreference_repair" for summary in result.model_calls)
    assert result.atomic_assignments[0].action is AtomicAction.MERGE
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'ATOMIC_N9_VALIDATION'
              AND json_extract(payload_json, '$.attempt') = 'initial'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    audit = json.loads(payload[0])
    assert audit["tasks"]["m1"]["assessment_recovery"]["final_action"] == "MERGE"
    assert any(
        item["kind"] == "MISSING_AXIS_FILLED_FROM_DETERMINISTIC_VIEW"
        for item in audit["normalizations"]
    )
