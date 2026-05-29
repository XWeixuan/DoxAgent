"""Run debug report builder."""

from pydantic import BaseModel, ConfigDict, Field

from doxagent.blackboard import BlackboardRun
from doxagent.models import DocumentType
from doxagent.workflows import WorkflowCheckpoint


class RunDebugReport(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    run_id: str
    ticker: str
    workflow_status: str
    completed_nodes: list[str] = Field(default_factory=list)
    next_node: str | None = None
    belief_document_types: list[DocumentType] = Field(default_factory=list)
    working_memory_count: int = 0
    commit_count: int = 0
    unresolved_objection_count: int = 0
    blocking_delegation_count: int = 0
    residual_risks: list[str] = Field(default_factory=list)
    audit_boundary_note: str = (
        "DoxAgent business audit uses Blackboard Commit Log; LangSmith/model "
        "tracing is observational metadata and is not a Commit Log substitute."
    )


def build_run_debug_report(
    run: BlackboardRun,
    checkpoint: WorkflowCheckpoint | None = None,
) -> RunDebugReport:
    unresolved_count = sum(1 for objection in run.objections if objection.is_unresolved)
    blocking_count = sum(1 for delegation in run.delegations if delegation.is_blocking)
    residual_risks: list[str] = []
    if unresolved_count:
        residual_risks.append("unresolved_objections")
    if blocking_count:
        residual_risks.append("blocking_delegations")
    if checkpoint is not None and checkpoint.status.value in {"blocked", "failed"}:
        residual_risks.append(f"workflow_{checkpoint.status.value}")
    if not residual_risks:
        residual_risks.append("none")
    return RunDebugReport(
        run_id=run.run_id,
        ticker=run.ticker,
        workflow_status=checkpoint.status.value
        if checkpoint is not None
        else run.workflow_state.value,
        completed_nodes=[node.value for node in checkpoint.completed_nodes]
        if checkpoint is not None
        else [],
        next_node=checkpoint.next_node.value
        if checkpoint is not None and checkpoint.next_node is not None
        else None,
        belief_document_types=list(run.belief_state.documents.keys()),
        working_memory_count=len(run.working_memory),
        commit_count=len(run.commit_log),
        unresolved_objection_count=unresolved_count,
        blocking_delegation_count=blocking_count,
        residual_risks=residual_risks,
    )
