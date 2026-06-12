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
