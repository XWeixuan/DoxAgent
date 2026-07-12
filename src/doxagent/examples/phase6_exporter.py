"""Phase 6 mock ticker export helpers."""

from typing import Any

from doxagent.blackboard import BlackboardRun
from doxagent.models import CommitLogEntry, Delegation, Objection, WorkingMemoryEntry
from doxagent.workflows import WorkflowExecutionResult

DOCUMENT_ORDER = [
    "global_research",
    "expectation_unit",
    "known_events",
    "monitoring_config",
    "monitoring_policy",
]


def export_phase6_run(
    *,
    workflow_result: WorkflowExecutionResult,
    blackboard_run: BlackboardRun,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    """Build a stable review JSON object from a completed mock workflow run."""
    return {
        "sample": {
            "name": "phase6_mock_ticker",
            "mock": True,
            "warning": (
                "Fixture-only review artifact. It is not investment research, "
                "trading advice, or a live external-service result."
            ),
        },
        "fixture": fixture,
        "workflow": {
            "status": workflow_result.status.value,
            "run_id": workflow_result.checkpoint.run_id,
            "ticker": workflow_result.checkpoint.ticker,
            "completed_nodes": [node.value for node in workflow_result.checkpoint.completed_nodes],
            "next_node": workflow_result.checkpoint.next_node.value
            if workflow_result.checkpoint.next_node
            else None,
            "stable_document_types": [
                item.value for item in workflow_result.summary.stable_document_types
            ],
            "summary": workflow_result.summary.model_dump(mode="json"),
        },
        "documents": _ordered_documents(blackboard_run),
        "working_memory": [
            _working_memory_summary(entry) for entry in blackboard_run.working_memory
        ],
        "commit_log": [_commit_summary(entry) for entry in blackboard_run.commit_log],
        "objections": [_objection_summary(item) for item in blackboard_run.objections],
        "delegations": [_delegation_summary(item) for item in blackboard_run.delegations],
        "residual_risks": _residual_risks(blackboard_run),
    }


def compact_summary(exported: dict[str, Any]) -> dict[str, Any]:
    """Return a small human-readable summary for stdout."""
    workflow = exported["workflow"]
    return {
        "sample": exported["sample"]["name"],
        "ticker": workflow["ticker"],
        "status": workflow["status"],
        "document_types": list(exported["documents"].keys()),
        "commit_count": len(exported["commit_log"]),
        "objection_count": len(exported["objections"]),
        "delegation_count": len(exported["delegations"]),
        "residual_risks": exported["residual_risks"],
    }


def _ordered_documents(blackboard_run: BlackboardRun) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for document_type in DOCUMENT_ORDER:
        for key, value in blackboard_run.belief_state.documents.items():
            if key.value == document_type:
                documents[document_type] = value
                break
    return documents


def _working_memory_summary(entry: WorkingMemoryEntry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "author_agent": entry.author_agent.value,
        "content_type": entry.content_type,
        "payload": entry.payload,
        "created_at": entry.created_at.isoformat(),
    }


def _commit_summary(entry: CommitLogEntry) -> dict[str, Any]:
    return {
        "commit_id": entry.commit_id,
        "author_agent": entry.patch.author_agent.value,
        "triggered_by": entry.triggered_by.value,
        "trigger_reason": entry.trigger_reason,
        "document_type": entry.patch.target.document_type.value,
        "field_path": entry.patch.target.field_path,
        "document_id": entry.patch.target.document_id,
        "expectation_id": entry.patch.target.expectation_id,
        "patch_id": entry.patch.patch_id,
        "before": entry.patch.before,
        "after": entry.patch.after,
        "resolved_objection_ids": list(entry.resolved_objection_ids),
        "residual_disputes": list(entry.residual_disputes),
        "created_at": entry.created_at.isoformat(),
    }


def _objection_summary(item: Objection) -> dict[str, Any]:
    return {
        "objection_id": item.objection_id,
        "source_agent": item.source_agent.value,
        "status": item.status.value,
        "severity": item.severity.value,
        "target": item.target.model_dump(mode="json"),
        "reason": item.reason,
        "resolution_note": item.resolution_note,
    }


def _delegation_summary(item: Delegation) -> dict[str, Any]:
    return {
        "delegation_id": item.delegation_id,
        "requester_agent": item.requester_agent.value,
        "target_agent": item.target_agent.value,
        "status": item.status.value,
        "question": item.question,
        "blocking_scope": item.blocking_scope.model_dump(mode="json"),
        "result_summary": item.result_summary,
    }


def _residual_risks(blackboard_run: BlackboardRun) -> list[str]:
    risks: list[str] = []
    if any(objection.is_unresolved for objection in blackboard_run.objections):
        risks.append("unresolved_objections")
    if any(delegation.is_blocking for delegation in blackboard_run.delegations):
        risks.append("blocking_delegations")
    if not risks:
        risks.append("mock_fixture_only_no_real_external_services")
    return risks
