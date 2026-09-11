"""Revision-aware graph membership comes from actual attempts and persisted results."""

from __future__ import annotations

import json
from datetime import datetime

NODES = (
    "SOURCE",
    "W1",
    "W2",
    "W3",
    "ARCHIVE",
    "EVENT_DISCOVERY",
    "BADCASE",
    "TRADE_EXECUTION",
    "FAILURE",
)


def project(
    store, ticker: str, summary: dict, incoming: list[dict]
) -> tuple[list[dict], list[dict]]:
    identity = summary["case_id"]
    with store.connect() as db:
        turns = {
            row[0]: json.loads(row[1])
            for row in db.execute(
                "SELECT id,payload FROM objects WHERE kind='attempt' AND ticker=? "
                "AND parent=? AND valid_to IS NULL",
                (ticker, identity),
            )
        }
    for item in incoming:
        if item["kind"] == "attempt" and item.get("parent") == identity:
            turns[item["id"]] = item["data"]
    prior = store.get("graph_case", ticker, identity) or {"nodes": [], "edges": []}
    nodes = {"SOURCE", *summary["results"]}
    nodes.update(turn["node_id"] for turn in turns.values())
    edges = set()
    if "W1" in nodes:
        edges.add(("SOURCE", "W1"))
    if "W2" in nodes:
        edges.add(
            (
                "W1"
                if summary["first_round_shape"] == "SEQUENTIAL" and "W1" in nodes
                else "SOURCE",
                "W2",
            )
        )
    if "W3" in nodes:
        edges.update((node, "W3") for node in ("W1", "W2") if node in nodes)
    for result in summary["results"]:
        if summary["w3_status"] == "RESOLVED" and "W3" in nodes:
            origin = "W3"
        elif result == "EVENT_DISCOVERY" and "W1" in nodes:
            origin = "W1"
        else:
            origin = "W2" if "W2" in nodes else "W1" if "W1" in nodes else "SOURCE"
        edges.add((origin, result))
    records, counts = [], []
    native = store.get("native:runtime_v2_cases", ticker, identity) or {}
    for item in incoming:
        if item["kind"] == "native:runtime_v2_cases" and item["id"] == identity:
            native = item["data"] or {}
    for node in set(prior["nodes"]) | nodes:
        selected = [t for t in turns.values() if t["node_id"] == node]
        completed = [t["timing"]["completed_at"]["value"] for t in selected]
        started = [t["timing"]["first_started_at"]["value"] for t in selected]
        elapsed = None
        if selected and all(completed) and all(started):
            elapsed = str(
                (
                    max(map(datetime.fromisoformat, completed))
                    - min(map(datetime.fromisoformat, started))
                ).total_seconds()
            )
        final = native.get(node.lower() + "_final") or {}
        if not final and not (node == "W3" and native.get("w3_result")):
            elapsed = None
        failed = (
            node == "FAILURE"
            and node in nodes
            or (
                summary["status"] in {"FAILED", "UNAVAILABLE"}
                and bool(selected)
                and not final
                and any(t["status"] == "FAILED" for t in selected)
            )
        )
        record = {
            "kind": "graph_member",
            "ticker": ticker,
            "id": identity + ":" + node,
            "parent": node,
            "day": summary["semantic_day"],
            "sort": summary["received_at"],
            "data": summary if node in nodes else None,
        }
        records.append(record)
        latest = max((value for value in completed if value), default=None)
        records.append(
            {
                "kind": "graph_observation",
                "ticker": ticker,
                "id": identity + ":" + node,
                "parent": node,
                "day": summary["semantic_day"],
                "sort": latest or summary["received_at"],
                "data": {"latest_at": latest} if latest else None,
            }
        )
        for metric, amount in {
            "graph_cases": int(node in nodes),
            "graph_failed": int(failed),
            "graph_low": int(final.get("confidence") == "low"),
            "graph_seconds": elapsed,
            "graph_samples": int(elapsed is not None),
        }.items():
            counts.append(
                {
                    "metric": metric,
                    "ticker": ticker,
                    "entity": identity + ":" + node,
                    "day": summary["semantic_day"],
                    "dimensions": {"node": node},
                    "value": amount,
                }
            )
        for result in NODES[4:]:
            counts.append(
                {
                    "metric": "graph_results",
                    "ticker": ticker,
                    "entity": identity + ":" + node + ":" + result,
                    "day": summary["semantic_day"],
                    "dimensions": {"node": node, "result": result},
                    "value": int(node in nodes and result in summary["results"]),
                }
            )
    for edge in set(tuple(e) for e in prior["edges"]) | edges:
        counts.append(
            {
                "metric": "graph_edges",
                "ticker": ticker,
                "entity": identity + ":" + ":".join(edge),
                "day": summary["semantic_day"],
                "dimensions": {"from": edge[0], "to": edge[1]},
                "value": int(edge in edges),
            }
        )
    records.append(
        {
            "kind": "graph_case",
            "ticker": ticker,
            "id": identity,
            "data": {"nodes": sorted(nodes), "edges": sorted(edges)},
        }
    )
    return records, counts
