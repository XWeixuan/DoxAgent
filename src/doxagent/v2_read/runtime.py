"""Turn-level timing and invocation contributions from immutable Runtime receipts."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from doxagent.api_v2.dto import available, missing, validate
from doxagent.api_v2.errors import ApiFailure
from doxagent.semantic_clock import semantic_day

from .repository import instant
from .usage import cost, observations


def hot_path_contributions(store: Any, case: dict[str, Any]) -> list[dict]:
    """Recovered R1/R2 intervals determine wall time, excluding W1 R3 extraction."""
    ticker, identity = case["source"]["snapshot"]["ticker"], case["case_id"]
    value = None
    if case["runtime_mode"] == "REALTIME" and case.get("w1_final") and case.get("w2_final"):
        with store.connect() as db:
            turns = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM objects WHERE kind='attempt' AND ticker=? "
                    "AND parent=? AND valid_to IS NULL AND route IN ('W1','W2')",
                    (ticker, identity),
                )
            ]
        turns = [turn for turn in turns if turn["round"] in {"R1", "R2"}]
        if (
            turns
            and {turn["node_id"] for turn in turns} == {"W1", "W2"}
            and all(
                turn["timing"]["first_started_at"]["value"]
                and turn["timing"]["completed_at"]["value"]
                for turn in turns
            )
        ):
            starts = [
                datetime.fromisoformat(t["timing"]["first_started_at"]["value"]) for t in turns
            ]
            ends = [datetime.fromisoformat(t["timing"]["completed_at"]["value"]) for t in turns]
            value = str((max(ends) - min(starts)).total_seconds())
    return [
        {
            "metric": metric,
            "ticker": ticker,
            "entity": identity,
            "day": case["trading_date"],
            "value": amount,
        }
        for metric, amount in (
            ("hot_path_seconds", value),
            ("hot_path_samples", "1" if value is not None else "0"),
        )
    ]


def timing(start: str | None, end: str | None) -> dict[str, Any]:
    begin = datetime.fromisoformat(start) if start else None
    finish = datetime.fromisoformat(end) if end else None
    if begin and finish and finish < begin:
        raise ValueError("invalid recorded interval")
    return {
        "first_started_at": available(instant(begin)) if begin else missing(),
        "completed_at": available(instant(finish)) if finish else missing(),
        "wall_seconds": available((finish - begin).total_seconds())
        if begin and finish
        else missing(),
        "basis": "RECORDED" if begin and finish else "UNAVAILABLE",
    }


def turn_records(turn: dict[str, Any], case: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    ticker = case["source"]["snapshot"]["ticker"]
    attempt = {
        "attempt_id": turn["turn_id"],
        "turn_id": turn["turn_id"],
        "node_id": turn["lane"],
        "round": turn["round_name"],
        "ordinal": turn["attempt_number"],
        "attempt_number": turn["attempt_number"],
        "status": "SUCCEEDED" if turn["status"] == "OK" else "FAILED",
        "timing": timing(turn.get("started_at"), turn.get("finished_at")),
        "error": ApiFailure(turn["error_code"]).payload(turn["turn_id"])["error"]
        if turn.get("error_code")
        else None,
    }
    records = [
        {
            "kind": "attempt",
            "ticker": ticker,
            "id": turn["turn_id"],
            "parent": turn["case_id"],
            "route": turn["lane"],
            "sort": turn["created_at"],
            "data": validate("ModelAttempt", attempt),
        }
    ]
    day = semantic_day(datetime.fromisoformat(turn.get("started_at") or turn["created_at"]))
    usage = {key: turn.get(key) for key in ("input_tokens", "output_tokens", "cached_input_tokens")}
    price = (
        cost(turn["model"], datetime.fromisoformat(turn["started_at"]), usage)
        if turn.get("started_at")
        else missing("NOT_RECORDED")
    )
    dimensions = {
        "scope": "API",
        "node": turn["lane"],
        "model": turn["model"],
        "provider": turn["provider"],
    }
    records.append(
        {
            "kind": "usage",
            "ticker": ticker,
            "id": turn["turn_id"],
            "day": str(day),
            "parent": turn["case_id"],
            "data": {
                "invocation_id": turn["turn_id"],
                "scope": "API",
                "node": turn["lane"],
                "model": turn["model"],
                "provider": turn["provider"],
                "usage": usage,
                "cost_usd": price,
                "timing": attempt["timing"],
                "coverage": "COMPLETE"
                if all(value is not None for value in usage.values())
                else "PARTIAL",
            },
        }
    )
    contributions = []
    values = observations(turn["model"], turn["provider"], "API", turn.get("started_at"), usage)
    for metric, value in values.items():
        contributions.append(
            {
                "metric": metric,
                "ticker": ticker,
                "entity": turn["turn_id"],
                "day": str(day),
                "dimensions": dimensions,
                "value": value,
            }
        )
    return records, contributions


def worker_records(value: dict) -> tuple[list[dict], list[dict]]:
    job, ticker = value["job"], value["ticker"]
    case_id = value.get("case_id")
    if value["node"] != "persistent_runtime_w3" or not case_id:
        return worker_usage(value)
    interval = timing(job.get("started_at"), job.get("finished_at"))
    attempt = validate(
        "ModelAttempt",
        {
            "attempt_id": job["job_id"],
            "turn_id": job.get("turn_id"),
            "node_id": "W3",
            "round": "AGENT",
            "ordinal": value["ordinal"],
            "attempt_number": value["ordinal"],
            "status": {
                "queued": "PENDING",
                "running": "RUNNING",
                "succeeded": "SUCCEEDED",
                "failed": "FAILED",
                "cancelled": "CANCELLED",
            }[job["status"]],
            "timing": interval,
            "error": ApiFailure(job["error_code"]).payload(job["job_id"])["error"]
            if job.get("error_code")
            else None,
        },
    )
    usage = (job.get("telemetry") or {}).get("observed_usage") or {}
    day = str(semantic_day(datetime.fromisoformat(job.get("started_at") or job["created_at"])))
    records = [
        {
            "kind": "attempt",
            "ticker": ticker,
            "id": job["job_id"],
            "parent": case_id,
            "route": "W3",
            "sort": job.get("started_at") or job["created_at"],
            "data": attempt,
        },
        {
            "kind": "usage",
            "ticker": ticker,
            "id": "codex:" + job["job_id"],
            "parent": case_id,
            "day": day,
            "data": {
                "invocation_id": job["job_id"],
                "scope": "CODEX",
                "node": "W3",
                "model": value["model"],
                "provider": value.get("provider") or "unknown",
                "timing": interval,
                "usage": usage,
                "cost_usd": missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE"),
                "coverage": "COMPLETE"
                if usage and all(v is not None for v in usage.values())
                else "PARTIAL",
            },
        },
    ]
    contributions = [
        {
            "metric": metric,
            "ticker": ticker,
            "entity": "codex:" + job["job_id"],
            "day": day,
            "dimensions": {
                "scope": "CODEX",
                "node": "W3",
                "model": value["model"],
                "provider": value.get("provider") or "unknown",
            },
            "value": amount,
        }
        for metric, amount in observations(
            value["model"],
            value.get("provider") or "unknown",
            "CODEX",
            job.get("started_at"),
            usage,
        ).items()
    ]
    return records, contributions


def worker_usage(value):
    job, ticker = value["job"], value["ticker"]
    if not job.get("started_at"):
        return [], []  # Queued work is not an observed invocation.
    identity = "codex:" + job["job_id"]
    day = str(semantic_day(datetime.fromisoformat(job["started_at"])))
    usage = (job.get("telemetry") or {}).get("observed_usage") or {}
    dimensions = {
        "scope": "CODEX",
        "node": value["node"],
        "model": value.get("model") or "unknown",
        "provider": value.get("provider") or "unknown",
    }
    record = {
        "kind": "usage",
        "ticker": ticker,
        "id": identity,
        "day": day,
        "parent": value.get("initialization_id") or value.get("run_id"),
        "data": {
            "invocation_id": identity,
            **dimensions,
            "usage": usage,
            "timing": timing(job["started_at"], job.get("finished_at")),
            "cost_usd": missing("CODEX_SUBSCRIPTION_NOT_PRICED", "NOT_APPLICABLE"),
        },
    }
    counts = [
        {
            "metric": name,
            "ticker": ticker,
            "entity": identity,
            "day": day,
            "dimensions": dimensions,
            "value": amount,
        }
        for name, amount in observations(
            dimensions["model"], dimensions["provider"], "CODEX", job["started_at"], usage
        ).items()
    ]
    return [record], counts


def w3_contributions(store: Any, case: dict) -> list[dict]:
    ticker, identity = case["source"]["snapshot"]["ticker"], case["case_id"]
    duration = None
    with store.connect() as db:
        turns = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM objects WHERE kind='attempt' AND ticker=? "
                "AND parent=? AND route='W3' AND valid_to IS NULL",
                (ticker, identity),
            )
        ]
    if (
        turns
        and case["status"] in {"COMPLETED", "FAILED", "UNAVAILABLE"}
        and all(
            t["timing"]["first_started_at"]["value"] and t["timing"]["completed_at"]["value"]
            for t in turns
        )
    ):
        starts = [datetime.fromisoformat(t["timing"]["first_started_at"]["value"]) for t in turns]
        ends = [datetime.fromisoformat(t["timing"]["completed_at"]["value"]) for t in turns]
        duration = str((max(ends) - min(starts)).total_seconds())
    return [
        {
            "metric": metric,
            "ticker": ticker,
            "entity": identity,
            "day": case["trading_date"],
            "value": amount,
        }
        for metric, amount in (
            ("w3_seconds", duration),
            ("w3_samples", "1" if duration is not None else "0"),
        )
    ]
