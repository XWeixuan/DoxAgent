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
    Participant,
    ParticipantRole,
    Predicate,
    Quantity,
    QuantityRole,
    SourceMessage,
    SourceType,
    TimePrecision,
)
from cdecr.coreference_rules import merge_event_times
from cdecr.cross_document import CrossDocumentEngine
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


def test_incremental_engine_batch_has_no_bulk_orchestration_switch() -> None:
    engine = object.__new__(CrossDocumentEngine)
    assert engine.process_batch([]) == []


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
        is_field_request = "typed field" in request.system_prompt
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
        elif "SAME_PARENT" in request.system_prompt:
            body = json.loads(request.user_prompt)
            payload = {
                "decisions": [
                    {"pair_id": pair["id"], "relation": "DIFFERENT_PARENT"}
                    for pair in body["pairs"]
                ]
            }
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
            seeds = {task["event_id"]: task["seed"] for task in tasks if isinstance(task, dict)}
            candidates = {
                task["event_id"]: task["candidates"] for task in tasks if isinstance(task, dict)
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
        atoms = body.get("atomics", body.get("atoms", {}))
        mentions = body.get("mentions", {})
        assert isinstance(tasks, list)
        assert isinstance(atoms, dict)
        assert isinstance(mentions, dict)
        decisions: list[dict[str, object]] = []
        for task in tasks:
            assert isinstance(task, dict)
            incoming_id = task.get("mention")
            incoming = (
                mentions[incoming_id]
                if isinstance(incoming_id, str) and incoming_id in mentions
                else task["incoming"]
            )
            candidates = task["candidates"]
            assert isinstance(incoming, dict)
            assert isinstance(candidates, list)
            expanded_candidates: list[tuple[str, dict[str, object], list[str]]] = []
            for candidate in candidates:
                assert isinstance(candidate, dict)
                if atoms:
                    candidate_id = candidate.get("atomic", candidate.get("event_id"))
                    atom = atoms[candidate_id]
                    assert isinstance(atom, dict)
                    axes = candidate.get("axes", [])
                    assert isinstance(candidate_id, str)
                    assert isinstance(axes, list)
                    expanded_candidates.append((candidate_id, atom, axes))
                else:
                    candidate_id = candidate["event_id"]
                    axes = incoming.get("identity_axes", [])
                    assert isinstance(candidate_id, str)
                    assert isinstance(axes, list)
                    expanded_candidates.append((candidate_id, candidate, axes))
            assessments = [
                {
                    "candidate_event_id": candidate_id,
                    "relation": (
                        self.atomic_relation
                        or (
                            "SAME_EVENT"
                            if candidate.get("identity", candidate.get("identity_profile"))
                            == incoming.get("identity", incoming.get("identity_profile"))
                            else "UNRELATED"
                        )
                    ),
                    "axis_assessments": [
                        {
                            "axis": axis,
                            "verdict": (
                                "MATCH"
                                if candidate.get("identity", candidate.get("identity_profile"))
                                == incoming.get("identity", incoming.get("identity_profile"))
                                else "CONFLICT"
                            ),
                        }
                        for axis in (
                            {"R": "REFERENT", "O": "OCCURRENCE", "F": "FACET"}.get(value, value)
                            for value in axes
                        )
                    ],
                    "claim_conflict": False,
                    "identity_differences": (
                        []
                        if candidate.get("identity", candidate.get("identity_profile"))
                        == incoming.get("identity", incoming.get("identity_profile"))
                        else ["identity_profile"]
                    ),
                }
                for candidate_id, candidate, axes in expanded_candidates
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
                    "mention_id": incoming_id or incoming["mention_id"],
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


def test_merge_event_times_does_not_join_occurrence_start_to_earlier_period_end() -> None:
    merged = merge_event_times(
        EventTime(
            event_start=date(2026, 6, 24),
            precision=TimePrecision.DAY,
            reference_period_id="micron_fiscal_q3",
        ),
        EventTime(
            event_end=date(2026, 5, 28),
            precision=TimePrecision.QUARTER,
            reference_period_id="micron_fiscal_q3",
        ),
    )

    assert merged == EventTime(
        event_start=date(2026, 6, 24),
        precision=TimePrecision.DAY,
        reference_period_id="micron_fiscal_q3",
    )


def engine(
    registry: SQLiteCDECRRegistry,
    *,
    m2: FakeStructured | None = None,
    m3: FakeStructured | None = None,
    hard_cannot_link_mode: str = "shadow",
    n12_wire_protocol: str = "on",
    n13_wire_protocol: str = "on",
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
