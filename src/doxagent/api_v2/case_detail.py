"""Case detail joins only the immutable versions pinned by that admitted Case."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request

from .case_evidence import CaseEvidence
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
        evidence = CaseEvidence(store, ticker, case_id, seq)
        case = evidence.fields(
            "native:runtime_v2_cases",
            case_id,
            [
                "version_pin",
                "trading_date",
                "w1_final",
                "w2_final",
                "w2_round1",
                "w2_skipped",
                "w3_result",
                "error_code",
                "status",
            ],
        )
        if not case or not summary:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        pin = case["version_pin"]
        if not pin:
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 404)
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

        groups = evidence.attempt_groups()

        def interval(node: str | None) -> dict:
            return evidence.interval(groups, node)

        reference_gaps = {}

        def partial(value, reference_result=None, policy_result=None, candidate_result=None):
            result = resource(value)
            refs = reference_gaps.get(id(reference_result), [])
            policies = reference_gaps.get(id(policy_result), [])
            candidates = reference_gaps.get(id(candidate_result), [])
            if value is not None and (refs or policies or candidates):
                if refs:
                    value["unresolved_reference_ids"] = refs
                if policies:
                    value["unresolved_policy_ids"] = policies
                result.update(
                    state="PARTIAL",
                    reason="UNRESOLVED_REFERENCE",
                    coverage=coverage(
                        count=len(value.get("references", [])) + len(value.get("policies", [])),
                        reasons=["PINNED_ARTIFACT_MISSING"],
                    ),
                )
            return result

        def references(result: dict) -> list[dict]:
            values = []
            attribution = {
                item["event_id"]: item["fact_ids"] for item in result.get("fact_attributions") or []
            }
            for identity in result.get("reference_ids", []):
                parent = library["library_snapshot_id"] + ":" + identity
                event = evidence.fields("event_detail", parent, ["event_key", "event.title"])
                provisional = None
                if event is None:
                    with store.connect() as db:
                        provisional = db.execute(
                            "SELECT id FROM objects WHERE kind='native:runtime_v2_candidates' "
                            "AND ticker=? AND json_extract(payload,'$.provisional_event_id')=? "
                            "AND json_extract(payload,'$.trading_date')=? "
                            "AND json_extract(payload,'$.snapshot_version')<=? "
                            "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
                            "ORDER BY json_extract(payload,'$.snapshot_version') DESC, "
                            "valid_from DESC LIMIT 1",
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
                    provisional = evidence.fields(
                        "native:runtime_v2_candidates", provisional[0], ["candidate"]
                    )
                facts = []
                with store.connect() as db:
                    for fact_id in attribution.get(identity, []) if event else []:
                        row = db.execute(
                            "SELECT json_extract(payload,'$.fact.proposition') AS proposition "
                            "FROM objects "
                            "WHERE kind='fact' AND ticker=? AND parent=? "
                            "AND json_extract(payload,'$.fact.fact_id')=? "
                            "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                            (ticker, parent, fact_id, seq, seq),
                        ).fetchone()
                        facts.append(
                            {
                                "fact_id": fact_id,
                                "proposition": available(row[0]) if row and row[0] else missing(),
                            }
                        )
                proposition = ((provisional or {}).get("candidate") or {}).get("proposition")
                values.append(
                    {
                        "kind": "CANONICAL" if event else "PROVISIONAL",
                        "event_key": event["event_key"] if event else None,
                        "event_id": identity,
                        "fact_ids": attribution.get(identity, []),
                        "title": available(event["event.title"])
                        if event and event["event.title"]
                        else missing(),
                        "facts": facts,
                        "provisional_proposition": available(proposition)
                        if proposition
                        else missing(),
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
                        "SELECT json_extract(payload,'$.summary.title') AS title, "
                        "json_extract(payload,'$.summary.policy_activation_revision') AS revision "
                        "FROM objects WHERE kind='policy_detail' AND ticker=? "
                        "AND parent=? AND json_extract(payload,'$.summary.policy_id')=? "
                        "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                        (ticker, activation["policy_set"]["artifact_id"], identity, seq, seq),
                    ).fetchone()
                    if not row:
                        reference_gaps.setdefault(id(result), []).append(identity)
                        continue
                    selected.append(
                        {
                            "policy_id": identity,
                            "policy_set_version": pin["policy_set_version"],
                            "policy_activation_revision": row["revision"],
                            "title": available(row["title"]) if row["title"] else missing(),
                            "condition_ids": attribution.get(identity, []),
                        }
                    )
            return selected

        w1, w2, w3 = case.get("w1_final"), case.get("w2_final"), case.get("w3_result")
        recalled = case.get("w2_round1")
        if recalled is None:
            recalled = (evidence.latest_success("W2", "R1") or {}).get("output")
        if not isinstance(recalled, dict) or not isinstance(
            recalled.get("candidate_policy_ids"), list
        ):
            recalled = None
        candidates_result = {"policy_ids": (recalled or {}).get("candidate_policy_ids", [])}
        candidate_policies = policies(candidates_result)
        first = (
            {
                "novelty": available(w1["result"]) if w1 else missing(),
                "confidence": available(w1["confidence"]) if w1 else missing(),
                "references": references(w1) if w1 else [],
                "fact_attributions": (w1 or {}).get("fact_attributions"),
                "rounds": evidence.rounds(groups, "W1"),
                "timing": interval("W1"),
                "attempts": page("attempt", "W1"),
                "reasoning": resource(reasons.get("w1")),
            }
            if w1 or any(g["node"] == "W1" for g in groups)
            else None
        )
        second = (
            {
                "skipped": bool(case["w2_skipped"]),
                "reasoning_stage": (
                    "R1" if not recalled.get("candidate_policy_ids") else "R2" if w2 else None
                )
                if recalled is not None
                else None,
                "policy_hit": available(bool(w2["policy_ids"])) if w2 else missing(),
                "confidence": available(w2["confidence"]) if w2 else missing(),
                "policies": policies(w2) if w2 else [],
                **({"candidate_policies": candidate_policies} if recalled is not None else {}),
                "unresolved_candidate_policy_ids": reference_gaps.get(id(candidates_result), []),
                "rounds": evidence.rounds(
                    groups,
                    "W2",
                    bool(case["w2_skipped"]),
                    recalled is not None and not recalled.get("candidate_policy_ids"),
                ),
                "timing": interval("W2"),
                "attempts": page("attempt", "W2"),
                "reasoning": resource(reasons.get("w2")),
            }
            if w2
            or recalled is not None
            or case["w2_skipped"]
            or any(g["node"] == "W2" for g in groups)
            else None
        )
        third = None
        if not w3 and summary["initial_route"] == "W3":
            route = evidence.fields("native:runtime_v2_w3_cases", case_id, ["mode"])
            if route:
                third = {
                    "status": summary["w3_status"],
                    "mode": route["mode"],
                    **{
                        name: missing()
                        for name in (
                            "novelty",
                            "policy_hit",
                            "expert_trade_evaluated",
                            "expert_trade",
                            "direction",
                            "prior_expectation",
                            "expectation_delta",
                        )
                    },
                    "references": [],
                    "policies": [],
                    "timing": interval("W3"),
                    "attempts": page("attempt", "W3"),
                    "reasoning": {
                        name: resource(None) for name in ("novelty", "policy", "expert_trade")
                    },
                }
        if w3:
            route = evidence.fields("native:runtime_v2_w3_cases", case_id, ["mode"])
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
        with store.connect() as db:
            failures = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT json_object('failure_id',id,'stage',route,"
                    "'status',json_extract(payload,'$.status'), "
                    "'error',json_extract(payload,'$.error'),'occurred_at',"
                    "json_extract(payload,'$.timing.completed_at.value')) "
                    "FROM objects WHERE kind='attempt' AND ticker=? AND parent=? "
                    "AND json_extract(payload,'$.error') IS NOT NULL "
                    "AND json_extract(payload,'$.timing.completed_at.value') IS NOT NULL "
                    "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
                    "ORDER BY sort_key DESC,id DESC LIMIT 20",
                    (ticker, case_id, seq, seq),
                )
            ]
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
            "w2": partial(second, policy_result=w2, candidate_result=candidates_result),
            "w3": partial(third, (w3 or {}).get("novelty"), (w3 or {}).get("policy")),
            "messages": page("message"),
            "failures": failures,
            "candidate_count": available(counts["candidate"]),
            "execution_count": available(counts["execution"]),
            "results": summary["results"],
        }
        return app.state.respond(
            request,
            "CaseDetail",
            value,
            view_id=args["view_id"],
            read_seq=seq,
            resource_coverage=coverage(
                complete=not reference_gaps,
                count=1,
                reasons=["PINNED_ARTIFACT_MISSING"] if reference_gaps else [],
            ),
        )
