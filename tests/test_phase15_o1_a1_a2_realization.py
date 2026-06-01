import pytest

from doxagent.agents import AgentRunner, default_agent_registry
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    DelegatedRetrievalRequest,
    DelegatedRetrievalResult,
    DocumentType,
    DoxAtlasAuditResult,
    EvidenceRef,
    EvidenceSourceType,
    ExpectationConstructionResult,
    ExpectationFieldReviewResult,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    ResearchSection,
    ResultStatus,
    TaskType,
    create_a2_retrieval_delegation,
    new_id,
)
from doxagent.tools import ToolRequest, default_tool_registry
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    InitializationMockResultFactory,
    WorkflowNode,
    WorkflowRunStatus,
)


def _evidence(source_type: EvidenceSourceType = EvidenceSourceType.EXTERNAL_REPORT) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=new_id("evidence"),
        source_type=source_type,
        source_id=f"{source_type.value}:phase15",
        title="Phase 15 evidence",
        summary="Deterministic evidence for O1/A1/A2 realization tests.",
        confidence=0.78,
        citation_scope="phase15",
    )


def _target(ticker: str = "NVDA") -> BlackboardTarget:
    return BlackboardTarget(
        document_type=DocumentType.EXPECTATION_UNIT,
        ticker=ticker,
        expectation_id="exp_mock_core",
        field_path="document",
    )


def _structured(
    task: AgentTask,
    direct: AgentResult,
    payload: dict[str, object] | None = None,
) -> AgentResult:
    if task.required_output_schema == "ResearchSection":
        evidence = _evidence()
        section = ResearchSection(
            text=f"{task.ticker} {task.agent_name.value} research text.",
            summary=f"{task.ticker} {task.agent_name.value} research summary.",
            evidence_refs=[evidence],
            author_agent=task.agent_name,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={"structured": section.model_dump(mode="json")},
            evidence_refs=[evidence],
        )
    return AgentResult(
        task_id=task.task_id,
        agent_name=task.agent_name,
        status=direct.status,
        payload={
            "runtime": "maf",
            "structured": payload
            if task.required_output_schema
            in {"ExpectationConstructionResult", "DoxAtlasAuditResult", "DelegatedRetrievalResult"}
            else {
                "payload": payload or direct.payload,
                "proposed_patches": [
                    patch.model_dump(mode="json") for patch in direct.proposed_patches
                ],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [
                    objection.model_dump(mode="json") for objection in direct.objections
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
            },
        },
    )


class RealizedO1A1A2Runner(AgentRunner):
    def __init__(self, *, a2_has_evidence: bool = True) -> None:
        self.factory = InitializationMockResultFactory(include_blockers=False)
        self.a2_has_evidence = a2_has_evidence
        self.calls: list[tuple[AgentName, TaskType, str | None]] = []
        self.objection_id: str | None = None

    def run(self, task: AgentTask) -> AgentResult:
        self.calls.append((task.agent_name, task.task_type, task.run_metadata.workflow_node))
        node = task.run_metadata.workflow_node
        if node == WorkflowNode.GENERATE_EXPECTATION_UNITS.value:
            direct = self.factory(task)
            return _structured(
                task,
                direct,
                ExpectationConstructionResult(
                    proposed_patches=direct.proposed_patches,
                    evidence_refs=direct.evidence_refs,
                    rationale="O1 built a sourced core expectation.",
                ).model_dump(mode="json"),
            )
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value:
            if task.agent_name is not AgentName.A1_DOXATLAS_AUDIT:
                evidence = _evidence()
                review = ExpectationFieldReviewResult(
                    findings=[
                        {
                            "field_path": "review_scope",
                            "status": "supported",
                            "rationale": f"{task.agent_name.value} review found no blocker.",
                            "evidence_refs": [evidence],
                        }
                    ],
                    evidence_refs=[evidence],
                    rationale=f"{task.agent_name.value} completed expectation-field review.",
                )
                return AgentResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status=ResultStatus.SUCCEEDED,
                    payload={"structured": review.model_dump(mode="json")},
                )
            evidence = _evidence(EvidenceSourceType.DOXATLAS_SOURCE)
            self.objection_id = new_id("objection")
            objection = Objection(
                objection_id=self.objection_id,
                source_agent=AgentName.A1_DOXATLAS_AUDIT,
                target=_target(task.ticker),
                severity=ObjectionSeverity.BLOCKING,
                reason="A1 needs external support for one realized fact.",
                evidence_refs=[evidence],
                status=ObjectionStatus.OPEN,
            )
            delegation = create_a2_retrieval_delegation(
                DelegatedRetrievalRequest(
                    requester_agent=AgentName.A1_DOXATLAS_AUDIT,
                    question="Find external support for the realized fact.",
                    blocking_scope=_target(task.ticker),
                )
            )
            audit = DoxAtlasAuditResult(
                findings=[
                    {
                        "field_path": "realized_facts",
                        "status": "needs_more_evidence",
                        "rationale": "DoxAtlas source support is incomplete.",
                        "evidence_refs": [evidence],
                    }
                ],
                evidence_refs=[evidence],
                objections=[objection],
                delegations=[delegation],
                unknowns=["External confirmation still required."],
                rationale="A1 created a blocking audit item.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": audit.model_dump(mode="json")},
            )
        if node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value:
            if task.agent_name is AgentName.A2_FACT_CHECK:
                evidence_refs = (
                    [_evidence(EvidenceSourceType.EXTERNAL_REPORT)]
                    if self.a2_has_evidence
                    else []
                )
                retrieval = DelegatedRetrievalResult(
                    answer="External source supports the delegated fact."
                    if self.a2_has_evidence
                    else "No sufficient support found.",
                    claim_verdict="supported" if self.a2_has_evidence else "unknown",
                    retrieval_summary="Tavily retrieval completed."
                    if self.a2_has_evidence
                    else "Tavily retrieval found no sufficient source.",
                    evidence_refs=evidence_refs,
                    source_refs=evidence_refs,
                    confidence=0.74 if self.a2_has_evidence else 0.2,
                    unknowns=[] if self.a2_has_evidence else ["No reliable source found."],
                    query_log=["tavily.search: realized fact"],
                    can_complete_delegation=self.a2_has_evidence,
                )
                return AgentResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status=ResultStatus.SUCCEEDED,
                    payload={"structured": retrieval.model_dump(mode="json")},
                )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={
                    "structured": {
                        "proposed_patches": [],
                        "evidence_refs": [],
                        "delegations": [],
                        "unknowns": [],
                        "rationale": "O1 resolved the A1 objection.",
                        "resolved_objection_ids": [self.objection_id],
                    }
                },
            )
        return _structured(task, self.factory(task))


class C1BlockingReviewRunner(RealizedO1A1A2Runner):
    def run(self, task: AgentTask) -> AgentResult:
        if (
            task.run_metadata.workflow_node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
            and task.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH
        ):
            evidence = _evidence()
            objection = Objection(
                objection_id=new_id("objection"),
                source_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
                target=_target(task.ticker),
                severity=ObjectionSeverity.BLOCKING,
                reason="C1 found the realized fact unsupported by fundamentals.",
                evidence_refs=[evidence],
                status=ObjectionStatus.OPEN,
            )
            review = ExpectationFieldReviewResult(
                findings=[
                    {
                        "field_path": "realized_facts",
                        "status": "unsupported",
                        "rationale": "The realized fact lacks company-fundamental support.",
                        "evidence_refs": [evidence],
                    }
                ],
                evidence_refs=[evidence],
                objections=[objection],
                rationale="C1 raised a blocking field objection.",
            )
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": review.model_dump(mode="json")},
            )
        return super().run(task)


class AcceptedObjectionRunner(RealizedO1A1A2Runner):
    def __init__(self, *, include_revision: bool) -> None:
        super().__init__(a2_has_evidence=True)
        self.include_revision = include_revision

    def run(self, task: AgentTask) -> AgentResult:
        if (
            task.run_metadata.workflow_node
            == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
            and task.agent_name is AgentName.O1_EXPECTATION_OWNER
        ):
            evidence = _evidence()
            structured: dict[str, object] = {
                "proposed_patches": [],
                "evidence_refs": [evidence.model_dump(mode="json")],
                "delegations": [],
                "unknowns": [],
                "rationale": "O1 accepted and revised the expectation."
                if self.include_revision
                else "O1 accepted the objection but omitted the required revision.",
                "accepted_objection_ids": [self.objection_id],
            }
            if self.include_revision:
                pending = task.input_context["pending_patches"][0]
                patch = BlackboardPatch.model_validate(pending)
                revised_after = dict(patch.after)
                revised_after["realized_facts_summary"] = "Revised after reviewer objection."
                revised_patch = patch.model_copy(
                    update={
                        "patch_id": new_id("patch"),
                        "after": revised_after,
                        "rationale": "Revise expectation after accepted reviewer objection.",
                        "evidence_refs": [evidence],
                    },
                    deep=True,
                )
                structured["proposed_patches"] = [revised_patch.model_dump(mode="json")]
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": structured},
            )
        return super().run(task)


def test_a2_registry_is_tavily_only_and_supports_delegated_retrieval() -> None:
    definition = default_agent_registry().get(AgentName.A2_FACT_CHECK)

    assert TaskType.DELEGATED_RETRIEVAL in definition.task_types
    assert set(definition.runtime.allowed_tools) == {"tavily.search", "tavily.extract"}
    assert definition.runtime.prompt_block_ids == ["agent.a2"]
    assert "tavily-retrieval-fact-check" in definition.runtime.default_internal_task_skill_ids
    assert definition.runtime.output_schema == "DelegatedRetrievalResult|FactCheckFinding"


def test_a2_delegation_helper_exposes_standard_blocking_request() -> None:
    request = DelegatedRetrievalRequest(
        requester_agent=AgentName.O1_EXPECTATION_OWNER,
        question="Find external confirmation for the event.",
        blocking_scope=_target(),
        purpose="fact_check",
    )

    delegation = create_a2_retrieval_delegation(request)

    assert delegation.target_agent is AgentName.A2_FACT_CHECK
    assert delegation.requester_agent is AgentName.O1_EXPECTATION_OWNER
    assert delegation.required_evidence == [EvidenceSourceType.EXTERNAL_REPORT]
    assert delegation.is_blocking


def test_mock_tool_registry_supports_a2_tavily_and_denies_old_fact_check_permission() -> None:
    registry = default_tool_registry()
    permissions = default_agent_registry().get(AgentName.A2_FACT_CHECK).runtime.to_permissions()

    tavily = registry.call(
        ToolRequest(tool_name="tavily.search", ticker="NVDA", agent_name=AgentName.A2_FACT_CHECK),
        permissions,
    )
    old_fact_check = registry.call(
        ToolRequest(
            tool_name="fact_check.search",
            ticker="NVDA",
            agent_name=AgentName.A2_FACT_CHECK,
        ),
        permissions,
    )

    assert tavily.status is ResultStatus.SUCCEEDED
    assert tavily.evidence_refs[0].source_type is EvidenceSourceType.EXTERNAL_REPORT
    assert old_fact_check.status is ResultStatus.FAILED
    assert old_fact_check.error is not None
    assert old_fact_check.error.code == "tool_not_allowed"


def test_workflow_uses_a2_retrieval_to_complete_delegation_and_o1_resolves_objection() -> None:
    runner = RealizedO1A1A2Runner(a2_has_evidence=True)
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.summary.unresolved_objection_count == 0
    assert result.summary.blocking_delegation_count == 0
    assert all(not objection.is_unresolved for objection in run.objections)
    assert all(not delegation.is_blocking for delegation in run.delegations)
    assert DocumentType.EXPECTATION_UNIT in run.belief_state.documents
    assert any(entry.content_type == "delegated_retrieval_result" for entry in run.working_memory)
    review_agents = [
        agent
        for agent, _, node in runner.calls
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
    ]
    assert review_agents == [
        AgentName.A1_DOXATLAS_AUDIT,
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    ]
    review_content_types = {entry.content_type for entry in run.working_memory}
    assert {
        "a1_doxatlas_audit",
        "c1_fundamental_review",
        "c3_industry_review",
        "o4_market_trace_review",
    }.issubset(review_content_types)


def test_workflow_blocks_when_a2_tavily_retrieval_has_no_sufficient_evidence() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=RealizedO1A1A2Runner(a2_has_evidence=False),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "A2 did not return sufficient Tavily evidence" in result.error
    assert DocumentType.EXPECTATION_UNIT not in run.belief_state.documents
    assert len(run.commit_log) == 1


def test_c1_reviewer_objection_blocks_expectation_promotion() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=C1BlockingReviewRunner(a2_has_evidence=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "left blockers unresolved" in result.error
    assert any(
        objection.source_agent is AgentName.C1_FUNDAMENTAL_RESEARCH
        for objection in run.objections
    )
    assert DocumentType.EXPECTATION_UNIT not in run.belief_state.documents


def test_o1_accepting_objection_requires_revised_expectation_patch() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=AcceptedObjectionRunner(include_revision=False),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "accepted an objection without returning a revised expectation patch" in result.error


def test_o1_revised_patch_replaces_pending_expectation_patch() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=AcceptedObjectionRunner(include_revision=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    expectation_bucket = run.belief_state.documents[DocumentType.EXPECTATION_UNIT]
    document = expectation_bucket["exp_mock_core"]["document"]

    assert result.status is WorkflowRunStatus.COMPLETED
    assert document["realized_facts_summary"] == "Revised after reviewer objection."


def test_a2_delegation_context_uses_react_requirements_not_legacy_tool_requests() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    delegation = create_a2_retrieval_delegation(
        DelegatedRetrievalRequest(
            requester_agent=AgentName.A1_DOXATLAS_AUDIT,
            question="Find evidence for a realized fact.",
            blocking_scope=_target(),
        )
    )

    context = workflow._a2_delegation_context(delegation)

    assert "tool_requests" not in context
    assert context["required_tool_names"] == ["tavily.search"]
    assert context["tool_requirements"][0]["tool_name"] == "tavily.search"


def test_agent_runner_cannot_use_mock_blocker_auto_resolve_backdoor() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=RealizedO1A1A2Runner(a2_has_evidence=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=True,
    )
    result = workflow.run("NVDA", stop_after=WorkflowNode.START_TICKER_INITIALIZATION)

    with pytest.raises(Exception, match="disabled in agent_runner mode"):
        workflow._mock_resolve_blockers(result.checkpoint)
