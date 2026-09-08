from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock
from typing import Any, cast

import pytest

from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    CanonicalEvent,
    CanonicalFact,
)
from doxagent.event_library.provider import EventDetailSnapshot, KnownEventIndexSnapshot
from doxagent.persistent_runtime_v2.daily import (
    PersistentRuntimeV2DailyCloseService,
    RuntimeDeltaBatchAdapter,
)
from doxagent.persistent_runtime_v2.providers import (
    RuntimeInputSnapshot,
    RuntimeKnownEventProvider,
    RuntimePolicyProvider,
)
from doxagent.persistent_runtime_v2.repository import (
    InMemoryPersistentRuntimeV2Repository,
    SQLitePersistentRuntimeV2Repository,
)
from doxagent.persistent_runtime_v2.router import route_runtime_case
from doxagent.persistent_runtime_v2.schema import (
    DailyCloseStage,
    PolicyActivationRecord,
    ProvisionalFactDetail,
    RuntimeCase,
    RuntimeConfidence,
    RuntimeFactCandidate,
    RuntimePrimaryRoute,
    RuntimeVersionPin,
    SourceMessageEnvelope,
    SourceMessageSnapshot,
    W1FactExtractionResult,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W1Round1Result,
    W2MatchedConditions,
    W2PolicyResult,
    W2Round1RecallResult,
    W3CaseResult,
    W3ContextVersionPin,
)
from doxagent.persistent_runtime_v2.service import (
    PersistentRuntimeV2Service,
    _w1_canonical_event_business_payload,
    _w1_final_business_payload,
    _w1_provisional_business_payload,
    _w2_detail_business_payload,
    _w2_projection_business_payload,
)
from doxagent.persistent_runtime_v2.transport import (
    BailianRuntimeResponsesClient,
    RuntimeResponsesError,
    RuntimeResponsesRequest,
    RuntimeResponsesResult,
    _safe_transport_error,
)
from doxagent.workflows.codex_document3.schema import (
    Policy,
    PolicyDecision,
    PolicyDetailSnapshot,
    RuntimePolicyProjection,
    RuntimePolicyRecord,
)


@pytest.mark.parametrize(
    ("novelty", "w1_low", "policy_hit", "w2_low", "expected"),
    [
        ("NEW", True, True, True, "W3"),
        ("NEW", False, True, True, "W3"),
        ("NEW", True, False, True, "W3"),
        ("NEW", False, False, True, "W3"),
        ("NEW", True, True, False, "W3"),
        ("NEW", False, True, False, "TRADE"),
        ("NEW", True, False, False, "W3"),
        ("NEW", False, False, False, "W3"),
        ("OLD", True, True, True, "W3"),
        ("OLD", False, True, True, "W3"),
        ("OLD", True, False, True, "W3"),
        ("OLD", False, False, True, "W3"),
        ("OLD", True, True, False, "W3"),
        ("OLD", False, True, False, "ARCHIVE"),
        ("OLD", True, False, False, "W3"),
        ("OLD", False, False, False, "ARCHIVE"),
    ],
)
def test_router_matches_frozen_sixteen_row_matrix(
    novelty: str,
    w1_low: bool,
    policy_hit: bool,
    w2_low: bool,
    expected: str,
) -> None:
    w1 = W1NoveltyResult(
        result=W1NoveltyVerdict(novelty),
        confidence=RuntimeConfidence.LOW if w1_low else RuntimeConfidence.NORMAL,
        reference_ids=["E1"] if novelty == "OLD" else [],
        reason="decisive ambiguity" if w1_low else "clear",
    )
    w2 = W2PolicyResult(
        policy_ids=["pol_1"] if policy_hit else [],
        confidence=RuntimeConfidence.LOW if w2_low else RuntimeConfidence.NORMAL,
        reason="boundary ambiguity" if w2_low else "clear",
    )
    decision = route_runtime_case(w1, w2)
    assert decision.primary_route is RuntimePrimaryRoute(expected)
    if expected == "ARCHIVE" and novelty == "OLD" and policy_hit:
        assert "mark_badcase" in decision.side_effects


def test_sqlite_provisional_allocator_is_monotonic_and_idempotent(tmp_path: Path) -> None:
    repository = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime-v2.sqlite3")
    candidate = RuntimeFactCandidate(
        proposition="Customer completed qualification.",
        assertion_state=CanonicalAssertionState.ACTUAL,
        occurrence_date=date(2026, 8, 29),
        entities=["MU"],
    )
    first = repository.allocate_provisional(
        ticker="MU",
        trading_date=date(2026, 8, 29),
        source_message_id="msg-1",
        candidate_index=0,
        candidate=candidate,
        published_max_event_numeric_id=184,
    )
    replay = repository.allocate_provisional(
        ticker="MU",
        trading_date=date(2026, 8, 29),
        source_message_id="msg-1",
        candidate_index=0,
        candidate=candidate,
        published_max_event_numeric_id=999,
    )
    second = repository.allocate_provisional(
        ticker="MU",
        trading_date=date(2026, 8, 29),
        source_message_id="msg-2",
        candidate_index=0,
        candidate=candidate,
        published_max_event_numeric_id=999,
    )
    assert first.provisional_event_id == "E185"
    assert replay == first
    assert second.provisional_event_id == "E186"
    assert repository.provisional_snapshot_version("MU", date(2026, 8, 29)) == 2


def test_sqlite_contexts_release_database_file_handle(tmp_path: Path) -> None:
    database = tmp_path / "runtime-v2.sqlite3"
    SQLitePersistentRuntimeV2Repository(database)
    moved = tmp_path / "runtime-v2-moved.sqlite3"
    database.rename(moved)
    assert moved.is_file()


class _FakeKnownEvents(RuntimeKnownEventProvider):
    def current_index(self, ticker: str) -> KnownEventIndexSnapshot:
        return KnownEventIndexSnapshot(
            ticker=ticker,
            version=7,
            published_at=datetime(2026, 8, 29, tzinfo=UTC),
            known_event_index="# Known\n",
            sha256="a" * 64,
        )

    def details(self, ticker: str, version: int, event_ids: list[str]) -> EventDetailSnapshot:
        return EventDetailSnapshot(
            ticker=ticker,
            version=version,
            requested_event_ids=event_ids,
            events=[],
            missing_event_ids=[],
        )

    def max_event_numeric_id(self, ticker: str, version: int) -> int:
        return 184


class _FakePolicies(RuntimePolicyProvider):
    def current_projection(self, ticker: str) -> RuntimePolicyProjection:
        return RuntimePolicyProjection(
            ticker=ticker,
            policy_set_version=3,
            policy_set_published_at=datetime(2026, 8, 29, tzinfo=UTC),
            policies=[],
        )

    def details(self, ticker: str, version: int, policy_ids: list[str]) -> PolicyDetailSnapshot:
        return PolicyDetailSnapshot(
            ticker=ticker,
            policy_set_version=version,
            requested_policy_ids=policy_ids,
            policies=[],
            missing_policy_ids=policy_ids,
        )

    def decision(self, ticker: str, version: int, policy_id: str) -> PolicyDecision | None:
        return None


class _FakeResponses:
    model = "qwen3.8-flash"

    def __init__(self) -> None:
        self.calls: list[RuntimeResponsesRequest[Any]] = []
        self._lock = Lock()

    def complete(self, request: RuntimeResponsesRequest[Any]) -> RuntimeResponsesResult[Any]:
        with self._lock:
            self.calls.append(request)
            number = len(self.calls)
        if request.output_model is W1Round1Result:
            value: Any = W1Round1Result(event_ids=[])
        elif request.output_model is W1NoveltyResult:
            value = W1NoveltyResult(
                result="NEW",
                confidence="normal",
                reference_ids=[],
                reason="new qualification fact",
            )
        elif request.output_model is W2Round1RecallResult:
            value = W2Round1RecallResult(candidate_policy_ids=[])
        else:
            value = W1FactExtractionResult(
                candidates=[
                    RuntimeFactCandidate(
                        proposition="Customer completed qualification.",
                        assertion_state="ACTUAL",
                        occurrence_date=date(2026, 8, 29),
                        entities=["MU"],
                    )
                ]
            )
        return RuntimeResponsesResult(
            value=value,
            response_id=f"resp-{number}",
            latency_ms=1,
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=1,
            cached_input_tokens=0,
        )


def _source() -> SourceMessageEnvelope:
    timestamp = datetime(2026, 8, 29, 14, tzinfo=UTC)
    return SourceMessageEnvelope(
        source_message_id="msg-1",
        source_id="benzinga",
        binding_id="MU:benzinga",
        url="https://example.test/msg-1",
        published_at=timestamp,
        collected_at=timestamp,
        message_bus_event_time=timestamp,
        stream_item_id="stream-msg-1",
        member_count=1,
        snapshot=SourceMessageSnapshot(
            ticker="MU",
            title="Customer completed qualification",
            body="Micron confirmed that the customer completed qualification.",
        ),
    )


class _FakeW3:
    closed = False

    def run(self, **_kwargs: Any) -> tuple[W3CaseResult, str, W3ContextVersionPin]:
        return (
            W3CaseResult(
                w3_case_id=_kwargs["w3_case"].w3_case_id,
                novelty={"result": "NEW", "reference_ids": [], "reason": "new fact"},
                policy={"policy_ids": [], "reason": "not covered"},
                expert_trade={
                    "evaluated": True,
                    "trade": False,
                    "direction": None,
                    "prior_expectation": "Qualification remained pending.",
                    "expectation_delta": "Qualification completed.",
                    "reason": "Important but timing transmission is not directional.",
                },
                delta_candidates=[
                    RuntimeFactCandidate(
                        proposition="Customer completed qualification.",
                        assertion_state="ACTUAL",
                        occurrence_date=date(2026, 8, 29),
                        entities=["MU"],
                    )
                ],
            ),
            "thread-main-mu",
            W3ContextVersionPin(
                document1_run_id="d1-mu",
                document2_run_id="d2-mu",
                event_library_version=7,
                policy_set_version=3,
            ),
        )

    def close(self) -> None:
        self.closed = True


def test_service_runs_parallel_hot_path_then_w3_owned_delta(tmp_path: Path) -> None:
    repository = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime-v2.sqlite3")
    responses = _FakeResponses()
    service = PersistentRuntimeV2Service(
        repository=repository,
        responses=responses,
        known_events=_FakeKnownEvents(),
        policies=_FakePolicies(),
        retry_delays_seconds=(0, 0),
        sleep=lambda _seconds: None,
        dispatch_effects=False,
        w3_agent=_FakeW3(),
        input_snapshot_loader=lambda ticker: RuntimeInputSnapshot(
            index=_FakeKnownEvents().current_index(ticker),
            projection=_FakePolicies().current_projection(ticker),
            activation_revision_id="revision-1",
            document1_run_id="d1-pinned",
            document2_run_id="d2-pinned",
        ),
    )
    adjudicated = service.execute_message(_source())
    assert adjudicated.version_pin.activation_revision_id == "revision-1"
    assert adjudicated.version_pin.document1_run_id == "d1-pinned"
    assert adjudicated.version_pin.document2_run_id == "d2-pinned"
    assert adjudicated.status == "PENDING_W3"
    assert adjudicated.route is not None
    assert adjudicated.route.primary_route == "W3"
    assert adjudicated.w1_extraction is None
    w2_request = next(
        request
        for request in responses.calls
        if request.output_model is W2Round1RecallResult
    )
    assert set(w2_request.payload) == {
        "source_message",
        "runtime_policy_projection",
    }
    assert w2_request.payload["runtime_policy_projection"] == {
        "activation_semantics": "OR",
        "policies": [],
    }
    assert w2_request.cache_context_keys == ("runtime_policy_projection",)
    w1_r1_request = next(
        request for request in responses.calls if request.output_model is W1Round1Result
    )
    assert set(w1_r1_request.payload) == {
        "source_message",
        "published_known_event_index",
        "today_provisional_facts",
    }
    assert w1_r1_request.payload["today_provisional_facts"] == []
    assert w1_r1_request.cache_context_keys == ("published_known_event_index",)
    w1_r2_request = next(
        request for request in responses.calls if request.output_model is W1NoveltyResult
    )
    assert w1_r2_request.payload["event_details"] == {
        "canonical_events": [],
        "provisional_events": [],
    }
    assert w1_r2_request.cache_context_keys == ("event_details",)

    assert service.process_pending_effects() == 1
    completed = repository.get_case(adjudicated.case_id)
    assert completed is not None
    assert completed.status == "COMPLETED"
    assert completed.w1_extraction is None
    assert completed.resolved_route is not None
    assert completed.resolved_route.primary_route == "ADD_TO_DELTA"
    provisional = repository.list_provisional("MU", adjudicated.trading_date)
    assert [item.provisional_event_id for item in provisional] == ["E185"]
    frozen = RuntimeDeltaBatchAdapter(repository).freeze(
        ticker="MU",
        trading_date=date(2026, 8, 29),
        candidates=provisional,
        as_of=datetime(2026, 8, 29, 22, tzinfo=UTC),
    )
    assert frozen.atomics[0].runtime_atomic_id == "runtime-v2-msg:msg-1:0"
    assert frozen.atomics[0].occurrence_date_candidates[0].source_kind == (
        "RUNTIME_CONFIRMED_OCCURRENCE"
    )
    service.close()
    service.close()
    with pytest.raises(RuntimeError, match="service is closed"):
        service.execute_message(_source())


class _AnyPolicies(_FakePolicies):
    record = RuntimePolicyRecord(
        policy_id="pol_any",
        match_scope="customer qualification",
        activation_revision="ar_1234567890abcdef12345678",
        condition_ids=["C1", "C2"],
        criterion=["qualification completed", "volume production started"],
    )

    def current_projection(self, ticker: str) -> RuntimePolicyProjection:
        return RuntimePolicyProjection(
            ticker=ticker,
            policy_set_version=3,
            policy_set_published_at=datetime(2026, 8, 29, tzinfo=UTC),
            policies=[self.record],
        )

    def decision(self, ticker: str, version: int, policy_id: str) -> PolicyDecision | None:
        return PolicyDecision.LONG if policy_id == self.record.policy_id else None

    def details(self, ticker: str, version: int, policy_ids: list[str]) -> PolicyDetailSnapshot:
        policies = (
            [
                Policy(
                    policy_id="pol_any",
                    title="Customer qualification or production milestone",
                    source_refs=[
                        {"shell_id": "shell_1", "expectation_id": "exp_1", "gap_id": "gap_1"}
                    ],
                    decision=PolicyDecision.LONG,
                    match_scope="customer qualification",
                    activation_conditions=[
                        {
                            "condition_id": "C1",
                            "criterion": "qualification completed",
                            "calibration": {
                                "reference_state": "qualification remained pending",
                                "trigger_boundary": "customer confirms qualification completion",
                            },
                        },
                        {
                            "condition_id": "C2",
                            "criterion": "volume production started",
                            "calibration": {
                                "reference_state": "volume production had not started",
                                "trigger_boundary": "commercial volume production begins",
                            },
                        },
                    ],
                )
            ]
            if "pol_any" in policy_ids
            else []
        )
        found = {policy.policy_id for policy in policies}
        return PolicyDetailSnapshot(
            ticker=ticker,
            policy_set_version=version,
            requested_policy_ids=policy_ids,
            policies=policies,
            missing_policy_ids=[policy_id for policy_id in policy_ids if policy_id not in found],
        )

    def activation(self, ticker: str, version: int, policy_id: str) -> RuntimePolicyRecord | None:
        return self.record if policy_id == self.record.policy_id else None


class _AnyResponses(_FakeResponses):
    def complete(self, request: RuntimeResponsesRequest[Any]) -> RuntimeResponsesResult[Any]:
        if request.output_model is W2Round1RecallResult:
            with self._lock:
                self.calls.append(request)
            policies = request.payload["runtime_policy_projection"]["policies"]
            value: Any = W2Round1RecallResult(
                candidate_policy_ids=["pol_any"] if policies else []
            )
        elif request.output_model is W2PolicyResult:
            with self._lock:
                self.calls.append(request)
            policies = request.payload["policy_details"]["policies"]
            value = W2PolicyResult(
                policy_ids=["pol_any"] if policies else [],
                matched_condition_ids=(
                    [W2MatchedConditions(policy_id="pol_any", condition_ids=["C1", "C2"])]
                    if policies
                    else []
                ),
                confidence="normal",
                reason="one ANY condition confirmed" if policies else "policy already consumed",
            )
        else:
            return super().complete(request)
        return RuntimeResponsesResult(
                value=value,
                response_id="resp-w2",
                latency_ms=1,
                input_tokens=10,
                output_tokens=5,
                reasoning_tokens=1,
                cached_input_tokens=0,
            )


def test_w2_model_payload_contains_only_business_policy_content() -> None:
    projection_payload = _w2_projection_business_payload(_AnyPolicies().current_projection("MU"))
    assert set(projection_payload) == {"activation_semantics", "policies"}
    assert projection_payload["activation_semantics"] == "OR"
    assert projection_payload["policies"] == [
        {
            "policy_id": "pol_any",
            "match_scope": "customer qualification",
            "condition_ids": ["C1", "C2"],
            "criterion": ["qualification completed", "volume production started"],
        }
    ]
    detail_payload = _w2_detail_business_payload(
        _AnyPolicies().details("MU", 3, ["pol_any"])
    )
    assert detail_payload == {
        "activation_semantics": "OR",
        "candidate_policy_ids": ["pol_any"],
        "policies": [
            {
                "policy_id": "pol_any",
                "title": "Customer qualification or production milestone",
                "match_scope": "customer qualification",
                "activation_conditions": [
                    {
                        "condition_id": "C1",
                        "criterion": "qualification completed",
                        "calibration": {
                            "reference_state": "qualification remained pending",
                            "trigger_boundary": "customer confirms qualification completion",
                        },
                    },
                    {
                        "condition_id": "C2",
                        "criterion": "volume production started",
                        "calibration": {
                            "reference_state": "volume production had not started",
                            "trigger_boundary": "commercial volume production begins",
                        },
                    },
                ],
            }
        ],
    }
    assert "decision" not in detail_payload["policies"][0]
    assert "source_refs" not in detail_payload["policies"][0]


def test_w2_round1_recall_is_bounded_to_three_candidates() -> None:
    result = W2Round1RecallResult(
        candidate_policy_ids=["pol_1", "pol_1", "pol_2", "pol_3"]
    )
    assert result.candidate_policy_ids == ["pol_1", "pol_2", "pol_3"]
    with pytest.raises(ValueError, match="at most 3 items"):
        W2Round1RecallResult(
            candidate_policy_ids=["pol_1", "pol_2", "pol_3", "pol_4"]
        )


def test_runtime_case_reads_legacy_w2_round1_as_recall_candidates() -> None:
    payload = RuntimeCase(
        trading_date=date(2026, 8, 29),
        source=_source(),
        version_pin=RuntimeVersionPin(
            event_library_version=7,
            provisional_snapshot_version=0,
            policy_set_version=3,
            runtime_projection_version=3,
        ),
    ).model_dump(mode="json")
    payload["w2_round1"] = {
        "policy_ids": ["pol_1"],
        "matched_condition_ids": [],
        "confidence": "normal",
        "reason": "legacy final-style R1",
    }

    restored = RuntimeCase.model_validate(payload)

    assert restored.w2_round1 == W2Round1RecallResult(
        candidate_policy_ids=["pol_1"]
    )


def test_w2_nonempty_recall_always_runs_r2() -> None:
    responses = _AnyResponses()
    service = PersistentRuntimeV2Service(
        repository=InMemoryPersistentRuntimeV2Repository(),
        responses=responses,
        known_events=_FakeKnownEvents(),
        policies=_AnyPolicies(),
        retry_delays_seconds=(0, 0),
        sleep=lambda _seconds: None,
        dispatch_effects=False,
    )
    case = RuntimeCase(
        trading_date=date(2026, 8, 29),
        source=_source(),
        version_pin=RuntimeVersionPin(
            event_library_version=7,
            provisional_snapshot_version=0,
            policy_set_version=3,
            runtime_projection_version=3,
        ),
    )

    recall, final, _response_id = service._run_w2_hot(
        case,
        _AnyPolicies().current_projection("MU"),
    )

    assert recall.candidate_policy_ids == ["pol_any"]
    assert final.policy_ids == ["pol_any"]
    assert [request.output_model for request in responses.calls] == [
        W2Round1RecallResult,
        W2PolicyResult,
    ]
    assert set(responses.calls[1].payload) == {"source_message", "policy_details"}
    assert responses.calls[1].cache_context_keys == ("policy_details",)
    service.close()


def test_w2_empty_recall_synthesizes_final_without_r2() -> None:
    responses = _FakeResponses()
    service = PersistentRuntimeV2Service(
        repository=InMemoryPersistentRuntimeV2Repository(),
        responses=responses,
        known_events=_FakeKnownEvents(),
        policies=_FakePolicies(),
        retry_delays_seconds=(0, 0),
        sleep=lambda _seconds: None,
        dispatch_effects=False,
    )
    case = RuntimeCase(
        trading_date=date(2026, 8, 29),
        source=_source(),
        version_pin=RuntimeVersionPin(
            event_library_version=7,
            provisional_snapshot_version=0,
            policy_set_version=3,
            runtime_projection_version=3,
        ),
    )

    recall, final, _response_id = service._run_w2_hot(
        case,
        _FakePolicies().current_projection("MU"),
    )

    assert recall.candidate_policy_ids == []
    assert final.policy_ids == []
    assert final.confidence is RuntimeConfidence.NORMAL
    assert [request.output_model for request in responses.calls] == [W2Round1RecallResult]
    service.close()


def test_w1_model_views_exclude_runtime_and_event_library_audit_fields() -> None:
    candidate = RuntimeFactCandidate(
        proposition="Customer completed qualification.",
        assertion_state="ACTUAL",
        subject_time="2026 H2",
        occurrence_date=date(2026, 8, 29),
        entities=["MU", "Customer"],
    )
    provisional = ProvisionalFactDetail(
        provisional_event_id="E185",
        ticker="MU",
        trading_date=date(2026, 8, 29),
        source_message_id="msg-audit",
        candidate_index=2,
        candidate=candidate,
        runtime_signature="a" * 64,
        snapshot_version=4,
        created_at=datetime(2026, 8, 29, 15, tzinfo=UTC),
    )
    assert _w1_provisional_business_payload(provisional) == {
        "provisional_event_id": "E185",
        "known_before_current_message": True,
        "semantic_day": "2026-08-29",
        "candidate": candidate.model_dump(mode="json"),
    }

    event = CanonicalEvent(
        event_id="E1",
        ticker="MU",
        title="Customer qualification completed",
        event_type="CUSTOMER_MILESTONE",
        occurred_at="2026-08-29",
        occurrence_time_precision="DAY",
        status="ACTIVE",
        canonical_summary="A customer completed qualification.",
        known_event_summary="2026-08-29 customer qualification completed.",
        is_important=True,
        include_in_reference_view=True,
        related_event_ids=[],
        supersedes_event_id=None,
        derived_from_event_ids=[],
        facts=[
            CanonicalFact(
                fact_id="F1",
                proposition="A customer completed qualification.",
                assertion_state="ACTUAL",
                subject_time="2026 H2",
                fact_occurred_at="2026-08-29",
                fact_occurrence_time_precision="DAY",
            )
        ],
        price_analysis={"return_1d": 0.01},
    )
    event_payload = _w1_canonical_event_business_payload(event)
    assert set(event_payload) == {
        "event_id",
        "title",
        "event_type",
        "occurred_at",
        "occurrence_time_precision",
        "status",
        "canonical_summary",
        "known_event_summary",
        "related_event_ids",
        "supersedes_event_id",
        "derived_from_event_ids",
        "facts",
    }
    assert not {
        "ticker",
        "is_important",
        "include_in_reference_view",
        "price_analysis",
    }.intersection(event_payload)

    final = W1NoveltyResult(
        result="NEW",
        confidence="low",
        reference_ids=["E1"],
        reason="The timing changed.",
    )
    assert _w1_final_business_payload(final) == {
        "result": "NEW",
        "reference_ids": ["E1"],
        "reason": "The timing changed.",
    }


def test_any_policy_is_consumed_once_and_removed_from_next_w2_input(tmp_path: Path) -> None:
    repository = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime-v2-consumption.sqlite3")
    responses = _AnyResponses()
    service = PersistentRuntimeV2Service(
        repository=repository,
        responses=responses,
        known_events=_FakeKnownEvents(),
        policies=_AnyPolicies(),
        retry_delays_seconds=(0, 0),
        sleep=lambda _seconds: None,
        dispatch_effects=False,
    )

    first = service.execute_message(_source())
    assert first.route is not None and first.route.primary_route is RuntimePrimaryRoute.TRADE
    first_w2_calls = [
        request
        for request in responses.calls
        if request.output_model in {W2Round1RecallResult, W2PolicyResult}
    ]
    assert [request.output_model for request in first_w2_calls] == [
        W2Round1RecallResult,
        W2PolicyResult,
    ]
    assert first.w2_round1 == W2Round1RecallResult(candidate_policy_ids=["pol_any"])
    assert first_w2_calls[1].cache_context_keys == ("policy_details",)
    assert set(first_w2_calls[1].payload) == {"source_message", "policy_details"}
    assert service.process_pending_effects() == 2
    trades = repository.list_daily_trades("MU", first.trading_date)
    assert len(trades) == 1
    assert trades[0].activation_revision == _AnyPolicies.record.activation_revision
    assert trades[0].matched_condition_ids == ["C1", "C2"]
    r3_request = next(
        request for request in responses.calls if request.output_model is W1FactExtractionResult
    )
    assert set(r3_request.payload) == {"source_message", "w1_final"}
    assert r3_request.payload["w1_final"] == {
        "result": "NEW",
        "reference_ids": [],
        "reason": "new qualification fact",
    }
    assert "Current Capture Mode" in r3_request.instructions
    assert "`NEW_CAPTURE`" in r3_request.instructions

    second_source = _source().model_copy(
        update={"source_message_id": "msg-2", "stream_item_id": "stream-msg-2"}
    )
    second = service.execute_message(second_source)
    assert second.w2_final is not None and second.w2_final.policy_ids == []
    assert second.route is not None and second.route.primary_route is RuntimePrimaryRoute.W3
    assert len(repository.list_daily_trades("MU", first.trading_date)) == 1
    service.close()


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_policy_activation_claim_is_atomic_and_idempotent(
    tmp_path: Path, repository_kind: str
) -> None:
    repository = (
        InMemoryPersistentRuntimeV2Repository()
        if repository_kind == "memory"
        else SQLitePersistentRuntimeV2Repository(tmp_path / "runtime-v2-claim.sqlite3")
    )
    source1 = _source()
    source2 = source1.model_copy(
        update={"source_message_id": "msg-race-2", "stream_item_id": "stream-race-2"}
    )
    cases = [
        RuntimeCase(
            case_id=f"case-race-{index}",
            trading_date=date(2026, 8, 29),
            source=source,
            version_pin=RuntimeVersionPin(
                event_library_version=7,
                provisional_snapshot_version=0,
                policy_set_version=3,
                runtime_projection_version=3,
            ),
        )
        for index, source in enumerate((source1, source2), start=1)
    ]
    for case in cases:
        repository.save_case(case)
    records = [
        PolicyActivationRecord(
            case_id=case.case_id,
            source_message_id=case.source.source_message_id,
            ticker="MU",
            policy_id="pol_any",
            activation_revision="ar_1234567890abcdef12345678",
            policy_set_version=3,
        )
        for case in cases
    ]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(repository.claim_policy_activation, records))

    assert sum(claimed) == 1
    winner = records[claimed.index(True)]
    assert repository.claim_policy_activation(winner) is True
    assert repository.list_consumed_policy_revisions("mu") == {
        ("pol_any", "ar_1234567890abcdef12345678")
    }


class _NoopO2:
    called = False

    async def run(self, **_kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("empty Daily Close must skip O2")


class _NoopO3:
    called = False

    async def maintain(self, **_kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("empty Daily Close must skip O3")


class _PublishedV1:
    def published_version(self, _ticker: str) -> int:
        return 1


def test_empty_daily_close_is_checkpointed_noop_and_idempotent(tmp_path: Path) -> None:
    repository = InMemoryPersistentRuntimeV2Repository()
    o2 = _NoopO2()
    o3 = _NoopO3()
    service = PersistentRuntimeV2DailyCloseService(
        repository=repository,
        event_repository=cast(Any, _PublishedV1()),
        o2_runner=cast(Any, o2),
        o3_maintainer=cast(Any, o3),
        export_root=tmp_path,
    )
    first = asyncio.run(service.close(ticker="MU", trading_date=date(2026, 8, 29)))
    replay = asyncio.run(service.close(ticker="MU", trading_date=date(2026, 8, 29)))
    assert first.stage is DailyCloseStage.COMPLETED
    assert replay == first
    assert first.o3_result == {"status": "NOOP", "reason": "empty_daily_feed"}
    assert not o2.called
    assert not o3.called


@dataclass
class _UsageDetails:
    reasoning_tokens: int = 2
    cached_tokens: int = 3


@dataclass
class _Usage:
    input_tokens: int = 10
    output_tokens: int = 4
    input_tokens_details: _UsageDetails = field(default_factory=_UsageDetails)
    output_tokens_details: _UsageDetails = field(default_factory=_UsageDetails)


class _ResponsesEndpoint:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return type(
            "Response",
            (),
            {
                "id": "resp-1",
                "output_text": '{"event_ids":[]}',
                "usage": _Usage(),
            },
        )()


class _OpenAIClient:
    def __init__(self) -> None:
        self.responses = _ResponsesEndpoint()


class _InvalidOpenAIClient:
    def __init__(self) -> None:
        self.responses = self

    def create(self, **_kwargs: Any) -> Any:
        return type(
            "Response",
            (),
            {
                "id": "resp-invalid",
                "output_text": ('{"result":"OLD","confidence":"normal","reason":"x"}'),
                "usage": _Usage(),
            },
        )()


def test_bailian_transport_uses_readonly_prefix_and_implicit_cache() -> None:
    fake = _OpenAIClient()
    client = BailianRuntimeResponsesClient(
        api_key="",
        base_url="https://example.invalid/compatible-mode/v1",
        client=cast(Any, fake),
    )
    result = client.complete(
        RuntimeResponsesRequest(
            instructions="core + W1 R1",
            payload={
                "published_known_event_index": "E1 known event",
                "source_message": {"title": "x"},
            },
            output_model=W1Round1Result,
            schema_name="w1_round1_result",
            cache_context_keys=("published_known_event_index",),
            previous_response_id="resp-prior-for-audit-only",
        )
    )
    assert result.value.event_ids == []
    assert fake.responses.kwargs["store"] is True
    assert fake.responses.kwargs["reasoning"] == {"effort": "medium"}
    assert fake.responses.kwargs["text"]["format"]["strict"] is True
    assert "extra_headers" not in fake.responses.kwargs
    assert "previous_response_id" not in fake.responses.kwargs
    input_text = fake.responses.kwargs["input"]
    assert input_text.startswith("# Read-Only Business Reference Data")
    assert input_text.index("E1 known event") < input_text.index("# Current Case Input")
    assert input_text.index("# Current Case Input") < input_text.index('"title":"x"')
    assert result.prefix_fingerprint is not None
    assert len(result.prefix_fingerprint) == 64
    instructions = fake.responses.kwargs["instructions"]
    assert "# Exact Output Contract" in instructions
    assert "Never rename a key" in instructions
    assert '"event_ids"' in instructions


def test_bailian_transport_can_still_enable_session_cache_explicitly() -> None:
    fake = _OpenAIClient()
    client = BailianRuntimeResponsesClient(
        api_key="",
        base_url="https://example.invalid/compatible-mode/v1",
        session_cache=True,
        client=cast(Any, fake),
    )
    client.complete(
        RuntimeResponsesRequest(
            instructions="test",
            payload={},
            output_model=W1Round1Result,
            schema_name="w1_round1_result",
        )
    )
    assert fake.responses.kwargs["extra_headers"] == {
        "x-dashscope-session-cache": "enable"
    }


def test_bailian_arrearage_is_non_retryable_and_secret_safe() -> None:
    error = _safe_transport_error(
        RuntimeError("400 Arrearage overdue-payment key=must-not-be-repeated")
    )
    assert error.code == "provider_account_in_arrears"
    assert error.retryable is False
    assert "must-not-be-repeated" not in str(error)


def test_bailian_validation_error_is_actionable_without_raw_output() -> None:
    client = BailianRuntimeResponsesClient(
        api_key="",
        base_url="https://example.invalid/compatible-mode/v1",
        client=cast(Any, _InvalidOpenAIClient()),
    )
    with pytest.raises(RuntimeResponsesError) as captured:
        client.complete(
            RuntimeResponsesRequest(
                instructions="test",
                payload={},
                output_model=W1NoveltyResult,
                schema_name="w1_novelty_result",
            )
        )
    message = str(captured.value)
    assert captured.value.code == "structured_output_validation_failed"
    assert "OLD requires at least one loaded reference ID" in message
    assert "output_text" not in message
