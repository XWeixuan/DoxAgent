import time
from importlib import import_module

from doxagent.agents import AgentRunner
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    DocumentType,
    EvidenceRef,
    EvidenceSourceType,
    ExpectationShellConstructionResult,
    GlobalResearchDocument,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    PatchOperation,
    ResearchSection,
    ResultStatus,
    TaskType,
    ToolCallSummary,
    ValidationStatus,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import (
    INITIALIZATION_NODES,
    BlackboardInitializationWorkflow,
    GlobalResearchInputs,
    InitializationMockResultFactory,
    WorkflowCheckpoint,
    WorkflowNode,
    WorkflowRunStatus,
)
from doxagent.workflows.document1 import build_document1_context_pack
from doxagent.workflows.document2 import (
    Document2ResolutionDecisionRecord,
    Document2ResolutionPlan,
)
from doxagent.workflows.document2.resolver import DOCUMENT2_RESOLUTION_PLANS_KEY
from doxagent.workflows.document2.review import DOCUMENT2_REVIEW_FINDINGS_KEY
from doxagent.workflows.document2.transaction import (
    DOCUMENT2_TRANSACTION_AUDITS_KEY,
)
from tests.test_phase13_real_workflow import StructuredInitializationRunner


def test_initialization_public_import_paths_remain_compatible() -> None:
    package = import_module("doxagent.workflows")
    module = import_module("doxagent.workflows.initialization")

    assert package.BlackboardInitializationWorkflow is BlackboardInitializationWorkflow
    assert package.INITIALIZATION_NODES is INITIALIZATION_NODES
    assert module.BlackboardInitializationWorkflow is BlackboardInitializationWorkflow
    assert module.INITIALIZATION_NODES is INITIALIZATION_NODES


def test_initialization_nodes_order_is_characterized() -> None:
    assert INITIALIZATION_NODES == (
        WorkflowNode.START_TICKER_INITIALIZATION,
        WorkflowNode.BUILD_GLOBAL_RESEARCH,
        WorkflowNode.REVIEW_GLOBAL_RESEARCH,
        WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
        WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
        WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
        WorkflowNode.GENERATE_EXPECTATION_DETAILS,
        WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
        WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        WorkflowNode.GENERATE_MONITORING_CONFIG,
        WorkflowNode.REVIEW_MONITORING_CONFIG,
        WorkflowNode.RESOLVE_MONITORING_CONFIG,
        WorkflowNode.GENERATE_MONITORING_POLICY,
        WorkflowNode.REVIEW_MONITORING_POLICY,
        WorkflowNode.RESOLVE_MONITORING_POLICY,
        WorkflowNode.FINALIZE_INITIALIZATION,
    )


def test_generate_expectation_units_alias_forwards_to_construction() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    partial = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_GLOBAL_RESEARCH)
    checkpoint = partial.checkpoint.model_copy(
        update={"next_node": WorkflowNode.GENERATE_EXPECTATION_UNITS},
        deep=True,
    )

    result = workflow._execute_node(
        checkpoint,
        WorkflowNode.GENERATE_EXPECTATION_UNITS,
    )

    assert WorkflowNode.GENERATE_EXPECTATION_UNITS not in result.completed_nodes
    assert result.completed_nodes == partial.checkpoint.completed_nodes + [
        WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION
    ]
    assert result.next_node is WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION
    assert result.metadata["expectation_shells"]


def _construction_objection(ticker: str, expectation_id: str = "exp_mock_core") -> Objection:
    return Objection(
        objection_id="obj_construction_guard",
        source_agent=AgentName.A1_DOXATLAS_AUDIT,
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=ticker,
            expectation_id=expectation_id,
            field_path="market_view",
        ),
        severity=ObjectionSeverity.BLOCKING,
        reason="Construction shell market_view needs better DoxAtlas evidence mapping.",
        status=ObjectionStatus.OPEN,
    )


def test_construction_resolution_transaction_closes_related_objection_with_audit() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=StructuredInitializationRunner(include_blockers=False),
    )
    factory = InitializationMockResultFactory(include_blockers=False)
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    objection = _construction_objection("NVDA")
    workflow.blackboard.create_objection(run.run_id, objection)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        next_node=WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
    )
    shells = factory._expectation_shells("NVDA")
    revised_shells = [
        shells[0].model_copy(
            update={"why_it_matters": "Revised shell cites construction evidence."},
            deep=True,
        ),
        shells[1],
    ]
    revised = ExpectationShellConstructionResult(
        shells=revised_shells,
        evidence_refs=list(revised_shells[0].evidence_refs),
        delegations=[],
        unknowns=[],
        rationale="O1 revised construction shells.",
    )

    audit = workflow._apply_document2_construction_resolution_transaction(
        checkpoint,
        previous_shells=shells,
        revised=revised,
        unresolved_objections=[objection],
    )

    current_run = workflow.blackboard.get_run(run.run_id)
    assert not current_run.objections[0].is_unresolved
    assert current_run.objections[0].resolution_changed_paths == ["expectation_shells"]
    assert audit.transaction_type == "construction_resolution"
    assert audit.status == "accepted"
    audits = [
        entry
        for entry in current_run.working_memory
        if entry.content_type == "document2_construction_transaction_audit"
    ]
    assert audits[-1].payload["status"] == "accepted"


def test_construction_resolution_transaction_rejects_empty_revision() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=StructuredInitializationRunner(include_blockers=False),
    )
    factory = InitializationMockResultFactory(include_blockers=False)
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    objection = _construction_objection("NVDA")
    workflow.blackboard.create_objection(run.run_id, objection)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        next_node=WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
    )
    shells = factory._expectation_shells("NVDA")
    revised = ExpectationShellConstructionResult(
        shells=shells,
        evidence_refs=[],
        delegations=[],
        unknowns=[],
        rationale="O1 attempted to close blockers without revising construction shells.",
    )

    try:
        workflow._apply_document2_construction_resolution_transaction(
            checkpoint,
            previous_shells=shells,
            revised=revised,
            unresolved_objections=[objection],
        )
    except Exception as exc:
        assert "empty revision" in str(exc)
    else:
        raise AssertionError("empty construction revision should not close blockers")

    current_run = workflow.blackboard.get_run(run.run_id)
    assert current_run.objections[0].is_unresolved
    audits = [
        entry
        for entry in current_run.working_memory
        if entry.content_type == "document2_construction_transaction_audit"
    ]
    assert audits[-1].payload["status"] == "rejected"


def test_construction_resolution_transaction_rejects_identity_drift() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=StructuredInitializationRunner(include_blockers=False),
    )
    factory = InitializationMockResultFactory(include_blockers=False)
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    objection = _construction_objection("NVDA")
    workflow.blackboard.create_objection(run.run_id, objection)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        next_node=WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
    )
    shells = factory._expectation_shells("NVDA")
    drifted = [
        shells[0].model_copy(update={"expectation_id": "exp_identity_drift"}, deep=True),
        shells[1],
    ]
    revised = ExpectationShellConstructionResult(
        shells=drifted,
        evidence_refs=[],
        delegations=[],
        unknowns=[],
        rationale="O1 attempted identity drift during construction resolution.",
    )

    try:
        workflow._apply_document2_construction_resolution_transaction(
            checkpoint,
            previous_shells=shells,
            revised=revised,
            unresolved_objections=[objection],
        )
    except Exception as exc:
        assert "expectation_id set" in str(exc)
    else:
        raise AssertionError("identity drift should not close construction blockers")

    current_run = workflow.blackboard.get_run(run.run_id)
    assert current_run.objections[0].is_unresolved


def test_document1_builder_freezes_global_research_document_and_task_contract() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )
    inputs = GlobalResearchInputs(
        sector_or_theme="AI accelerators",
        industry_angle="data-center demand",
        universe=["NVDA", "AMD"],
        peers=["AMD"],
    )

    result = workflow.run(
        "NVDA",
        research_inputs=inputs,
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )

    assert result.status is WorkflowRunStatus.RUNNING
    assert result.checkpoint.next_node is WorkflowNode.REVIEW_GLOBAL_RESEARCH
    assert result.summary.stable_document_types == [DocumentType.GLOBAL_RESEARCH]
    assert result.summary.commit_count == 1
    assert result.checkpoint.metadata["research_inputs"]["timeframe"] == (
        "recent developments with longer-cycle context"
    )
    assert result.checkpoint.metadata["research_inputs"]["market_trace_period"] == "3mo"

    build_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.BUILD_GLOBAL_RESEARCH.value
    ]
    assert {task.agent_name for task in build_tasks} == {
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    }
    assert all(task.permissions.can_raise_objection is False for task in build_tasks)
    assert all(
        task.permissions.writable_targets == [DocumentType.GLOBAL_RESEARCH.value]
        for task in build_tasks
    )
    assert all("prior_sections" not in task.input_context for task in build_tasks)
    for task in build_tasks:
        focus = task.input_context["document1_research_focus"]
        assert "recent" in focus["primary_focus"]
        assert "longer history" in focus["background_use"]
        assert task.ticker == "NVDA"
        assert (
            task.input_context["global_research_inputs"]["sector_or_theme"]
            == "AI accelerators"
        )

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    global_docs = run.belief_state.documents[DocumentType.GLOBAL_RESEARCH]
    document = next(iter(global_docs.values()))["document"]
    assert document["fundamental_report"]["author_agent"] == (
        AgentName.C1_FUNDAMENTAL_RESEARCH.value
    )
    assert document["macro_report"]["author_agent"] == AgentName.C2_MACRO_RESEARCH.value
    assert document["industry_report"]["author_agent"] == AgentName.C3_INDUSTRY_RESEARCH.value
    assert document["market_trace_report"]["author_agent"] == AgentName.O4_MARKET_TRACE.value
    assert document["market_narrative_report"] is None


def test_document1_context_freezes_o1_global_research_context_shape() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run(
        "NVDA",
        stop_after=WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
    )

    assert result.status is WorkflowRunStatus.RUNNING
    o1_tasks = [
        task
        for task in runner.tasks
        if task.agent_name is AgentName.O1_EXPECTATION_OWNER
        and task.run_metadata.workflow_node
        == WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION.value
    ]
    assert o1_tasks
    task = o1_tasks[0]
    document1_context_pack = task.input_context["document1_context_pack"]
    context = task.input_context["global_research_context"]
    assert context["ticker"] == "NVDA"
    assert context["document1_context_pack"] == document1_context_pack
    assert document1_context_pack["window_days"] == 30
    assert document1_context_pack["compaction"]["omitted_full_text"] is True
    assert document1_context_pack["recent_company_facts"]
    assert document1_context_pack["recent_industry_macro_market_drivers"]
    assert document1_context_pack["evidence_refs"]
    assert set(context["sections"]) == {
        "fundamental_report",
        "macro_report",
        "industry_report",
        "market_trace_report",
    }
    assert "market_narrative_report" not in context["sections"]
    fundamental = context["sections"]["fundamental_report"]
    assert set(fundamental) == {
        "summary",
        "author_agent",
        "evidence_count",
        "claim_ids",
        "freshness",
    }
    assert "text" not in fundamental
    assert fundamental["author_agent"] == AgentName.C1_FUNDAMENTAL_RESEARCH.value
    assert fundamental["evidence_count"] == 1
    assert task.input_context["required_tool_names"] == ["doxa_get_narrative_report"]
    assert task.permissions.writable_targets == []


def test_document1_context_pack_keeps_old_background_out_of_fresh_catalysts() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )
    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    document_payload = next(
        iter(run.belief_state.documents[DocumentType.GLOBAL_RESEARCH].values())
    )["document"]
    document = GlobalResearchDocument.model_validate(document_payload)
    old_fact_text = (
        "2023 background supply-chain fact; this is not a fresh catalyst for the "
        "current 30-day window."
    )
    stale_document = document.model_copy(
        update={
            "fundamental_report": document.fundamental_report.model_copy(
                update={"summary": old_fact_text, "text": old_fact_text}
            )
        },
        deep=True,
    )

    pack = build_document1_context_pack(stale_document)

    assert [
        claim.source_section
        for claim in pack.stale_background_facts
        if claim.source_section == "fundamental_report"
    ] == ["fundamental_report"]
    assert not [
        claim
        for claim in pack.catalysts
        if claim.source_section == "fundamental_report"
    ]


def test_document2_detail_and_review_contexts_prefer_document1_context_pack() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS)

    assert result.status is WorkflowRunStatus.RUNNING
    detail_tasks = [
        task
        for task in runner.tasks
        if task.agent_name is AgentName.O1_EXPECTATION_OWNER
        and task.run_metadata.workflow_node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value
    ]
    assert detail_tasks
    for task in detail_tasks:
        assert task.input_context["document1_context_pack"]["ticker"] == "NVDA"
        section = task.input_context["global_research_context"]["sections"][
            "fundamental_report"
        ]
        assert "text" not in section

    review_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
    ]
    assert {task.agent_name for task in review_tasks} == {
        AgentName.A1_DOXATLAS_AUDIT,
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    }
    for task in review_tasks:
        assert task.input_context["document1_context_pack"]["ticker"] == "NVDA"
        assert task.input_context["global_research_context"]["compaction"][
            "omitted_full_text"
        ] is True


def test_generate_expectation_details_exports_candidate_revisions_not_o1_patches() -> None:
    runner = StructuredInitializationRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    assert result.status is WorkflowRunStatus.RUNNING
    detail_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value
    ]
    assert detail_tasks
    assert all(
        task.required_output_schema == "ExpectationDetailCandidateResult"
        for task in detail_tasks
    )
    assert all(task.permissions.writable_targets == [] for task in detail_tasks)
    assert all(task.permissions.can_propose_patch is False for task in detail_tasks)

    revision_entries = result.checkpoint.metadata["document2_pending_revisions"]
    assert len(revision_entries) == 2
    assert result.checkpoint.metadata["document2_detail_state"] == {
        "primary_state": "document2_pending_revisions",
        "revision_count": 2,
        "legacy_pending_patch_count": 2,
    }
    assert [
        entry["revision"]["source"]
        for entry in revision_entries
    ] == ["candidate_generation", "candidate_generation"]
    assert all(entry["legacy_pending_patch_derived"] is True for entry in revision_entries)
    assert {
        entry["candidate"]["document"]["expectation_id"]
        for entry in revision_entries
    } == {"exp_mock_core", "exp_mock_risk"}
    assert [
        patch.patch_id for patch in result.checkpoint.pending_patches
    ] == [entry["legacy_patch_id"] for entry in revision_entries]

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    detail_entries = [
        entry
        for entry in run.working_memory
        if entry.content_type == "expectation_detail_candidate_result"
    ]
    assert len(detail_entries) == 2
    assert all(entry.payload["patch_ids"] == [] for entry in detail_entries)


def test_generate_expectation_details_blocks_candidate_identity_change() -> None:
    class IdentityChangingDetailRunner(StructuredInitializationRunner):
        def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
            result = super()._structured(task, direct)
            if task.required_output_schema != "ExpectationDetailCandidateResult":
                return result
            structured = dict(result.payload["structured"])
            candidate = dict(structured["candidate"])
            candidate["expectation_id"] = "exp_changed"
            structured["candidate"] = candidate
            return result.model_copy(
                update={"payload": result.payload | {"structured": structured}},
                deep=True,
            )

    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=IdentityChangingDetailRunner(include_blockers=False),
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "changed the construction expectation_id" in result.error


def test_review_expectation_fields_records_typed_findings_without_candidate_mutation() -> None:
    class FindingReviewRunner(StructuredInitializationRunner):
        def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
            result = super()._structured(task, direct)
            if (
                task.run_metadata.workflow_node
                != WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
                or task.required_output_schema != "ExpectationFieldReviewResult"
                or task.agent_name is not AgentName.C1_FUNDAMENTAL_RESEARCH
            ):
                return result
            structured = dict(result.payload["structured"])
            structured["findings"] = [
                {
                    "field_path": "market_view.text",
                    "status": "needs_more_evidence",
                    "rationale": "Fundamental evidence should be supplemented before promotion.",
                    "evidence_refs": [],
                }
            ]
            return result.model_copy(
                update={"payload": result.payload | {"structured": structured}},
                deep=True,
            )

    runner = FindingReviewRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS)

    assert result.status is WorkflowRunStatus.RUNNING
    revision_entries = result.checkpoint.metadata["document2_pending_revisions"]
    assert all(
        entry["revision"]["source"] == "candidate_generation"
        for entry in revision_entries
    )
    assert all(entry["revision"]["review_finding_ids"] == [] for entry in revision_entries)
    findings = result.checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY]
    assert {finding["expectation_id"] for finding in findings} == {
        "exp_mock_core",
        "exp_mock_risk",
    }
    assert all(finding["target_path"] == "market_view.text" for finding in findings)
    assert all(
        finding["evidence_assessments"][0]["status"] == "insufficient"
        for finding in findings
    )
    assert result.checkpoint.metadata["document2_review_state"] == {
        "primary_state": DOCUMENT2_REVIEW_FINDINGS_KEY,
        "finding_count": 2,
        "legacy_objection_bridge_count": 0,
    }

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    review_entries = [
        entry
        for entry in run.working_memory
        if entry.content_type
        in {
            "a1_doxatlas_audit",
            "c1_fundamental_review",
            "c3_industry_review",
            "o4_market_trace_review",
        }
    ]
    assert review_entries
    assert all(entry.payload["patch_ids"] == [] for entry in review_entries)


def test_review_expectation_fields_adds_placeholder_typed_findings_without_objection() -> None:
    class PlaceholderDetailRunner(StructuredInitializationRunner):
        def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
            result = super()._structured(task, direct)
            if task.required_output_schema != "ExpectationDetailCandidateResult":
                return result
            structured = dict(result.payload["structured"])
            candidate = dict(structured["candidate"])
            market_view = dict(candidate["market_view"])
            market_view["text"] = "TBD placeholder"
            candidate["market_view"] = market_view
            structured["candidate"] = candidate
            return result.model_copy(
                update={"payload": result.payload | {"structured": structured}},
                deep=True,
            )

    runner = PlaceholderDetailRunner(include_blockers=False)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS)

    assert result.status is WorkflowRunStatus.RUNNING
    findings = result.checkpoint.metadata[DOCUMENT2_REVIEW_FINDINGS_KEY]
    placeholder_findings = [
        finding
        for finding in findings
        if "deterministic_placeholder_detector" in finding["supplemental_context"][0]
    ]
    assert len(placeholder_findings) == 2
    assert {finding["target_path"] for finding in placeholder_findings} == {
        "market_view.text"
    }
    assert all(finding["blocks_promotion"] is True for finding in placeholder_findings)
    assert result.checkpoint.metadata["document2_review_state"][
        "placeholder_finding_count"
    ] == 2
    assert result.summary.unresolved_objection_count == 0


def test_review_expectation_fields_rejects_reviewer_patch_output() -> None:
    class PatchReturningReviewRunner(StructuredInitializationRunner):
        def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
            result = super()._structured(task, direct)
            if (
                task.run_metadata.workflow_node
                != WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
                or task.agent_name is not AgentName.C1_FUNDAMENTAL_RESEARCH
            ):
                return result
            expectation_id = task.input_context["pending_patches"][0]["expectation_id"]
            patch = BlackboardPatch(
                patch_id="patch_review_forbidden",
                target=BlackboardTarget(
                    document_type=DocumentType.EXPECTATION_UNIT,
                    ticker=task.ticker,
                    expectation_id=expectation_id,
                    field_path="document",
                ),
                operation=PatchOperation.UPDATE,
                after={"expectation_id": expectation_id},
                rationale="Reviewer patches are forbidden.",
                author_agent=task.agent_name,
                validation_status=ValidationStatus.PENDING,
            )
            return result.model_copy(update={"proposed_patches": [patch]}, deep=True)

    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=PatchReturningReviewRunner(include_blockers=False),
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.REVIEW_EXPECTATION_FIELDS)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "ReviewExpectationFields reviewers must not propose patches" in result.error


def test_resolve_objections_uses_resolution_plan_and_transaction_audit() -> None:
    runner = StructuredInitializationRunner(include_blockers=True)
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=runner,
    )

    result = workflow.run(
        "NVDA",
        stop_after=WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    )

    assert result.status is WorkflowRunStatus.RUNNING
    resolver_tasks = [
        task
        for task in runner.tasks
        if task.run_metadata.workflow_node
        == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
        and task.agent_name is AgentName.O1_EXPECTATION_OWNER
    ]
    assert resolver_tasks
    assert all(task.required_output_schema == "Document2ResolutionPlan" for task in resolver_tasks)
    assert all(task.permissions.writable_targets == [] for task in resolver_tasks)
    assert all(task.permissions.can_propose_patch is False for task in resolver_tasks)
    assert all(
        task.input_context["internal_task_skill_ids"] == ["document2-resolution-plan"]
        for task in resolver_tasks
    )
    assert all(
        task.input_context["react_runtime_budget"]["max_steps"] == 1
        for task in resolver_tasks
    )
    assert all(
        task.input_context["react_runtime_budget"]["max_tool_call_batches"] == 0
        for task in resolver_tasks
    )
    assert result.summary.unresolved_objection_count == 0
    assert result.summary.blocking_delegation_count == 0

    plans = result.checkpoint.metadata[DOCUMENT2_RESOLUTION_PLANS_KEY]
    audits = result.checkpoint.metadata[DOCUMENT2_TRANSACTION_AUDITS_KEY]
    assert plans
    assert plans[0]["decisions"][0]["decision"] == "resolved"
    assert audits
    assert audits[-1]["transaction_type"] == "resolution"
    assert audits[-1]["status"] == "accepted"

    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    resolution_entries = [
        entry
        for entry in run.working_memory
        if entry.content_type == "objection_resolution_result"
    ]
    assert resolution_entries
    assert all(entry.payload["patch_ids"] == [] for entry in resolution_entries)
    assert [
        entry.content_type
        for entry in run.working_memory
        if entry.content_type == "document2_transaction_audit"
    ]


def test_serial_o1_resolver_timeout_writes_blocking_dispatch_checkpoint() -> None:
    class SlowResolverRunner(AgentRunner):
        def run(self, task: AgentTask) -> AgentResult:
            time.sleep(0.05)
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": {"late": True}},
            )

    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=SlowResolverRunner(),
        settings=DoxAgentSettings(model_request_timeout_seconds=0.01),
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker="NVDA")

    result = workflow._run_agent(
        checkpoint,
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        AgentName.O1_EXPECTATION_OWNER,
        TaskType.REVIEW_EXPECTATION_FIELD,
        "Document2ResolutionPlan",
    )

    assert result.status is ResultStatus.FAILED
    assert result.error is not None
    assert result.error.code == "workflow_agent_timeout"
    assert result.error.retryable is False
    assert result.error.details == {
        "workflow_node": WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value,
        "agent_name": AgentName.O1_EXPECTATION_OWNER.value,
        "task_type": TaskType.REVIEW_EXPECTATION_FIELD.value,
        "required_output_schema": "Document2ResolutionPlan",
        "timeout_seconds": 0.01,
    }
    saved = workflow.checkpoint_repository.get_latest(run.run_id)
    dispatch = saved.metadata["serial_agent_dispatch"]
    assert dispatch["status"] == "failed"
    assert dispatch["workflow_node"] == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
    assert dispatch["agent_name"] == AgentName.O1_EXPECTATION_OWNER.value
    assert dispatch["task_type"] == TaskType.REVIEW_EXPECTATION_FIELD.value
    assert dispatch["required_output_schema"] == "Document2ResolutionPlan"
    assert dispatch["timeout_seconds"] == 0.01
    assert dispatch["error_code"] == "workflow_agent_timeout"


def test_resolution_transaction_retains_numeric_blocker_when_revalidation_fails() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=StructuredInitializationRunner(include_blockers=False),
    )
    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)
    checkpoint = result.checkpoint
    patch = checkpoint.pending_patches[0]
    doxatlas_ref = EvidenceRef(
        evidence_id="evidence_doxatlas_numeric_only",
        source_type=EvidenceSourceType.DOXATLAS_SOURCE,
        source_id="doxatlas:narrative:NVDA",
        title="DoxAtlas narrative",
        summary="Narrative-only source with no market data.",
        confidence=0.8,
        citation_scope="test.numeric_sanity",
    )
    after = dict(patch.after)
    market_view = dict(after["market_view"])
    market_view["text"] = "The stock price rose 12.5% on narrative-only evidence."
    market_view["summary"] = "Stock price +12.5% is unsupported by market data."
    market_view["evidence_refs"] = [doxatlas_ref.model_dump(mode="json")]
    after["market_view"] = market_view
    unsupported_patch = patch.model_copy(
        update={"after": after, "evidence_refs": [doxatlas_ref]},
        deep=True,
    )
    checkpoint.pending_patches = [unsupported_patch, *checkpoint.pending_patches[1:]]
    objection = workflow._numeric_sanity_objections_for_patch("NVDA", unsupported_patch)[0]
    workflow.blackboard.create_objection(checkpoint.run_id, objection)
    plan = Document2ResolutionPlan(
        expectation_id=unsupported_patch.target.expectation_id,
        decision="resolved",
        decisions=[
            Document2ResolutionDecisionRecord(
                objection_id=objection.objection_id,
                decision="resolved",
                resolution_note="O1 claims the existing text is resolved.",
                changed_paths=["document.market_view"],
                evidence_refs=[doxatlas_ref],
            )
        ],
        rationale="No revised candidate was provided.",
    )

    audit = workflow._apply_document2_resolution_transaction(checkpoint, plan)

    run = workflow.blackboard.get_run(checkpoint.run_id)
    current = next(item for item in run.objections if item.objection_id == objection.objection_id)
    assert current.is_unresolved is True
    assert audit.status == "rejected"
    assert audit.output_summary["retained_objection_ids"] == [objection.objection_id]


def test_generate_global_narrative_report_freezes_tool_fragment_recovery_before_document3() -> None:
    class ToolCallNarrativeRunner(StructuredInitializationRunner):
        def _research_section(self, task: AgentTask) -> AgentResult:
            if (
                task.run_metadata.workflow_node
                != WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT.value
            ):
                return super()._research_section(task)
            evidence = EvidenceRef(
                evidence_id="evidence_narrative_characterization",
                source_type=EvidenceSourceType.DOXATLAS_SOURCE,
                source_id="doxatlas:get-narrative-report:NVDA",
                title="DoxAtlas narrative report",
                summary="DoxAtlas narrative report was retrieved.",
                retrieval_metadata={"tool_name": "doxa_get_narrative_report"},
                confidence=0.8,
                citation_scope="doxatlas_narrative_report",
            )
            tool_text = "name: doxa_get_narrative_report\narguments:\nticker: NVDA"
            section = ResearchSection(
                text=tool_text,
                summary=tool_text,
                evidence_refs=[evidence],
                author_agent=task.agent_name,
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": section.model_dump(mode="json")},
                evidence_refs=[evidence],
                tool_calls=[
                    ToolCallSummary(
                        tool_name="doxa_get_narrative_report",
                        status=ResultStatus.SUCCEEDED,
                        input_summary="narrative lookup",
                        output_summary="DoxAtlas narrative report was retrieved.",
                        evidence_refs=[evidence],
                    )
                ],
            )

    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        runner=ToolCallNarrativeRunner(include_blockers=False),
    )

    result = workflow.run(
        "NVDA",
        stop_after=WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT,
    )

    assert result.status is WorkflowRunStatus.RUNNING
    assert result.checkpoint.next_node is WorkflowNode.GENERATE_KNOWN_EVENTS
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    global_doc = next(iter(run.belief_state.documents[DocumentType.GLOBAL_RESEARCH].values()))[
        "document"
    ]
    narrative = global_doc["market_narrative_report"]
    assert "name: doxa_get_narrative_report" not in narrative["text"]
    assert "name: doxa_get_narrative_report" not in narrative["summary"]
    assert "DoxAtlas" in narrative["text"]
    assert narrative["evidence_refs"][0]["source_id"] == "doxatlas:get-narrative-report:NVDA"
    assert [
        commit.patch.target.field_path
        for commit in run.commit_log
        if commit.patch.target.document_type is DocumentType.GLOBAL_RESEARCH
    ] == ["document", "document.market_narrative_report"]
