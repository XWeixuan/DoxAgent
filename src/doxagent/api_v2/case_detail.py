"""Case detail joins only the immutable versions pinned by that admitted Case."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request

from doxagent.v2_read.runtime import timing

from .dto import available, coverage, missing
from .errors import ApiFailure


def resource(data: Any, reason: str = "NOT_RECORDED") -> dict:
    return {
        "state": "AVAILABLE" if data is not None else "NOT_PRODUCED",
        "data": data,
        "reason": None if data is not None else reason,
        "coverage": coverage(),
    }


def install(app: FastAPI) -> None:
    store, views = app.state.store, app.state.views

    @app.get("/api/doxagent/v2/tickers/{ticker}/runtime/cases/{case_id}")
    async def detail(ticker: str, case_id: str, request: Request) -> Any:
        args = app.state.query(request, {"view_id", "stream_cursor"})
        owner = request.state.principal.user_id
        view = views.get(owner, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "RUNTIME":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        seq = view["seq"]
        if args.get("stream_cursor"):
            seq = app.state.graphs.cursor(owner, args["stream_cursor"], ticker, args["view_id"])[
                "seq"
            ]
            view = {**view, "seq": seq}
        summary = store.get("case", ticker, case_id, seq)
        case = store.get("native:runtime_v2_cases", ticker, case_id, seq)
        if not case or not summary:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        pin = case["version_pin"]
        activation = store.get(
            "activation_revision", ticker, pin.get("activation_revision_id") or "", seq
        )
        if (
            not activation
            or activation["event_library"]["library_version"] != pin["event_library_version"]
            or activation["policy_set"]["policy_set_version"] != pin["policy_set_version"]
        ):
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 404)
        library = activation["event_library"]
        reasons = store.get("case_reasoning", ticker, case_id, seq) or {}

        def page(kind: str, route: str | None = None) -> dict:
            return views.page(
                owner,
                kind,
                ticker,
                view=view,
                view_id=args["view_id"],
                parent=case_id,
                route=route,
                limit=20,
            )

        with store.connect() as db:
            attempts = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM objects WHERE kind='attempt' AND ticker=? AND parent=? "
                    "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
                    (ticker, case_id, seq, seq),
                )
            ]

        def interval(node: str | None) -> dict:
            selected = (
                [a for a in attempts if a["node_id"] == node]
                if node
                else [
                    a
                    for a in attempts
                    if a["node_id"] in {"W1", "W2"} and a["round"] in {"R1", "R2"}
                ]
            )
            if not selected:
                return timing(None, None)
            starts = [a["timing"]["first_started_at"]["value"] for a in selected]
            ends = [a["timing"]["completed_at"]["value"] for a in selected]
            if not all(starts) or not all(ends):
                return timing(None, None)
            value = timing(
                min(starts, key=datetime.fromisoformat), max(ends, key=datetime.fromisoformat)
            )
            value["basis"] = "DERIVED_FROM_RECORDED_INTERVALS"
            return value

        reference_gaps = {}

        def partial(value, reference_result=None, policy_result=None):
            result = resource(value)
            refs = reference_gaps.get(id(reference_result), [])
            policies = reference_gaps.get(id(policy_result), [])
            if refs or policies:
                if refs:
                    value["unresolved_reference_ids"] = refs
                if policies:
                    value["unresolved_policy_ids"] = policies
                result.update(state="PARTIAL", reason="UNRESOLVED_REFERENCE",
                    coverage=coverage(count=len(value.get("references", [])) + len(value.get("policies", [])),
                                      reasons=["PINNED_ARTIFACT_MISSING"]))
            return result

        def references(result: dict) -> list[dict]:
            values = []
            for identity in result.get("reference_ids", []):
                event = store.get(
                    "event_detail", ticker, library["library_snapshot_id"] + ":" + identity, seq
                )
                if event is None:
                    with store.connect() as db:
                        provisional = db.execute(
                            "SELECT 1 FROM objects WHERE kind='native:runtime_v2_candidates' "
                            "AND ticker=? AND json_extract(payload,'$.provisional_event_id')=? "
                            "AND json_extract(payload,'$.trading_date')=? "
                            "AND json_extract(payload,'$.snapshot_version')<=? "
                            "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                            (
                                ticker,
                                identity,
                                case["trading_date"],
                                pin["provisional_snapshot_version"],
                                seq,
                                seq,
                            ),
                        ).fetchone()
                    if not provisional:
                        reference_gaps.setdefault(id(result), []).append(identity)
                        continue
                values.append(
                    {
                        "kind": "CANONICAL" if event else "PROVISIONAL",
                        "event_key": event["event_key"] if event else None,
                        "event_id": identity,
                        "fact_ids": [],
                        "library_snapshot_id": library["library_snapshot_id"],
                        "library_version": library["library_version"],
                        "provisional_snapshot_version": None
                        if event
                        else pin["provisional_snapshot_version"],
                        "semantic_day": None if event else case["trading_date"],
                    }
                )
            return values

        def policies(result: dict) -> list[dict]:
            selected = []
            attribution = {
                p["policy_id"]: p["condition_ids"] for p in result.get("matched_condition_ids", [])
            }
            with store.connect() as db:
                for identity in result.get("policy_ids", []):
                    row = db.execute(
                        "SELECT payload FROM objects WHERE kind='policy_detail' AND ticker=? "
                        "AND parent=? AND json_extract(payload,'$.summary.policy_id')=? "
                        "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                        (ticker, activation["policy_set"]["artifact_id"], identity, seq, seq),
                    ).fetchone()
                    if not row:
                        reference_gaps.setdefault(id(result), []).append(identity)
                        continue
                    policy = json.loads(row[0])["summary"]
                    selected.append(
                        {
                            "policy_id": identity,
                            "policy_set_version": pin["policy_set_version"],
                            "policy_activation_revision": policy["policy_activation_revision"],
                            "condition_ids": attribution.get(identity, []),
                        }
                    )
            return selected

        w1, w2, w3 = case.get("w1_final"), case.get("w2_final"), case.get("w3_result")
        first = (
            {
                "novelty": available(w1["result"]),
                "confidence": available(w1["confidence"]),
                "references": references(w1),
                "timing": interval("W1"),
                "attempts": page("attempt", "W1"),
                "reasoning": resource(reasons.get("w1")),
            }
            if w1
            else None
        )
        second = (
            {
                "skipped": case["w2_skipped"],
                "policy_hit": available(bool(w2["policy_ids"])),
                "confidence": available(w2["confidence"]),
                "policies": policies(w2),
                "timing": interval("W2"),
                "attempts": page("attempt", "W2"),
                "reasoning": resource(reasons.get("w2")),
            }
            if w2 and not case["w2_skipped"]
            else None
        )
        third = None
        if not w3 and summary["initial_route"] == "W3":
            route = store.get("native:runtime_v2_w3_cases", ticker, case_id, seq)
            if route:
                third = {
                    "status": summary["w3_status"], "mode": route["mode"],
                    **{name: missing() for name in ("novelty", "policy_hit", "expert_trade_evaluated",
                        "expert_trade", "direction", "prior_expectation", "expectation_delta")},
                    "references": [], "policies": [], "timing": interval("W3"),
                    "attempts": page("attempt", "W3"),
                    "reasoning": {name: resource(None) for name in ("novelty", "policy", "expert_trade")},
                }
        if w3:
            route = store.get("native:runtime_v2_w3_cases", ticker, case_id, seq)
            if not route:
                raise ApiFailure("PINNED_ARTIFACT_MISSING", 404)
            expert = w3["expert_trade"]
            third = {
                "status": summary["w3_status"],
                "mode": route["mode"],
                "novelty": available(w3["novelty"]["result"]),
                "references": references(w3["novelty"]),
                "policy_hit": available(bool(w3["policy"]["policy_ids"])),
                "policies": policies(w3["policy"]),
                "expert_trade_evaluated": available(expert["evaluated"]),
                "expert_trade": available(expert["trade"]),
                **{
                    k: available(expert[k]) if expert.get(k) is not None else missing()
                    for k in ("direction", "prior_expectation", "expectation_delta")
                },
                "timing": interval("W3"),
                "attempts": page("attempt", "W3"),
                "reasoning": {
                    k: resource(reasons.get("w3_" + k))
                    for k in ("novelty", "policy", "expert_trade")
                },
            }
        with store.connect() as db:
            counts = {
                kind: db.execute(
                    "SELECT COUNT(*) FROM objects WHERE kind=? AND ticker=? "
                    "AND parent=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
                    (kind, ticker, case_id, seq, seq),
                ).fetchone()[0]
                for kind in ("candidate", "execution")
            }
        failures = []
        for attempt in attempts:
            if attempt["error"] and attempt["timing"]["completed_at"]["value"]:
                failures.append({"failure_id": attempt["attempt_id"], "stage": attempt["node_id"],
                                 "status": attempt["status"], "error": attempt["error"],
                                 "occurred_at": attempt["timing"]["completed_at"]["value"]})
        if case.get("error_code") and summary["completed_at"]["value"]:
            failures.append(
                {
                    "failure_id": case_id + ":terminal",
                    "stage": "CASE",
                    "status": case["status"],
                    "error": ApiFailure(case["error_code"]).payload(case_id)["error"],
                    "occurred_at": summary["completed_at"]["value"],
                }
            )
        value = {
            "summary": summary,
            "runtime_activation_id": available(pin["activation_revision_id"]),
            "document1": available(activation["document1"]),
            "document2": available(activation["document2"]),
            "library": library,
            "policy_set": activation["policy_set"],
            "provisional_snapshot_version": pin["provisional_snapshot_version"],
            "hot_path": interval(None),
            "w1": partial(first, w1),
            "w2": partial(second, policy_result=w2),
            "w3": partial(third, (w3 or {}).get("novelty"), (w3 or {}).get("policy")),
            "messages": page("message"),
            "failures": failures,
            "candidate_count": available(counts["candidate"]),
            "execution_count": available(counts["execution"]),
            "results": summary["results"],
        }
        return app.state.respond(
            request, "CaseDetail", value, view_id=args["view_id"], read_seq=seq,
            resource_coverage=coverage(complete=not reference_gaps, count=1,
                reasons=["PINNED_ARTIFACT_MISSING"] if reference_gaps else []),
        )
