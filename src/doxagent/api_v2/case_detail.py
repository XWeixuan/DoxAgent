"""Case detail joins only the immutable versions pinned by that admitted Case."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request

from doxagent.persistent_runtime_v2.router import alignment_flags
from doxagent.persistent_runtime_v2.schema import (
    normalize_w2_result,
)

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

    @app.get("/api/doxagent/v2/tickers/{ticker}/runtime/cases/{case_id}/policies/{policy_id}")
    async def export_policy(ticker: str, case_id: str, policy_id: str, request: Request) -> Any:
        args = app.state.query(request, {"view_id"})
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "RUNTIME":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        evidence = CaseEvidence(store, ticker, case_id, view["seq"])
        case = evidence.fields(
            "native:runtime_v2_cases",
            case_id,
            [
                "version_pin",
                "w2_round1",
                "w2_final",
                "w3_result",
                "w3_operations_result",
                "w3_trade_decision",
            ],
        )
        if not case or not store.get("case", ticker, case_id, view["seq"]):
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        recall = (
            case.get("w2_round1") or (evidence.latest_success("W2", "R1") or {}).get("output") or {}
        )
        referenced = set(recall.get("candidate_policy_ids", []))
        referenced.update((case.get("w2_final") or {}).get("policy_ids", []))
        referenced.update((case.get("w2_final") or {}).get("related_policy_ids", []))
        for field in ("hit_condition_ids", "matched_condition_ids"):
            referenced.update(g["policy_id"] for g in (case.get("w2_final") or {}).get(field, []))
        referenced.update(((case.get("w3_result") or {}).get("policy") or {}).get("policy_ids", []))
        referenced.update(
            ((case.get("w3_operations_result") or {}).get("policy") or {}).get("policy_ids", [])
        )
        selected = (case.get("w3_trade_decision") or {}).get("policy_trade")
        if selected:
            referenced.add(selected["policy_id"])
        if policy_id not in referenced:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        pin = case.get("version_pin") or {}
        activation = store.get(
            "activation_revision", ticker, pin.get("activation_revision_id", ""), view["seq"]
        )
        if not activation or activation["policy_set"]["policy_set_version"] != pin.get(
            "policy_set_version"
        ):
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 404)
        with store.connect() as db:
            row = db.execute(
                "SELECT payload FROM " + evidence.relation("policy_detail") +
                " WHERE kind='policy_detail' AND ticker=? AND parent=? "
                "AND json_extract(payload,'$.summary.policy_id')=? AND valid_from<=? "
                "AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                (
                    ticker,
                    activation["policy_set"]["artifact_id"],
                    policy_id,
                    view["seq"],
                    view["seq"],
                ),
            ).fetchone()
        if not row:
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 404)
        return app.state.respond(
            request,
            "PolicyDetail",
            json.loads(row[0]),
            view_id=args["view_id"],
            read_seq=view["seq"],
        )

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
                "w1_extraction",
                "w2_alignment",
                "w2_alignment_status",
                "w2_alignment_error",
                "w2_contract_version",
                "first_round_tags",
                "orchestration_revision",
                "w2_final",
                "w2_round1",
                "w2_skipped",
                "w3_result",
                "w3_trade_decision",
                "w3_operations_result",
                "w3_operations_status",
                "w3_operations_error",
                "frozen_inputs.policy_watch_followup",
                "error_code",
                "error_message",
                "updated_at",
                "frozen_inputs.w3_execution_state",
                "status",
            ],
        )
        if not case or not summary:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        from doxagent.v2_read.trade_outcomes import legacy_case_summary

        summary = legacy_case_summary(store, ticker, summary, seq)
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

        def partial(
            value,
            reference_result=None,
            policy_result=None,
            candidate_result=None,
            related_result=None,
        ):
            result = resource(value)
            refs = reference_gaps.get(id(reference_result), [])
            policies = reference_gaps.get(id(policy_result), [])
            candidates = reference_gaps.get(id(candidate_result), [])
            related = reference_gaps.get(id(related_result), [])
            if value is not None and (refs or policies or candidates or related):
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
                            "SELECT id FROM " + evidence.relation("native:runtime_v2_candidates") +
                            " WHERE kind='native:runtime_v2_candidates' "
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
                            "SELECT id "
                            "FROM " + evidence.relation("fact") + " "
                            "WHERE kind='fact' AND ticker=? AND parent=? "
                            "AND json_extract(payload,'$.fact.fact_id')=? "
                            "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                            (ticker, parent, fact_id, seq, seq),
                        ).fetchone()
                        fields = (
                            evidence.fields('fact', row[0], ['fact.proposition']) if row else None
                        )
                        value = fields.get('fact.proposition') if fields else None
                        facts.append(
                            {
                                "fact_id": fact_id,
                                "proposition": available(value) if value else missing(),
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

        def policies(result: dict, attribution_key="matched_condition_ids") -> list[dict]:
            selected = []
            attribution = {
                p["policy_id"]: p["condition_ids"] for p in result.get(attribution_key, [])
            }
            with store.connect() as db:
                for identity in result.get("policy_ids", []):
                    row = db.execute(
                        "SELECT json_extract(payload,'$.summary.title') AS title, "
                        "json_extract(payload,'$.summary.policy_activation_revision') AS revision, "
                        "json_extract(payload,'$.summary.policy_revision_id') AS content_revision "
                        "FROM " + evidence.relation("policy_detail") +
                        " WHERE kind='policy_detail' AND ticker=? "
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
                            **(
                                {"policy_revision_id": row["content_revision"]}
                                if row["revision"] is None
                                else {}
                            ),
                            "title": available(row["title"]) if row["title"] else missing(),
                            "condition_ids": attribution.get(identity, []),
                        }
                    )
            return selected

        w1, w2, w3 = case.get("w1_final"), case.get("w2_final"), case.get("w3_result")
        w1 = w1 or (evidence.latest_success("W1", "R2") or {}).get("output")
        w2 = w2 or (evidence.latest_success("W2", "R2") or {}).get("output")
        modern = (case.get("orchestration_revision") or 1) >= 2
        if w2:
            w2 = normalize_w2_result(w2)
        alignment = case.get("w2_alignment") or (evidence.latest_success("W2", "R3") or {}).get(
            "output"
        )
        eligible = bool(
            w1 and w2 and w1["result"] == "NEW" and (w2["policy_hit"] or w2["policy_related"])
        )
        no_r3 = (
            "LEGACY_CONTRACT"
            if not modern
            else "NOVELTY_NOT_NEW"
            if w1 and w1["result"] != "NEW"
            else "NO_POLICY_HIT_OR_RELATED"
            if w2 and not eligible
            else None
        )
        alignment_status = (
            "NOT_RECORDED"
            if not modern
            else "SUCCEEDED"
            if alignment is not None
            else "NOT_EXECUTED"
            if no_r3
            else "FAILED"
            if case["status"] in {"FAILED", "UNAVAILABLE"}
            else "RUNNING"
            if any(g["node"] == "W2" and g["round"] == "R3" for g in groups)
            else "PENDING"
        )
        from doxagent.persistent_runtime_v2.w2_alignment import alignment_result, assessment

        if case.get("w2_alignment_status") == "UNAVAILABLE":
            alignment_status = "UNAVAILABLE"
        flags = (
            alignment_flags(
                assessment(w2),
                alignment_result(alignment),
            )
            if alignment is not None and w2
            else (False, False)
        )
        alignment_view = {
            "status": alignment_status,
            "error": case.get("w2_alignment_error"),
            "not_executed_reason": no_r3
            if alignment_status in {"NOT_RECORDED", "NOT_EXECUTED"}
            else None,
            "policy_fact_novelty": available("NEW" if any(flags) else "OLD")
            if alignment_status == "SUCCEEDED"
            else missing(
                "NOT_APPLICABLE"
                if no_r3 and modern
                else "NOT_RECORDED"
                if not modern
                else "NOT_PRODUCED"
            ),
            "condition_results": (alignment or {}).get("condition_results", []),
            "related_policy_results": (alignment or {}).get("related_policy_results", []),
        }

        def rounds(node, skipped=False, empty_recall=False):
            result = evidence.rounds(groups, node, skipped, empty_recall)
            if not any(r["round"] == "R3" for r in result):
                reason = (
                    no_r3
                    if node == "W2"
                    else "DELTA_OWNED_BY_W3"
                    if summary["initial_route"] == "W3" and summary["runtime_mode"] != "CLOSED"
                    else "NO_DELTA_CAPTURE_REQUIRED"
                    if w1 and w1["result"] == "OLD"
                    else None
                )
                result.append({"round": "R3", "attempt_count": 0, "not_executed_reason": reason})
            return result

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
                "rounds": rounds("W1"),
                "extraction": resource(
                    {
                        "capture_mode": "NEW_CAPTURE"
                        if w1 and w1["result"] == "NEW"
                        else "AMBIGUOUS_CAPTURE",
                        "candidate_count": len(case["w1_extraction"]["candidates"]),
                    }
                    if case.get("w1_extraction")
                    else None
                ),
                "timing": interval("W1"),
                "attempts": page("attempt", "W1"),
                "reasoning": resource(reasons.get("w1")),
            }
            if w1 or any(g["node"] == "W1" for g in groups)
            else None
        )
        related_result = {
            "policy_ids": (w2 or {}).get(
                "related_policy_ids",
                list(
                    dict.fromkeys(
                        g["policy_id"] for g in (w2 or {}).get("matched_condition_ids", [])
                    )
                ),
            ),
            "matched_condition_ids": (w2 or {}).get("matched_condition_ids", []),
        }
        related_policies = policies(related_result) if w2 and modern else []
        second = (
            {
                "skipped": bool(case["w2_skipped"]),
                "reasoning_stage": (
                    "R1" if not recalled.get("candidate_policy_ids") else "R2" if w2 else None
                )
                if recalled is not None
                else None,
                "policy_hit": available(w2["policy_hit"]) if w2 else missing(),
                "policy_related": available(w2["policy_related"]) if w2 and modern else missing(),
                "related_policies": related_policies,
                "unresolved_related_policy_ids": reference_gaps.get(id(related_result), []),
                "novel_fact_alignment": alignment_view,
                "confidence": available(w2["confidence"]) if w2 else missing(),
                "policies": policies(w2, "hit_condition_ids") if w2 else [],
                **({"candidate_policies": candidate_policies} if recalled is not None else {}),
                "unresolved_candidate_policy_ids": reference_gaps.get(id(candidates_result), []),
                "rounds": rounds(
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
        if case.get("w3_trade_decision"):
            decision = case["w3_trade_decision"]
            ops = case.get("w3_operations_result") or {}
            expert = decision.get("expert_trade") or {}
            if third is None:
                route = evidence.fields("native:runtime_v2_w3_cases", case_id, ["mode"])
                third = dict(mode=(route or {}).get("mode", "UNCOVERED_NEW"))
            third.update(
                status="RESOLVED",
                decision=decision,
                operations_result=case.get("w3_operations_result"),
                operations_status=case.get("w3_operations_status"),
                operations_error=case.get("w3_operations_error"),
                trade_timing=evidence.interval(
                    [g for g in groups if g.get("phase") == "trade_decision"], "W3"
                ),
                operations_timing=evidence.interval(
                    [g for g in groups if g.get("phase") == "post_decision_operations"], "W3"
                ),
                novelty=available(ops["novelty"]["result"]) if ops else missing(),
                references=references(ops["novelty"]) if ops else [],
                policy_hit=available(bool(ops["policy"]["policy_ids"])) if ops else missing(),
                policies=policies(ops["policy"]) if ops else [],
                expert_trade_evaluated=available(decision["decision"] == "EXPERT_TRADE"),
                expert_trade=available(decision["decision"] == "EXPERT_TRADE"),
                direction=available(expert["direction"]) if expert else missing(),
                prior_expectation=available(expert["prior_expectation"]) if expert else missing(),
                expectation_delta=available(expert["expectation_delta"]) if expert else missing(),
                timing=interval("W3"),
                attempts=page("attempt", "W3"),
                reasoning={
                    k: resource(reasons.get("w3_" + k))
                    for k in ("novelty", "policy", "expert_trade")
                },
            )
        with store.connect() as db:
            counts = {
                kind: db.execute(
                    "SELECT COUNT(*) FROM " + evidence.members + " WHERE kind=? AND ticker=? "
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
                    "FROM " + evidence.members + " WHERE kind='attempt' AND ticker=? AND parent=? "
                    "AND json_extract(payload,'$.error') IS NOT NULL "
                    "AND json_extract(payload,'$.timing.completed_at.value') IS NOT NULL "
                    "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) "
                    "ORDER BY sort_key DESC,id DESC LIMIT 20",
                    (ticker, case_id, seq, seq),
                )
            ]
        if case.get("error_code"):
            failures.append(
                {
                    "failure_id": case_id + ":terminal",
                    "stage": case.get("frozen_inputs", {})
                    .get("w3_execution_state", {})
                    .get("phase", "CASE"),
                    "status": summary.get("w3_status") or case["status"],
                    "error": {
                        **ApiFailure(
                            case["error_code"],
                            retryable=summary.get("w3_status") == "FAILED_RETRYABLE",
                        ).payload(case_id)["error"],
                        "message": case.get("error_message") or case["error_code"],
                    },
                    "occurred_at": summary["completed_at"]["value"] or case["updated_at"],
                }
            )
        if case.get("w3_operations_error"):
            failures.append(
                dict(
                    failure_id=case_id + ":operations",
                    stage="post_decision_operations",
                    status=case.get("w3_operations_status") or "FAILED",
                    error={
                        **ApiFailure("w3_operations_failed").payload(case_id)["error"],
                        "message": case["w3_operations_error"],
                    },
                    occurred_at=case["updated_at"],
                )
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
            "w2": partial(
                second,
                policy_result=w2,
                candidate_result=candidates_result,
                related_result=related_result,
            ),
            "w3": partial(third, (w3 or {}).get("novelty"), (w3 or {}).get("policy")),
            "messages": page("message"),
            "failures": failures,
            "candidate_count": available(counts["candidate"]),
            "execution_count": available(counts["execution"]),
            "results": summary["results"],
        }
        if (case.get("orchestration_revision") or 1) >= 3:
            receipt = store.get(
                "native:runtime_values", ticker, "policy_watch_applied:" + case_id, seq
            )
            invalid = store.get(
                "native:runtime_values", ticker, "policy_watch_validation:" + case_id, seq
            )
            if invalid:
                receipt = {**(receipt or {}), "validation_issues": invalid["issues"]}
            value["policy_watch"] = dict(
                followup=case.get("frozen_inputs.policy_watch_followup") or [],
                actions=(w3 or {}).get("watch_actions", []),
                receipt=receipt,
            )
            value["policy_execution"] = (w3 or {}).get("policy_execution")
        if case.get("w3_trade_decision"):
            state = store.get(
                "native:runtime_values", ticker, "w3_operations_input:" + case_id, seq
            )
            state = (
                state
                or store.get("native:runtime_values", ticker, "w3_global_input:" + case_id, seq)
                or {}
            )
            value["w3_state"] = dict(
                watch=state.get("watch"),
                pins=state.get("pins"),
                receipt=store.get(
                    "native:runtime_values", ticker, "w3_operations_applied:" + case_id, seq
                ),
            )
            value["policy_watch"]["actions"] = (case.get("w3_operations_result") or {}).get(
                "watch_actions", []
            )
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
