"""Six public progress stages derived from the durable initialization ledger."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from doxagent.api_v2.dto import available, missing, validate
from doxagent.api_v2.errors import ApiFailure

from .repository import ReadStore, instant

STEPS = (
    "RESEARCH",
    "EVENT_LIBRARY",
    "EXPECTATIONS",
    "POLICIES",
    "SOURCES_ACTIVATION",
    "START_RUNTIME",
)
BLOCKS = {
    "D1": "RESEARCH",
    "CDECR": "EVENT_LIBRARY",
    "O2": "EVENT_LIBRARY",
    "D2": "EXPECTATIONS",
    "D3": "POLICIES",
    "O4": "SOURCES_ACTIVATION",
    "REGISTER": "SOURCES_ACTIVATION",
    "ACTIVATION": "SOURCES_ACTIVATION",
    "BUS_START": "SOURCES_ACTIVATION",
    "RUNTIME_START": "START_RUNTIME",
}


def progress(store: ReadStore, run: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    ticker, identity = run["ticker"], run["initialization_id"]
    groups: dict[str, dict[str, Any]] = {}
    for kind in ("native:initialization_nodes", "native:initialization_events"):
        with store.connect() as db:
            rows = db.execute(
                "SELECT id,payload FROM objects WHERE kind=? AND ticker=? AND parent=? "
                "AND valid_to IS NULL",
                (kind, ticker, identity),
            )
            groups[kind] = {r[0]: json.loads(r[1]) for r in rows}
    if incoming["kind"] in groups:
        groups[incoming["kind"]][incoming["id"]] = incoming["data"]
    nodes = list(groups["native:initialization_nodes"].values())
    events = list(groups["native:initialization_events"].values())
    steps = []
    all_quality = set()
    for step in STEPS:
        selected = [n for n in nodes if BLOCKS.get(n["block"]) == step]
        states = {n["status"] for n in selected}
        status = (
            "FAILED"
            if "FAILED" in states
            else "RUNNING"
            if "RUNNING" in states
            else "SUCCEEDED"
            if states == {"SUCCEEDED"}
            else "PENDING"
        )
        keys = {n["key"] for n in selected}
        started = [
            e["created_at"]
            for e in events
            if e["kind"] == "node.started" and e["payload"].get("node") in keys
        ]
        ended = [
            e["created_at"]
            for e in events
            if e["kind"] in {"node.settled", "native.reconciled", "manual.adopt"}
            and e["payload"].get("node") in keys
        ]
        begin = datetime.fromisoformat(min(started)) if started else None
        end = (
            datetime.fromisoformat(max(ended))
            if ended and states <= {"SUCCEEDED", "FAILED"}
            else None
        )
        quality = sorted(
            {q for n in selected for q in (n.get("result") or {}).get("quality_annotations", [])}
        )
        all_quality.update(quality)
        steps.append(
            {
                "step_key": step,
                "status": status,
                "first_started_at": available(instant(begin)) if begin else missing(),
                "settled_at": available(instant(end)) if end else missing(),
                "duration_seconds": available(max(0, (end - begin).total_seconds()))
                if begin and end
                else missing(),
                "quality_annotations": quality,
            }
        )
    control = store.get("ticker", ticker, ticker)
    value = {
        "initialization_id": identity,
        "ticker": ticker,
        "status": run["status"],
        "state_seq": run["state_seq"],
        "control_etag": control["control_etag"] if control else '"0"',
        "manual_resume_allowed": run["status"] == "FAILED"
        and run["manual_resume_required"]
        and run.get("error") != "OPERATOR_STOPPED",
        "failed_node_keys": sorted(n["key"] for n in nodes if n["status"] == "FAILED"),
        "steps": steps,
        "quality_annotations": sorted(all_quality),
        "failure": ApiFailure("INITIALIZATION_FAILED").payload(identity)["error"]
        if run.get("error")
        else None,
    }
    return {
        "kind": "initialization",
        "ticker": ticker,
        "id": identity,
        "data": validate("InitializationProgress", value),
    }
