from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock
from typing import Any, cast

import pytest

from doxagent.event_library.contracts import CanonicalAssertionState
from doxagent.event_library.provider import EventDetailSnapshot, KnownEventIndexSnapshot
from doxagent.persistent_runtime_v2.daily import (
    PersistentRuntimeV2DailyCloseService,
    RuntimeDeltaBatchAdapter,
)
from doxagent.persistent_runtime_v2.providers import (
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
    RuntimeConfidence,
    RuntimeFactCandidate,
    RuntimePrimaryRoute,
    SourceMessageEnvelope,
    SourceMessageSnapshot,
    W1FactExtractionResult,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W1Round1Result,
    W2PolicyResult,
)
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import (
    BailianRuntimeResponsesClient,
    RuntimeResponsesError,
    RuntimeResponsesRequest,
    RuntimeResponsesResult,
    _safe_transport_error,
)
from doxagent.workflows.codex_document3.schema import (
    PolicyDecision,
    PolicyDetailSnapshot,
    RuntimePolicyProjection,
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
        ("NEW", True, False, False, "ADD_TO_DELTA"),
        ("NEW", False, False, False, "ADD_TO_DELTA"),
        ("OLD", True, True, True, "W3"),
        ("OLD", False, True, True, "ARCHIVE"),
        ("OLD", True, False, True, "W3"),
        ("OLD", False, False, True, "ARCHIVE"),
        ("OLD", True, True, False, "W3"),
        ("OLD", False, True, False, "ARCHIVE"),
        ("OLD", True, False, False, "ADD_TO_DELTA"),
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
    if novelty == "OLD" and not w1_low and policy_hit:
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

    def details(
        self, ticker: str, version: int, event_ids: list[str]
    ) -> EventDetailSnapshot:
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

    def details(
        self, ticker: str, version: int, policy_ids: list[str]
    ) -> PolicyDetailSnapshot:
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
        elif request.output_model is W2PolicyResult:
            value = W2PolicyResult(
                policy_ids=[],
                confidence="normal",
                reason="no policy hit",
            )
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
        collected_at=timestamp,
        message_bus_event_time=timestamp,
        snapshot=SourceMessageSnapshot(
            ticker="MU",
            source_type="media",
            interface_type="polling",
            title="Customer completed qualification",
            body="Micron confirmed that the customer completed qualification.",
        ),
    )


def test_service_runs_parallel_hot_path_then_async_r3(tmp_path: Path) -> None:
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
    )
    adjudicated = service.execute_message(_source())
    assert adjudicated.status == "ADJUDICATED"
    assert adjudicated.route is not None
    assert adjudicated.route.primary_route == "ADD_TO_DELTA"
    assert adjudicated.w1_extraction is None

    assert service.process_pending_effects() == 1
    completed = repository.get_case(adjudicated.case_id)
    assert completed is not None
    assert completed.status == "COMPLETED"
    assert completed.w1_extraction is not None
    provisional = repository.list_provisional("MU", date(2026, 8, 29))
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
    first = asyncio.run(
        service.close(ticker="MU", trading_date=date(2026, 8, 29))
    )
    replay = asyncio.run(
        service.close(ticker="MU", trading_date=date(2026, 8, 29))
    )
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
                "output_text": (
                    '{"result":"OLD","confidence":"normal","reason":"x"}'
                ),
                "usage": _Usage(),
            },
        )()


def test_bailian_transport_forces_strict_responses_medium_and_store() -> None:
    fake = _OpenAIClient()
    client = BailianRuntimeResponsesClient(
        api_key="",
        base_url="https://example.invalid/compatible-mode/v1",
        client=cast(Any, fake),
    )
    result = client.complete(
        RuntimeResponsesRequest(
            instructions="core + W1 R1",
            payload={"source_message": {"title": "x"}},
            output_model=W1Round1Result,
            schema_name="w1_round1_result",
        )
    )
    assert result.value.event_ids == []
    assert fake.responses.kwargs["store"] is True
    assert fake.responses.kwargs["reasoning"] == {"effort": "medium"}
    assert fake.responses.kwargs["text"]["format"]["strict"] is True
    assert fake.responses.kwargs["extra_headers"] == {
        "x-dashscope-session-cache": "enable"
    }
    instructions = fake.responses.kwargs["instructions"]
    assert "# Exact Output Contract" in instructions
    assert "Never rename a key" in instructions
    assert '"event_ids"' in instructions


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
