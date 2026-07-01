"""Run an isolated Document1 + Document2 initialization smoke.

The harness runs the normal BlackboardInitializationWorkflow through Document1,
clones the Document1-only run, then resumes the cloned run through Document2.
It stops at PromoteExpectationToBeliefState by default and never enters
Document3 unless the caller explicitly selects a later stop node.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doxagent.debug_viewer.query import DebugRunQueryService
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    GlobalResearchInputs,
    WorkflowNode,
)
from eval.run_document2_expectation_units_smoke import (
    _checkpoint_with_document2_smoke_metadata,
    _resolve_stop_after,
    clone_document1_state,
)

JsonDict = dict[str, Any]


def main() -> int:
    args = _parse_args()
    ticker = args.ticker.upper()
    stop_after = _resolve_stop_after(args.stop_after)
    settings = _persistent_real_smoke_settings()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner", settings=settings)

    started_at = datetime.now(UTC).isoformat()
    source_result = workflow.run(
        ticker,
        research_inputs=_research_inputs_for_ticker(ticker),
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )
    document1_context_pack_present = (
        workflow._document1_context_pack_from_checkpoint(source_result.checkpoint) is not None
        if source_result.error is None
        else False
    )
    if source_result.error is not None or WorkflowNode.BUILD_GLOBAL_RESEARCH not in (
        source_result.checkpoint.completed_nodes
    ):
        output = _base_output(
            ticker=ticker,
            round_label=args.round_label,
            started_at=started_at,
            source_run_id=source_result.checkpoint.run_id,
            execution_run_id=None,
            stop_after=stop_after,
        ) | {
            "event": "document1_document2_smoke_finished",
            "status": source_result.status.value,
            "stage": "document1",
            "document1_context_pack_present": document1_context_pack_present,
            "completed_nodes": [
                node.value for node in source_result.checkpoint.completed_nodes
            ],
            "next_node": source_result.checkpoint.next_node.value
            if source_result.checkpoint.next_node is not None
            else None,
            "error": source_result.error,
        }
        print(json.dumps(output, ensure_ascii=False), flush=True)
        return 1

    source_run = workflow.blackboard.get_run(source_result.checkpoint.run_id)
    seed = clone_document1_state(
        workflow.blackboard,
        workflow.checkpoint_repository,
        source_run,
        source_result.checkpoint,
    )
    base_seed_checkpoint = _checkpoint_with_document2_smoke_metadata(
        seed.checkpoint,
        mode=seed.mode,
        source_run_id=seed.source_run_id,
        stop_after=stop_after,
    )
    seed_checkpoint = base_seed_checkpoint.model_copy(
        update={
            "metadata": base_seed_checkpoint.metadata
            | {
                "document1_document2_smoke_round_label": args.round_label,
                "document1_document2_smoke_ticker": ticker,
                "document1_context_pack_present": document1_context_pack_present,
            }
        },
        deep=True,
    )
    workflow.checkpoint_repository.save_checkpoint(seed_checkpoint)

    print(
        json.dumps(
            _base_output(
                ticker=ticker,
                round_label=args.round_label,
                started_at=started_at,
                source_run_id=seed.source_run_id,
                execution_run_id=seed.execution_run_id,
                stop_after=stop_after,
            )
            | {
                "event": "document1_document2_smoke_started",
                "mode": seed.mode,
                "document1_context_pack_present": document1_context_pack_present,
                "next_node": seed_checkpoint.next_node.value
                if seed_checkpoint.next_node is not None
                else None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    result = workflow.resume(seed_checkpoint, stop_after=stop_after)
    run_summary = DebugRunQueryService(settings).run_summary(result.checkpoint.run_id)
    expectation_unit_count = _summary_document_count(run_summary, "expectation_unit")
    output = _base_output(
        ticker=ticker,
        round_label=args.round_label,
        started_at=started_at,
        source_run_id=seed.source_run_id,
        execution_run_id=result.checkpoint.run_id,
        stop_after=stop_after,
    ) | {
        "event": "document1_document2_smoke_finished",
        "mode": seed.mode,
        "status": result.status.value,
        "stage": "document2",
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
        "document1_context_pack_present": document1_context_pack_present,
        "error": result.error,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0 if _successful_document1_document2_smoke(output, stop_after) else 1


def _research_inputs_for_ticker(ticker: str) -> GlobalResearchInputs:
    peers_by_ticker = {
        "NVDA": ["AMD", "AVGO", "TSM"],
        "SNDK": ["MU", "WDC", "STX"],
    }
    return GlobalResearchInputs(
        sector_or_theme=f"{ticker} semiconductor and AI infrastructure context",
        industry_angle=(
            "recent company catalysts, semiconductor cycle, AI infrastructure demand, "
            "pricing power, and market reaction"
        ),
        universe=[ticker],
        benchmarks=["SOXX", "QQQ"],
        peers=peers_by_ticker.get(ticker, []),
    )


def _base_output(
    *,
    ticker: str,
    round_label: str,
    started_at: str,
    source_run_id: str,
    execution_run_id: str | None,
    stop_after: WorkflowNode,
) -> JsonDict:
    return {
        "ticker": ticker,
        "round_label": round_label,
        "started_at": started_at,
        "source_run_id": source_run_id,
        "execution_run_id": execution_run_id,
        "stop_after": stop_after.value,
    }


def _successful_document1_document2_smoke(
    output: JsonDict,
    stop_after: WorkflowNode,
) -> bool:
    if output.get("error") is not None:
        return False
    completed_nodes = output.get("completed_nodes")
    if not isinstance(completed_nodes, list) or stop_after.value not in completed_nodes:
        return False
    if stop_after is WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE:
        return int(output.get("expectation_unit_count") or 0) > 0
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


def _persistent_real_smoke_settings() -> DoxAgentSettings:
    if os.getenv("DOXAGENT_RUN_REAL_API_TESTS") != "1":
        raise RuntimeError(
            "Set DOXAGENT_RUN_REAL_API_TESTS=1 to consume real API and model quota."
        )
    settings = DoxAgentSettings()
    if settings.storage_mode != "postgres":
        raise RuntimeError(
            "Set DOXAGENT_STORAGE_MODE=postgres so real smoke runs persist to DB."
        )
    if not settings.database_url:
        raise RuntimeError("Set DOXAGENT_DATABASE_URL so smoke runs persist to DB.")
    return settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True, help="Ticker to initialize.")
    parser.add_argument(
        "--round-label",
        default="manual",
        help="Stable label written to smoke output and checkpoint metadata.",
    )
    parser.add_argument(
        "--stop-after",
        default="PromoteExpectationToBeliefState",
        choices=[
            "GenerateExpectationDetails",
            "ReviewExpectationFields",
            "ResolveObjectionsAndDelegations",
            "PromoteExpectationToBeliefState",
            "details",
            "review",
            "resolve",
            "promote",
        ],
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
