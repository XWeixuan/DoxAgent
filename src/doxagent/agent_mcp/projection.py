"""Explicit frontend display allowlists. Never serialize native/wire DTOs wholesale."""

from __future__ import annotations


def pick(value, *keys):
    return {key: value[key] for key in keys if key in value} if isinstance(value, dict) else {}


def quality(value):
    return pick(value, "state", "reasons")


def metric(value, *, comparison=True):
    out = pick(value, "metric_id", "unit", "current", "provisional")
    if comparison:
        out.update(pick(value, "change_pct"))
    if "current_coverage" in value:
        out["current_coverage"] = quality(value["current_coverage"])
    return out


def metrics(value, *, comparison=True):
    return {key: metric(raw, comparison=comparison) for key, raw in value.items()}


def resource(value, projector):
    out = pick(value, "state", "reason")
    out["coverage"] = quality(value.get("coverage"))
    out["data"] = projector(value["data"]) if value.get("data") is not None else None
    return out


def case_row(value):
    out = pick(
        value,
        "case_id",
        "title",
        "source_name",
        "status",
        "received_at",
        "completed_at",
        "duration_seconds",
        "first_round_tags",
        "w3_decision",
        "w3_operations_status",
        "result_settled",
        "runtime_tags",
        "w3_model_seconds",
        "non_model_seconds",
    )
    out["source"] = pick(value.get("source"), "source_id", "name")
    out["results"] = [
        r for r in value.get("results", []) if r not in {"TRADE_EXECUTION", "TRADE_NOT_EXECUTED"}
    ]
    out["trade"] = pick(value.get("trade"), "state", "reason_codes")
    return out


def ticker_row(value):
    out = pick(value, "last_standard_message_at", "source_counts")
    out["state"] = pick(
        value["state"],
        "ticker",
        "requested_mode",
        "effective_mode",
        "run_state",
        "health",
        "health_reasons",
    )
    out["metrics"] = metrics(value["metrics"], comparison=False)
    if value.get("initialization"):

        def initialization(raw):
            data = pick(raw, "status", "failure")
            data["steps"] = [
                pick(
                    step,
                    "step_key",
                    "status",
                    "first_started_at",
                    "duration_seconds",
                    "quality_annotations",
                )
                for step in raw.get("steps", [])
            ]
            if isinstance(data.get("failure"), dict):
                data["failure"] = pick(data["failure"], "message")
            return data

        out["initialization"] = resource(value["initialization"], initialization)
    return out


def bus_kpis(value):
    body = value["metrics"].get("new_article_body")
    return {
        "metrics": {
            "published_revisions": metric(value["metrics"]["published_revisions"]),
            "new_article_body": pick(body, "ratio", "full", "total", "pending"),
        },
        **pick(value, "counts", "mean_e2e_seconds", "e2e_samples", "coverage",
               "mean_capture_seconds", "capture_samples"),
    }


def source_row(value):
    out = pick(
        value,
        "publication_mode",
        "poll_status",
        "target_interval_seconds",
        "countdown_state",
        "next_target_at",
        "last_success_at",
        "published_today",
    )
    out["source"] = pick(value["source"], "source_id", "binding_id", "name", "kind")
    out["current_error"] = (
        pick(value["current_error"], "code", "message") if value.get("current_error") else None
    )
    out["recent"] = pick(
        value.get("recent"),
        "mean_e2e_seconds",
        "p95_e2e_seconds",
        "e2e_samples",
        "refresh_seconds",
        "body_ratio",
        "body_full",
        "body_total",
    )
    return out


def catalog_row(value):
    out = pick(value, "source_id", "name", "short_name", "description", "enabled",
               "delivery_mode", "acquisition_mode", "configured_intervals_seconds")
    out["poll_counts"] = pick(value.get("poll_counts"), "succeeded", "partial", "failed", "unknown")
    out["state_counts"] = pick(value.get("state_counts"), "running", "sleeping", "disabled",
                               "abnormal", "unknown")
    raw = value.get("classification")
    out["classification"] = None if raw is None else {
        **pick(raw, "applicability", "sector_ids"),
        **{k: pick(raw.get(k), "primary", "fallback") for k in ("listing", "body")},
    }
    for name, fields in (("entry_points", ("url", "label", "ticker")),
                         ("subscriptions", ("ticker", "binding_id", "enabled")),
                         ("errors", ("ticker", "code", "message"))):
        out[name] = [pick(row, *fields) for row in value.get(name, [])]
    out["metrics"] = pick(value.get("metrics"), "last_success_at", "published_today",
                          "mean_e2e_seconds", "p95_e2e_seconds", "e2e_samples",
                          "mean_capture_seconds", "capture_samples", "refresh_seconds",
                          "refresh_samples", "body_total", "body_full", "body_ratio",
                          "body_applicable", "article_samples")
    return out


def projection_clock(rows):
    return [pick(r, "source", "head", "checkpoint", "checked_at", "error", "historical_gaps",
                 "source_event_at", "oldest_pending_at") for r in rows]


def operations_row(value):
    return {
        "overview": ticker_row(value["overview"]),
        "period": value["period"],
        "runtime": resource(value["runtime"], lambda v: metrics(pick(
            v, "processed_cases", "hot_path_mean_seconds", "w3_mean_seconds",
            "new_cases", "old_cases", "hit_cases", "related_cases", "long_w3_cases",
            "non_model_mean_seconds", "executed_cases",
        ))),
        "bus": {**resource(value["bus"], bus_kpis), "meta": {"read_at": value["bus_read_at"]}},
    }


def cost_kpis(value):
    raw = value["totals"]
    totals = {
        key: metric(raw[key], comparison=False)
        for key in (
            "total_tokens",
            "total_cost_usd",
            "input_tokens",
            "input_cost_usd",
            "output_tokens",
            "output_cost_usd",
            "cached_input_tokens",
            "cache_hit_ratio",
        )
    }
    totals.update(
        pick(
            raw,
            "usage_known_attempts",
            "observed_attempts",
            "usage_unknown_attempts",
            "unpriced_attempts",
            "unknown_model_price_attempts",
            "reported_cost_attempts",
            "estimated_cost_attempts",
            "normalized_attempts",
        )
    )
    return {"totals": totals, "source_coverage": quality(value.get("source_coverage"))}


def attempts(value):
    out = pick(value, "node_id", "round", "phase", "attempt_number", "status")
    out["timing"] = pick(value.get("timing"), "wall_seconds")
    if value.get("node_id") == "W3":
        out.update(pick(value, "sdk_seconds"))
    return out


def message(value):
    return pick(value, "title", "body")


def candidate(value):
    return pick(
        value,
        "originating_node",
        "proposition",
        "assertion_state",
        "subject_time",
        "occurrence_date",
    )


def execution(value):
    out = pick(
        value,
        "direction",
        "intent_status",
        "execution_state",
        "entry_result",
        "intake_status",
        "filled_quantity",
        "filled_notional_usd",
        "first_fill_at",
        "entry_reason",
        "execution_reason_codes",
    )
    if value.get("execution_group"):
        out["execution_group"] = pick(
            value["execution_group"],
            "account_type",
            "state",
            "main_side",
            "main_asset",
            "main_quantity",
            "hedge_asset",
            "hedge_quantity",
            "hedge_result",
            "hedge_reason",
        )
    if value.get("exit_strategy"):
        out["exit_strategy"] = pick(
            value["exit_strategy"],
            "phase",
            "data_health",
            "pnl",
            "mfe",
            "atr",
            "stop_net",
            "confirmation",
            "remaining_quantity",
            "exit_reason",
        )
    return out


def page(value, projector):
    return {
        **pick(value, "limit", "has_more", "next_cursor", "snapshot_id"),
        "items": [projector(row) for row in value.get("items", [])],
    }


def event_ref(value):
    out = pick(value, "kind", "event_id", "title", "fact_ids", "provisional_proposition")
    if "facts" in value:
        out["facts"] = [pick(f, "fact_id", "proposition") for f in value["facts"]]
    return out


def policy_ref(value):
    return pick(value, "policy_id", "title", "condition_ids")


def judgment(value, node):
    out = pick(
        value,
        "novelty",
        "confidence",
        "skipped",
        "policy_hit",
        "policy_related",
        "status",
        "unresolved_reference_ids",
        "unresolved_policy_ids",
        "unresolved_related_policy_ids",
        "unresolved_candidate_policy_ids",
        "prior_expectation",
        "expectation_delta",
        "expert_trade",
        "direction",
    )
    for key in ("references",):
        if key in value:
            out[key] = [event_ref(ref) for ref in value[key]]
    for key in ("policies", "related_policies", "candidate_policies"):
        if key in value:
            out[key] = [policy_ref(ref) for ref in value[key]]
    if "fact_attributions" in value:
        out["fact_attributions"] = (
            [pick(f, "event_id", "fact_ids") for f in value["fact_attributions"]]
            if value["fact_attributions"] is not None
            else None
        )
    if "rounds" in value:
        out["rounds"] = [
            pick(r, "round", "attempt_count", "not_executed_reason") for r in value["rounds"]
        ]
    if value.get("extraction"):
        out["extraction"] = resource(value["extraction"], lambda r: pick(r, "candidate_count"))
    if value.get("novel_fact_alignment"):
        alignment = value["novel_fact_alignment"]
        data = pick(alignment, "status", "policy_fact_novelty", "error")
        data["condition_results"] = [
            pick(r, "condition_ids", "is_new") for r in alignment.get("condition_results", [])
        ]
        if "related_policy_results" in alignment:
            data["related_policy_results"] = [
                pick(r, "policy_id", "is_new") for r in alignment["related_policy_results"]
            ]
        out["novel_fact_alignment"] = data
    if "attempts" in value:
        out["attempts"] = page(value["attempts"], attempts)
    if "reasoning" in value:
        out["reasoning"] = (
            resource(value["reasoning"], lambda ref: ref)
            if node != "w3"
            else {
                key: resource(raw, lambda ref: ref)
                for key, raw in pick(value["reasoning"], "novelty", "policy").items()
            }
        )
        if node == "w3":
            raw = value["reasoning"].get("trade") or value["reasoning"].get("expert_trade")
            if raw:
                out["reasoning"]["trade"] = resource(raw, lambda ref: ref)
    if node == "w3":
        out.update(pick(value, "operations_status", "operations_error"))
        if value.get("decision"):
            for key in ("expert_trade", "direction", "status"):
                out.pop(key, None)
            decision = value["decision"]
            out["decision"] = pick(decision, "decision", "reason")
            if decision.get("policy_trade"):
                out["decision"]["policy_trade"] = pick(
                    decision["policy_trade"], "policy_id", "condition_id"
                )
            if decision.get("expert_trade"):
                out["decision"]["expert_trade"] = pick(decision["expert_trade"], "direction")
        for key in ("trade_timing", "operations_timing"):
            if value.get(key):
                out[key] = pick(value[key], "wall_seconds")
    return out


def watch_pin(value):
    out = {}
    raw_watch = value.get("policy_watch") or {}
    if raw_watch.get("followup") or raw_watch.get("actions"):
        raw = value["policy_watch"]
        out["policy_watch"] = {
            "followup": [
                pick(r, "policy_id", "reason", "expires_at") for r in raw.get("followup", [])
            ],
            "actions": [pick(r, "policy_id", "action", "reason") for r in raw.get("actions", [])],
            "receipt": raw.get("receipt"),  # This JSON is explicitly expandable in the UI.
        }
    if value.get("w3_state"):
        raw = value["w3_state"]
        out["w3_state"] = {
            "watch": {
                "items": [
                    pick(r, "policy_id", "reason", "expires_at")
                    for r in (raw.get("watch") or {}).get("items", [])
                ]
            },
            "pins": {
                "items": [
                    {
                        **pick(r, "pin_id", "reason", "expires_at"),
                        "message": pick(r.get("message"), "title", "source_name", "body"),
                    }
                    for r in (raw.get("pins") or {}).get("items", [])
                ]
            },
            "receipt": {
                key: [
                    pick(r, "policy_id", "pin_id", "action", "status")
                    for r in ((raw.get("receipt") or {}).get(key) or [])
                ]
                for key in ("watch", "pins")
            },
        }
    return out


def detail(value, part):
    if part in {"w1", "w2", "w3"}:
        return {part: resource(value[part], lambda r: judgment(r, part))}
    if part == "watch_pin":
        return watch_pin(value)
    out = {"summary": pick(value["summary"], "title", "first_round_tags", "source_name",
                           "runtime_tags", "w3_model_seconds")}
    out["summary"]["source"] = pick(value["summary"]["source"], "name", "kind")
    out["lifecycle_timing"] = pick(
        value.get("lifecycle_timing"),
        "elapsed_seconds",
        "settled",
        "invocation_union_seconds",
        "uncovered_seconds",
        "partial",
    )
    out["failures"] = [
        {**pick(f, "stage", "status"), "error": pick(f.get("error"), "message")}
        for f in value.get("failures", [])
    ]
    for node in ("w1", "w2", "w3"):
        if node in value:
            out.update(detail(value, node))
    out.update(watch_pin(value))
    if value.get("policy_execution"):
        w3 = (out.get("w3") or {}).get("data") or {}
        if not w3.get("decision"):
            w3.pop("expert_trade", None)
            w3.pop("direction", None)
        out["policy_execution"] = {
            "selected_condition": pick(
                value["policy_execution"].get("selected_condition"), "policy_id", "condition_id"
            ),
            **pick(value["policy_execution"], "reason"),
        }
    out["messages"] = page(value.get("messages", {}), message)
    return out
