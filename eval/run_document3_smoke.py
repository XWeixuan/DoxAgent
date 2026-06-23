"""Run Document 3-only smoke from an existing Document 1/2 run.

The script clones a Postgres-visible source run, preserves only the stable
Document 1/2 state, removes prior Document 3 artifacts, and resumes the normal
BlackboardInitializationWorkflow at GenerateKnownEvents by default.
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
    WorkflowNodeStatus,
    WorkflowRunStatus,
)

JsonDict = dict[str, Any]

_PRE_DOCUMENT3_TYPES = {
    DocumentType.GLOBAL_RESEARCH,
    DocumentType.EXPECTATION_UNIT,
}
_DOCUMENT3_TYPES = {
    DocumentType.KNOWN_EVENTS,
    DocumentType.MONITORING_CONFIG,
    DocumentType.MONITORING_POLICY,
}
_DOCUMENT3_NODES = {
    WorkflowNode.GENERATE_KNOWN_EVENTS,
    WorkflowNode.GENERATE_MONITORING_CONFIG,
    WorkflowNode.REVIEW_MONITORING_CONFIG,
    WorkflowNode.RESOLVE_MONITORING_CONFIG,
    WorkflowNode.GENERATE_MONITORING_POLICY,
    WorkflowNode.REVIEW_MONITORING_POLICY,
    WorkflowNode.RESOLVE_MONITORING_POLICY,
    WorkflowNode.FINALIZE_INITIALIZATION,
}
_DEFAULT_COMPLETED_PREFIX = [
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
]
_STOP_AFTER_ALIASES: dict[str, WorkflowNode] = {
    "GenerateKnownEvents": WorkflowNode.GENERATE_KNOWN_EVENTS,
    "known_events": WorkflowNode.GENERATE_KNOWN_EVENTS,
    "GenerateMonitoringConfig": WorkflowNode.GENERATE_MONITORING_CONFIG,
    "monitoring_config": WorkflowNode.GENERATE_MONITORING_CONFIG,
    "ReviewMonitoringConfig": WorkflowNode.REVIEW_MONITORING_CONFIG,
    "review_config": WorkflowNode.REVIEW_MONITORING_CONFIG,
    "ResolveMonitoringConfig": WorkflowNode.RESOLVE_MONITORING_CONFIG,
    "resolve_config": WorkflowNode.RESOLVE_MONITORING_CONFIG,
    "GenerateMonitoringPolicy": WorkflowNode.GENERATE_MONITORING_POLICY,
    "monitoring_policy": WorkflowNode.GENERATE_MONITORING_POLICY,
    "ReviewMonitoringPolicy": WorkflowNode.REVIEW_MONITORING_POLICY,
    "review_policy": WorkflowNode.REVIEW_MONITORING_POLICY,
    "ResolveMonitoringPolicy": WorkflowNode.RESOLVE_MONITORING_POLICY,
    "resolve_policy": WorkflowNode.RESOLVE_MONITORING_POLICY,
    "FinalizeInitialization": WorkflowNode.FINALIZE_INITIALIZATION,
    "finalize": WorkflowNode.FINALIZE_INITIALIZATION,
}


@dataclass(frozen=True)
class Document3Seed:
    source_run_id: str
    execution_run_id: str
    checkpoint: WorkflowCheckpoint


def main() -> int:
    args = _parse_args()
    stop_after = _resolve_stop_after(args.stop_after) if args.stop_after else None
    settings = _persistent_real_smoke_settings()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner", settings=settings)

    source_run = workflow.blackboard.get_run(args.source_run_id)
    source_checkpoint = workflow.checkpoint_repository.get_latest(args.source_run_id)
    validate_document3_source_state(source_run, source_checkpoint, ticker=args.ticker)

    seed = clone_document2_state_for_document3(
        workflow.blackboard,
        workflow.checkpoint_repository,
        source_run,
        source_checkpoint,
    )
    seed_checkpoint = _checkpoint_with_document3_smoke_metadata(
        seed.checkpoint,
        source_run_id=seed.source_run_id,
        stop_after=stop_after,
    )
    workflow.checkpoint_repository.save_checkpoint(seed_checkpoint)
    seed = Document3Seed(
        source_run_id=seed.source_run_id,
        execution_run_id=seed.execution_run_id,
        checkpoint=seed_checkpoint,
    )

    print(
        json.dumps(
            {
                "event": "document3_smoke_started",
                "source_run_id": seed.source_run_id,
                "execution_run_id": seed.execution_run_id,
                "ticker": seed.checkpoint.ticker,
                "start_node": seed.checkpoint.next_node.value
                if seed.checkpoint.next_node is not None
                else None,
                "stop_after": stop_after.value if stop_after is not None else None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    result = workflow.resume(seed.checkpoint, stop_after=stop_after)
    brief_state = DebugRunQueryService(settings).brief_state(result.checkpoint.run_id)
    export_path = None
    if args.export_brief_state:
        from eval.export_brief_state import export_brief_state

        export_path = export_brief_state(result.checkpoint.run_id)

    execution_run = workflow.blackboard.get_run(result.checkpoint.run_id)
    document3_summary = _document3_summary(execution_run)
    output = {
        "event": "document3_smoke_finished",
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
        "document3": document3_summary,
        "brief_state_document3_keys": sorted(
            key for key in brief_state if key in {"known_events", "monitoring_config", "policies"}
        ),
        "brief_state_export": str(export_path) if export_path is not None else None,
        "error": result.error,
    }
    print(json.dumps(output, ensure_ascii=False), flush=True)
    return (
        0
        if _is_successful_document3_smoke(result.checkpoint, document3_summary, result.error)
        else 1
    )


def clone_document2_state_for_document3(
    blackboard: BlackboardService,
    checkpoint_repository: WorkflowCheckpointRepository,
    source_run: BlackboardRun,
    source_checkpoint: WorkflowCheckpoint,
) -> Document3Seed:
    """Clone a source run and reset it to the Document 3 starting boundary."""

    validate_document3_source_state(source_run, source_checkpoint)

    now = datetime.now(UTC)
    run_id = new_id("run")
    kept_commits = [
        entry
        for entry in source_run.commit_log
        if entry.patch.target.document_type in _PRE_DOCUMENT3_TYPES
    ]
    kept_objections = [
        item for item in source_run.objections if item.target.document_type not in _DOCUMENT3_TYPES
    ]
    kept_delegations = [
        item
        for item in source_run.delegations
        if item.blocking_scope.document_type not in _DOCUMENT3_TYPES
    ]
    kept_working_memory = [
        entry for entry in source_run.working_memory if not _is_document3_working_memory(entry)
    ]
    id_mapping = _clone_id_mapping(
        kept_working_memory,
        kept_commits,
        kept_objections,
        kept_delegations,
    )
    id_mapping[source_run.run_id] = run_id

    cloned_commits = [_clone_commit_log_entry(entry, id_mapping) for entry in kept_commits]
    cloned_belief = source_run.belief_state.model_copy(
        update={
            "snapshot_id": new_id("belief"),
            "commit_ids": [entry.commit_id for entry in cloned_commits],
            "documents": {
                item: deepcopy(documents)
                for item, documents in source_run.belief_state.documents.items()
                if item in _PRE_DOCUMENT3_TYPES
            },
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
                _clone_working_memory_entry(entry, id_mapping) for entry in kept_working_memory
            ],
            "commit_log": cloned_commits,
            "objections": [_clone_objection(item, id_mapping) for item in kept_objections],
            "delegations": [_clone_delegation(item, id_mapping) for item in kept_delegations],
        },
        deep=True,
    )
    blackboard.repository.add(cloned_run)

    metadata = _scrub_document3_clone_metadata(
        _rewrite_json_ids(source_checkpoint.metadata, id_mapping)
    ) | {
        "document3_smoke_source_run_id": source_run.run_id,
        "document3_smoke_cloned_at": now.isoformat(),
        "document3_smoke_start_node": WorkflowNode.GENERATE_KNOWN_EVENTS.value,
        "document3_smoke_skipped_global_narrative_report": True,
    }
    seed_checkpoint = source_checkpoint.model_copy(
        update={
            "run_id": run_id,
            "status": WorkflowRunStatus.RUNNING,
            "completed_nodes": list(_DEFAULT_COMPLETED_PREFIX),
            "node_statuses": {
                node: WorkflowNodeStatus.COMPLETED for node in _DEFAULT_COMPLETED_PREFIX
            },
            "next_node": WorkflowNode.GENERATE_KNOWN_EVENTS,
            "stable_document_types": [
                DocumentType.GLOBAL_RESEARCH,
                DocumentType.EXPECTATION_UNIT,
            ],
            "pending_patches": [],
            "metadata": metadata,
            "summary": None,
        },
        deep=True,
    )
    checkpoint_repository.save_checkpoint(seed_checkpoint)
    return Document3Seed(
        source_run_id=source_run.run_id,
        execution_run_id=run_id,
        checkpoint=seed_checkpoint,
    )


def validate_document3_source_state(
    source_run: BlackboardRun,
    source_checkpoint: WorkflowCheckpoint,
    *,
    ticker: str | None = None,
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
    if ticker is not None and source_run.ticker != ticker:
        raise ValueError(f"Source run ticker must be {ticker}, got {source_run.ticker}.")
    documents = source_run.belief_state.documents
    missing = [item.value for item in _PRE_DOCUMENT3_TYPES if not documents.get(item)]
    if missing:
        raise ValueError(
            "Source run must contain stable Document 1/2 state before Document 3 smoke; "
            f"missing: {', '.join(sorted(missing))}."
        )
    non_document3_unresolved = [
        item.objection_id
        for item in source_run.objections
        if item.target.document_type not in _DOCUMENT3_TYPES and item.is_unresolved
    ]
    if non_document3_unresolved:
        raise ValueError(
            "Source run has unresolved pre-Document3 objections: "
            f"{non_document3_unresolved}."
        )
    non_document3_blocking = [
        item.delegation_id
        for item in source_run.delegations
        if item.blocking_scope.document_type not in _DOCUMENT3_TYPES and item.is_blocking
    ]
    if non_document3_blocking:
        raise ValueError(
            "Source run has blocking pre-Document3 delegations: "
            f"{non_document3_blocking}."
        )


def _checkpoint_with_document3_smoke_metadata(
    checkpoint: WorkflowCheckpoint,
    *,
    source_run_id: str,
    stop_after: WorkflowNode | None,
) -> WorkflowCheckpoint:
    metadata = _scrub_document3_clone_metadata(dict(checkpoint.metadata)) | {
        "document3_smoke_mode": "clone",
        "document3_smoke_source_run_id": source_run_id,
        "document3_smoke_stop_after": stop_after.value if stop_after is not None else None,
        "document3_smoke_target_node": stop_after.value
        if stop_after is not None
        else WorkflowNode.FINALIZE_INITIALIZATION.value,
    }
    return checkpoint.model_copy(update={"metadata": metadata}, deep=True)


def _scrub_document3_clone_metadata(metadata: JsonDict) -> JsonDict:
    cloned = dict(metadata)
    for key in (
        "last_error_code",
        "last_error_message",
        "last_error_boundary",
        "last_error_node",
        "document3_smoke_mode",
        "document3_smoke_source_run_id",
        "document3_smoke_stop_after",
        "document3_smoke_target_node",
    ):
        cloned.pop(key, None)
    return cloned


def _is_document3_working_memory(entry: WorkingMemoryEntry) -> bool:
    content_type = entry.content_type.lower()
    if any(fragment in content_type for fragment in ("known_event", "monitoring", "document3")):
        return True
    workflow_node = entry.payload.get("workflow_node")
    if isinstance(workflow_node, str):
        try:
            return WorkflowNode(workflow_node) in _DOCUMENT3_NODES
        except ValueError:
            return False
    return False


def _clone_id_mapping(
    working_memory: list[WorkingMemoryEntry],
    commits: list[CommitLogEntry],
    objections: list[Objection],
    delegations: list[Delegation],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in working_memory:
        mapping[entry.entry_id] = new_id("wm")
    for commit in commits:
        mapping[commit.commit_id] = new_id("commit")
        mapping[commit.patch.patch_id] = new_id("patch")
    for objection in objections:
        mapping[objection.objection_id] = new_id("objection")
    for delegation in delegations:
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
        return {key: _rewrite_json_ids(item, id_mapping) for key, item in value.items()}
    return value


def _persistent_real_smoke_settings() -> DoxAgentSettings:
    if os.getenv("DOXAGENT_RUN_REAL_API_TESTS") != "1":
        raise RuntimeError(
            "Set DOXAGENT_RUN_REAL_API_TESTS=1 to consume real API and model quota."
        )
    settings = DoxAgentSettings()
    if settings.storage_mode != "postgres":
        raise RuntimeError(
            "Set DOXAGENT_STORAGE_MODE=postgres so Document 3 smoke runs persist to DB."
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


def _document3_summary(run: BlackboardRun) -> JsonDict:
    documents = run.belief_state.documents
    monitoring_configs = documents.get(DocumentType.MONITORING_CONFIG, {})
    applied_versions = []
    for document in monitoring_configs.values():
        if isinstance(document, dict) and document.get("applied_config_version"):
            applied_versions.append(document["applied_config_version"])
    return {
        "known_events_count": len(documents.get(DocumentType.KNOWN_EVENTS, {})),
        "monitoring_config_count": len(monitoring_configs),
        "monitoring_policy_count": len(documents.get(DocumentType.MONITORING_POLICY, {})),
        "applied_config_versions": applied_versions,
    }


def _is_successful_document3_smoke(
    checkpoint: WorkflowCheckpoint,
    document3_summary: JsonDict,
    error: str | None,
) -> bool:
    if error is not None:
        return False
    if checkpoint.status is not WorkflowRunStatus.COMPLETED:
        return False
    if checkpoint.next_node is not None:
        return False
    if checkpoint.pending_patches:
        return False
    required_documents = {
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    }
    if not required_documents.issubset(set(checkpoint.stable_document_types)):
        return False
    return bool(document3_summary.get("applied_config_versions"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_run_id", help="Existing run_id with stable Document 1/2 state.")
    parser.add_argument("--ticker", default=None, help="Optional ticker guard for the source run.")
    parser.add_argument(
        "--stop-after",
        default=None,
        choices=sorted(_STOP_AFTER_ALIASES),
        help="Optional workflow node where the Document 3 smoke should stop.",
    )
    parser.add_argument(
        "--export-brief-state",
        action="store_true",
        help="Export eval/brief_state_exports/<run_id>.json after the smoke finishes.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
