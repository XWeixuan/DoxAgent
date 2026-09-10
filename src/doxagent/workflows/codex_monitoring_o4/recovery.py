"""Per-need ingestion and terminal settlement; no fabricated crawler success."""

from doxagent.codex_runtime.recovery import bounded_text, ingest_model, json_value

from .schema import (
    ConfigureCompletion,
    DeliveryCheckpoint,
    DeliveryItemSettlement,
    DeliverySettlement,
    DeliveryWorkItemCheckpoint,
    SourceNeedPlanItem,
)


def ingest(model, text):
    raw = json_value(text)
    if not isinstance(raw, dict):
        raise ValueError("O4 response has no object envelope")
    if model is ConfigureCompletion:
        body = raw.get("plan")
        if not isinstance(body, dict):
            raise ValueError("O4 response has no plan")
        field, item_model = "source_needs", SourceNeedPlanItem
    else:
        body = raw
        field = "items"
        item_model = (
            DeliveryWorkItemCheckpoint if model is DeliveryCheckpoint else DeliveryItemSettlement
        )
    rows = body.get(field, [])
    accepted = {}
    conflicts = set()
    issues = []
    for row in rows if isinstance(rows, list) else []:
        try:
            item = ingest_model(item_model, row)
            key = item.source_need_id
            candidate = item.model_dump(mode="json")
            if key in conflicts:
                continue
            if key in accepted and accepted[key] != candidate:
                accepted.pop(key)
                conflicts.add(key)
                issues.append(f"Conflicting duplicate source need isolated: {key}")
                continue
            accepted[key] = candidate
        except (ValueError, TypeError) as exc:
            issues.append(bounded_text(exc, 800))
    body[field] = list(accepted.values())
    if model is ConfigureCompletion:
        body["deliberate_omissions"] = list(body.get("deliberate_omissions") or []) + issues
    elif model is DeliverySettlement and issues:
        body["summary"] = str(body.get("summary") or "") + "; isolated invalid delivery items"
    return ingest_model(model, raw)
