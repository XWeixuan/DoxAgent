"""Run Document 2 expectation-unit smoke from an existing Document 1 run.

This script intentionally resumes the normal BlackboardInitializationWorkflow
instead of calling O1 or expectation helpers directly. The source run must be a
Postgres-visible run with stable Global Research and no started Document 2 state.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doxagent.blackboard import BlackboardService
from doxagent.blackboard.state import BlackboardRun
from doxagent.debug_viewer.query import DebugRunQueryService
from doxagent.models import (
    CommitLogEntry,
    Delegation,
    DocumentType,
    Objection,
    WorkingMemoryEntry,
    new_id,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    WorkflowCheckpoint,
    WorkflowCheckpointRepository,
    WorkflowNode,
    WorkflowRunStatus,
)

JsonDict = dict[str, Any]

_DOCUMENT1_ONLY_COMPLETED_PREFIXES = {
    (
        WorkflowNode.START_TICKER_INITIALIZATION,
        WorkflowNode.BUILD_GLOBAL_RESEARCH,
    ),
    (
        WorkflowNode.START_TICKER_INITIALIZATION,
        WorkflowNode.BUILD_GLOBAL_RESEARCH,
        WorkflowNode.REVIEW_GLOBAL_RESEARCH,
    ),
}

_STOP_AFTER_ALIASES: dict[str, WorkflowNode] = {
    "GenerateExpectationDetails": WorkflowNode.GENERATE_EXPECTATION_DETAILS,
    "generate_expectation_details": WorkflowNode.GENERATE_EXPECTATION_DETAILS,
    "details": WorkflowNode.GENERATE_EXPECTATION_DETAILS,
    "ReviewExpectationFields": WorkflowNode.REVIEW_EXPECTATION_FIELDS,
    "review_expectation_fields": WorkflowNode.REVIEW_EXPECTATION_FIELDS,
    "review": WorkflowNode.REVIEW_EXPECTATION_FIELDS,
    "ResolveObjectionsAndDelegations": WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    "resolve_objections": WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    "resolve": WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    "PromoteExpectationToBeliefState": WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    "promote_expectation": WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    "promote": WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
}


@dataclass(frozen=True)
class Document2Seed:
    source_run_id: str
    execution_run_id: str
    mode: str
    checkpoint: WorkflowCheckpoint


def main() -> int:
    args = _parse_args()
    stop_after = _resolve_stop_after(args.stop_after)
    settings = _persistent_real_smoke_settings()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner", settings=settings)

    source_run = workflow.blackboard.get_run(args.source_run_id)
    source_checkpoint = workflow.checkpoint_repository.get_latest(args.source_run_id)
    validate_document1_source_state(source_run, source_checkpoint)

    if args.mode == "clone":
        seed = clone_document1_state(
            workflow.blackboard,
            workflow.checkpoint_repository,
            source_run,
            source_checkpoint,
        )
    else:
        seed = Document2Seed(
            source_run_id=source_run.run_id,
            execution_run_id=source_run.run_id,
            mode="in-place",
            checkpoint=source_checkpoint,
        )

    seed_checkpoint = _checkpoint_with_document2_smoke_metadata(
        seed.checkpoint,
        mode=seed.mode,
        source_run_id=seed.source_run_id,
        stop_after=stop_after,
    )
    workflow.checkpoint_repository.save_checkpoint(seed_checkpoint)
    seed = Document2Seed(
        source_run_id=seed.source_run_id,
        execution_run_id=seed.execution_run_id,
        mode=seed.mode,
        checkpoint=seed_checkpoint,
    )

    print(
        json.dumps(
            {
                "event": "document2_smoke_started",
                "mode": seed.mode,
                "source_run_id": seed.source_run_id,
                "execution_run_id": seed.execution_run_id,
                "stop_after": stop_after.value,
                "next_node": seed.checkpoint.next_node.value
                if seed.checkpoint.next_node is not None
                else None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    result = workflow.resume(seed.checkpoint, stop_after=stop_after)
    run_summary = DebugRunQueryService(settings).run_summary(result.checkpoint.run_id)
    expectation_unit_count = _summary_document_count(run_summary, "expectation_unit")
    if args.export_brief_state:
        from eval.export_brief_state import export_brief_state

        export_path = export_brief_state(result.checkpoint.run_id)
    else:
        export_path = None

    output = {
        "event": "document2_smoke_finished",
        "mode": seed.mode,
        "source_run_id": seed.source_run_id,
        "execution_run_id": result.checkpoint.run_id,
        "status": result.status.value,
        "next_node": result.checkpoint.next_node.value
        if result.checkpoint.next_node is not None
        else None,
        "completed_nodes": [node.value for node in result.checkpoint.completed_nodes],
        "stable_document_types": [item.value for item in result.summary.stable_document_types],
        "pending_patch_count": len(result.checkpoint.pending_patches),
        "working_memory_count": result.summary.working_memory_count,
        "commit_count": result.summary.commit_count,
        "unresolved_objection_count": result.summary.unresolved_objection_count,
        "blocking_delegation_count": result.summary.blocking_delegation_count,
        "global_research_status": _summary_document_status(run_summary, "global_research"),
        "expectation_unit_count": expectation_unit_count,
        "brief_state_export": str(export_path) if export_path is not None else None,
        "error": result.error,
    }
    print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0 if _is_successful_smoke_result(result.checkpoint, stop_after, result.error) else 1


def clone_document1_state(
    blackboard: BlackboardService,
    checkpoint_repository: WorkflowCheckpointRepository,
    source_run: BlackboardRun,
    source_checkpoint: WorkflowCheckpoint,
) -> Document2Seed:
    """Clone a Document 1-only run and persist a matching resume checkpoint."""

    validate_document1_source_state(source_run, source_checkpoint)

    now = datetime.now(UTC)
    run_id = new_id("run")
    id_mapping = _clone_id_mapping(source_run)
    id_mapping[source_run.run_id] = run_id

    cloned_belief = source_run.belief_state.model_copy(
        update={
            "snapshot_id": new_id("belief"),
            "commit_ids": [
                str(_rewrite_json_ids(commit_id, id_mapping))
                for commit_id in source_run.belief_state.commit_ids
            ],
            "documents": deepcopy(source_run.belief_state.documents),
            "created_at": now,
        },
        deep=True,
    )
    cloned_run = source_run.model_copy(
        update={
            "run_id": run_id,
            "created_at": now,
            "belief_state": cloned_belief,
            "working_memory": [
                _clone_working_memory_entry(entry, id_mapping)
                for entry in source_run.working_memory
            ],
            "commit_log": [
                _clone_commit_log_entry(entry, id_mapping) for entry in source_run.commit_log
            ],
            "objections": [
                _clone_objection(item, id_mapping) for item in source_run.objections
            ],
            "delegations": [
                _clone_delegation(item, id_mapping) for item in source_run.delegations
            ],
        },
        deep=True,
    )
    blackboard.repository.add(cloned_run)

    cloned_metadata = _scrub_document2_clone_metadata(
        _rewrite_json_ids(source_checkpoint.metadata, id_mapping)
    ) | {
        "document2_smoke_source_run_id": source_run.run_id,
        "document2_smoke_cloned_at": now.isoformat(),
    }
    cloned_summary = (
        source_checkpoint.summary.model_copy(update={"run_id": run_id}, deep=True)
        if source_checkpoint.summary is not None
        else None
    )
    cloned_checkpoint = source_checkpoint.model_copy(
        update={
            "run_id": run_id,
            "metadata": cloned_metadata,
            "summary": cloned_summary,
        },
        deep=True,
    )
    checkpoint_repository.save_checkpoint(cloned_checkpoint)
    return Document2Seed(
        source_run_id=source_run.run_id,
        execution_run_id=run_id,
        mode="clone",
        checkpoint=cloned_checkpoint,
    )


def _checkpoint_with_document2_smoke_metadata(
    checkpoint: WorkflowCheckpoint,
    *,
    mode: str,
    source_run_id: str,
    stop_after: WorkflowNode,
) -> WorkflowCheckpoint:
    metadata = _scrub_document2_clone_metadata(dict(checkpoint.metadata)) | {
        "document2_smoke_mode": mode,
        "document2_smoke_source_run_id": source_run_id,
        "document2_smoke_stop_after": stop_after.value,
        "document2_smoke_target_node": stop_after.value,
    }
    return checkpoint.model_copy(update={"metadata": metadata}, deep=True)


def _scrub_document2_clone_metadata(metadata: JsonDict) -> JsonDict:
    cloned = dict(metadata)
    for key in (
        "last_error_code",
        "last_error_message",
        "last_error_boundary",
        "last_error_node",
    ):
        cloned.pop(key, None)
    return cloned


def validate_document1_source_state(
    source_run: BlackboardRun,
    source_checkpoint: WorkflowCheckpoint,
) -> None:
    if source_run.run_id != source_checkpoint.run_id:
        raise ValueError(
            f"Source run/checkpoint mismatch: {source_run.run_id} != "
            f"{source_checkpoint.run_id}."
        )
    if source_run.ticker != source_checkpoint.ticker:
        raise ValueError(
            f"Source ticker/checkpoint mismatch: {source_run.ticker} != "
            f"{source_checkpoint.ticker}."
        )
    if source_checkpoint.status is not WorkflowRunStatus.RUNNING:
        raise ValueError(
            "Source run must be a paused/running intermediate smoke checkpoint, "
            f"got {source_checkpoint.status.value}."
        )
    completed = tuple(source_checkpoint.completed_nodes)
    if completed not in _DOCUMENT1_ONLY_COMPLETED_PREFIXES:
        raise ValueError(
            "Source run must be Document 1-only and stop immediately after Document 1 "
            "setup: completed_nodes must be StartTickerInitialization+BuildGlobalResearch, "
            f"optionally plus ReviewGlobalResearch; got {[node.value for node in completed]}."
        )
    expected_next = (
        WorkflowNode.REVIEW_GLOBAL_RESEARCH
        if completed[-1] is WorkflowNode.BUILD_GLOBAL_RESEARCH
        else WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION
    )
    if source_checkpoint.next_node is not expected_next:
        raise ValueError(
            f"Source run next_node must be {expected_next.value}, got "
            f"{source_checkpoint.next_node.value if source_checkpoint.next_node else None}."
        )
    if DocumentType.GLOBAL_RESEARCH not in source_checkpoint.stable_document_types:
        raise ValueError("Source checkpoint has not marked global_research stable.")
    global_research_docs = source_run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
    if not global_research_docs:
        raise ValueError("Source Blackboard run has no stable global_research document.")
    if source_run.belief_state.documents.get(DocumentType.EXPECTATION_UNIT):
        raise ValueError("Source run already has stable expectation_unit documents.")
    if source_checkpoint.pending_patches:
        raise ValueError("Source checkpoint already has pending patches; not Document 1-only.")
    if any(objection.is_unresolved for objection in source_run.objections):
        raise ValueError("Source run has unresolved objections before Document 2 start.")
    if any(delegation.is_blocking for delegation in source_run.delegations):
        raise ValueError("Source run has blocking delegations before Document 2 start.")


def _clone_id_mapping(source_run: BlackboardRun) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in source_run.working_memory:
        mapping[entry.entry_id] = new_id("wm")
    for commit in source_run.commit_log:
        mapping[commit.commit_id] = new_id("commit")
        mapping[commit.patch.patch_id] = new_id("patch")
    for objection in source_run.objections:
        mapping[objection.objection_id] = new_id("objection")
    for delegation in source_run.delegations:
        mapping[delegation.delegation_id] = new_id("delegation")
    return mapping


def _clone_working_memory_entry(
    entry: WorkingMemoryEntry,
    id_mapping: dict[str, str],
) -> WorkingMemoryEntry:
    return entry.model_copy(
        update={
            "entry_id": str(_rewrite_json_ids(entry.entry_id, id_mapping)),
            "payload": _rewrite_json_ids(entry.payload, id_mapping),
        },
        deep=True,
    )


def _clone_commit_log_entry(
    entry: CommitLogEntry,
    id_mapping: dict[str, str],
) -> CommitLogEntry:
    patch = entry.patch.model_copy(
        update={
            "patch_id": str(_rewrite_json_ids(entry.patch.patch_id, id_mapping)),
            "before": _rewrite_json_ids(entry.patch.before, id_mapping),
            "after": _rewrite_json_ids(entry.patch.after, id_mapping),
        },
        deep=True,
    )
    return entry.model_copy(
        update={
            "commit_id": str(_rewrite_json_ids(entry.commit_id, id_mapping)),
            "patch": patch,
            "resolved_objection_ids": [
                str(_rewrite_json_ids(item, id_mapping))
                for item in entry.resolved_objection_ids
            ],
            "residual_disputes": [
                str(_rewrite_json_ids(item, id_mapping)) for item in entry.residual_disputes
            ],
        },
        deep=True,
    )


def _clone_objection(item: Objection, id_mapping: dict[str, str]) -> Objection:
    return item.model_copy(
        update={
            "objection_id": str(_rewrite_json_ids(item.objection_id, id_mapping)),
            "merged_objection_ids": [
                str(_rewrite_json_ids(merged, id_mapping))
                for merged in item.merged_objection_ids
            ],
        },
        deep=True,
    )


def _clone_delegation(item: Delegation, id_mapping: dict[str, str]) -> Delegation:
    return item.model_copy(
        update={
            "delegation_id": str(_rewrite_json_ids(item.delegation_id, id_mapping)),
        },
        deep=True,
    )


def _rewrite_json_ids(value: Any, id_mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return id_mapping.get(value, value)
    if isinstance(value, list):
        return [_rewrite_json_ids(item, id_mapping) for item in value]
    if isinstance(value, tuple):
        return tuple(_rewrite_json_ids(item, id_mapping) for item in value)
    if isinstance(value, dict):
        return {
            key: _rewrite_json_ids(item, id_mapping)
            for key, item in value.items()
        }
    return value


def _persistent_real_smoke_settings() -> DoxAgentSettings:
    if os.getenv("DOXAGENT_RUN_REAL_API_TESTS") != "1":
        raise RuntimeError(
            "Set DOXAGENT_RUN_REAL_API_TESTS=1 to consume real API and model quota."
        )
    settings = DoxAgentSettings()
    if settings.storage_mode != "postgres":
        raise RuntimeError(
            "Set DOXAGENT_STORAGE_MODE=postgres so Document 2 smoke runs persist to DB."
        )
    if not settings.database_url:
        raise RuntimeError("Set DOXAGENT_DATABASE_URL so smoke runs persist to DB.")
    return settings


def _resolve_stop_after(value: str) -> WorkflowNode:
    try:
        return _STOP_AFTER_ALIASES[value]
    except KeyError as exc:
        allowed = ", ".join(sorted(_STOP_AFTER_ALIASES))
        raise ValueError(f"Unknown --stop-after {value!r}; allowed: {allowed}") from exc


def _is_successful_smoke_result(
    checkpoint: WorkflowCheckpoint,
    stop_after: WorkflowNode,
    error: str | None,
) -> bool:
    if error is not None:
        return False
    if stop_after not in checkpoint.completed_nodes:
        return False
    if stop_after is WorkflowNode.GENERATE_EXPECTATION_DETAILS:
        return bool(checkpoint.pending_patches)
    return True


def _safe_nested(data: JsonDict, first: str, second: str) -> Any:
    raw = data.get(first)
    if not isinstance(raw, dict):
        return None
    return raw.get(second)


def _summary_document_count(summary: JsonDict, document_type: str) -> int:
    counts = _safe_nested(summary, "belief_state", "document_counts")
    if not isinstance(counts, dict):
        return 0
    try:
        return int(counts.get(document_type) or 0)
    except (TypeError, ValueError):
        return 0


def _summary_document_status(summary: JsonDict, document_type: str) -> str:
    return "present" if _summary_document_count(summary, document_type) > 0 else "missing"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_run_id", help="Existing Document 1-only run_id.")
    parser.add_argument(
        "--mode",
        choices=["clone", "in-place"],
        default="clone",
        help=(
            "clone creates a new DB run seeded from the source; in-place resumes the source "
            "run directly."
        ),
    )
    parser.add_argument(
        "--stop-after",
        default="GenerateExpectationDetails",
        choices=sorted(_STOP_AFTER_ALIASES),
        help="Workflow node where the Document 2 smoke should stop.",
    )
    parser.add_argument(
        "--export-brief-state",
        action="store_true",
        help="Export eval/brief_state_exports/<run_id>.json after the smoke finishes.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
