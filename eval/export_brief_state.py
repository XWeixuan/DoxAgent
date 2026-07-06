"""Export a DoxAgent run's Brief State as eval-ready JSON."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doxagent.settings import DoxAgentSettings
from doxagent.workflows.storage import default_workflow_storage

JsonDict = dict[str, Any]


def export_brief_state(run_id: str, output_path: Path | None = None) -> Path:
    settings = DoxAgentSettings()
    storage = default_workflow_storage(settings)
    run = storage.blackboard.get_run(run_id)
    try:
        latest_checkpoint = storage.checkpoint_repository.get_latest(run_id)
    except Exception:
        latest_checkpoint = None

    storage_status = {"storage_mode": settings.storage_mode}
    stable_documents = _to_json(run.belief_state.documents)
    checkpoints = [_to_json(latest_checkpoint)] if latest_checkpoint is not None else []
    working_memory = [_to_json(item) for item in run.working_memory]
    commit_log = [_to_json(item) for item in run.commit_log]
    objections = [_to_json(item) for item in run.objections]
    delegations = [_to_json(item) for item in run.delegations]
    evidence_refs: list[JsonDict] = []

    brief_state = _brief_state_from_storage(
        run=_to_json(run),
        stable_documents=stable_documents,
        latest_checkpoint=_to_json(latest_checkpoint) if latest_checkpoint is not None else {},
    )
    agent_metrics = {"agents": []}
    hard_validators = _dict(brief_state.get("hard_validators"))

    export = {
        "export_metadata": {
            "schema_version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "ticker": run.ticker,
            "source": "eval.export_brief_state.local_storage",
            "intended_use": "blackboard_eval_contract",
        },
        "storage": storage_status,
        "brief_state": brief_state,
        "agent_metrics": agent_metrics,
        "hard_validators": hard_validators,
        "stable_documents": stable_documents,
        "workflow_checkpoints": checkpoints,
        "working_memory": working_memory,
        "commit_log": commit_log,
        "objections": objections,
        "delegations": delegations,
        "evidence_refs": evidence_refs,
        "eval_index": _eval_index(
            brief_state=brief_state,
            hard_validators=hard_validators,
            stable_documents=stable_documents,
            checkpoints=checkpoints,
            working_memory=working_memory,
            commit_log=commit_log,
            objections=objections,
            delegations=delegations,
            evidence_refs=evidence_refs,
        ),
    }

    resolved_output = output_path or _default_output_path(run_id)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(
        json.dumps(export, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return resolved_output


def _brief_state_from_storage(
    *,
    run: JsonDict,
    stable_documents: JsonDict,
    latest_checkpoint: JsonDict,
) -> JsonDict:
    global_research = _first_document(stable_documents, "global_research")
    expectation_units = [
        value.get("document") if isinstance(value.get("document"), dict) else value
        for value in _dict(stable_documents.get("expectation_unit")).values()
        if isinstance(value, dict)
    ]
    checkpoint_payload = _dict(latest_checkpoint)
    return {
        "run": {
            "run_id": run.get("run_id"),
            "ticker": run.get("ticker"),
            "workflow_state": run.get("workflow_state"),
            "created_at": run.get("created_at"),
        },
        "latest_checkpoint": {
            "status": checkpoint_payload.get("status"),
            "next_node": checkpoint_payload.get("next_node"),
            "completed_nodes": checkpoint_payload.get("completed_nodes", []),
            "checkpoint": checkpoint_payload,
        },
        "belief_state": {
            "document_types": sorted(str(key) for key in stable_documents),
        },
        "stable_documents": stable_documents,
        "global_research": {
            "status": "present" if global_research else "missing",
            "sections": _global_research_sections(global_research),
        },
        "expectation_units": expectation_units,
        "hard_validators": {
            "status": "not_run",
            "summary": {
                "validator_count": 0,
                "failed_count": 0,
                "warning_count": 0,
                "finding_count": 0,
            },
            "validators": [],
        },
    }


def _global_research_sections(document: JsonDict) -> list[JsonDict]:
    sections: list[JsonDict] = []
    for field in (
        "fundamental_report",
        "macro_report",
        "industry_report",
        "market_trace_report",
        "market_narrative_report",
    ):
        section = _dict(document.get(field))
        if section:
            sections.append(
                {
                    "field": field,
                    "summary": section.get("summary"),
                    "text": section.get("text"),
                }
            )
    return sections


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


def _to_json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(getattr(key, "value", key)): _to_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_to_json(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


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
