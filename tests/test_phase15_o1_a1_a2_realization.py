from doxagent.agents import AgentRunner, default_agent_registry
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardTarget,
    DelegatedRetrievalRequest,
    DelegatedRetrievalResult,
    DocumentType,
    DoxAtlasAuditResult,
    EvidenceRef,
    EvidenceSourceType,
    ExpectationConstructionResult,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
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
    return AgentResult(
        task_id=task.task_id,
        agent_name=task.agent_name,
        status=direct.status,
        payload={
            "runtime": "maf",
            "structured": {
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
                payload={"structured": {"resolved_objection_ids": [self.objection_id]}},
            )
        return _structured(task, self.factory(task))


def test_a2_registry_is_tavily_only_and_supports_delegated_retrieval() -> None:
    definition = default_agent_registry().get(AgentName.A2_FACT_CHECK)

    assert TaskType.DELEGATED_RETRIEVAL in definition.task_types
    assert set(definition.runtime.allowed_tools) == {"tavily.search", "tavily.extract"}
    assert "Tavily" in definition.runtime.role_instruction
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
    workflow = BlackboardInitializationWorkflow(
        runner=RealizedO1A1A2Runner(a2_has_evidence=True),
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
