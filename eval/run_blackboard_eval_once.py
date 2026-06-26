"""Run one full real Blackboard initialization eval and print JSON status events."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_phase17_real_initialization_smoke import _EVAL_RESEARCH_INPUTS

from doxagent.models import AgentName
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import BlackboardInitializationWorkflow, WorkflowCheckpoint, WorkflowNode


def main() -> int:
    settings = DoxAgentSettings()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner", settings=settings)
    run = workflow.blackboard.start_run("MU", AgentName.SYSTEM)
    research_inputs = workflow._resolve_research_inputs("MU", _EVAL_RESEARCH_INPUTS)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="MU",
        next_node=WorkflowNode.START_TICKER_INITIALIZATION,
        metadata=workflow._base_metadata(research_inputs),
    )
    workflow.checkpoint_repository.save_checkpoint(checkpoint)
    print(json.dumps({"event": "run_started", "run_id": run.run_id}, ensure_ascii=False), flush=True)
    try:
        result = workflow._execute(checkpoint, stop_after=None)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "run_exception",
                    "run_id": run.run_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise
    print(
        json.dumps(
            {
                "event": "run_finished",
                "run_id": result.checkpoint.run_id,
                "status": result.status.value,
                "next_node": result.checkpoint.next_node.value
                if result.checkpoint.next_node
                else None,
                "completed_nodes": [node.value for node in result.checkpoint.completed_nodes],
                "stable_document_types": [
                    item.value for item in result.summary.stable_document_types
                ],
                "commit_count": result.summary.commit_count,
                "working_memory_count": result.summary.working_memory_count,
                "unresolved_objection_count": result.summary.unresolved_objection_count,
                "blocking_delegation_count": result.summary.blocking_delegation_count,
                "error": result.error,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
