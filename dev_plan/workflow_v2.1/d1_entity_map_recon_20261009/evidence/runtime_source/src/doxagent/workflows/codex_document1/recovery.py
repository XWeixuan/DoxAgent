"""Recover research text independently of optional agent-authored records."""

from doxagent.codex_runtime.recovery import bounded_text, ingest_model, json_value
from doxagent.codex_runtime.schema import AgentObservationCandidate, EntityRelation, FutureNode

from .schema import NodeOutput


def recover_output(text: str) -> tuple[NodeOutput, list[dict]]:
    quarantined = []
    try:
        raw = json_value(text)
    except (ValueError, TypeError):
        raw = {}
        quarantined.append({"scope": "response", "reason": "invalid JSON"})
    if not isinstance(raw, dict):
        raw = {}
        quarantined.append({"scope": "response", "reason": "not an object"})
    values = {
        "status": str(raw.get("status") or "PARTIAL"),
        "summary": raw.get("summary") if isinstance(raw.get("summary"), str) else "",
        "report_markdown": raw.get("report_markdown")
        if isinstance(raw.get("report_markdown"), str)
        else "",
        "warnings": [x for x in raw.get("warnings", []) if isinstance(x, str)]
        if isinstance(raw.get("warnings"), list)
        else [],
    }
    for key, model in (
        ("observation_candidates", AgentObservationCandidate),
        ("entity_relations", EntityRelation),
        ("future_nodes", FutureNode),
    ):
        values[key] = []
        rows = raw.get(key) or []
        if not isinstance(rows, list):
            quarantined.append({"scope": key, "raw": rows, "reason": "not an array"})
            continue
        for index, row in enumerate(rows):
            try:
                values[key].append(ingest_model(model, row))
            except (ValueError, TypeError) as exc:
                quarantined.append(
                    {"scope": key, "index": index, "raw": row, "reason": bounded_text(exc)}
                )
    if raw.get("metadata") or set(raw) - set(NodeOutput.model_fields):
        quarantined.append(
            {"scope": "extensions", "reason": "noncanonical metadata retained in raw response"}
        )
    if quarantined:
        values["warnings"].append(f"INGEST_RECOVERED: {len(quarantined)} noncanonical items")
    return NodeOutput.model_validate(values), quarantined
