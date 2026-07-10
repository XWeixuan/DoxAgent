from __future__ import annotations

from typing import Any

import pytest

from doxagent.models import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    DocumentType,
    EvidenceSourceType,
    GlobalResearchDocument,
    ResearchSection,
    ResultStatus,
)
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    WorkflowNode,
    WorkflowRunStatus,
)
from doxagent.workflows.document1 import build_document1_context_pack
from tests.test_phase13_real_workflow import StructuredInitializationRunner


class Document1NodeMatrixRunner(StructuredInitializationRunner):
    def __init__(
        self,
        *,
        build_case: str | None = None,
        narrative_case: str | None = None,
    ) -> None:
        super().__init__(include_blockers=False)
        self.build_case = build_case
        self.narrative_case = narrative_case

    def _research_section(self, task: AgentTask) -> AgentResult:
        result = super()._research_section(task)
        node = task.run_metadata.workflow_node
        if node == WorkflowNode.BUILD_GLOBAL_RESEARCH.value:
            return self._mutate_build_global_research(task, result)
        if node == WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT.value:
            return self._mutate_global_narrative(task, result)
        return result

    def _mutate_build_global_research(
        self,
        task: AgentTask,
        result: AgentResult,
    ) -> AgentResult:
        if self.build_case is None:
            return self._with_section(result, self._chinese_section(task))
        if task.agent_name is not AgentName.C1_FUNDAMENTAL_RESEARCH:
            return self._with_section(result, self._chinese_section(task))
        if self.build_case == "tool_fragment_section":
            section = self._section_payload(result)
            tool_text = "name: doxa_get_narrative_report\narguments:\nticker: NVDA"
            section["text"] = tool_text
            section["summary"] = tool_text
            return self._with_section_payload(result, section)
        if self.build_case == "missing_section_evidence":
            section = self._section_payload(result)
            section["evidence_refs"] = []
            return self._with_section_payload(result, section)
        if self.build_case == "missing_all_evidence":
            section = self._section_payload(result)
            section["evidence_refs"] = []
            return self._with_section_payload(
                result.model_copy(update={"evidence_refs": []}, deep=True),
                section,
            )
        if self.build_case == "malformed_section_payload":
            return result.model_copy(
                update={"payload": {"structured": {"summary": "missing text"}}},
                deep=True,
            )
        if self.build_case == "agent_failure":
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.FAILED,
                error=AgentError(
                    code="temporary_document1_failure",
                    message="temporary Document1 fixture failure",
                    retryable=False,
                ),
            )
        if self.build_case == "proposed_patch_leak":
            patch = self.factory._document_patch(
                self.factory._global_research(task.ticker),
                DocumentType.GLOBAL_RESEARCH,
                task.agent_name,
            )
            return result.model_copy(update={"proposed_patches": [patch]}, deep=True)
        return result

    def _mutate_global_narrative(
        self,
        task: AgentTask,
        result: AgentResult,
    ) -> AgentResult:
        if self.narrative_case == "tool_fragment_section":
            section = self._section_payload(result)
            tool_text = "name: doxa_get_narrative_report\narguments:\nticker: NVDA"
            section["text"] = tool_text
            section["summary"] = tool_text
            return self._with_section_payload(result, section)
        return result

    def _chinese_section(self, task: AgentTask) -> ResearchSection:
        evidence = self.factory._evidence(EvidenceSourceType.AGENT_OUTPUT)
        return ResearchSection(
            text=f"{task.ticker} {task.agent_name.value} 近期研究正文，包含可复核证据。",
            summary=f"{task.ticker} {task.agent_name.value} 近期研究摘要。",
            evidence_refs=[evidence],
            author_agent=task.agent_name,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )

    def _section_payload(self, result: AgentResult) -> dict[str, Any]:
        structured = result.payload.get("structured")
        assert isinstance(structured, dict)
        return dict(structured)

    def _with_section(self, result: AgentResult, section: ResearchSection) -> AgentResult:
        return self._with_section_payload(result, section.model_dump(mode="json"))

    def _with_section_payload(
        self,
        result: AgentResult,
        section: dict[str, Any],
    ) -> AgentResult:
        return result.model_copy(
            update={"payload": result.payload | {"structured": section}},
            deep=True,
        )


def _run_document1_matrix(
    *,
    stop_after: WorkflowNode,
    runner: Document1NodeMatrixRunner | None = None,
) -> tuple[BlackboardInitializationWorkflow, Any]:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner or Document1NodeMatrixRunner(),
    )
    return workflow, workflow.run("NVDA", stop_after=stop_after)


def _assert_running_to(result: Any, next_node: WorkflowNode) -> None:
    assert result.status is WorkflowRunStatus.RUNNING
    assert result.error is None
    assert result.checkpoint.next_node is next_node


def _assert_blocked(result: Any, message: str) -> None:
    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert message in result.error


def _latest_global_research_document(
    workflow: BlackboardInitializationWorkflow,
    run_id: str,
) -> dict[str, Any]:
    run = workflow.blackboard.get_run(run_id)
    bucket = run.belief_state.documents[DocumentType.GLOBAL_RESEARCH]
    return next(iter(bucket.values()))["document"]


@pytest.mark.parametrize(
    ("case", "expected_status", "message"),
    [
        pytest.param(
            None,
            WorkflowRunStatus.RUNNING,
            "",
            id="BuildGlobalResearch__canonical_sections__accepted",
        ),
        pytest.param(
            "tool_fragment_section",
            WorkflowRunStatus.RUNNING,
            "",
            id="BuildGlobalResearch__tool_fragment__recovered",
        ),
        pytest.param(
            "missing_section_evidence",
            WorkflowRunStatus.RUNNING,
            "",
            id="BuildGlobalResearch__section_missing_evidence__hydrated_from_result",
        ),
        pytest.param(
            "missing_all_evidence",
            WorkflowRunStatus.RUNNING,
            "",
            id="BuildGlobalResearch__all_section_evidence_missing__agent_output_fallback",
        ),
        pytest.param(
            "malformed_section_payload",
            WorkflowRunStatus.BLOCKED,
            "ResearchSection",
            id="BuildGlobalResearch__malformed_section__schema_failure",
        ),
        pytest.param(
            "agent_failure",
            WorkflowRunStatus.BLOCKED,
            "temporary Document1 fixture failure",
            id="BuildGlobalResearch__agent_failure__blocked",
        ),
        pytest.param(
            "proposed_patch_leak",
            WorkflowRunStatus.BLOCKED,
            "forbids proposed_patches",
            id="BuildGlobalResearch__proposed_patch_leak__blocked",
        ),
    ],
)
def test_build_global_research_node_matrix(
    case: str | None,
    expected_status: WorkflowRunStatus,
    message: str,
) -> None:
    workflow, result = _run_document1_matrix(
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
        runner=Document1NodeMatrixRunner(build_case=case),
    )

    if expected_status is WorkflowRunStatus.RUNNING:
        _assert_running_to(result, WorkflowNode.REVIEW_GLOBAL_RESEARCH)
        document = _latest_global_research_document(workflow, result.checkpoint.run_id)
        assert document["market_narrative_report"] is None
        assert document["fundamental_report"]["evidence_refs"]
        if case == "tool_fragment_section":
            assert "name: doxa_get_narrative_report" not in document["fundamental_report"]["text"]
            assert "name: doxa_get_narrative_report" not in document[
                "fundamental_report"
            ]["summary"]
        if case == "missing_section_evidence":
            assert document["fundamental_report"]["evidence_refs"][0]["source_id"].startswith(
                "test:"
            )
        if case == "missing_all_evidence":
            assert document["fundamental_report"]["evidence_refs"][0]["source_type"] == (
                EvidenceSourceType.AGENT_OUTPUT.value
            )
    else:
        _assert_blocked(result, message)


def test_review_global_research_node_matrix_noop_boundary() -> None:
    workflow, result = _run_document1_matrix(
        stop_after=WorkflowNode.REVIEW_GLOBAL_RESEARCH,
        runner=Document1NodeMatrixRunner(),
    )

    _assert_running_to(result, WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION)
    assert result.summary.commit_count == 1
    document = _latest_global_research_document(workflow, result.checkpoint.run_id)
    assert document["market_narrative_report"] is None


def test_document1_context_pack_matrix_freshness_and_evidence_boundaries() -> None:
    workflow, result = _run_document1_matrix(
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
        runner=Document1NodeMatrixRunner(),
    )
    document = _latest_global_research_document(workflow, result.checkpoint.run_id)
    global_research = GlobalResearchDocument.model_validate(document)
    pack = build_document1_context_pack(global_research)
    assert pack.window_days == 30
    assert pack.recent_company_facts
    assert pack.evidence_refs
    assert pack.compaction["omitted_full_text"] is True

    stale_document = global_research.model_copy(
        update={
            "fundamental_report": global_research.fundamental_report.model_copy(
                update={
                    "summary": (
                        "2024 background supply-chain fact; this is not a fresh catalyst."
                    ),
                    "text": "2024 background supply-chain fact; not a fresh catalyst.",
                },
                deep=True,
            )
        },
        deep=True,
    )
    stale_pack = build_document1_context_pack(stale_document)
    assert [
        claim.source_section
        for claim in stale_pack.stale_background_facts
        if claim.source_section == "fundamental_report"
    ] == ["fundamental_report"]
    assert not [
        claim
        for claim in stale_pack.catalysts
        if claim.source_section == "fundamental_report"
    ]

    missing_evidence_document = stale_document.model_copy(
        update={
            "fundamental_report": stale_document.fundamental_report.model_copy(
                update={"evidence_refs": []},
                deep=True,
            )
        },
        deep=True,
    )
    gap_pack = build_document1_context_pack(missing_evidence_document)
    assert any(
        gap.gap_id == "fundamental_report:missing_evidence"
        for gap in gap_pack.known_gaps
    )
    company_claim = gap_pack.recent_company_facts or gap_pack.stale_background_facts
    assert company_claim[0].evidence_ids == []


@pytest.mark.parametrize(
    ("stop_after", "expected_next"),
    [
        pytest.param(
            WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
            WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
            id="Handoff__construction_receives_document1_context_pack",
        ),
        pytest.param(
            WorkflowNode.GENERATE_EXPECTATION_DETAILS,
            WorkflowNode.REVIEW_EXPECTATION_FIELDS,
            id="Handoff__detail_receives_document1_context_pack",
        ),
        pytest.param(
            WorkflowNode.REVIEW_EXPECTATION_FIELDS,
            WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
            id="Handoff__review_receives_document1_context_pack",
        ),
    ],
)
def test_document1_to_document2_handoff_matrix(
    stop_after: WorkflowNode,
    expected_next: WorkflowNode,
) -> None:
    runner = Document1NodeMatrixRunner()
    _workflow, result = _run_document1_matrix(
        stop_after=stop_after,
        runner=runner,
    )

    _assert_running_to(result, expected_next)
    downstream_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node
        in {
            WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION.value,
            WorkflowNode.GENERATE_EXPECTATION_DETAILS.value,
            WorkflowNode.REVIEW_EXPECTATION_FIELDS.value,
        }
    ]
    assert downstream_tasks
    context_pack_seen = False
    for task in downstream_tasks:
        assert "document1_context_pack" not in task.input_context
        context = task.input_context.get("global_research_context")
        if isinstance(context, dict):
            context_pack = context.get("document1_context_pack")
            if isinstance(context_pack, dict):
                context_pack_seen = True
                assert context_pack["ticker"] == "NVDA"
                assert context_pack["compaction"]["omitted_full_text"] is True
            assert "market_narrative_report" not in context["sections"]
            assert all("text" not in section for section in context["sections"].values())
        brief = task.input_context.get("document1_context_pack_brief")
        if isinstance(brief, dict):
            context_pack_seen = True
            assert brief["ticker"] == "NVDA"
            assert brief["compaction"]["omitted_full_pack"] is True
    assert context_pack_seen


def test_generate_global_narrative_report_node_matrix_tool_fragment_recovered() -> None:
    workflow, result = _run_document1_matrix(
        stop_after=WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT,
        runner=Document1NodeMatrixRunner(narrative_case="tool_fragment_section"),
    )

    _assert_running_to(result, WorkflowNode.GENERATE_KNOWN_EVENTS)
    document = _latest_global_research_document(workflow, result.checkpoint.run_id)
    narrative = document["market_narrative_report"]
    assert narrative is not None
    assert "name: doxa_get_narrative_report" not in narrative["text"]
    assert "name: doxa_get_narrative_report" not in narrative["summary"]
