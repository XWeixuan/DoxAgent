from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from doxagent.agents import MockAgentRunner, default_agent_registry
from doxagent.agents.runtime.react import ReActHarnessConfig
from doxagent.agents.runtime.runner import ModelGatewayAgentRunner
from doxagent.models import (
    AgentError,
    AgentName,
    AgentPermissions,
    AgentResult,
    AgentTask,
    DocumentType,
    ResultStatus,
    RunMetadata,
    TaskType,
    new_id,
)
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import (
    EventStreamItem,
    InterfaceType,
    SourceType,
    StandardMessage,
    UpdateActor,
)
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime import (
    A2Result,
    A2VerificationStatus,
    AgentRunnerA2Worker,
    AgentRunnerO3Worker,
    AgentRunnerW1Worker,
    AgentRunnerW2Worker,
    Conviction,
    HeuristicW1Worker,
    HeuristicW2Worker,
    InMemoryPersistentRuntimeRepository,
    KnownEventsPatch,
    LazyAgentRunnerA2Worker,
    LazyAgentRunnerO3Worker,
    LazyAgentRunnerW1Worker,
    LazyAgentRunnerW2Worker,
    O3PrimaryAction,
    O3Result,
    O3RuntimeBudget,
    PersistentRuntimeExecutionService,
    RuntimeRoute,
    RuntimeSourceMessage,
    RuntimeWorkerTimeout,
    SizeBucket,
    SQLitePersistentRuntimeRepository,
    TradeDecisionSource,
    TradeIntent,
    TradeSide,
    W1Confidence,
    W1NoveltyLabel,
    W1Result,
    W2Result,
    W2Type,
)
from doxagent.prompts import default_prompt_registry
from doxagent.settings import DoxAgentSettings


def _message(
    *,
    source_type: SourceType = SourceType.MEDIA,
    message_id: str = "std_1",
    url: str | None = None,
    metadata: dict[str, object] | None = None,
) -> RuntimeSourceMessage:
    return RuntimeSourceMessage(
        source_message_id=message_id,
        ticker="asts",
        source_type=source_type,
        source_id="benzinga_news" if source_type is SourceType.MEDIA else "stocktwits_messages",
        title="ASTS receives new contract milestone",
        body="ASTS receives a new contract milestone with official confirmation.",
        url=url,
        metadata=metadata or {},
    )


def _event(
    *,
    message_id: str,
    source_type: SourceType = SourceType.MEDIA,
    stream_offset: int = 1,
    batch_window_id: str | None = None,
) -> EventStreamItem:
    message = _message(source_type=source_type, message_id=message_id)
    metadata = dict(message.metadata)
    if batch_window_id is not None:
        metadata["batch_window_id"] = batch_window_id
    payload = message.model_copy(update={"metadata": metadata}).model_dump(mode="json")
    return EventStreamItem(
        event_id=f"evt_{message_id}",
        stream_offset=stream_offset,
        standard_message_id=message_id,
        ticker=message.ticker,
        source_id=message.source_id,
        payload=payload,
    )


def _standard_message(*, message_id: str = "std_message_bus_event") -> StandardMessage:
    now = datetime.now(UTC)
    return StandardMessage(
        standard_message_id=message_id,
        raw_message_id=f"raw_{message_id}",
        source_id="benzinga_news",
        binding_id="ASTS:benzinga_news",
        ticker="ASTS",
        source_type=SourceType.MEDIA,
        interface_type=InterfaceType.BY_TICKER,
        title="ASTS receives new contract milestone",
        body="ASTS receives a new contract milestone with official confirmation.",
        symbols=["ASTS"],
        published_at=now,
        collected_at=now,
    )


def _w1(*, is_new: bool = True, confidence: W1Confidence = W1Confidence.HIGH) -> W1Result:
    return W1Result(
        is_new=is_new,
        novelty_label=W1NoveltyLabel.NEW_EVENT if is_new else W1NoveltyLabel.OLD_DUPLICATE,
        matched_known_event_ids=[] if is_new else ["KE_001"],
        confidence=confidence,
        reasoning="novelty fixture",
    )


def _w2(
    decision_type: W2Type = W2Type.DIRECT_TRADE_CANDIDATE,
    *,
    policy_code: str | None = "POLICY_DTC",
) -> W2Result:
    return W2Result(
        matched_policy_code=policy_code
        if decision_type in {W2Type.DIRECT_TRADE_CANDIDATE, W2Type.ESCALATE_TO_BACKGROUND_AGENT}
        else None,
        type=decision_type,
        reasoning="policy fixture",
    )


class StaticW1:
    def __init__(self, result: W1Result | Exception) -> None:
        self.result = result
        self.calls = 0

    def classify(self, message: RuntimeSourceMessage, context: dict[str, object]) -> W1Result:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StaticW2:
    def __init__(self, result: W2Result | Exception) -> None:
        self.result = result
        self.calls = 0

    def classify(self, message: RuntimeSourceMessage, context: dict[str, object]) -> W2Result:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StaticA2:
    def __init__(self, result: A2Result | Exception) -> None:
        self.result = result
        self.calls = 0

    def verify(self, message: RuntimeSourceMessage, context: dict[str, object]) -> A2Result:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StaticO3:
    def __init__(self, result: O3Result | Exception, *, sleep_seconds: float = 0.0) -> None:
        self.result = result
        self.sleep_seconds = sleep_seconds
        self.budgets: list[O3RuntimeBudget] = []
        self.contexts: list[dict[str, object]] = []
        self.messages: list[RuntimeSourceMessage] = []

    def judge(
        self,
        message: RuntimeSourceMessage,
        context: dict[str, object],
        budget: O3RuntimeBudget,
    ) -> O3Result:
        self.budgets.append(budget)
        self.contexts.append(dict(context))
        self.messages.append(message)
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_w1_and_w2_contracts_enforce_prd_classification_boundaries() -> None:
    with pytest.raises(ValidationError, match="is_new must follow"):
        W1Result(
            is_new=False,
            novelty_label=W1NoveltyLabel.NEW_EVENT,
            matched_known_event_ids=[],
            confidence=W1Confidence.HIGH,
            reasoning="invalid",
        )
    with pytest.raises(ValidationError, match="DTC/EBA"):
        W2Result(
            matched_policy_code=None,
            type=W2Type.DIRECT_TRADE_CANDIDATE,
            reasoning="invalid",
        )
    with pytest.raises(ValidationError, match="NULL/Irrelevant"):
        W2Result(
            matched_policy_code="POLICY_OLD_CACHE",
            type=W2Type.NULL,
            reasoning="invalid",
        )
    with pytest.raises(ValidationError):
        W2Result.model_validate(
            {
                "matched_policy_code": None,
                "type": "Irrelevant",
                "confidence": "low",
                "reasoning": "W2 must not output confidence",
            }
        )


def test_o3_is_registered_as_bounded_runtime_agent() -> None:
    definition = default_agent_registry().get(AgentName.O3_TRADING_STRATEGY)

    assert definition.task_types == [TaskType.RUNTIME_O3_JUDGMENT]
    assert definition.runtime.prompt_block_ids == ["agent.o3"]
    assert definition.runtime.output_schema == "O3Result"
    assert DocumentType.KNOWN_EVENTS.value in definition.runtime.writable_targets
    assert "monitoring.recent_events" in definition.runtime.allowed_tools
    assert definition.runtime.can_delegate is False
    assert definition.runtime.can_raise_objection is True


def test_w1_and_w2_are_registered_prompt_backed_runtime_workers() -> None:
    registry = default_agent_registry()
    prompts = default_prompt_registry()
    w1 = registry.get(AgentName.W1_RUNTIME_NOVELTY)
    w2 = registry.get(AgentName.W2_RUNTIME_POLICY)

    assert w1.task_types == [TaskType.RUNTIME_W1_NOVELTY]
    assert w1.runtime.execution_mode == "single_shot"
    assert w1.runtime.prompt_block_ids == ["runtime.w1"]
    assert w1.runtime.output_schema == "W1Result"
    assert w2.task_types == [TaskType.RUNTIME_W2_POLICY]
    assert w2.runtime.execution_mode == "single_shot"
    assert w2.runtime.prompt_block_ids == ["runtime.w2"]
    assert w2.runtime.output_schema == "W2Result"
    assert prompts.get("runtime.w1").resource_id == "runtime.w1"
    assert prompts.get("runtime.w2").resource_id == "runtime.w2"
    w1_prompt = prompts.get("runtime.w1").body
    w2_prompt = prompts.get("runtime.w2").body
    assert '"additionalProperties": false' in w1_prompt
    assert '"novelty_label"' in w1_prompt
    assert "provider JSON mode" in w1_prompt
    assert "Direct Trade Candidate" in w2_prompt
    assert '"matched_policy_code"' in w2_prompt
    assert "Do not output `cache`, `ignore`, or" in w2_prompt
    assert "ingest_queue/archive are Route Engine outcomes" in w2_prompt


def test_heuristic_w1_w2_workers_produce_prd_contract_outputs() -> None:
    message = _message(
        message_id="std_worker",
        url="https://example.test/worker",
        metadata={"content_hash": "worker-hash"},
    )
    w1 = HeuristicW1Worker().classify(
        message,
        {
            "known_events": [
                {
                    "event_id": "KE_WORKER",
                    "duplicate_detection_keys": ["contract milestone"],
                    "core_fact": "ASTS receives a new contract milestone.",
                }
            ]
        },
    )
    w2 = HeuristicW2Worker().classify(
        message,
        {
            "monitoring_policies": [
                {
                    "policy_id": "POLICY_WORKER",
                    "policy_type": "direct_trade",
                    "trigger_condition": "contract milestone",
                }
            ]
        },
    )

    assert w1.is_new is False
    assert w1.novelty_label is W1NoveltyLabel.OLD_DUPLICATE
    assert w1.matched_known_event_ids == ["KE_WORKER"]
    assert w2.type is W2Type.DIRECT_TRADE_CANDIDATE
    assert w2.matched_policy_code == "POLICY_WORKER"


def test_heuristic_w2_applies_stricter_irrelevant_threshold_for_social() -> None:
    media = _message(
        message_id="std_media_hype",
        metadata={"content_hash": "media-hype"},
    ).model_copy(update={"title": "ASTS moon pump"})
    social = _message(
        source_type=SourceType.SOCIAL,
        message_id="std_social_hype",
        metadata={"content_hash": "social-hype"},
    ).model_copy(update={"title": "ASTS moon pump"})

    worker = HeuristicW2Worker()

    assert worker.classify(media, {}).type is W2Type.NULL
    assert worker.classify(social, {}).type is W2Type.IRRELEVANT


def test_agent_runner_runtime_workers_validate_structured_payloads_and_budget() -> None:
    seen_o3_budget: dict[str, object] = {}

    def result_factory(task: AgentTask) -> AgentResult:
        if task.task_type is TaskType.RUNTIME_W1_NOVELTY:
            payload = _w1().model_dump(mode="json")
        elif task.task_type is TaskType.RUNTIME_W2_POLICY:
            payload = _w2(W2Type.NULL, policy_code=None).model_dump(mode="json")
        elif task.agent_name is AgentName.A2_FACT_CHECK:
            payload = A2Result(
                is_new=True,
                verification_status=A2VerificationStatus.VERIFIED,
                reasoning="A2 runner adapter result",
            ).model_dump(mode="json")
        else:
            runtime_context = task.input_context["runtime_context"]
            assert isinstance(runtime_context, dict)
            seen_o3_budget.update(dict(runtime_context["o3_runtime_budget"]))
            payload = O3Result(
                primary_action=O3PrimaryAction.INGEST_QUEUE,
                reasoning="O3 runner adapter result",
            ).model_dump(mode="json")
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload=payload,
        )

    runner = MockAgentRunner(result_factory=result_factory)
    message = _message(message_id="std_agent_worker")

    w1 = AgentRunnerW1Worker(runner).classify(message, {})
    w2 = AgentRunnerW2Worker(runner).classify(message, {})
    a2 = AgentRunnerA2Worker(runner).verify(message, {})
    o3 = AgentRunnerO3Worker(runner).judge(
        message,
        {"o3_mode": "unit"},
        O3RuntimeBudget(target_seconds=17),
    )

    assert w1.is_new is True
    assert w2.type is W2Type.NULL
    assert a2.passed_for_runtime is True
    assert o3.primary_action is O3PrimaryAction.INGEST_QUEUE
    assert seen_o3_budget["target_seconds"] == 17
    assert runner.calls == 4


def test_agent_runner_w1_w2_workers_normalize_deepseek_schema_drift() -> None:
    seen_context_keys: dict[str, list[str]] = {}
    seen_runtime_clocks: dict[str, dict[str, object]] = {}

    def result_factory(task: AgentTask) -> AgentResult:
        runtime_context = task.input_context["runtime_context"]
        assert isinstance(runtime_context, dict)
        seen_context_keys[task.required_output_schema] = sorted(runtime_context)
        runtime_clock = runtime_context.get("runtime_clock")
        assert isinstance(runtime_clock, dict)
        seen_runtime_clocks[task.required_output_schema] = dict(runtime_clock)
        if task.task_type is TaskType.RUNTIME_W1_NOVELTY:
            payload: dict[str, object] = {
                "structured": {
                    "novelty_assessment": "novel",
                    "reasoning": "message is new",
                }
            }
        elif task.task_type is TaskType.RUNTIME_W2_POLICY:
            payload = {
                "structured": {
                    "W2Result": {
                        "policy_trigger_assessment": {
                            "POLICY_DTC": {
                                "triggered": False,
                                "reasoning": "no trigger matched",
                            }
                        },
                        "recommendation": "相关但未命中任何政策。",
                    }
                }
            }
        else:
            raise AssertionError(task.task_type)
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload=payload,
        )

    runner = MockAgentRunner(result_factory=result_factory)
    context: dict[str, object] = {
        "known_events": [{"event_id": "KE1"}],
        "monitoring_policies": [{"policy_id": "POLICY_DTC", "policy_type": "direct_trade"}],
        "expectation_summaries": [{"expectation_id": "E1"}],
    }

    w1 = AgentRunnerW1Worker(runner).classify(_message(message_id="std_w1_drift"), context)
    w2 = AgentRunnerW2Worker(runner).classify(_message(message_id="std_w2_drift"), context)

    assert w1.is_new is True
    assert w1.novelty_label is W1NoveltyLabel.NEW_EVENT
    assert w2.type is W2Type.NULL
    assert w2.matched_policy_code is None
    assert "runtime_clock" in seen_context_keys["W1Result"]
    assert "known_events" in seen_context_keys["W1Result"]
    assert "expectation_summaries" not in seen_context_keys["W1Result"]
    assert "runtime_clock" in seen_context_keys["W2Result"]
    assert "monitoring_policies" in seen_context_keys["W2Result"]
    assert "expectation_summaries" not in seen_context_keys["W2Result"]
    for runtime_clock in seen_runtime_clocks.values():
        assert set(runtime_clock) == {"now_et", "tz_abbrev", "utc_offset"}
        assert isinstance(runtime_clock["tz_abbrev"], str)
        assert runtime_clock["tz_abbrev"] in {"EST", "EDT"}
        assert isinstance(runtime_clock["utc_offset"], str)
        assert runtime_clock["utc_offset"] in {"-05:00", "-04:00"}
        now_et = runtime_clock["now_et"]
        assert isinstance(now_et, str)
        assert datetime.fromisoformat(now_et).tzinfo is not None


def test_agent_runner_w1_worker_normalizes_event_id_short_shape() -> None:
    def result_factory(task: AgentTask) -> AgentResult:
        assert task.task_type is TaskType.RUNTIME_W1_NOVELTY
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "structured": {
                    "event_id": "evt_mu_012",
                    "reason": "The earnings call transcript was released earlier.",
                }
            },
        )

    w1 = AgentRunnerW1Worker(MockAgentRunner(result_factory=result_factory)).classify(
        _message(message_id="std_w1_short_shape"),
        {"known_events": [{"event_id": "evt_mu_012"}]},
    )

    assert w1.is_new is False
    assert w1.novelty_label is W1NoveltyLabel.OLD_DUPLICATE
    assert w1.matched_known_event_ids == ["evt_mu_012"]


def test_agent_runner_w1_worker_normalizes_novelty_score_shape() -> None:
    def result_factory(task: AgentTask) -> AgentResult:
        assert task.task_type is TaskType.RUNTIME_W1_NOVELTY
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "structured": {
                    "novelty_score": 5,
                    "reasoning": "This mostly repeats known earnings discussion; low novelty.",
                }
            },
        )

    w1 = AgentRunnerW1Worker(MockAgentRunner(result_factory=result_factory)).classify(
        _message(message_id="std_w1_score_shape"),
        {"known_events": [{"event_id": "evt_mu_012"}]},
    )

    assert w1.is_new is False
    assert w1.novelty_label is W1NoveltyLabel.KNOWN_EVENT_RECAP
    assert w1.confidence is W1Confidence.MEDIUM


def test_agent_runner_w2_worker_normalizes_policy_triggered_false_shape() -> None:
    def result_factory(task: AgentTask) -> AgentResult:
        assert task.task_type is TaskType.RUNTIME_W2_POLICY
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "structured": {
                    "policy_triggered": False,
                    "triggered_policy_ids": [],
                    "reasoning": "No direct policy condition matched this message.",
                }
            },
        )

    w2 = AgentRunnerW2Worker(MockAgentRunner(result_factory=result_factory)).classify(
        _message(message_id="std_w2_triggered_false"),
        {"monitoring_policies": [{"policy_id": "POLICY_DTC", "policy_type": "direct_trade"}]},
    )

    assert w2.type is W2Type.NULL
    assert w2.matched_policy_code is None


def test_agent_runner_w1_w2_workers_fallback_on_deepseek_non_json_text() -> None:
    def result_factory(task: AgentTask) -> AgentResult:
        text = (
            "material update with missing JSON"
            if task.task_type is TaskType.RUNTIME_W1_NOVELTY
            else "No policy trigger matched this relevant message."
        )
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.FAILED,
            error=AgentError(
                code="model_gateway_error",
                message="JSON response requested, but provider text was not a JSON object.",
                retryable=False,
                details={
                    "gateway_error": {
                        "code": "invalid_json",
                        "details": {"text": text},
                    }
                },
            ),
        )

    runner = MockAgentRunner(result_factory=result_factory)

    w1 = AgentRunnerW1Worker(runner).classify(_message(message_id="std_w1_text"), {})
    w2 = AgentRunnerW2Worker(runner).classify(_message(message_id="std_w2_text"), {})

    assert w1.is_new is True
    assert w1.novelty_label is W1NoveltyLabel.MATERIAL_UPDATE
    assert w1.confidence is W1Confidence.LOW
    assert w2.type is W2Type.NULL
    assert w2.matched_policy_code is None


def test_o3_runtime_budget_tightens_react_runner_loop_config() -> None:
    runner = ModelGatewayAgentRunner(react_config=ReActHarnessConfig(max_steps=5))
    task = AgentTask(
        task_id=new_id("task"),
        ticker="ASTS",
        agent_name=AgentName.O3_TRADING_STRATEGY,
        task_type=TaskType.RUNTIME_O3_JUDGMENT,
        input_context={
            "o3_runtime_budget": {
                "target_seconds": 120,
                "max_model_calls": 2,
                "max_parallel_tool_call_batches": 1,
            }
        },
        required_output_schema="O3Result",
        permissions=AgentPermissions(),
        run_metadata=RunMetadata(
            run_id=new_id("run"),
            ticker="ASTS",
            workflow_node="persistent_runtime_execution",
            created_at=datetime.now(UTC),
        ),
    )

    config = runner._react_config_for_task(task)

    assert config.max_steps == 2
    assert config.max_tool_calls_per_name == 1


def test_media_new_dtc_high_confidence_bypasses_o3_and_records_trade() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    o3 = StaticO3(
        O3Result(
            primary_action=O3PrimaryAction.INGEST_QUEUE,
            side_effects=["known_events_update"],
            known_events_patch=KnownEventsPatch(
                event_id="KE_NEW",
                core_fact="ASTS receives a new milestone.",
                duplicate_detection_keys=["ASTS", "milestone"],
            ),
            reasoning="update known events only",
        )
    )
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
        o3_worker=o3,
    )

    record = service.execute_message(
        _message(),
        context={
            "monitoring_policies": [
                {
                    "policy_id": "POLICY_DTC",
                    "action": {
                        "side": "long",
                        "conviction": "high",
                        "size_bucket": "normal",
                        "reasoning": "fixture policy",
                    },
                }
            ]
        },
    )

    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    trading = repository.list_trading_records(ticker="ASTS")[0]
    assert trading.trade_intent.conviction is Conviction.HIGH
    assert trading.source_type is SourceType.MEDIA
    assert trading.route == "new_dtc"
    assert trading.status.value == "recorded_only"
    assert trading.audit_snapshot is not None
    assert trading.audit_snapshot.decision_source is TradeDecisionSource.W2_POLICY_DIRECT
    assert trading.audit_snapshot.runtime_execution_id == record.execution_id
    assert trading.audit_snapshot.runtime_started_at is not None
    assert trading.audit_snapshot.intent_generated_at == trading.created_at
    assert repository.list_archive(ticker="ASTS") == []
    assert len(repository.list_known_events_patch_logs(ticker="ASTS")) == 1
    assert o3.budgets[0].max_model_calls == 2
    assert o3.budgets[0].max_parallel_tool_call_batches == 1
    assert "routed_to_trading_records" in record.message_statuses
    assert "known_events_updated" in record.message_statuses
    assert {trace.node for trace in record.node_traces} >= {"W1", "W2", "O3_KNOWN_EVENTS"}
    assert all(trace.duration_ms >= 0 for trace in record.node_traces)


def test_o3_known_events_patch_updates_runtime_memory_for_next_w1_run() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    policy_context: dict[str, object] = {
        "monitoring_policies": [
            {
                "policy_id": "POLICY_RUNTIME_KNOWN_EVENT",
                "policy_type": "direct_trade",
                "trigger_condition": "contract milestone",
            }
        ]
    }
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=HeuristicW1Worker(),
        w2_worker=HeuristicW2Worker(),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.INGEST_QUEUE,
                side_effects=["known_events_update"],
                known_events_patch=KnownEventsPatch(
                    event_id="KE_RUNTIME_1",
                    core_fact="ASTS contract milestone was confirmed.",
                    duplicate_detection_keys=["contract milestone"],
                ),
                reasoning="runtime Known Events update",
            )
        ),
    )

    first = service.execute_message(
        _message(message_id="std_ke_first"),
        context=policy_context,
    )
    second = service.execute_message(
        _message(message_id="std_ke_second"),
        context=policy_context,
    )

    current_events = repository.list_known_events(ticker="ASTS")
    assert first.route_decision.route is RuntimeRoute.TRADING_RECORD
    assert [event.event_id for event in current_events] == ["KE_RUNTIME_1"]
    assert second.w1_result is not None
    assert second.w1_result.is_new is False
    assert second.w1_result.matched_known_event_ids == ["KE_RUNTIME_1"]
    assert second.route_decision.route is RuntimeRoute.ARCHIVE


def test_media_null_low_confidence_goes_to_o3_and_can_enter_ingest_queue() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1(confidence=W1Confidence.LOW)),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.INGEST_QUEUE,
                reasoning="important but no real-time trade value",
            )
        ),
    )

    record = service.execute_message(_message(message_id="std_null_low"))

    assert record.route_decision.route is RuntimeRoute.INGEST_QUEUE
    assert record.route_decision.o3_must_check_novelty_first is True
    assert repository.list_ingest_queue(ticker="ASTS")[0].reason.startswith("O3 final action")
    assert repository.list_trading_records(ticker="ASTS") == []


def test_w1_worker_timeout_records_exception_without_blocking_queue() -> None:
    class SlowW1:
        def classify(
            self,
            message: RuntimeSourceMessage,
            context: dict[str, object],
        ) -> W1Result:
            time.sleep(0.2)
            return _w1()

    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=SlowW1(),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        w1_w2_worker_timeout_seconds=0.01,
    )

    started = time.monotonic()
    record = service.execute_message(
        _message(source_type=SourceType.SOCIAL, message_id="std_slow_w1")
    )

    assert time.monotonic() - started < 0.15
    assert record.route_decision.route is RuntimeRoute.INGEST_QUEUE
    exception = repository.list_exceptions(ticker="ASTS")[0]
    assert exception.node == "W1"
    assert exception.exception_type == "timeout"
    assert record.node_traces[0].node == "W1"
    assert record.node_traces[0].status == "failed"


def test_worker_traces_record_input_size_timeout_budget_and_retries() -> None:
    class FlakyO3:
        def __init__(self) -> None:
            self.calls = 0

        def judge(
            self,
            message: RuntimeSourceMessage,
            context: dict[str, object],
            budget: O3RuntimeBudget,
        ) -> O3Result:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient O3 failure")
            return O3Result(
                primary_action=O3PrimaryAction.INGEST_QUEUE,
                reasoning="retry recovered O3 judgment",
            )

    o3 = FlakyO3()
    service = PersistentRuntimeExecutionService(
        InMemoryPersistentRuntimeRepository(),
        w1_worker=StaticW1(_w1(confidence=W1Confidence.LOW)),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=o3,
        o3_budget=O3RuntimeBudget(target_seconds=9),
        w1_w2_worker_timeout_seconds=12,
        worker_retry_attempts=2,
    )

    record = service.execute_message(
        _message(message_id="std_observed_workers"),
        context={
            "known_events": [{"event_id": "KE_TRACE", "core_fact": "old milestone"}],
            "monitoring_policies": [
                {"policy_id": "POLICY_TRACE", "trigger_condition": "contract milestone"}
            ],
        },
    )

    traces = {trace.node: trace for trace in record.node_traces}
    assert traces["W1"].timeout_budget_ms == 12_000
    assert traces["W2"].timeout_budget_ms == 12_000
    assert traces["O3"].timeout_budget_ms == 9_000
    assert traces["O3"].attempts == 2
    assert o3.calls == 2
    for node in ("W1", "W2", "O3"):
        assert traces[node].source_message_bytes is not None
        assert traces[node].source_message_bytes > 0
        assert traces[node].runtime_context_bytes is not None
        assert traces[node].runtime_context_bytes > 0
        assert traces[node].prompt_input_bytes == (
            traces[node].source_message_bytes + traces[node].runtime_context_bytes
        )


def test_worker_retry_attempts_are_recorded_for_w1_w2_path() -> None:
    class FlakyW1:
        def __init__(self) -> None:
            self.calls = 0

        def classify(
            self,
            message: RuntimeSourceMessage,
            context: dict[str, object],
        ) -> W1Result:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient W1 failure")
            return _w1()

    w1 = FlakyW1()
    service = PersistentRuntimeExecutionService(
        InMemoryPersistentRuntimeRepository(),
        w1_worker=w1,
        w2_worker=StaticW2(_w2()),
        worker_retry_attempts=2,
    )

    record = service.execute_message(_message(message_id="std_flaky_w1"))

    traces = {trace.node: trace for trace in record.node_traces}
    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    assert traces["W1"].attempts == 2
    assert w1.calls == 2


def test_social_new_dtc_must_pass_a2_and_o3_before_trading_record() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
        a2_worker=StaticA2(
            A2Result(
                is_new=True,
                verification_status=A2VerificationStatus.VERIFIED,
                reasoning="verified social item",
            )
        ),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.TRADING_RECORD,
                trade_intent=TradeIntent(
                    side=TradeSide.LONG,
                    conviction=Conviction.MEDIUM,
                    size_bucket=SizeBucket.SMALL,
                    reasoning="O3 approved social trade intent",
                ),
                reasoning="O3 approved",
            )
        ),
    )

    record = service.execute_message(
        _message(source_type=SourceType.SOCIAL, message_id="std_social_dtc")
    )

    assert record.a2_result is not None
    assert record.o3_result is not None
    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    trading = repository.list_trading_records(ticker="ASTS")[0]
    assert trading.trade_intent.size_bucket is SizeBucket.SMALL
    assert trading.a2_result is not None
    assert trading.o3_result is not None
    assert trading.route == "o3_trade"
    assert trading.audit_snapshot is not None
    assert trading.audit_snapshot.decision_source is TradeDecisionSource.O3_DUTY_EXPERT


def test_social_batch_merges_a2_passed_items_into_one_o3_input_package() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    a2 = StaticA2(
        A2Result(
            is_new=True,
            verification_status=A2VerificationStatus.VERIFIED,
            reasoning="verified social items",
        )
    )
    o3 = StaticO3(
        O3Result(
            primary_action=O3PrimaryAction.INGEST_QUEUE,
            reasoning="batch has sentiment value but no immediate trade.",
        )
    )
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        a2_worker=a2,
        o3_worker=o3,
    )

    records = service.execute_social_batch(
        [
            _message(source_type=SourceType.SOCIAL, message_id="std_social_o3_1"),
            _message(source_type=SourceType.SOCIAL, message_id="std_social_o3_2"),
        ],
        ticker="ASTS",
        batch_window_id="social-window-1",
    )

    assert a2.calls == 2
    assert len(o3.contexts) == 1
    assert {record.route_decision.route for record in records} == {RuntimeRoute.INGEST_QUEUE}
    assert {record.route_decision.batch_id for record in records} == {"social-window-1"}
    package = o3.contexts[0]["social_batch"]
    assert isinstance(package, dict)
    assert package["batch_window_id"] == "social-window-1"
    assert package["summary_stats"] == {
        "total_items": 2,
        "new_items": 2,
        "non_irrelevant_items": 2,
        "a2_passed_items": 2,
    }
    assert [item["source_message_id"] for item in package["items"]] == [
        "std_social_o3_1",
        "std_social_o3_2",
    ]
    assert len(repository.list_ingest_queue(ticker="ASTS")) == 2


def test_social_batch_dtc_o3_timeout_records_trade_with_exception() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
        a2_worker=StaticA2(
            A2Result(
                is_new=True,
                verification_status=A2VerificationStatus.VERIFIED,
                reasoning="verified social item",
            )
        ),
        o3_worker=StaticO3(RuntimeWorkerTimeout("social batch O3 exceeded budget")),
    )

    record = service.execute_social_batch(
        [_message(source_type=SourceType.SOCIAL, message_id="std_social_timeout")],
        ticker="ASTS",
        batch_window_id="social-window-timeout",
    )[0]

    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    trading = repository.list_trading_records(ticker="ASTS")[0]
    assert trading.status.value == "recorded_with_exception"
    assert trading.exception_type == "o3_timeout"
    assert trading.audit_snapshot is not None
    assert trading.audit_snapshot.decision_source is TradeDecisionSource.O3_UPSTREAM_RETAINED
    assert repository.list_ingest_queue(ticker="ASTS") == []
    assert repository.list_exceptions(ticker="ASTS")[0].exception_type == "o3_timeout"


def test_o3_timeout_on_trade_path_records_trade_with_exception_not_ingest_queue() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(repository)

    record = service.record_o3_timeout_on_trade_path(
        _message(message_id="std_o3_timeout"),
        w1=_w1(),
        w2=_w2(),
    )

    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    trading = repository.list_trading_records(ticker="ASTS")[0]
    assert trading.status.value == "recorded_with_exception"
    assert trading.exception_type == "o3_timeout"
    assert repository.list_ingest_queue(ticker="ASTS") == []
    assert repository.list_exceptions(ticker="ASTS")[0].exception_type == "o3_timeout"


def test_o3_timeout_outside_trade_path_still_records_trade_with_exception() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=StaticO3(RuntimeWorkerTimeout("O3 exceeded bounded runtime")),
    )

    record = service.execute_message(_message(message_id="std_o3_timeout_null"))

    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    trading = repository.list_trading_records(ticker="ASTS")[0]
    assert trading.status.value == "recorded_with_exception"
    assert trading.exception_type == "o3_timeout"
    assert trading.trade_intent.conviction is Conviction.LOW
    assert repository.list_ingest_queue(ticker="ASTS") == []
    assert repository.list_exceptions(ticker="ASTS")[0].exception_type == "o3_timeout"


def test_o3_runtime_budget_enforces_timeout_without_waiting_for_slow_worker() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.INGEST_QUEUE,
                reasoning="slow but eventually harmless",
            ),
            sleep_seconds=0.05,
        ),
        o3_budget=O3RuntimeBudget(target_seconds=0),
    )

    record = service.execute_message(_message(message_id="std_o3_budget_timeout"))

    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    assert repository.list_exceptions(ticker="ASTS")[0].exception_type == "o3_timeout"
    assert any(
        trace.node == "O3" and trace.status == "failed" and trace.exception_id
        for trace in record.node_traces
    )
    assert repository.list_ingest_queue(ticker="ASTS") == []


def test_blocking_objection_frequency_limit_downgrades_to_daily_objection_note() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.OBJECTION,
                blackboard_target="document3.known_events",
                reasoning="Known Events missing a runtime-critical event.",
            )
        ),
    )

    first = service.execute_message(_message(message_id="std_objection_1"))
    second = service.execute_message(_message(message_id="std_objection_2"))

    objections = repository.list_objections(ticker="ASTS")
    queue_items = repository.list_ingest_queue(ticker="ASTS")
    assert first.route_decision.route is RuntimeRoute.OBJECTION
    assert second.route_decision.route is RuntimeRoute.OBJECTION_NOTE
    assert [item.objection_type for item in objections] == [
        O3PrimaryAction.OBJECTION,
        O3PrimaryAction.OBJECTION_NOTE,
    ]
    assert queue_items[0].queue_type == "daily_close_review"
    assert queue_items[0].available_for_doxatlas is False
    assert queue_items[0].available_for_research_agent is True
    assert queue_items[0].available_after is not None


def test_low_confidence_o3_objection_is_forced_to_objection_note_queue() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.OBJECTION,
                confidence=W1Confidence.LOW,
                blackboard_target="document3.monitoring_policy",
                reasoning="Potential issue, but confidence is low.",
            )
        ),
    )

    record = service.execute_message(_message(message_id="std_low_confidence_objection"))

    assert record.route_decision.route is RuntimeRoute.OBJECTION_NOTE
    assert repository.list_objections(ticker="ASTS")[0].objection_type is (
        O3PrimaryAction.OBJECTION_NOTE
    )
    assert repository.list_ingest_queue(ticker="ASTS")[0].queue_type == "daily_close_review"


def test_w2_failure_routes_new_media_to_o3_fallback_and_keeps_exception_audit() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(RuntimeError("schema invalid")),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.ARCHIVE,
                reasoning="O3 judged no value after W2 failure",
            )
        ),
    )

    record = service.execute_message(_message(message_id="std_w2_fail"))

    assert record.route_decision.route is RuntimeRoute.ARCHIVE
    assert record.exception_ids
    assert repository.list_exceptions(ticker="ASTS")[0].payload["w2_failed"] is True
    assert repository.list_archive(ticker="ASTS")[0].source_message_id == "std_w2_fail"


def test_w1_failure_on_media_executes_o3_fallback_and_marks_exception_payload() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(RuntimeError("schema invalid")),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.ARCHIVE,
                reasoning="O3 fallback judged W1-failed media not actionable.",
            )
        ),
    )

    record = service.execute_message(_message(message_id="std_w1_fail_media"))

    exception = repository.list_exceptions(ticker="ASTS")[0]
    assert record.route_decision.route is RuntimeRoute.ARCHIVE
    assert record.o3_result is not None
    assert exception.payload["w1_failed"] is True
    assert "w1_running" in record.message_statuses
    assert "failed_with_exception" in record.message_statuses


def test_a2_failure_marks_payload_and_preserves_low_confidence_dtc_for_review() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1(confidence=W1Confidence.LOW)),
        w2_worker=StaticW2(_w2()),
        a2_worker=StaticA2(RuntimeError("A2 unavailable")),
    )

    record = service.execute_message(_message(message_id="std_a2_fail_media"))

    exception = repository.list_exceptions(ticker="ASTS")[0]
    assert record.route_decision.route is RuntimeRoute.INGEST_QUEUE
    assert exception.payload["a2_failed"] is True
    assert repository.list_ingest_queue(ticker="ASTS")[0].source_message_id == ("std_a2_fail_media")


def test_sqlite_runtime_repository_is_idempotent_for_trading_records(tmp_path: Path) -> None:
    repository = SQLitePersistentRuntimeRepository(tmp_path / "runtime.sqlite3")
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
    )
    message = _message(message_id="std_sqlite_idempotent")

    w1_worker = service.w1_worker
    w2_worker = service.w2_worker
    first = service.execute_message(message)
    second = service.execute_message(message)

    assert first.source_message.source_message_id == second.source_message.source_message_id
    assert len(repository.list_trading_records(ticker="ASTS")) == 1
    assert isinstance(w1_worker, StaticW1)
    assert isinstance(w2_worker, StaticW2)
    assert w1_worker.calls == 1
    assert w2_worker.calls == 1


def test_sqlite_runtime_repository_reads_legacy_nested_evidence_refs(tmp_path: Path) -> None:
    path = tmp_path / "runtime-legacy-evidence.sqlite3"
    repository = SQLitePersistentRuntimeRepository(path)
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
        a2_worker=StaticA2(
            A2Result(
                is_new=True,
                verification_status=A2VerificationStatus.VERIFIED,
                reasoning="verified social item",
            )
        ),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.TRADING_RECORD,
                trade_intent=TradeIntent(
                    side=TradeSide.LONG,
                    conviction=Conviction.MEDIUM,
                    size_bucket=SizeBucket.SMALL,
                    reasoning="O3 approved social trade intent",
                ),
                reasoning="O3 approved",
            )
        ),
    )
    service.execute_message(
        _message(source_type=SourceType.SOCIAL, message_id="std_legacy_evidence")
    )

    with sqlite3.connect(path) as conn:
        for table in ("persistent_runtime_executions", "persistent_trading_records"):
            row = conn.execute(f"select rowid, payload_json from {table}").fetchone()
            assert row is not None
            payload = json.loads(row[1])
            for result_name in ("w1_result", "w2_result", "a2_result", "o3_result"):
                result = payload.get(result_name)
                if isinstance(result, dict):
                    result["evidence_refs"] = [
                        {"source_type": "legacy", "source_id": "pre-restructure"}
                    ]
            conn.execute(
                f"update {table} set payload_json = ? where rowid = ?",
                (json.dumps(payload), row[0]),
            )

    execution = repository.list_executions(ticker="ASTS")[0]
    trading = repository.list_trading_records(ticker="ASTS")[0]

    assert execution.o3_result is not None
    assert execution.o3_result.reasoning == "O3 approved"
    assert trading.o3_result is not None
    assert trading.o3_result.reasoning == "O3 approved"

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "select rowid, payload_json from persistent_runtime_executions"
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        payload["o3_result"]["unrelated_unknown_field"] = True
        conn.execute(
            "update persistent_runtime_executions set payload_json = ? where rowid = ?",
            (json.dumps(payload), row[0]),
        )

    with pytest.raises(ValidationError, match="unrelated_unknown_field"):
        repository.list_executions(ticker="ASTS")


def test_sqlite_runtime_repository_supports_limited_newest_reads(tmp_path: Path) -> None:
    repository = SQLitePersistentRuntimeRepository(tmp_path / "runtime-limited.sqlite3")
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
    )

    first = service.execute_message(_message(message_id="std_sqlite_limit_first"))
    second = service.execute_message(_message(message_id="std_sqlite_limit_second"))

    assert len(repository.list_executions(ticker="ASTS")) == 2
    newest = repository.list_executions(ticker="ASTS", limit=1, newest_first=True)
    assert [record.execution_id for record in newest] == [second.execution_id]
    oldest = repository.list_executions(ticker="ASTS", limit=1)
    assert [record.execution_id for record in oldest] == [first.execution_id]


def test_duplicate_url_archives_without_retriggering_workers() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    w1 = StaticW1(_w1())
    w2 = StaticW2(_w2())
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=w1,
        w2_worker=w2,
    )

    first = service.execute_message(
        _message(message_id="std_dup_original", url="https://example.test/news/asts")
    )
    second = service.execute_message(
        _message(message_id="std_dup_copy", url="https://example.test/news/asts/")
    )

    assert first.route_decision.route is RuntimeRoute.TRADING_RECORD
    assert second.route_decision.route is RuntimeRoute.ARCHIVE
    assert second.route_decision.duplicate_of_source_message_id == "std_dup_original"
    assert second.route_decision.duplicate_key == "url:https://example.test/news/asts"
    assert "deduplicated" in second.message_statuses
    assert repository.list_archive(ticker="ASTS")[0].source_message_id == "std_dup_copy"
    assert len(repository.list_trading_records(ticker="ASTS")) == 1
    assert w1.calls == 1
    assert w2.calls == 1


def test_duplicate_content_hash_archives_in_sqlite_repository(tmp_path: Path) -> None:
    repository = SQLitePersistentRuntimeRepository(tmp_path / "runtime-duplicates.sqlite3")
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2(W2Type.IRRELEVANT, policy_code=None)),
    )

    service.execute_message(
        _message(message_id="std_hash_original", metadata={"content_hash": "HASH-1"})
    )
    duplicate = service.execute_message(
        _message(message_id="std_hash_duplicate", metadata={"content_hash": "hash-1"})
    )

    assert duplicate.route_decision.route is RuntimeRoute.ARCHIVE
    assert duplicate.route_decision.duplicate_of_source_message_id == "std_hash_original"
    assert duplicate.route_decision.duplicate_key == "content_hash:hash-1"


def test_duplicate_url_hash_and_source_time_archive_without_reprocessing() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    w1 = StaticW1(_w1(is_new=False))
    w2 = StaticW2(_w2(W2Type.IRRELEVANT, policy_code=None))
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=w1,
        w2_worker=w2,
    )
    published_at = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)

    service.execute_message(
        _message(
            message_id="std_url_hash_original",
            metadata={"url_hash": "url-hash-1"},
        )
    )
    url_hash_duplicate = service.execute_message(
        _message(
            message_id="std_url_hash_duplicate",
            metadata={"url_hash": "URL-HASH-1"},
        )
    )
    service.execute_message(
        _message(
            message_id="std_source_time_original",
        ).model_copy(update={"published_at": published_at})
    )
    source_time_duplicate = service.execute_message(
        _message(
            message_id="std_source_time_duplicate",
        ).model_copy(update={"published_at": published_at})
    )

    assert url_hash_duplicate.route_decision.duplicate_key == "url_hash:url-hash-1"
    assert source_time_duplicate.route_decision.duplicate_key is not None
    assert source_time_duplicate.route_decision.duplicate_key.startswith(
        "source_time:media:benzinga_news:"
    )
    assert w1.calls == 2
    assert w2.calls == 2


def test_duplicate_social_batch_item_archives_by_batch_window_and_item_id() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    w1 = StaticW1(_w1(is_new=False))
    w2 = StaticW2(_w2(W2Type.IRRELEVANT, policy_code=None))
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=w1,
        w2_worker=w2,
    )

    records = service.execute_social_batch(
        [
            _message(
                source_type=SourceType.SOCIAL,
                message_id="std_social_batch_item_original",
                metadata={"batch_window_id": "window-dup", "item_id": "item-1"},
            ),
            _message(
                source_type=SourceType.SOCIAL,
                message_id="std_social_batch_item_duplicate",
                metadata={"batch_window_id": "window-dup", "item_id": "item-1"},
            ),
        ],
        ticker="ASTS",
        batch_window_id="window-dup",
    )

    assert records[1].route_decision.route is RuntimeRoute.ARCHIVE
    assert records[1].route_decision.duplicate_of_source_message_id == (
        "std_social_batch_item_original"
    )
    assert records[1].route_decision.duplicate_key == "batch_item:window-dup:item-1"
    assert w1.calls == 1
    assert w2.calls == 1


def test_recent_executions_exposes_status_trace_and_final_route() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1(is_new=False)),
        w2_worker=StaticW2(_w2(W2Type.IRRELEVANT, policy_code=None)),
    )

    service.execute_message(_message(message_id="std_observable"))

    execution = service.recent_executions(ticker="ASTS")[0]
    assert execution.route_decision.route is RuntimeRoute.ARCHIVE
    assert "routed_to_archive" in execution.message_statuses
    assert {trace.node for trace in execution.node_traces} == {"W1", "W2"}
    assert all(trace.status == "succeeded" for trace in execution.node_traces)


def test_runtime_observations_expose_prd_debug_view_fields() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2(W2Type.NULL, policy_code=None)),
        o3_worker=StaticO3(
            O3Result(
                primary_action=O3PrimaryAction.OBJECTION_NOTE,
                side_effects=["known_events_update"],
                known_events_patch=KnownEventsPatch(
                    event_id="KE_OBSERVE",
                    core_fact="ASTS observation event.",
                    duplicate_detection_keys=["observation event"],
                ),
                blackboard_target="document3.known_events",
                reasoning="important for after-close review",
            )
        ),
    )

    service.execute_message(_message(message_id="std_observation"))

    observation = service.runtime_observations(ticker="ASTS")[0]
    assert observation.source_message_id == "std_observation"
    assert observation.source_type is SourceType.MEDIA
    assert observation.w1_result is not None
    assert observation.w2_result is not None
    assert observation.o3_result is not None
    assert observation.final_route is RuntimeRoute.OBJECTION_NOTE
    assert observation.entered_ingest_queue is True
    assert observation.entered_archive is False
    assert observation.known_events_updated is True
    assert observation.objection_note_created is True
    assert "O3" in observation.node_durations_ms


def test_execute_event_stream_item_converts_message_and_routes_once() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1(is_new=False)),
        w2_worker=StaticW2(_w2(W2Type.IRRELEVANT, policy_code=None)),
    )

    first = service.execute_event(_event(message_id="std_event_archive"))
    second = service.execute_event(_event(message_id="std_event_archive"))

    assert first.execution_id == second.execution_id
    assert repository.list_archive(ticker="ASTS")[0].source_message_id == "std_event_archive"


def test_trade_snapshot_uses_immutable_message_bus_event_time() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
    )
    event_time = datetime.fromisoformat("2026-07-08T13:31:00+00:00")
    event = _event(message_id="std_event_trade").model_copy(update={"event_time": event_time})

    service.execute_event(event)

    trading = repository.list_trading_records(ticker="ASTS")[0]
    assert trading.audit_snapshot is not None
    assert trading.audit_snapshot.message_bus_event_time == event_time
    assert trading.audit_snapshot.runtime_execution_id.startswith("pre_")
    assert trading.audit_snapshot.intent_generated_at == trading.created_at


def test_execute_event_can_mark_message_bus_event_consumed() -> None:
    monitoring_repository = InMemoryMonitoringRepository()
    monitoring_service = MonitoringBusService(monitoring_repository)
    monitoring_service.configure_ticker_source(
        "ASTS",
        "benzinga_news",
        updated_by=UpdateActor.USER,
    )
    standard = monitoring_repository.save_standard_message(
        _standard_message(message_id="std_consumable_event")
    )
    event = monitoring_repository.append_event(standard)
    runtime_repository = InMemoryPersistentRuntimeRepository()
    runtime_service = PersistentRuntimeExecutionService(
        runtime_repository,
        w1_worker=StaticW1(_w1(is_new=False)),
        w2_worker=StaticW2(_w2(W2Type.IRRELEVANT, policy_code=None)),
    )

    record = runtime_service.execute_event(
        event,
        mark_consumed=monitoring_service.mark_event_consumed,
    )

    assert record.source_message.source_message_id == "std_consumable_event"
    assert monitoring_service.recent_events(ticker="ASTS")[0].consumed is True
    assert runtime_repository.list_archive(ticker="ASTS")[0].source_message_id == (
        "std_consumable_event"
    )


def test_execute_events_groups_social_by_polling_window_and_preserves_batch_id() -> None:
    repository = InMemoryPersistentRuntimeRepository()
    service = PersistentRuntimeExecutionService(
        repository,
        w1_worker=StaticW1(_w1(is_new=False)),
        w2_worker=StaticW2(_w2(W2Type.IRRELEVANT, policy_code=None)),
    )

    records = service.execute_events(
        [
            _event(
                message_id="std_social_batch_2",
                source_type=SourceType.SOCIAL,
                stream_offset=2,
                batch_window_id="window-1",
            ),
            _event(
                message_id="std_social_batch_1",
                source_type=SourceType.SOCIAL,
                stream_offset=1,
                batch_window_id="window-1",
            ),
        ]
    )

    assert [record.source_message.source_message_id for record in records] == [
        "std_social_batch_1",
        "std_social_batch_2",
    ]
    assert {record.route_decision.batch_id for record in records} == {"window-1"}
    assert len(repository.list_archive(ticker="ASTS")) == 2


def test_service_from_settings_creates_sqlite_runtime_store(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime-from-settings.sqlite3"
    service = PersistentRuntimeExecutionService.from_settings(
        DoxAgentSettings(
            persistent_runtime_storage_mode="sqlite",
            persistent_runtime_sqlite_path=str(db_path),
        ),
        w1_worker=StaticW1(_w1()),
        w2_worker=StaticW2(_w2()),
    )

    service.execute_message(_message(message_id="std_settings"))

    assert db_path.exists()


def test_service_from_settings_configures_lazy_real_workers_without_eager_model_setup(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime-real-defaults.sqlite3"
    service = PersistentRuntimeExecutionService.from_settings(
        DoxAgentSettings(
            persistent_runtime_storage_mode="sqlite",
            persistent_runtime_sqlite_path=str(db_path),
        )
    )

    assert isinstance(service.w1_worker, LazyAgentRunnerW1Worker)
    assert service.w1_worker._delegate is None
    assert isinstance(service.w2_worker, LazyAgentRunnerW2Worker)
    assert service.w2_worker._delegate is None
    assert isinstance(service.a2_worker, LazyAgentRunnerA2Worker)
    assert service.a2_worker._delegate is None
    assert isinstance(service.o3_worker, LazyAgentRunnerO3Worker)
    assert service.o3_worker._delegate is None


def test_service_from_settings_can_use_injected_heuristic_runtime_workers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime-heuristic-workers.sqlite3"
    service = PersistentRuntimeExecutionService.from_settings(
        DoxAgentSettings(
            persistent_runtime_storage_mode="sqlite",
            persistent_runtime_sqlite_path=str(db_path),
        ),
        w1_worker=HeuristicW1Worker(),
        w2_worker=HeuristicW2Worker(),
    )

    record = service.execute_message(
        _message(
            message_id="std_default_workers",
            metadata={"content_hash": "default-worker-hash"},
        ),
        context={
            "known_events": [],
            "monitoring_policies": [
                {
                    "policy_id": "POLICY_DEFAULT_WORKER",
                    "policy_type": "direct_trade",
                    "trigger_condition": "contract milestone",
                }
            ],
        },
    )

    assert record.w1_result is not None
    assert record.w2_result is not None
    assert record.route_decision.route is RuntimeRoute.TRADING_RECORD
    assert db_path.exists()
