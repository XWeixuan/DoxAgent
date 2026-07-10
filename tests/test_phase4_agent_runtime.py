import pytest

from doxagent.agents import (
    MafAgentAdapter,
    MockAgentRunner,
    ModelGatewayAgentRunner,
    UnknownAgentError,
    default_agent_registry,
    default_real_agent_runner,
)
from doxagent.blackboard import BlackboardService
from doxagent.context import ContextBuilder
from doxagent.gateway import MockModelClient, ModelGateway, ModelRequest, ModelResponse
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentTask,
    DocumentType,
    ResultStatus,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools import ToolRequest, default_tool_registry
from tests.fixtures.phase1_contracts import (
    NOW,
    TICKER,
    agent_task,
    delegation,
    evidence_ref,
    objection,
    patch,
)


def test_default_registry_contains_phase4_agent_set() -> None:
    registry = default_agent_registry()

    assert set(registry.names()) == {
        AgentName.W1_RUNTIME_NOVELTY,
        AgentName.W2_RUNTIME_POLICY,
        AgentName.O1_EXPECTATION_OWNER,
        AgentName.O2_MONITORING_CONFIG,
        AgentName.O3_TRADING_STRATEGY,
        AgentName.O4_MARKET_TRACE,
        AgentName.A1_DOXATLAS_AUDIT,
        AgentName.A2_FACT_CHECK,
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
    }

    definition = registry.get(AgentName.O1_EXPECTATION_OWNER)
    assert definition.runtime.allowed_tools == ["doxa_get_narrative_report"]
    assert DocumentType.EXPECTATION_UNIT.value in definition.runtime.writable_targets
    assert definition.runtime.to_permissions().can_propose_patch is True
    assert definition.model_dump(mode="json")["agent_name"] == "O1"


def test_registry_unknown_agent_raises() -> None:
    registry = default_agent_registry()

    with pytest.raises(UnknownAgentError):
        registry.get("UNKNOWN")  # type: ignore[arg-type]


def test_mock_runner_returns_agent_result_without_blackboard_mutation() -> None:
    service = BlackboardService()
    run = service.start_run(TICKER, AgentName.SYSTEM)
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "run_metadata": base_task.run_metadata.model_copy(update={"run_id": run.run_id}),
        },
        deep=True,
    )
    runner = MockAgentRunner()

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.task_id == task.task_id
    assert result.agent_name is AgentName.O1_EXPECTATION_OWNER
    assert result.proposed_patches == []
    assert result.payload["context_keys"] == ["document_ids"]
    assert service.get_run(run.run_id).belief_state.documents == {}
    assert service.get_run(run.run_id).commit_log == []


def test_maf_adapter_accepts_explicit_runner_contract() -> None:
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(text='{"summary":"bad schema"}')),
        tool_mode="disabled",
    )

    result = MafAgentAdapter(runner=runner).run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.error is None
    assert result.payload["structured"] == {"summary": "bad schema"}


def test_single_shot_model_request_carries_assembled_system_prompt() -> None:
    class RecordingModelClient(MockModelClient):
        def __init__(self) -> None:
            super().__init__(structured={"summary": "ok"})
            self.requests: list[ModelRequest] = []

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            return await super().complete(request)

    client = RecordingModelClient()
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_mode="disabled",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert client.requests
    assert client.requests[0].system_prompt is not None
    assert "Return one JSON object" in client.requests[0].system_prompt


def test_default_real_runner_applies_model_request_timeout_from_settings() -> None:
    runner = default_real_agent_runner(
        settings=DoxAgentSettings(
            dashscope_api_key="test-key",
            model_request_timeout_seconds=42.0,
            react_tool_call_timeout_seconds=7.5,
        )
    )

    assert runner.model_timeout_seconds == 42.0
    assert runner.react_config.model_request_timeout_seconds == 42.0
    assert runner.react_config.tool_call_timeout_seconds == 7.5


def context_task(run_id: str) -> AgentTask:
    task = agent_task()
    return task.model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=[
                    DocumentType.EXPECTATION_UNIT.value,
                    "working_memory",
                ],
                writable_targets=[DocumentType.EXPECTATION_UNIT.value],
                allowed_tools=["doxatlas.query"],
                can_propose_patch=True,
            ),
            "run_metadata": task.run_metadata.model_copy(
                update={"run_id": run_id, "created_at": NOW},
            ),
        },
        deep=True,
    )


def test_context_builder_returns_permission_bounded_snapshot() -> None:
    service = BlackboardService()
    run = service.start_run(TICKER, AgentName.SYSTEM)
    service.submit_patch(
        run.run_id,
        patch(),
        permissions=AgentPermissions(
            writable_targets=[DocumentType.EXPECTATION_UNIT.value],
            can_propose_patch=True,
        ),
        trigger_reason="Seed belief state.",
    )
    memory = service.add_working_memory_entry(
        run.run_id,
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        content_type="agent_note",
        payload={"draft": "working memory only"},
        evidence_refs=[evidence_ref()],
    )
    created_objection = service.create_objection(run.run_id, objection())
    created_delegation = service.create_delegation(run.run_id, delegation())

    snapshot = ContextBuilder(service).build(context_task(run.run_id), run.run_id)

    assert snapshot.run_id == run.run_id
    assert set(snapshot.belief_state_summary) == {DocumentType.EXPECTATION_UNIT.value}
    assert snapshot.working_memory_summary[0].entry_id == memory.entry_id
    assert snapshot.unresolved_objections[0].objection_id == created_objection.objection_id
    assert snapshot.blocking_delegations[0].delegation_id == created_delegation.delegation_id
    assert snapshot.evidence_refs
    dumped = snapshot.model_dump()
    assert "commit_log" not in dumped
    assert "repository" not in dumped
    assert service.get_run(run.run_id).belief_state.documents
    assert service.get_run(run.run_id).working_memory[0] == memory


def test_context_builder_hides_working_memory_without_scope() -> None:
    service = BlackboardService()
    run = service.start_run(TICKER, AgentName.SYSTEM)
    service.add_working_memory_entry(
        run.run_id,
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        content_type="agent_note",
        payload={"draft": "hidden"},
    )
    task = context_task(run.run_id).model_copy(
        update={
            "permissions": context_task(run.run_id).permissions.model_copy(
                update={"readable_context_scopes": [DocumentType.EXPECTATION_UNIT.value]},
            ),
        },
        deep=True,
    )

    snapshot = ContextBuilder(service).build(task, run.run_id)

    assert snapshot.working_memory_summary == []


def test_context_builder_compacts_agent_result_working_memory_payload() -> None:
    service = BlackboardService()
    run = service.start_run(TICKER, AgentName.SYSTEM)
    service.add_working_memory_entry(
        run.run_id,
        author_agent=AgentName.O4_MARKET_TRACE,
        content_type="agent_result",
        payload={
            "status": "succeeded",
            "payload": {
                "runtime": "react",
                "structured": {
                    "text": "x" * 5_000,
                    "summary": "trace summary",
                    "evidence_refs": [],
                    "author_agent": AgentName.O4_MARKET_TRACE.value,
                },
                "text": "y" * 5_000,
                "react_audit": {
                    "schema_version": "react_task_memory.v1",
                    "runtime_guards": {
                        "tool_counts": {"twelvedata.daily_ohlcv": 1}
                    },
                    "event_log": [{"kind": "tool_result", "raw_result_id": "tc1"}],
                    "observation_data": {
                        "raw_tool_results": {
                            "tc1": {"tool_result": {"output": {"blob": "z" * 50_000}}}
                        },
                        "block_index": [],
                    },
                    "memory_state": {},
                    "warnings": [],
                    "context_budget_history": [],
                },
                "model_audits": [{"request": "large"}],
                "context_snapshot": {"large": "w" * 50_000},
                "market_evidence_snapshot": {
                    "daily_ohlcv": [
                        {
                            "kind": "daily_ohlcv_snapshot",
                            "symbol": "NVDA",
                            "bar_count": 60,
                        }
                    ]
                },
            },
            "tool_calls": [],
        },
    )

    snapshot = ContextBuilder(service).build(context_task(run.run_id), run.run_id)

    payload = snapshot.working_memory_summary[0].payload
    inner = payload["payload"]
    assert "react_audit" not in inner
    assert "context_snapshot" not in inner
    assert "model_audits" not in inner
    assert inner["react_audit_summary"]["tool_counts"]["twelvedata.daily_ohlcv"] == 1
    assert inner["structured"]["summary"] == "trace summary"
    assert len(inner["structured"]["text_preview"]) < 1_600
    assert payload["market_evidence_snapshot"]["daily_ohlcv"][0]["symbol"] == "NVDA"
    assert "z" * 100 not in str(payload)


def test_tool_registry_allows_registered_tool_and_converts_to_evidence() -> None:
    registry = default_tool_registry()
    task = agent_task()
    request = ToolRequest(
        tool_name="doxatlas.query",
        ticker=TICKER,
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        input={"query": "AI demand"},
    )

    result = registry.call(request, task.permissions)

    assert result.succeeded
    assert result.output["ticker"] == TICKER
    assert result.evidence_refs
    evidence = result.to_evidence_ref(source_id="manual-source", title="Manual evidence")
    assert evidence.source_id == "manual-source"
    assert evidence.summary == result.output_summary


def test_tool_registry_denies_unauthorized_tool_without_blackboard_write() -> None:
    service = BlackboardService()
    run = service.start_run(TICKER, AgentName.SYSTEM)
    registry = default_tool_registry()
    request = ToolRequest(
        tool_name="market_data.snapshot",
        ticker=TICKER,
        agent_name=AgentName.O1_EXPECTATION_OWNER,
    )

    result = registry.call(request, AgentPermissions(allowed_tools=["doxatlas.query"]))

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "tool_not_allowed"
    assert result.evidence_refs == []
    assert service.get_run(run.run_id).belief_state.documents == {}
