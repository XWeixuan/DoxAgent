"""Frozen optional business inputs. No production reads occur during replay."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from doxagent.persistent_runtime_v2.w3_context import base_trade_input
from doxagent.persistent_runtime_v2.w3_v6 import trade_input


def read_context(dataset, key):
    import json

    path = Path(dataset) / "inputs" / key / "context.json"
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def business_payload(raw, case, context):
    case.frozen_inputs.setdefault("w3_replay", {})["policy_title_rows"] = context.get(
        "policy_title_rows"
    )
    w1 = context.get("w1_assessment", raw.get("w1", {}))
    w2 = context.get("w2_assessment", raw.get("w2", {}))
    payload = base_trade_input(
        case.source, w1, w2, events=context.get("w1_events"), cutoff_at=raw["as_of"]
    )
    for key in ("policies", "alignment", "pins", "recent_trade", "additional_tags"):
        if key in context:
            payload[key] = context[key]
    if "additional_tags" not in payload:
        tags = []
        for node, value in (("W1", w1), ("W2", w2)):
            if str(value.get("confidence", "")).lower() == "low":
                tags.append(node + "_LOW_CONFIDENCE")
        if not (raw["message"].get("body") or "").strip():
            tags.append("BODY_MISSING")
        payload["additional_tags"] = tags
    payload["contexts"] = [
        dict(name=name, cutoff_at=cutoff)
        for name, cutoff in context.get("research_cutoffs", {}).items()
        if name in {"D1", "D2"}
    ]
    return payload


def visible_input(raw, case, context):
    return trade_input(case, business_payload(raw, case, context), [])


def coverage(raw, context, components, evidence, market):
    return dict(
        w1_assessment=bool(context.get("w1_assessment", raw.get("w1"))),
        w2_identity=any(
            k in context.get("w2_assessment", raw.get("w2", {}))
            for k in ("policy_hit", "policy_related")
        ),
        **{k: k in context for k in ("w1_events", "policies", "pins", "recent_trade", "alignment")},
        d1="d1" in components,
        d2="d2" in components,
        web=any(k.startswith("sources/") for k in evidence),
        market=bool(market),
    )


def execution_profile(raw, context, model=None, effort=None):
    from types import SimpleNamespace

    from doxagent.persistent_runtime_v2.w3_models import freeze_case_profile

    w2 = context.get("w2_assessment", raw.get("w2", {}))
    chosen = freeze_case_profile(
        SimpleNamespace(
            frozen_inputs={},
            w2_final=SimpleNamespace(
                policy_hit=bool(w2.get("policy_hit")),
                policy_related=bool(w2.get("policy_related")),
            ),
        )
    )
    if model is not None:
        chosen.update(model=model, effort=effort or ("medium" if "sol" in model else "max"))
    elif effort is not None:
        chosen["effort"] = effort
    return chosen


def validate_market(market, raw):
    """Absent data is optional; malformed or future-complete bars are not."""
    if not market:
        return
    if market.get("ticker", raw["ticker"]) != raw["ticker"]:
        raise ValueError("DATA_ERROR: market ticker differs from Case")
    if market.get("observed_at") != raw["as_of"]:
        raise ValueError("DATA_ERROR: price snapshot and Case clock differ")
    cutoff = datetime.fromisoformat(raw["as_of"])
    for name, segment in market.get("series", {}).items():
        if segment.get("forming"):
            raise ValueError("DATA_ERROR: replay must not expose forming bars")
        for row in segment.get("rows", []):
            if not row or not isinstance(row[0], str):
                raise ValueError("DATA_ERROR: malformed market bar")
            start = datetime.fromisoformat(row[0])
            end = start + timedelta(minutes=5 if name == "fine" else 60)
            if name == "daily":
                from zoneinfo import ZoneInfo

                local = start.astimezone(ZoneInfo("America/New_York"))
                end = local.replace(hour=16, minute=0, second=0)
            if end > cutoff:
                raise ValueError("DATA_ERROR: complete bar ends after Case cutoff")
