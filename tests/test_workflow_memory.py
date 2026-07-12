import json
from datetime import UTC, datetime
from typing import Any

import pytest

from doxagent.agents.config import default_agent_registry
from doxagent.agents.runtime.memory import TaskMemoryRuntime
from doxagent.agents.runtime.react import ReActHarnessConfig, _react_user_prompt
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentTask,
    DocumentType,
    RunMetadata,
    TaskType,
)
from doxagent.prompts import PromptAssembler, PromptInjector
from doxagent.skills.registry import default_skill_registry
from doxagent.workflow_memory import (
    INITIALIZATION_WORKFLOW_NODES,
    UnknownWorkflowMemoryPolicy,
    WorkflowMemoryCompiler,
    WorkflowMemoryOverBudget,
    WorkflowMemoryPolicy,
    WorkflowMemoryPolicyRegistry,
    default_workflow_memory_policy_registry,
)
from doxagent.workflows.schema import WorkflowNode
from tests.fixtures.phase1_contracts import (
    expectation_document,
    global_research_document,
    monitoring_config_document,
)

NOW = datetime(2026, 7, 11, tzinfo=UTC)
MODEL_INPUT_KEYS = {
    "react_protocol",
    "task_contract",
    "tool_call_policy",
    "output_contract",
    "available_tools",
    "available_skills",
    "loaded_skills",
    "workflow_memory",
    "task_memory",
}


class StaticDocumentReader:
    def __init__(self, documents: dict[DocumentType, list[dict[str, Any]]]) -> None:
        self.documents = documents
        self.requested: tuple[DocumentType, ...] = ()

    def read(
        self,
        *,
        run_id: str,
        ticker: str,
        document_types: tuple[DocumentType, ...],
    ) -> dict[DocumentType, list[dict[str, Any]]]:
        self.requested = document_types
        return {
            document_type: self.documents.get(document_type, [])
            for document_type in document_types
        }


def _task(
    *,
    node: str,
    task_type: TaskType,
    schema: str,
    agent: AgentName,
    scopes: list[str] | None = None,
    input_context: dict[str, Any] | None = None,
) -> AgentTask:
    return AgentTask(
        task_id="task_workflow_memory",
        ticker="NVDA",
        agent_name=agent,
        task_type=task_type,
        input_context=input_context or {},
        required_output_schema=schema,
        permissions=AgentPermissions(
            readable_context_scopes=scopes or [],
            can_propose_patch=True,
        ),
        run_metadata=RunMetadata(
            run_id="run_workflow_memory",
            ticker="NVDA",
            workflow_node=node,
            created_at=NOW,
        ),
    )


def test_document1_projection_keeps_full_text_and_strips_provenance() -> None:
    raw = global_research_document().model_dump(mode="json")
    raw["fundamental_report"]["text"] = " ".join(["FULL_TEXT_SENTINEL"] * 100)
    reader = StaticDocumentReader({DocumentType.GLOBAL_RESEARCH: [raw]})
    task = _task(
        node=WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION.value,
        task_type=TaskType.GENERATE_EXPECTATION_UNIT,
        schema="ExpectationShellConstructionResult",
        agent=AgentName.O1_EXPECTATION_OWNER,
        scopes=[DocumentType.GLOBAL_RESEARCH.value],
    )

    compiled = WorkflowMemoryCompiler(document_reader=reader).compile(task)
    document = compiled.workflow_memory.documents[DocumentType.GLOBAL_RESEARCH.value][0]

    assert document["fundamental_report"]["text"] == raw["fundamental_report"]["text"]
    assert document["fundamental_report"]["summary"]
    rendered = json.dumps(document, ensure_ascii=False)
    assert "evidence_refs" not in rendered
    assert "author_agent" not in rendered
    assert "reviewer_agents" not in rendered
    audit = compiled.audit.model_dump(mode="json")
    assert "FULL_TEXT_SENTINEL" not in json.dumps(audit)
    assert audit["source_documents"][0]["content_hash"]


def test_policy_and_permissions_are_intersected_before_document_read() -> None:
    reader = StaticDocumentReader(
        {
            DocumentType.GLOBAL_RESEARCH: [
                global_research_document().model_dump(mode="json")
            ],
            DocumentType.EXPECTATION_UNIT: [expectation_document()],
        }
    )
    task = _task(
        node=WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT.value,
        task_type=TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT,
        schema="ResearchSection",
        agent=AgentName.O1_EXPECTATION_OWNER,
        scopes=[DocumentType.GLOBAL_RESEARCH.value],
    )

    compiled = WorkflowMemoryCompiler(document_reader=reader).compile(task)

    assert reader.requested == (DocumentType.GLOBAL_RESEARCH,)
    assert set(compiled.workflow_memory.documents) == {
        DocumentType.GLOBAL_RESEARCH.value
    }
    assert compiled.audit.permission_excluded_document_types == [
        DocumentType.EXPECTATION_UNIT
    ]


def test_unmatched_task_is_default_deny_and_node_coverage_is_fail_fast() -> None:
    task = _task(
        node="ad_hoc_task",
        task_type=TaskType.FACT_CHECK,
        schema="A2Result",
        agent=AgentName.A2_FACT_CHECK,
        scopes=["all"],
    )
    compiled = WorkflowMemoryCompiler().compile(task)

    assert compiled.workflow_memory.model_view() == {}
    assert compiled.audit.policy_id.startswith("default-deny:")

    registry = WorkflowMemoryPolicyRegistry(strict_workflow_nodes={"NodeA", "NodeB"})
    with pytest.raises(UnknownWorkflowMemoryPolicy, match="NodeA, NodeB"):
        registry.validate_node_coverage()


def test_initialization_node_manifest_and_default_policies_are_complete() -> None:
    assert INITIALIZATION_WORKFLOW_NODES == {node.value for node in WorkflowNode}
    default_workflow_memory_policy_registry().validate_node_coverage()


def test_resolver_receives_only_scoped_business_object_without_patch_audit() -> None:
    patch = {
        "patch_id": "patch_secret",
        "operation": "update",
        "target": {
            "document_type": DocumentType.MONITORING_CONFIG.value,
            "ticker": "NVDA",
            "document_id": "monitoring-config-001",
        },
        "after": monitoring_config_document(),
        "rationale": "audit-only rationale",
        "evidence_refs": [{"evidence_id": "evidence_secret"}],
        "validation_status": "pending",
        "author_agent": "O2",
    }
    task = _task(
        node=WorkflowNode.RESOLVE_MONITORING_CONFIG.value,
        task_type=TaskType.RESOLVE_MONITORING_CONFIG,
        schema="MonitoringConfigDocument",
        agent=AgentName.O2_MONITORING_CONFIG,
        input_context={
            "document3_pending_patch": patch,
            "working_memory_summary": [{"entry_id": "wm_secret"}],
            "commit_log": [{"commit_id": "commit_secret"}],
        },
    )

    compiled = WorkflowMemoryCompiler().compile(task)
    rendered = json.dumps(compiled.workflow_memory.model_view(), ensure_ascii=False)

    assert "monitoring_items" in rendered
    for excluded in (
        "patch_secret",
        "evidence_secret",
        "audit-only rationale",
        "wm_secret",
        "commit_secret",
        "validation_status",
        "author_agent",
    ):
        assert excluded not in rendered


def test_over_budget_is_explicit_and_never_silently_truncates() -> None:
    raw = global_research_document().model_dump(mode="json")
    raw["fundamental_report"]["text"] = "x" * 10_000
    policy = WorkflowMemoryPolicy(
        policy_id="test.tiny_budget.v1",
        workflow_node="TinyBudgetNode",
        task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
        required_output_schema="ResearchSection",
        agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
        document_types=(DocumentType.GLOBAL_RESEARCH,),
        max_input_tokens=10,
    )
    registry = WorkflowMemoryPolicyRegistry(
        [policy],
        strict_workflow_nodes={"TinyBudgetNode"},
    )
    task = _task(
        node="TinyBudgetNode",
        task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
        schema="ResearchSection",
        agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
        scopes=[DocumentType.GLOBAL_RESEARCH.value],
    )

    with pytest.raises(WorkflowMemoryOverBudget) as exc_info:
        WorkflowMemoryCompiler(
            policy_registry=registry,
            document_reader=StaticDocumentReader(
                {DocumentType.GLOBAL_RESEARCH: [raw]}
            ),
        ).compile(task)

    assert exc_info.value.document_chars
    assert exc_info.value.estimated_tokens > exc_info.value.max_input_tokens


def test_runtime_workflow_memory_strips_execution_and_agent_audit_records() -> None:
    task = _task(
        node="persistent_runtime_execution",
        task_type=TaskType.RUNTIME_O3_JUDGMENT,
        schema="O3Result",
        agent=AgentName.O3_TRADING_STRATEGY,
        input_context={
            "source_message": {
                "source_message_id": "source_1",
                "body": "Material business update.",
                "metadata": {"tool_audit": {"secret": "tool_secret"}},
            },
            "runtime_context": {
                "known_events": [{"core_fact": "Prior business event."}],
                "runtime_execution_record": {"secret": "execution_secret"},
                "agent_results": [{"payload": "agent_result_secret"}],
                "model_audit": {"prompt": "model_audit_secret"},
                "transaction_audit": {"id": "transaction_secret"},
            },
        },
    )

    compiled = WorkflowMemoryCompiler().compile(task)
    rendered = json.dumps(compiled.workflow_memory.model_view(), ensure_ascii=False)

    assert "Material business update." in rendered
    assert "Prior business event." in rendered
    for excluded in (
        "runtime_execution_record",
        "execution_secret",
        "agent_result_secret",
        "model_audit_secret",
        "transaction_secret",
        "tool_secret",
    ):
        assert excluded not in rendered


def test_react_and_single_shot_share_compiled_contract_and_workflow_memory() -> None:
    task = _task(
        node=WorkflowNode.BUILD_GLOBAL_RESEARCH.value,
        task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
        schema="ResearchSection",
        agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
        input_context={
            "required_section_key": "fundamental_report",
            "section_instruction": "Write the full section.",
        },
    )
    definition = default_agent_registry().get(task.agent_name)
    injected = PromptInjector().inject(task, definition)
    assert injected.prompt_bundle is not None
    compiled = WorkflowMemoryCompiler().compile(injected)
    assembled = PromptAssembler().assemble(
        injected,
        definition,
        injected.prompt_bundle,
        compiled,
        [],
    )
    single_shot = json.loads(assembled.user_prompt)
    react = json.loads(
        _react_user_prompt(
            task=injected,
            definition=definition,
            assembled_prompt=assembled,
            context_snapshot=compiled,
            runtime=TaskMemoryRuntime(injected),
            tool_registry=None,
            skill_registry=default_skill_registry(),
            active_context={
                "working_synthesis": [],
                "fresh_observations": [],
                "recent_trajectory": [],
            },
            config=ReActHarnessConfig(),
        )
    )

    assert set(single_shot) == MODEL_INPUT_KEYS
    assert set(react) == MODEL_INPUT_KEYS
    assert react["task_contract"] == single_shot["task_contract"]
    assert react["workflow_memory"] == single_shot["workflow_memory"]
    rendered = json.dumps(react, ensure_ascii=False)
    for forbidden in (
        "context_snapshot",
        "belief_state_summary",
        "working_memory_summary",
        '"input_context"',
    ):
        assert forbidden not in rendered
