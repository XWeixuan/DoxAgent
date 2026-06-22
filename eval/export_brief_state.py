"""Export a DoxAgent run's Brief State as eval-ready JSON."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doxagent.debug_viewer.query import (
    DebugRunQueryService,
    build_agent_metrics_view,
    build_brief_state_view,
)
from doxagent.settings import DoxAgentSettings

JsonDict = dict[str, Any]


def export_brief_state(run_id: str, output_path: Path | None = None) -> Path:
    settings = DoxAgentSettings()
    service = DebugRunQueryService(settings)
    bundle = service.load_bundle(run_id)
    storage_status = service.status()

    brief_state = build_brief_state_view(bundle, storage_status=storage_status)
    agent_metrics = build_agent_metrics_view(bundle, storage_status=storage_status)
    hard_validators = _dict(brief_state.get("hard_validators"))
    stable_documents = _dict(bundle.belief_state.get("documents"))
    checkpoints = list(bundle.checkpoints)

    export = {
        "export_metadata": {
            "schema_version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "ticker": bundle.run.get("ticker"),
            "source": "doxagent.debug_viewer.query.DebugRunQueryService",
            "intended_use": "blackboard_eval_contract",
        },
        "storage": storage_status,
        "brief_state": brief_state,
        "agent_metrics": agent_metrics,
        "hard_validators": hard_validators,
        "stable_documents": stable_documents,
        "workflow_checkpoints": checkpoints,
        "working_memory": list(bundle.working_memory),
        "commit_log": list(bundle.commit_log),
        "objections": list(bundle.objections),
        "delegations": list(bundle.delegations),
        "evidence_refs": list(bundle.evidence_refs),
        "eval_index": _eval_index(
            brief_state=brief_state,
            hard_validators=hard_validators,
            stable_documents=stable_documents,
            checkpoints=checkpoints,
            working_memory=list(bundle.working_memory),
            commit_log=list(bundle.commit_log),
            objections=list(bundle.objections),
            delegations=list(bundle.delegations),
            evidence_refs=list(bundle.evidence_refs),
        ),
    }

    resolved_output = output_path or _default_output_path(run_id)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(export, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return resolved_output


def _eval_index(
    *,
    brief_state: JsonDict,
    hard_validators: JsonDict,
    stable_documents: JsonDict,
    checkpoints: list[JsonDict],
    working_memory: list[JsonDict],
    commit_log: list[JsonDict],
    objections: list[JsonDict],
    delegations: list[JsonDict],
    evidence_refs: list[JsonDict],
) -> JsonDict:
    global_research = _dict(brief_state.get("global_research"))
    expectation_units = _list(brief_state.get("expectation_units"))
    latest_checkpoint = _dict(brief_state.get("latest_checkpoint"))
    stable_doc_types = sorted(str(key) for key in stable_documents)
    monitoring_policy = _first_document(stable_documents, "monitoring_policy")
    monitoring_config = _first_document(stable_documents, "monitoring_config")
    known_events = _first_document(stable_documents, "known_events")

    return {
        "workflow": {
            "latest_status": latest_checkpoint.get("status"),
            "next_node": latest_checkpoint.get("next_node"),
            "completed_nodes": latest_checkpoint.get("completed_nodes", []),
            "checkpoint_count": len(checkpoints),
        },
        "stable_document_types": stable_doc_types,
        "global_research": {
            "status": global_research.get("status"),
            "section_names": [
                str(section.get("field") or section.get("name"))
                for section in _list(global_research.get("sections"))
                if section.get("field") or section.get("name")
            ],
        },
        "expectation_units": [
            {
                "expectation_id": item.get("expectation_id"),
                "expectation_name": item.get("expectation_name"),
                "direction": item.get("direction"),
                "realized_fact_count": len(_list(item.get("realized_facts"))),
                "key_variable_count": len(_list(item.get("key_variables"))),
                "is_blocked": _dict(item.get("blockers")).get("is_blocked"),
            }
            for item in expectation_units
        ],
        "known_events": {
            "event_count": len(_list(known_events.get("events"))),
        },
        "monitoring": {
            "monitoring_item_count": len(_list(monitoring_config.get("monitoring_items"))),
            "direct_trade_rule_count": len(_list(monitoring_policy.get("direct_trade_rules"))),
            "push_to_agent_rule_count": len(_list(monitoring_policy.get("push_to_agent_rules"))),
            "cache_rule_count": len(_list(monitoring_policy.get("cache_rules"))),
        },
        "audit_counts": {
            "working_memory_entries": len(working_memory),
            "commit_log_entries": len(commit_log),
            "objections": len(objections),
            "open_or_unresolved_objections": len(
                [
                    item
                    for item in objections
                    if str(item.get("status", "")).lower() in {"open", "unresolved"}
                ]
            ),
            "delegations": len(delegations),
            "open_or_assigned_delegations": len(
                [
                    item
                    for item in delegations
                    if str(item.get("status", "")).lower() in {"open", "assigned"}
                ]
            ),
            "evidence_refs": len(evidence_refs),
        },
        "hard_validators": {
            "status": hard_validators.get("status"),
            "failed_count": _dict(hard_validators.get("summary")).get("failed_count", 0),
            "warning_count": _dict(hard_validators.get("summary")).get("warning_count", 0),
            "finding_count": _dict(hard_validators.get("summary")).get("finding_count", 0),
            "validators": [
                {
                    "validator_id": item.get("validator_id"),
                    "status": item.get("status"),
                    "error_count": _dict(item.get("summary")).get("error_count", 0),
                    "warning_count": _dict(item.get("summary")).get("warning_count", 0),
                }
                for item in _list(hard_validators.get("validators"))
                if isinstance(item, dict)
            ],
        },
        "process_review_required": [
            "Review LangSmith loops for C1/C2/C3/O4 BuildGlobalResearch.",
            "Review O1 construction, resolution, detail, narrative, known-events loops.",
            "Review A1/C1/C3/O4 field-review loops and objection handling.",
            "Review A2 search or verification loops when delegations exist.",
            "Review O2 monitoring config and policy loops.",
            "Verify tool calls behind cited evidence refs.",
        ],
    }


def _first_document(stable_documents: JsonDict, document_type: str) -> JsonDict:
    records = _dict(stable_documents.get(document_type))
    for value in records.values():
        if isinstance(value, dict):
            document = value.get("document")
            return document if isinstance(document, dict) else value
    return {}


def _default_output_path(run_id: str) -> Path:
    safe_run_id = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in run_id)
    return Path("eval") / "brief_state_exports" / f"{safe_run_id}.json"


def _dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a DoxAgent Brief State bundle for Blackboard eval.",
    )
    parser.add_argument("run_id", help="DoxAgent blackboard run_id to export.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JSON path. Defaults to eval/brief_state_exports/<run_id>.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output = export_brief_state(args.run_id, args.output)
    print(output)


if __name__ == "__main__":
    main()
