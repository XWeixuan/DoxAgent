from datetime import UTC, datetime

import pytest

from doxagent.agents import default_agent_registry
from doxagent.blackboard import BlackboardService
from doxagent.context import ContextBuilder
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentResult,
    BlackboardPatch,
    BlackboardTarget,
    DelegatedRetrievalResult,
    DocumentType,
    EvidenceSourceType,
    MonitoringPolicyDocument,
    MonitoringPolicyRule,
    PatchOperation,
    PolicyActionType,
    ResultStatus,
    ToolCallSummary,
    ValidationStatus,
)
from doxagent.models.output_schemas import REQUIRED_OUTPUT_SCHEMA_MODELS
from doxagent.tools import ToolRegistry, ToolRequest, ToolResult
from doxagent.workflows import BlackboardInitializationWorkflow
from doxagent.workflows.errors import WorkflowContractError
from doxagent.workflows.output_validation import AgentOutputSchemaValidator
from doxagent.workflows.schema import WorkflowCheckpoint
from tests.fixtures.phase1_contracts import (
    evidence_ref,
    known_events_document,
    monitoring_config_document,
    monitoring_policy_document,
)
from tests.fixtures.required_output_schemas import golden_required_output_payloads


class _RecordingMonitoringTool:
    def __init__(self) -> None:
        self.requests: list[ToolRequest] = []

    def call(self, request: ToolRequest) -> ToolResult:
        self.requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={"binding": {"binding_id": "MU:stocktwits_messages"}},
            output_summary="applied monitoring config",
        )


class _RunnerWithToolRegistry:
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry

    def run(self, task: object) -> AgentResult:
        raise AssertionError("This test should not execute an agent task.")


def test_required_output_schema_registry_accepts_golden_payloads() -> None:
    validator = AgentOutputSchemaValidator()
    payloads = golden_required_output_payloads()

    assert set(payloads) == set(REQUIRED_OUTPUT_SCHEMA_MODELS)
    for schema_name, payload in payloads.items():
        validator.validate({"structured": payload}, schema_name)


def test_monitoring_policy_quality_gate_accepts_full_routing_policy() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")

    workflow._validate_monitoring_policy_quality(monitoring_policy_document())


def test_document3_agent_registry_assigns_policy_generation_to_o4() -> None:
    registry = default_agent_registry()

    o2 = registry.get(AgentName.O2_MONITORING_CONFIG)
    o4 = registry.get(AgentName.O4_MARKET_TRACE)
    c1 = registry.get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    c3 = registry.get(AgentName.C3_INDUSTRY_RESEARCH)

    assert "generate_monitoring_policy" not in [task.value for task in o2.task_types]
    assert "review_monitoring_policy" in [task.value for task in o2.task_types]
    assert "resolve_monitoring_config" in [task.value for task in o2.task_types]
    assert "generate_monitoring_policy" in [task.value for task in o4.task_types]
    assert "resolve_monitoring_policy" in [task.value for task in o4.task_types]
    assert "review_monitoring_config" in [task.value for task in c1.task_types]
    assert "review_monitoring_config" in [task.value for task in c3.task_types]
    assert DocumentType.MONITORING_POLICY.value not in o2.runtime.writable_targets
    assert DocumentType.MONITORING_POLICY.value in o4.runtime.writable_targets
    assert "anysearch.search" in o2.runtime.allowed_tools
    assert "tavily.search" in o2.runtime.allowed_tools
    assert "monitoring.update_ticker_config" not in o2.runtime.allowed_tools


def test_monitoring_policy_normalizer_builds_document3_action_payloads() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")

    document = workflow._normalize_monitoring_policy_document(
        "MU",
        {
            "direct_trade_rules": [
                {
                    "rule_id": "rule_direct",
                    "action_type": "direct_trade",
                    "trigger_condition": "HBM share confirmation above 30%",
                    "expectation_id": "expectation_mu_001",
                    "action": "mark as direct-trade candidate for human/O3 review",
                    "strategy_note": "No broker action is triggered.",
                    "evidence_fields": ["source_id", "event_time"],
                    "escalation_path": "human_review",
                }
            ],
            "push_to_agent_rules": [
                {
                    "rule_id": "rule_push",
                    "action_type": "push_to_agent",
                    "trigger_condition": "peer signal divergence",
                    "expectation_id": "expectation_mu_001",
                    "action": "push to C1/C2 for demand signal interpretation",
                    "strategy_note": "Needs agent review.",
                    "evidence_fields": ["source_id", "claim"],
                    "escalation_path": "O1",
                }
            ],
            "cache_rules": [
                {
                    "rule_id": "rule_cache",
                    "action_type": "cache",
                    "trigger_condition": "duplicate low-confidence chatter",
                    "expectation_id": "expectation_mu_001",
                    "action": "cache for batch review",
                    "strategy_note": "No immediate action.",
                    "evidence_fields": ["source_id"],
                    "escalation_path": "batch_review",
                }
            ],
        },
    )

    assert document.direct_trade_rules[0].policy_type == "direct_trade"
    assert document.direct_trade_rules[0].action["side"] == "long"
    assert document.direct_trade_rules[0].action["conviction"] == "medium"
    assert document.direct_trade_rules[0].action["size_bucket"] == "normal"
    assert document.push_to_agent_rules[0].policy_type == "escalate"
    assert document.push_to_agent_rules[0].action["send_to"] == ["O1", "O4"]
    assert document.push_to_agent_rules[0].action["priority"] == "medium"
    assert document.cache_rules[0].policy_type == "cache"
    assert document.cache_rules[0].action["cache_label"] == "background_only"
    assert document.cache_rules[0].strategy_note == (
        "低置信度、重复或时效性较弱的信号先缓存，等待批量复核。"
    )


def test_monitoring_config_apply_uses_message_bus_tool_and_records_version() -> None:
    tool = _RecordingMonitoringTool()
    registry = ToolRegistry()
    registry.register("monitoring.update_ticker_config", tool)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=_RunnerWithToolRegistry(registry),
    )
    document = monitoring_config_document()
    patch = BlackboardPatch(
        patch_id="patch_monitoring_config",
        target=BlackboardTarget(
            document_type=DocumentType.MONITORING_CONFIG,
            ticker=document.ticker,
            document_id=document.document_id,
            field_path="document",
        ),
        operation=PatchOperation.CREATE,
        before=None,
        after=document.model_dump(mode="json"),
        rationale="test monitoring config apply",
        evidence_refs=[evidence_ref()],
        author_agent=AgentName.O2_MONITORING_CONFIG,
        validation_status=ValidationStatus.PENDING,
    )

    updated_patch, audit = workflow._apply_monitoring_config_patch(
        WorkflowCheckpoint(run_id="run_apply", ticker=document.ticker),
        patch,
    )

    assert tool.requests
    request = tool.requests[0]
    assert request.tool_name == "monitoring.update_ticker_config"
    assert request.agent_name is AgentName.O2_MONITORING_CONFIG
    assert request.input["ticker"] == document.ticker
    assert request.input["source_id"]
    assert "poll_interval_seconds" not in request.input
    assert request.metadata["workflow_node"] == "ResolveMonitoringConfig"
    assert updated_patch.operation is PatchOperation.UPDATE
    assert updated_patch.before == patch.after
    assert isinstance(updated_patch.after, dict)
    assert updated_patch.after["applied_config_version"]
    assert audit["applied_item_count"] == 1


def test_document3_runtime_context_exposes_known_events_and_policy_actions() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    permissions = AgentPermissions(
        writable_targets=[
            DocumentType.KNOWN_EVENTS.value,
            DocumentType.MONITORING_POLICY.value,
        ],
        can_propose_patch=True,
    )
    for document in (known_events_document(), monitoring_policy_document()):
        service.submit_patch(
            run.run_id,
            BlackboardPatch(
                patch_id=f"patch_{document.document_type.value}",
                target=BlackboardTarget(
                    document_type=document.document_type,
                    ticker=document.ticker,
                    document_id=document.document_id,
                    field_path="document",
                ),
                operation=PatchOperation.CREATE,
                before=None,
                after=document.model_dump(mode="json"),
                rationale="seed document3 runtime context",
                evidence_refs=[evidence_ref()],
                author_agent=AgentName.SYSTEM,
                validation_status=ValidationStatus.PENDING,
            ),
            permissions=permissions,
            trigger_reason="seed document3 runtime context",
        )

    context = ContextBuilder(service).build_document3_runtime_context(run.run_id)

    assert context["ticker"] == "NVDA"
    assert context["known_events"][0]["core_fact"]
    assert context["known_events"][0]["duplicate_detection_keys"]
    assert {policy["policy_type"] for policy in context["monitoring_policies"]} == {
        "direct_trade",
        "escalate",
        "cache",
    }
    assert "source_condition" not in str(context["monitoring_policies"])


def test_monitoring_config_quality_gate_rejects_resource_budget_overflow() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    document = monitoring_config_document()
    item = document.monitoring_items[0].model_copy(
        update={
            "tool_input": {
                **document.monitoring_items[0].tool_input,
                "keywords": [f"keyword_{index}" for index in range(61)],
            }
        },
        deep=True,
    )
    document = document.model_copy(update={"monitoring_items": [item]}, deep=True)

    with pytest.raises(WorkflowContractError, match="resource budget"):
        workflow._validate_monitoring_config_quality(document)


def test_monitoring_policy_quality_gate_rejects_time_fields() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    policy = monitoring_policy_document()
    direct = policy.direct_trade_rules[0].model_copy(
        update={"trigger": {"condition": "confirmed order", "event_time": "market close"}},
        deep=True,
    )
    policy = policy.model_copy(update={"direct_trade_rules": [direct]}, deep=True)

    with pytest.raises(WorkflowContractError, match="forbidden policy field: event_time"):
        workflow._validate_monitoring_policy_quality(policy)


def test_objection_changed_path_actions_are_localized() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")

    paths = workflow._localized_changed_paths(
        [
            "expectation_unit:exp.realized_facts (removed event_gap)",
            "expectation_unit:exp.key_variables (populated with 4 variables)",
            "expectation_unit:exp.source (replaced evidence_gap source)",
        ]
    )

    assert paths == [
        "expectation_unit:exp.realized_facts （移除 event_gap）",
        "expectation_unit:exp.key_variables （补全 4 个变量）",
        "expectation_unit:exp.source （替换 evidence_gap 溯源）",
    ]


def test_monitoring_policy_quality_gate_rejects_cache_only_without_rationale() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    policy = MonitoringPolicyDocument(
        document_id="doc_policy",
        ticker="NVDA",
        created_at=datetime(2026, 6, 12, tzinfo=UTC),
        cache_rules=[
            MonitoringPolicyRule(
                policy_id="policy_cache",
                rule_id="rule_cache",
                policy_type="cache",
                action_type=PolicyActionType.CACHE,
                scope={"expectation_unit_id": "exp_ai_demand"},
                trigger={"condition": "Low-confidence duplicate supplier chatter."},
                trigger_condition="Low-confidence duplicate supplier chatter.",
                confirmation={"market_confirmation": "duplicate low-confidence signal"},
                expectation_id="exp_ai_demand",
                action={"cache_label": "weak_signal", "handling": "cache for review"},
                risk_guard={"guardrail": "No immediate action."},
                strategy_note="No immediate action.",
                reasoning="Duplicate supplier chatter is too weak for immediate action.",
                evidence_fields=["source_id"],
                escalation_path="batch_review",
            )
        ],
    )

    with pytest.raises(WorkflowContractError, match="omitted action paths"):
        workflow._validate_monitoring_policy_quality(policy)


def test_monitoring_policy_quality_gate_allows_explicit_no_action_rationale() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    policy = MonitoringPolicyDocument(
        document_id="doc_policy",
        ticker="NVDA",
        created_at=datetime(2026, 6, 12, tzinfo=UTC),
        cache_rules=[
            MonitoringPolicyRule(
                policy_id="policy_cache",
                rule_id="rule_cache",
                policy_type="cache",
                action_type=PolicyActionType.CACHE,
                scope={"expectation_unit_id": "exp_ai_demand"},
                trigger={"condition": "Low-confidence duplicate supplier chatter."},
                trigger_condition="Low-confidence duplicate supplier chatter.",
                confirmation={"market_confirmation": "duplicate low-confidence signal"},
                expectation_id="exp_ai_demand",
                action={"cache_label": "weak_signal", "handling": "cache for review"},
                risk_guard={"guardrail": "No immediate action."},
                strategy_note="No immediate action.",
                reasoning="Duplicate supplier chatter is too weak for immediate action.",
                evidence_fields=["source_id"],
                escalation_path="batch_review",
            )
        ],
        no_action_rationale=(
            "Direct-trade and push-to-agent routes are intentionally omitted "
            "because this fixture covers cache-only duplicate handling."
        ),
    )

    workflow._validate_monitoring_policy_quality(policy)


def test_monitoring_policy_quality_gate_rejects_execution_language() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    policy = monitoring_policy_document()
    direct = policy.direct_trade_rules[0].model_copy(
        update={"action": "place order through broker_api"},
        deep=True,
    )
    policy = policy.model_copy(update={"direct_trade_rules": [direct]}, deep=True)

    with pytest.raises(WorkflowContractError, match="broker execution"):
        workflow._validate_monitoring_policy_quality(policy)


def test_a2_completion_gate_rejects_raw_dump_and_inconclusive_results() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    evidence = evidence_ref().model_copy(
        update={"source_type": EvidenceSourceType.EXTERNAL_REPORT},
        deep=True,
    )
    tool_call = ToolCallSummary(
        tool_name="anysearch.search",
        status=ResultStatus.SUCCEEDED,
        input_summary="searched focused query",
        output_summary="found source",
        evidence_refs=[evidence],
    )
    supported = DelegatedRetrievalResult(
        answer="Public source supports the delegated fact.",
        claim_verdict="supported",
        retrieval_summary="A focused query found a relevant public source.",
        evidence_refs=[evidence],
        source_refs=[evidence],
        confidence=0.7,
        query_log=["anysearch.search: delegated fact"],
        tool_calls=[tool_call],
        can_complete_delegation=True,
    )
    result = AgentResult(
        task_id="task_a2",
        agent_name=AgentName.A2_FACT_CHECK,
        status=ResultStatus.SUCCEEDED,
        payload={"structured": supported.model_dump(mode="json")},
        tool_calls=[tool_call],
    )

    assert workflow._validate_a2_retrieval_quality(supported, result)

    inconclusive = supported.model_copy(
        update={"claim_verdict": "inconclusive"},
        deep=True,
    )
    assert not workflow._validate_a2_retrieval_quality(inconclusive, result)

    raw_dump = supported.model_copy(
        update={
            "answer": "Result 1 title: Example url: https://example.com snippet: raw",
        },
        deep=True,
    )
    assert not workflow._validate_a2_retrieval_quality(raw_dump, result)


def test_tool_usage_audit_flags_declared_unexecuted_tool_evidence() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    result = AgentResult(
        task_id="task_tool_audit",
        agent_name=AgentName.A2_FACT_CHECK,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "source_refs": [
                    {
                        **evidence_ref().model_dump(mode="json"),
                        "retrieval_metadata": {"tool_name": "anysearch.search"},
                    }
                ],
                "tool_calls": [
                    {
                        "tool_name": "anysearch.search",
                        "status": "succeeded",
                        "input_summary": "declared only",
                    }
                ],
            },
            "react_audit": {"tool_counts": {}},
        },
    )

    audited = workflow._with_tool_usage_audit(result)

    assert audited.payload["tool_usage_audit"]["status"] == "warning"
    assert audited.payload["tool_usage_audit"]["unexecuted_declared_tool_names"] == [
        "anysearch.search"
    ]


def test_tool_usage_audit_counts_successful_prefetched_tool_calls() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    evidence = evidence_ref().model_dump(mode="json")
    result = AgentResult(
        task_id="task_tool_audit",
        agent_name=AgentName.A2_FACT_CHECK,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "source_refs": [
                    {
                        **evidence,
                        "retrieval_metadata": {"tool_name": "anysearch.search"},
                    }
                ]
            },
            "react_audit": {"tool_counts": {}},
        },
        tool_calls=[
            ToolCallSummary(
                tool_name="anysearch.search",
                status=ResultStatus.SUCCEEDED,
                input_summary="workflow prefetch",
                output_summary="found source",
            )
        ],
    )

    audited = workflow._with_tool_usage_audit(result)

    assert audited.payload["tool_usage_audit"]["status"] == "ok"
    assert audited.payload["tool_usage_audit"]["actual_tool_names"] == ["anysearch.search"]
    assert audited.payload["tool_usage_audit"]["unexecuted_declared_tool_names"] == []
