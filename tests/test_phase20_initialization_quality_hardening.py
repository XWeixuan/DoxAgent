from datetime import UTC, datetime

import pytest

from doxagent.agents import default_agent_registry
from doxagent.agents.runtime.react import _output_contract
from doxagent.blackboard import BlackboardService
from doxagent.context import ContextBuilder
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    DelegatedRetrievalResult,
    DocumentType,
    EvidenceSourceType,
    ExpectationUnitDocument,
    MonitoringConfigDocument,
    MonitoringPolicyDocument,
    MonitoringPolicyRule,
    PatchOperation,
    PolicyActionType,
    ResultStatus,
    RunMetadata,
    TaskType,
    ToolCallSummary,
    ValidationStatus,
)
from doxagent.models.output_schemas import REQUIRED_OUTPUT_SCHEMA_MODELS
from doxagent.tools import ToolRegistry, ToolRequest, ToolResult
from doxagent.workflows import BlackboardInitializationWorkflow
from doxagent.workflows.errors import WorkflowContractError
from doxagent.workflows.output_validation import AgentOutputSchemaValidator
from doxagent.workflows.schema import WorkflowCheckpoint, WorkflowNode, WorkflowRunStatus
from tests.fixtures.phase1_contracts import (
    delegation,
    evidence_ref,
    expectation_document,
    global_research_document,
    known_events_document,
    monitoring_config_document,
    monitoring_policy_document,
    objection,
    research_section,
)
from tests.fixtures.required_output_schemas import golden_required_output_payloads

TEST_NOW = datetime(2026, 6, 12, tzinfo=UTC)


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


class _RecordingResearchSectionRunner:
    def __init__(self) -> None:
        self.tasks: list[AgentTask] = []

    def run(self, task: AgentTask) -> AgentResult:
        self.tasks.append(task)
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "runtime": "test",
                "structured": research_section(task.agent_name).model_dump(mode="json"),
            },
            evidence_refs=[evidence_ref()],
        )


def _monitoring_config_patch(payload: dict[str, object]) -> BlackboardPatch:
    return BlackboardPatch(
        patch_id="patch_monitoring_config",
        target=BlackboardTarget(
            document_type=DocumentType.MONITORING_CONFIG,
            ticker=str(payload.get("ticker") or "NVDA"),
            document_id=str(payload.get("document_id") or "doc_monitoring_config"),
            field_path="document",
        ),
        operation=PatchOperation.CREATE,
        before=None,
        after=payload,
        rationale="test monitoring config apply",
        evidence_refs=[evidence_ref()],
        author_agent=AgentName.O2_MONITORING_CONFIG,
        validation_status=ValidationStatus.PENDING,
    )


def _document_patch(document_type: DocumentType, payload: dict[str, object]) -> BlackboardPatch:
    document_id = str(
        payload.get("document_id")
        or payload.get("expectation_id")
        or f"doc_{document_type.value}"
    )
    return BlackboardPatch(
        patch_id=f"patch_{document_type.value}",
        target=BlackboardTarget(
            document_type=document_type,
            ticker=str(payload.get("ticker") or "NVDA"),
            document_id=document_id,
            expectation_id=str(payload.get("expectation_id"))
            if payload.get("expectation_id")
            else None,
            field_path="document",
        ),
        operation=PatchOperation.CREATE,
        before=None,
        after=payload,
        rationale=f"seed {document_type.value}",
        evidence_refs=[evidence_ref()],
        author_agent=AgentName.SYSTEM,
        validation_status=ValidationStatus.PENDING,
    )


def _seed_document3_context_run(service: BlackboardService) -> str:
    run = service.start_run("NVDA", AgentName.SYSTEM)
    permissions = AgentPermissions(
        writable_targets=[item.value for item in DocumentType],
        can_propose_patch=True,
    )
    documents = [
        (
            DocumentType.GLOBAL_RESEARCH,
            global_research_document().model_dump(mode="json"),
        ),
        (
            DocumentType.EXPECTATION_UNIT,
            ExpectationUnitDocument.model_validate(expectation_document()).model_dump(mode="json"),
        ),
        (
            DocumentType.KNOWN_EVENTS,
            known_events_document().model_dump(mode="json"),
        ),
        (
            DocumentType.MONITORING_CONFIG,
            monitoring_config_document().model_dump(mode="json"),
        ),
        (
            DocumentType.MONITORING_POLICY,
            monitoring_policy_document().model_dump(mode="json"),
        ),
    ]
    for document_type, payload in documents:
        service.submit_patch(
            run.run_id,
            _document_patch(document_type, payload),
            permissions=permissions,
            trigger_reason=f"seed {document_type.value}",
        )
    service.add_working_memory_entry(
        run.run_id,
        author_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
        content_type="large_review_history",
        payload={"text": "history" * 100},
        evidence_refs=[evidence_ref()],
    )
    service.create_objection(run.run_id, objection())
    service.create_delegation(run.run_id, delegation())
    return run.run_id


def _document3_task(
    run_id: str,
    *,
    node: WorkflowNode,
    agent_name: AgentName,
    task_type: TaskType,
    readable_scopes: list[str],
) -> AgentTask:
    return AgentTask(
        task_id="task_document3_context",
        ticker="NVDA",
        agent_name=agent_name,
        task_type=task_type,
        input_context={},
        required_output_schema="ResearchSection",
        permissions=AgentPermissions(
            readable_context_scopes=readable_scopes,
            writable_targets=[],
            can_propose_patch=True,
        ),
        run_metadata=RunMetadata(
            run_id=run_id,
            ticker="NVDA",
            workflow_node=node.value,
            created_at=TEST_NOW,
        ),
    )


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


def test_monitoring_config_output_contract_is_api_shaped() -> None:
    contract = _output_contract("MonitoringConfigDocument")["MonitoringConfigDocument"]
    tool_input = contract["final_payload"]["monitoring_items"][0]["tool_input"]

    assert tool_input["source_id"] == "benzinga_news"
    assert set(tool_input) == {
        "ticker",
        "source_id",
        "enabled",
        "mode",
        "reason",
        "search_terms",
    }
    rules_text = " ".join(contract["rules"])
    assert "finnhub_company_news and stocktwits_messages are ticker-only" in rules_text
    assert "Never put keywords, source_filters, extra, poll_interval_seconds" in rules_text


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
        },
    )

    assert document.direct_trade_rules[0].policy_type == "direct_trade"
    assert document.direct_trade_rules[0].action["side"] == "long"
    assert document.direct_trade_rules[0].action["conviction"] == "medium"
    assert document.direct_trade_rules[0].action["size_bucket"] == "normal"
    assert document.push_to_agent_rules[0].policy_type == "escalate"
    assert document.push_to_agent_rules[0].action["send_to"] == ["O1", "O4"]
    assert document.push_to_agent_rules[0].action["priority"] == "medium"
    assert document.cache_rules == []


def test_monitoring_config_apply_uses_message_bus_tool_and_records_version() -> None:
    tool = _RecordingMonitoringTool()
    registry = ToolRegistry()
    registry.register("monitoring.update_ticker_config", tool)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=_RunnerWithToolRegistry(registry),
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    document = monitoring_config_document()
    patch = _monitoring_config_patch(document.model_dump(mode="json"))

    updated_patch, audit = workflow._apply_monitoring_config_patch(
        WorkflowCheckpoint(run_id=run.run_id, ticker=document.ticker),
        patch,
    )

    assert tool.requests
    request = tool.requests[0]
    assert request.tool_name == "monitoring.update_ticker_config"
    assert request.agent_name is AgentName.O2_MONITORING_CONFIG
    assert request.input["ticker"] == document.ticker
    assert request.input["source_id"]
    assert "keywords" not in request.input
    assert "search_terms" not in request.input
    assert "source_filters" not in request.input
    assert "extra" not in request.input
    assert "poll_interval_seconds" not in request.input
    assert request.metadata["workflow_node"] == "FinalizeInitialization"
    assert updated_patch.operation is PatchOperation.UPDATE
    assert updated_patch.before == patch.after
    assert isinstance(updated_patch.after, dict)
    assert updated_patch.after["applied_config_version"]
    assert audit["applied_item_count"] == 1
    assert audit["status"] == "applied"


def test_monitoring_config_runtime_apply_sanitizes_source_contract_fields() -> None:
    tool = _RecordingMonitoringTool()
    registry = ToolRegistry()
    registry.register("monitoring.update_ticker_config", tool)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=_RunnerWithToolRegistry(registry),
    )
    run = workflow.blackboard.start_run("META", AgentName.SYSTEM)
    payload = {
        "document_id": "doc_monitoring_config",
        "document_type": "monitoring_config",
        "ticker": "META",
        "created_at": TEST_NOW.isoformat(),
        "monitoring_items": [
            {
                "item_id": "monitor_meta_finnhub",
                "tool_input": {
                    "ticker": "META",
                    "source_id": "finnhub_company_news",
                    "keywords": ["Meta AI"],
                    "source_filters": ["press"],
                    "extra": {"expectation_id": "exp_meta_ai"},
                    "reason": "Track company news.",
                    "mode": "merge",
                    "enabled": True,
                },
                "reasoning": "Track company news.",
                "base_keywords": ["Meta AI"],
                "expectation_id": "exp_meta_ai",
                "priority": "high",
                "trigger_condition": "Meta company news changes.",
            },
            {
                "item_id": "monitor_meta_stocktwits",
                "tool_input": {
                    "ticker": "META",
                    "source_id": "stocktwits_messages",
                    "search_terms": ["Meta AI"],
                    "usernames": ["meta"],
                    "extra": {"priority": "medium"},
                    "reason": "Track social chatter.",
                    "mode": "merge",
                    "enabled": True,
                },
                "reasoning": "Track social chatter.",
                "related_entities": ["Meta AI"],
                "priority": "medium",
                "trigger_condition": "Social chatter changes.",
            },
            {
                "item_id": "monitor_meta_benzinga",
                "tool_input": {
                    "ticker": "META",
                    "source_id": "benzinga_news",
                    "search_terms": ["Meta AI", "Reality Labs"],
                    "keywords": ["unsupported keyword"],
                    "extra": {"priority": "medium"},
                    "reason": "Track parameterized news.",
                    "mode": "merge",
                    "enabled": True,
                },
                "reasoning": "Track parameterized news.",
                "priority": "medium",
                "trigger_condition": "News changes.",
            },
        ],
    }

    updated_patch, audit = workflow._apply_monitoring_config_patch(
        WorkflowCheckpoint(run_id=run.run_id, ticker="META"),
        _monitoring_config_patch(payload),
    )

    assert updated_patch is not None
    assert audit["status"] == "applied"
    requests_by_source = {request.input["source_id"]: request.input for request in tool.requests}
    assert requests_by_source["finnhub_company_news"] == {
        "ticker": "META",
        "source_id": "finnhub_company_news",
        "enabled": True,
        "mode": "merge",
        "reason": "Track company news.",
    }
    assert requests_by_source["stocktwits_messages"] == {
        "ticker": "META",
        "source_id": "stocktwits_messages",
        "enabled": True,
        "mode": "merge",
        "reason": "Track social chatter.",
    }
    assert requests_by_source["benzinga_news"]["search_terms"] == [
        "Meta AI",
        "Reality Labs",
    ]
    assert "keywords" not in requests_by_source["benzinga_news"]
    assert "extra" not in requests_by_source["benzinga_news"]
    dropped_by_item = {
        item["item_id"]: item["sanitizer"]["dropped_fields"]
        for item in audit["applied_items"]
    }
    assert "keywords" in dropped_by_item["monitor_meta_finnhub"]
    assert "extra" in dropped_by_item["monitor_meta_finnhub"]
    assert "source_filters" in dropped_by_item["monitor_meta_finnhub"]
    assert "search_terms" in dropped_by_item["monitor_meta_stocktwits"]
    assert "usernames" in dropped_by_item["monitor_meta_stocktwits"]
    assert "keywords" in dropped_by_item["monitor_meta_benzinga"]
    assert "extra" in dropped_by_item["monitor_meta_benzinga"]


def test_finalize_monitoring_config_apply_partial_failure_does_not_block() -> None:
    tool = _RecordingMonitoringTool()
    registry = ToolRegistry()
    registry.register("monitoring.update_ticker_config", tool)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=_RunnerWithToolRegistry(registry),
    )
    run = workflow.blackboard.start_run("META", AgentName.SYSTEM)
    payload = {
        "document_id": "doc_monitoring_config",
        "document_type": "monitoring_config",
        "ticker": "META",
        "created_at": TEST_NOW.isoformat(),
        "monitoring_items": [
            {
                "item_id": "monitor_meta_benzinga",
                "tool_input": {
                    "ticker": "META",
                    "source_id": "benzinga_news",
                    "search_terms": ["Meta AI"],
                    "keywords": ["unsupported keyword"],
                    "extra": {"priority": "high"},
                    "reason": "Track Meta AI news.",
                    "mode": "merge",
                    "enabled": True,
                },
                "reasoning": "Track Meta AI news.",
                "priority": "high",
                "trigger_condition": "Meta AI news changes.",
            },
            {
                "item_id": "monitor_meta_missing_source",
                "tool_input": {
                    "ticker": "META",
                    "keywords": ["Meta AI"],
                    "reason": "Missing source should be skipped.",
                    "mode": "merge",
                    "enabled": True,
                },
                "reasoning": "Missing source should be skipped.",
                "priority": "medium",
                "trigger_condition": "Missing source fixture.",
            },
        ],
    }
    workflow._submit_patch(
        run.run_id,
        _monitoring_config_patch(payload),
        "seed monitoring config",
        permissions=AgentPermissions(
            writable_targets=[DocumentType.MONITORING_CONFIG.value],
            can_propose_patch=True,
        ),
    )
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="META",
        next_node=WorkflowNode.FINALIZE_INITIALIZATION,
        stable_document_types=[DocumentType.MONITORING_CONFIG],
    )

    finalized = workflow._finalize_initialization(
        checkpoint,
        WorkflowNode.FINALIZE_INITIALIZATION,
    )

    assert finalized.status is WorkflowRunStatus.COMPLETED
    assert finalized.next_node is None
    assert len(tool.requests) == 1
    assert tool.requests[0].input["source_id"] == "benzinga_news"
    audit = finalized.metadata["monitoring_config_apply"]
    assert audit["status"] == "partially_applied"
    assert audit["applied_item_count"] == 1
    assert audit["skipped_item_count"] == 1
    current = workflow.blackboard.get_run(run.run_id)
    bucket = current.belief_state.documents[DocumentType.MONITORING_CONFIG]
    stable_payload = next(iter(bucket.values()))["document"]
    stable_document = MonitoringConfigDocument.model_validate(stable_payload)
    assert stable_document.applied_config_version
    system_objections = [
        objection
        for objection in current.objections
        if objection.source_agent is AgentName.SYSTEM
        and objection.taxonomy == "document3_monitoring_runtime_apply"
    ]
    assert len(system_objections) == 1
    reason = system_objections[0].reason
    assert "monitor_meta_missing_source" in reason
    assert "missing source_id" in reason
    assert "monitoring_items" not in reason
    assert len(reason) < 700


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
    }
    assert "source_condition" not in str(context["monitoring_policies"])


def test_document3_context_snapshot_keeps_scoped_belief_docs_without_history() -> None:
    service = BlackboardService()
    run_id = _seed_document3_context_run(service)
    task = _document3_task(
        run_id,
        node=WorkflowNode.GENERATE_MONITORING_POLICY,
        agent_name=AgentName.O4_MARKET_TRACE,
        task_type=TaskType.GENERATE_MONITORING_POLICY,
        readable_scopes=[
            DocumentType.GLOBAL_RESEARCH.value,
            DocumentType.EXPECTATION_UNIT.value,
            DocumentType.KNOWN_EVENTS.value,
            DocumentType.MONITORING_CONFIG.value,
            DocumentType.MONITORING_POLICY.value,
            "working_memory",
            "objections",
            "delegations",
        ],
    )

    snapshot = ContextBuilder(service).build(task, run_id)

    assert set(snapshot.belief_state_summary) == {
        DocumentType.GLOBAL_RESEARCH.value,
        DocumentType.EXPECTATION_UNIT.value,
        DocumentType.KNOWN_EVENTS.value,
        DocumentType.MONITORING_CONFIG.value,
    }
    assert DocumentType.MONITORING_POLICY.value not in snapshot.belief_state_summary
    assert snapshot.belief_state_summary[DocumentType.GLOBAL_RESEARCH.value]
    assert snapshot.belief_state_summary[DocumentType.EXPECTATION_UNIT.value]
    assert snapshot.working_memory_summary == []
    assert snapshot.unresolved_objections == []
    assert snapshot.blocking_delegations == []
    assert snapshot.evidence_refs == []


def test_document3_review_policy_task_uses_scoped_patch_and_config_brief() -> None:
    runner = _RecordingResearchSectionRunner()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner", runner=runner)
    run_id = _seed_document3_context_run(workflow.blackboard)
    policy = monitoring_policy_document()
    patch = BlackboardPatch(
        patch_id="patch_monitoring_policy_pending",
        target=BlackboardTarget(
            document_type=DocumentType.MONITORING_POLICY,
            ticker=policy.ticker,
            document_id=policy.document_id,
            field_path="document",
        ),
        operation=PatchOperation.CREATE,
        before=None,
        after=policy.model_dump(mode="json"),
        rationale="pending policy for review",
        evidence_refs=[evidence_ref()],
        author_agent=AgentName.O4_MARKET_TRACE,
        validation_status=ValidationStatus.PENDING,
    )
    checkpoint = WorkflowCheckpoint(
        run_id=run_id,
        ticker="NVDA",
        stable_document_types=[
            DocumentType.GLOBAL_RESEARCH,
            DocumentType.EXPECTATION_UNIT,
            DocumentType.KNOWN_EVENTS,
            DocumentType.MONITORING_CONFIG,
        ],
        pending_patches=[patch],
    )

    workflow._review_monitoring_policy(checkpoint, WorkflowNode.REVIEW_MONITORING_POLICY)

    assert len(runner.tasks) == 1
    context = runner.tasks[0].input_context
    assert "document3_pending_patch" in context
    assert context["document3_pending_patch"]["patch_id"] == "patch_monitoring_policy_pending"
    assert "review_scope" in context
    assert "review_instruction" in context
    assert "monitoring_config_brief" in context
    brief = context["monitoring_config_brief"]
    assert brief["status"] == "available"
    assert brief["items"][0]["source_id"]
    assert brief["items"][0]["tool_input"]["source_id"]
    for noisy_key in (
        "pending_patches",
        "pending_patch_ids",
        "working_memory_summary",
        "unresolved_objections",
        "blocking_delegations",
        "global_research_context",
        "document1_context_pack",
    ):
        assert noisy_key not in context


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


def test_monitoring_policy_quality_gate_rejects_cache_policy_type() -> None:
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

    with pytest.raises(WorkflowContractError, match="invalid policy_type"):
        workflow._validate_monitoring_policy_quality(policy)


def test_monitoring_policy_quality_gate_rejects_cache_policy_even_with_rationale() -> None:
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

    with pytest.raises(WorkflowContractError, match="invalid policy_type"):
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
