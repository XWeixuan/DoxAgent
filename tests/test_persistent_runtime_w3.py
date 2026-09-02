from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.event_library.contracts import CanonicalAssertionState
from doxagent.event_library.provider import EventDetailSnapshot, KnownEventIndexSnapshot
from doxagent.persistent_runtime_v2.daily import PersistentRuntimeV2DailyCloseService
from doxagent.persistent_runtime_v2.providers import (
    RuntimeKnownEventProvider,
    RuntimePolicyProvider,
)
from doxagent.persistent_runtime_v2.repository import (
    InMemoryPersistentRuntimeV2Repository,
    SQLitePersistentRuntimeV2Repository,
)
from doxagent.persistent_runtime_v2.router import route_w3_result
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCase,
    RuntimeCaseStatus,
    RuntimeConfidence,
    RuntimeFactCandidate,
    RuntimeTechnicalStatus,
    RuntimeVersionPin,
    SourceMessageEnvelope,
    SourceMessageSnapshot,
    TradeDecisionOrigin,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W1Round1Result,
    W2PolicyResult,
    W3CaseResult,
    W3ContextVersionPin,
    W3CoverageGapRecord,
    W3ExpertTradeResult,
    W3Mode,
    W3NoveltyResult,
    W3PolicyResult,
    W3RouteCase,
    W3ThreadKind,
    W3ThreadSlot,
)
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import (
    RuntimeResponsesRequest,
    RuntimeResponsesResult,
)
from doxagent.persistent_runtime_v2.w3 import (
    CodexW3AgentRunner,
    W3Error,
    W3PreparedContext,
)
from doxagent.workflows.codex_document2.schema import (
    Document2Document,
    Document2InputManifest,
)
from doxagent.workflows.codex_document3.schema import (
    Document2Ref,
    PolicyDecision,
    PolicyDetailSnapshot,
    PolicySet,
    PublicationState,
    RuntimePolicyProjection,
)


def _source(message_id: str = "msg-w3", ticker: str = "MU") -> SourceMessageEnvelope:
    now = datetime(2026, 8, 31, 14, tzinfo=UTC)
    return SourceMessageEnvelope(
        source_message_id=message_id,
        source_id="benzinga",
        binding_id=f"{ticker}:benzinga",
        url=f"https://example.test/{message_id}",
        published_at=now,
        collected_at=now,
        message_bus_event_time=now,
        stream_item_id=f"stream-{message_id}",
        member_count=1,
        snapshot=SourceMessageSnapshot(
            ticker=ticker,
            title="Customer qualification completed",
            body="The company confirmed qualification completion.",
        ),
    )


def _runtime_case(message_id: str = "msg-w3") -> RuntimeCase:
    return RuntimeCase(
        case_id=f"case-{message_id}",
        trading_date=date(2026, 8, 31),
        source=_source(message_id),
        version_pin=RuntimeVersionPin(
            event_library_version=7,
            provisional_snapshot_version=0,
            policy_set_version=3,
            runtime_projection_version=3,
        ),
        status=RuntimeCaseStatus.PENDING_W3,
        technical_status=RuntimeTechnicalStatus.OK,
        w1_round1=W1Round1Result(event_ids=[]),
        w1_final=W1NoveltyResult(
            result=W1NoveltyVerdict.NEW,
            confidence=RuntimeConfidence.NORMAL,
            reference_ids=[],
            reason="new",
        ),
        w2_round1=W2PolicyResult(
            policy_ids=[], confidence=RuntimeConfidence.NORMAL, reason="none"
        ),
        w2_final=W2PolicyResult(
            policy_ids=[], confidence=RuntimeConfidence.NORMAL, reason="none"
        ),
    )


def _w3_result(*, trade: bool = False) -> W3CaseResult:
    return W3CaseResult(
        w3_case_id="w3-case",
        novelty=W3NoveltyResult(
            result=W1NoveltyVerdict.NEW,
            reference_ids=[],
            reason="new reality",
        ),
        policy=W3PolicyResult(policy_ids=[], reason="not covered"),
        expert_trade=W3ExpertTradeResult(
            evaluated=True,
            trade=trade,
            direction=PolicyDecision.LONG if trade else None,
            prior_expectation="Qualification remained pending.",
            expectation_delta="Qualification completed earlier than expected.",
            reason="Direct customer adoption transmission." if trade else "No timing edge.",
        ),
        delta_candidates=[
            RuntimeFactCandidate(
                proposition="Customer qualification completed.",
                assertion_state=CanonicalAssertionState.ACTUAL,
                occurrence_date=date(2026, 8, 31),
                entities=["MU"],
            )
        ],
    )


def test_w3_schema_and_resolved_routes_fail_closed() -> None:
    direct = _w3_result(trade=True)
    assert route_w3_result(direct).primary_route == "TRADE"
    no_trade = _w3_result(trade=False)
    assert route_w3_result(no_trade).primary_route == "ADD_TO_DELTA"
    with pytest.raises(ValidationError, match="requires at least one RuntimeFactCandidate"):
        W3CaseResult(
            w3_case_id="w3-case",
            novelty=W3NoveltyResult(
                result=W1NoveltyVerdict.NEW,
                reference_ids=[],
                reason="new",
            ),
            policy=W3PolicyResult(policy_ids=[], reason="none"),
            expert_trade=W3ExpertTradeResult(
                evaluated=True,
                trade=False,
                direction=None,
                prior_expectation="prior",
                expectation_delta="delta",
                reason="reason",
            ),
            delta_candidates=[],
        )


def test_w3_has_web_search_skill_but_no_data_mcp_budget() -> None:
    from doxagent.codex_runtime.schema import (
        CodexPersistentRuntimeAgentRole,
        CodexPersistentRuntimeNode,
    )

    assert DataToolPolicyRegistry().allowed_tools(
        CodexPersistentRuntimeNode.W3,
        CodexPersistentRuntimeAgentRole.W3,
    ) == frozenset()


def test_sqlite_w3_main_and_fallback_slots_enforce_only_ticker_limit(
    tmp_path: Path,
) -> None:
    repository = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime.sqlite3")
    cases = [_runtime_case(f"msg-{index}") for index in range(6)]
    for case in cases:
        repository.save_case(case)
    slots = [
        repository.acquire_w3_slot(ticker="MU", case_id=case.case_id)
        for case in cases[:5]
    ]
    assert slots[0] is not None and slots[0].kind is W3ThreadKind.MAIN
    assert all(slot is not None and slot.kind is W3ThreadKind.FALLBACK for slot in slots[1:])
    assert repository.acquire_w3_slot(ticker="MU", case_id=cases[5].case_id) is None
    repository.release_w3_slot(cast(Any, slots[0]), thread_id="thread-mu-main")
    resumed = repository.acquire_w3_slot(ticker="MU", case_id=cases[5].case_id)
    assert resumed is not None
    assert resumed.kind is W3ThreadKind.MAIN
    assert resumed.thread_id == "thread-mu-main"


class _Known(RuntimeKnownEventProvider):
    def current_index(self, ticker: str) -> KnownEventIndexSnapshot:
        return KnownEventIndexSnapshot(
            ticker=ticker,
            version=7,
            published_at=datetime(2026, 8, 31, tzinfo=UTC),
            known_event_index="# index",
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
        return 20


class _Policies(RuntimePolicyProvider):
    def current_projection(self, ticker: str) -> RuntimePolicyProjection:
        return RuntimePolicyProjection(
            ticker=ticker,
            policy_set_version=3,
            policy_set_published_at=datetime(2026, 8, 31, tzinfo=UTC),
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


class _HotResponses:
    model = "qwen3.8-flash"

    def complete(self, request: RuntimeResponsesRequest[Any]) -> RuntimeResponsesResult[Any]:
        if request.output_model is W1Round1Result:
            value: Any = W1Round1Result(event_ids=[])
        elif request.output_model is W1NoveltyResult:
            value = W1NoveltyResult(
                result=W1NoveltyVerdict.NEW,
                confidence=RuntimeConfidence.NORMAL,
                reference_ids=[],
                reason="new",
            )
        else:
            value = W2PolicyResult(
                policy_ids=[], confidence=RuntimeConfidence.NORMAL, reason="none"
            )
        return RuntimeResponsesResult(
            value=value,
            response_id="resp",
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            reasoning_tokens=0,
            cached_input_tokens=0,
        )


class _RetryingW3:
    def __init__(self, failures: int, *, trade: bool = True) -> None:
        self.calls = 0
        self.failures = failures
        self.trade = trade

    def run(self, **_kwargs: Any) -> tuple[W3CaseResult, str, W3ContextVersionPin]:
        self.calls += 1
        if self.calls <= self.failures:
            raise W3Error("temporary", "temporary failure")
        return (
            _w3_result(trade=self.trade),
            "thread-mu-main",
            W3ContextVersionPin(
                document1_run_id="d1-mu",
                document2_run_id="d2-mu",
                event_library_version=7,
                policy_set_version=3,
            ),
        )

    def close(self) -> None:
        return None


def test_w3_retries_twice_then_persists_direct_trade_delta_and_o3_gap() -> None:
    repository = InMemoryPersistentRuntimeV2Repository()
    w3 = _RetryingW3(2, trade=True)
    service = PersistentRuntimeV2Service(
        repository=repository,
        responses=_HotResponses(),
        known_events=_Known(),
        policies=_Policies(),
        w3_agent=w3,
        retry_delays_seconds=(0, 0),
        dispatch_effects=False,
    )
    case = service.execute_message(_source())
    assert case.status is RuntimeCaseStatus.PENDING_W3
    assert repository.get_w3_case(case.case_id) is not None
    assert service.process_pending_effects() == 0
    assert service.process_pending_effects() == 0
    assert service.process_pending_effects() == 1
    assert w3.calls == 3
    resolved = repository.get_case(case.case_id)
    assert resolved is not None and resolved.status is RuntimeCaseStatus.COMPLETED
    assert resolved.resolved_route is not None
    assert resolved.resolved_route.primary_route == "TRADE"
    trades = repository.list_daily_trades("MU", date(2026, 8, 31))
    assert len(trades) == 1
    assert trades[0].decision_origin is TradeDecisionOrigin.W3
    assert trades[0].executed_policy_id is None
    assert trades[0].w3_case_id
    assert len(repository.list_daily_w3_coverage_gaps("MU", date(2026, 8, 31))) == 1
    assert len(repository.list_daily_candidates("MU", date(2026, 8, 31))) == 1
    service.close()


class _PublishedOne:
    def published_version(self, ticker: str) -> int:
        return 1


class _MustSkipO2:
    async def run(self, **_kwargs: Any) -> Any:
        raise AssertionError("no candidates means O2 must be skipped")


class _RecordingO3:
    def __init__(self) -> None:
        self.feed: Any = None

    async def maintain(self, **kwargs: Any) -> dict[str, str]:
        self.feed = kwargs["maintenance_feed"]
        return {"status": "COMPLETED"}


def test_daily_close_delivers_no_trade_w3_coverage_gap_to_o3(tmp_path: Path) -> None:
    repository = InMemoryPersistentRuntimeV2Repository()
    case = _runtime_case("msg-gap")
    repository.save_case(case)
    repository.save_w3_coverage_gap(
        W3CoverageGapRecord(
            case_id=case.case_id,
            w3_case_id="w3-gap",
            ticker="MU",
            trading_date=case.trading_date,
            source=case.source,
            policy_set_version=3,
            result=_w3_result(trade=False),
        )
    )
    o3 = _RecordingO3()
    daily = PersistentRuntimeV2DailyCloseService(
        repository=repository,
        event_repository=cast(Any, _PublishedOne()),
        o2_runner=cast(Any, _MustSkipO2()),
        o3_maintainer=cast(Any, o3),
        export_root=tmp_path,
    )
    result = asyncio.run(daily.close(ticker="MU", trading_date=case.trading_date))
    assert result.stage == "COMPLETED"
    assert o3.feed is not None
    assert len(o3.feed.w3_coverage_gaps) == 1
    assert repository.list_daily_w3_coverage_gaps("MU", case.trading_date) == []


class _Workspace:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def write_text(self, run_id: str, relative_path: str, content: str) -> Any:
        self.files[f"{run_id}:{relative_path}"] = content
        return None


class _Worker:
    def __init__(self, result: W3CaseResult) -> None:
        self.result = result
        self.requests: list[WorkerRunRequest] = []

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        self.requests.append(request)
        return WorkerJob(
            job_id="job-w3",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id="thread-main-returned",
            final_response=self.result.model_dump_json(),
        )

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return None


class _Context:
    async def load(self, case: RuntimeCase) -> W3PreparedContext:
        document2 = Document2Document.model_construct(
            document2_run_id="d2-mu",
            ticker=case.ticker,
            as_of=datetime(2026, 8, 31, tzinfo=UTC),
            source_global_run_id="d1-mu",
            input_manifest=Document2InputManifest.model_construct(),
            shells=[],
            shell_outcomes=[],
        )
        policy_set = PolicySet.model_construct(
            ticker=case.ticker,
            policy_set_version=3,
            publication_state=PublicationState.COMPLETE,
            document2_ref=Document2Ref.model_construct(
                run_id="d2-mu",
                artifact_id="d2-doc",
                sha256="a" * 64,
                published_at=datetime(2026, 8, 31, tzinfo=UTC),
                publication_state=PublicationState.COMPLETE,
            ),
            event_library_ref=None,
            policies=[],
            published_at=datetime(2026, 8, 31, tzinfo=UTC),
        )
        return W3PreparedContext(
            version_pin=W3ContextVersionPin(
                document1_run_id="d1-mu",
                document2_run_id="d2-mu",
                event_library_version=7,
                policy_set_version=3,
            ),
            document1="# D1\nPrior expectation.",
            document2=document2,
            policy_set=policy_set,
            reference_view="# Reference View\n",
            document2_publication_state="COMPLETE",
        )


def test_codex_w3_runner_uses_main_thread_and_strict_isolated_request(
    tmp_path: Path,
) -> None:
    prompt_root = tmp_path / "prompts"
    (prompt_root / "skills").mkdir(parents=True)
    (prompt_root / "agent.md").write_text("W3 agent", encoding="utf-8")
    (prompt_root / "skills" / "uncovered_new.md").write_text(
        "Mode 1 skill", encoding="utf-8"
    )
    worker = _Worker(_w3_result())
    workspace = _Workspace()
    runner = CodexW3AgentRunner(
        worker=worker,
        workspace=cast(Any, workspace),
        context_provider=_Context(),
        prompt_root=prompt_root,
        timeout_seconds=30,
    )
    case = _runtime_case()
    w3_case = W3RouteCase(
        w3_case_id="w3-case",
        case_id=case.case_id,
        ticker=case.ticker,
        source=case.source,
        w1_final=cast(W1NoveltyResult, case.w1_final),
        w2_final=cast(W2PolicyResult, case.w2_final),
        event_library_version=7,
        policy_set_version=3,
        route_reason="uncovered",
        mode=W3Mode.UNCOVERED_NEW,
        attempt_count=1,
    )
    result, thread_id, _pin = runner.run(
        case=case,
        w3_case=w3_case,
        slot=W3ThreadSlot(
            ticker="MU",
            case_id=case.case_id,
            kind=W3ThreadKind.MAIN,
            thread_id="thread-main-existing",
        ),
    )
    # Use the validated slot for the actual assertion path.
    assert result.novelty.result is W1NoveltyVerdict.NEW
    assert thread_id == "thread-main-returned"
    request = worker.requests[0]
    assert request.workflow_version == "persistent_runtime_w3_v1"
    assert request.research_lane == "persistent_runtime"
    assert request.thread_id == "thread-main-existing"
    assert request.read_only is True
    assert request.data_mcp_enabled is False
    assert request.allow_subagents is False and request.max_subagents == 0
    assert any(path.endswith(":cases/w3-case/task.json") for path in workspace.files)
    assert any(
        path.endswith(":cases/w3-case/context/policy_set.json")
        for path in workspace.files
    )
    assert request.run_id == "persistent-runtime-w3-mu-main"
    assert '"w3_case_id":"w3-case"' in request.prompt
    worker.result = _w3_result().model_copy(update={"w3_case_id": "stale-case"})
    with pytest.raises(W3Error) as exc_info:
        runner.run(
            case=case,
            w3_case=w3_case,
            slot=W3ThreadSlot(
                ticker="MU",
                case_id=case.case_id,
                kind=W3ThreadKind.MAIN,
                thread_id="thread-main-returned",
            ),
        )
    assert exc_info.value.code == "w3_case_correlation_mismatch"
    runner.close()


def test_codex_w3_mode2_injects_revalidation_and_uncovered_skills(
    tmp_path: Path,
) -> None:
    prompt_root = tmp_path / "prompts"
    (prompt_root / "skills").mkdir(parents=True)
    (prompt_root / "agent.md").write_text("W3 agent", encoding="utf-8")
    (prompt_root / "skills" / "revalidate_then_evaluate.md").write_text(
        "Mode 2 Stage A",
        encoding="utf-8",
    )
    (prompt_root / "skills" / "uncovered_new.md").write_text(
        "Shared Stage B",
        encoding="utf-8",
    )
    worker = _Worker(
        _w3_result().model_copy(update={"w3_case_id": "w3-mode2-case"})
    )
    workspace = _Workspace()
    runner = CodexW3AgentRunner(
        worker=worker,
        workspace=cast(Any, workspace),
        context_provider=_Context(),
        prompt_root=prompt_root,
        timeout_seconds=30,
    )
    case = _runtime_case()
    w1_final = cast(W1NoveltyResult, case.w1_final).model_copy(
        update={"confidence": RuntimeConfidence.LOW}
    )
    case = case.model_copy(update={"w1_final": w1_final})
    w3_case = W3RouteCase(
        w3_case_id="w3-mode2-case",
        case_id=case.case_id,
        ticker=case.ticker,
        source=case.source,
        w1_final=w1_final,
        w2_final=cast(W2PolicyResult, case.w2_final),
        event_library_version=7,
        policy_set_version=3,
        route_reason="low-confidence novelty",
        mode=W3Mode.REVALIDATE_THEN_EVALUATE,
        attempt_count=1,
    )
    result, _thread_id, _pin = runner.run(
        case=case,
        w3_case=w3_case,
        slot=W3ThreadSlot(
            ticker="MU",
            case_id=case.case_id,
            kind=W3ThreadKind.MAIN,
        ),
    )
    assert result.novelty.result is W1NoveltyVerdict.NEW
    assert any(
        path.endswith(":skills/revalidate_then_evaluate.md")
        for path in workspace.files
    )
    assert any(path.endswith(":skills/uncovered_new.md") for path in workspace.files)
    request = worker.requests[0]
    assert "Read AGENTS.md and skills/revalidate_then_evaluate.md first" in request.prompt
    assert (
        "then read skills/uncovered_new.md and continue Stage B in this same turn"
        in request.prompt
    )
    runner.close()
