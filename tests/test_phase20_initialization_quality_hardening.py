import pytest

from doxagent.models import (
    AgentName,
    AgentResult,
    DelegatedRetrievalResult,
    EvidenceSourceType,
    MonitoringPolicyDocument,
    MonitoringPolicyRule,
    PolicyActionType,
    ResultStatus,
    ToolCallSummary,
)
from doxagent.models.output_schemas import REQUIRED_OUTPUT_SCHEMA_MODELS
from doxagent.workflows import BlackboardInitializationWorkflow
from doxagent.workflows.errors import WorkflowContractError
from doxagent.workflows.output_validation import AgentOutputSchemaValidator
from tests.fixtures.phase1_contracts import evidence_ref, monitoring_policy_document
from tests.fixtures.required_output_schemas import golden_required_output_payloads


def test_required_output_schema_registry_accepts_golden_payloads() -> None:
    validator = AgentOutputSchemaValidator()
    payloads = golden_required_output_payloads()

    assert set(payloads) == set(REQUIRED_OUTPUT_SCHEMA_MODELS)
    for schema_name, payload in payloads.items():
        validator.validate({"structured": payload}, schema_name)


def test_monitoring_policy_quality_gate_accepts_full_routing_policy() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")

    workflow._validate_monitoring_policy_quality(monitoring_policy_document())


def test_monitoring_policy_normalizer_uses_chinese_routing_fallbacks() -> None:
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

    assert document.direct_trade_rules[0].action == "标记为 direct_trade 候选，交由人工或 O3 复核"
    assert document.push_to_agent_rules[0].action == "推送给相关研究 agent 复核信号含义"
    assert document.cache_rules[0].strategy_note == (
        "低置信度、重复或时效性较弱的信号先缓存，等待批量复核。"
    )


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
        created_at="2026-06-12T00:00:00Z",
        cache_rules=[
            MonitoringPolicyRule(
                rule_id="rule_cache",
                action_type=PolicyActionType.CACHE,
                trigger_condition="Low-confidence duplicate supplier chatter.",
                expectation_id="exp_ai_demand",
                action="cache for review",
                strategy_note="No immediate action.",
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
        created_at="2026-06-12T00:00:00Z",
        cache_rules=[
            MonitoringPolicyRule(
                rule_id="rule_cache",
                action_type=PolicyActionType.CACHE,
                trigger_condition="Low-confidence duplicate supplier chatter.",
                expectation_id="exp_ai_demand",
                action="cache for review",
                strategy_note="No immediate action.",
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
