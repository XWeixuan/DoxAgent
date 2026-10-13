"""Read-only Bus status joins, with no inference from cumulative poll counters."""

from __future__ import annotations

from datetime import datetime
import json

from fastapi import FastAPI, Request

from doxagent.message_bus_v2.schema import TickerSourceBinding
from doxagent.v2_read.repository import instant

from .dto import available, missing, validate
from .errors import ApiFailure


def source_status(store, binding, seq, view):
    native = TickerSourceBinding.model_validate(binding)
    source = store.get("native:source_definitions", "", native.source_id, seq)
    if source is None:
        raise ApiFailure("SOURCE_GAP", 503, retryable=True)
    poll = store.get("native:poll_states", native.ticker, native.binding_id, seq) or {}
    ticker = store.get("ticker", native.ticker, native.ticker, seq) or {}
    status = poll.get("status", "never_polled")
    enabled = native.enabled and native.polling.enabled and source["enabled"]
    health = (
        "EXCLUDED"
        if not enabled or status in {"never_polled", "disabled"}
        else "NORMAL"
        if status == "succeeded"
        else "ABNORMAL"
    )
    due = poll.get("target_due_at")
    now = datetime.fromisoformat(view["as_of"])
    countdown = "SCHEDULED" if due else "UNKNOWN"
    if not enabled:
        countdown = "DISABLED"
    elif ticker.get("run_state") != "RUNNING":
        countdown = "PAUSED"
    elif not native.polling.active_at(now):
        countdown = "WINDOW_CLOSED"
    elif view["wire"]["clock"]["session"]["value"] == "CLOSED_SLEEP":
        countdown = "CLOSED_SLEEP"

    def observed(key, transform=lambda value: value):
        return available(transform(poll[key])) if poll.get(key) is not None else missing()

    return validate(
        "SourceStatus",
        {
            "source": {
                "source_id": native.source_id,
                "binding_id": native.binding_id,
                "name": source["display_name"],
                "kind": source["kind"],
            },
            "binding_version": native.version,
            "source_version": source["version"],
            "publication_mode": native.streaming.publication_mode.value,
            "enabled": native.enabled,
            "global_enabled": source["enabled"],
            "poll_status": status,
            "health_group": health,
            "last_success_at": observed(
                "last_success_at", lambda value: instant(datetime.fromisoformat(value))
            ),
            "last_published_count": observed("last_standard_revision_count"),
            "last_poll_latency_seconds": observed("last_latency_ms", lambda value: value / 1000),
            "current_error": ApiFailure(poll["last_error_code"]).payload(native.binding_id)["error"]
            if poll.get("last_error_code")
            else None,
            "target_interval_seconds": native.polling.target_interval_seconds,
            "active_windows": [
                window.model_dump(mode="json") for window in native.polling.active_windows
            ],
            "next_target_at": available(instant(datetime.fromisoformat(due)))
            if due and countdown == "SCHEDULED"
            else missing(),
            "countdown_state": countdown,
        },
    )


def aggregates_many(store, tickers, seq):
    """Bounded scalar reads avoid materializing all poll histories for a JOIN."""
    columns = "ticker,id,source_id,json_extract(payload,'$.enabled'),json_extract(payload,'$.polling.enabled')"
    where = "kind='native:ticker_source_bindings' AND ticker IN (SELECT value FROM json_each(?)) AND valid_from<=? AND json_extract(payload,'$.tombstoned_at') IS NULL"
    with store.connect() as db:
        bindings = db.execute("SELECT " + columns + " FROM object_current WHERE " + where +
                              " UNION ALL SELECT " + columns + " FROM objects WHERE " + where + " AND valid_to>?",
                              (json.dumps(tickers), seq, json.dumps(tickers), seq, seq)).fetchall()
    source_ids = sorted({("", row[2]) for row in bindings})
    poll_ids = [(row[0], row[1]) for row in bindings]
    sources, polls = {}, {}
    for offset in range(0, len(source_ids), 500):
        sources.update({row[1]: row[2] for row in store.source_scalars("native:source_definitions", source_ids[offset:offset + 500], seq)})
    for offset in range(0, len(poll_ids), 500):
        polls.update({(row[0], row[1]): (row[2], row[3]) for row in store.source_scalars("native:poll_states", poll_ids[offset:offset + 500], seq)})
    totals = {ticker: [0, 0, 0, []] for ticker in tickers}
    for ticker, identity, source, enabled, poll_enabled in bindings:
        value = totals[ticker]
        value[0] += source not in sources
        if not (enabled and poll_enabled and sources.get(source)):
            continue
        status, latency = polls.get((ticker, identity), ("never_polled", None))
        value[1] += status == "succeeded"
        value[2] += status in {"partial", "failed"}
        if status in {"succeeded", "partial", "failed"} and latency is not None:
            value[3].append(latency)
    return {ticker: (
        {"normal": missing("SOURCE_GAP") if gaps else available(normal),
         "abnormal": missing("SOURCE_GAP") if gaps else available(abnormal)},
        available(sum(latencies) / len(latencies) / 1000) if latencies else missing(),
        len(latencies),
    ) for ticker, (gaps, normal, abnormal, latencies) in totals.items()}


def aggregates(store, ticker, seq):
    return aggregates_many(store, [ticker], seq)[ticker]


def install(app: FastAPI):
    prefix = "/api/doxagent/v2/tickers/{ticker}/message-bus"
    store, views = app.state.store, app.state.views

    def context(request, ticker, args):
        view = views.get(request.state.principal.user_id, args.get("view_id", ""), ticker)
        if view["wire"]["page"] != "MESSAGE_BUS":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        return view

    @app.get(prefix + "/metrics")
    async def metrics(ticker: str, request: Request):
        from .bus_metrics import metrics as calculate
        from .streaming import MessageStreams

        args = app.state.query(request, {"view_id", "source_kind", "source_id", "route", "q"})
        view = context(request, ticker, args)
        filters = MessageStreams(store, views).filters(args, view)
        data = calculate(store, ticker, view, filters)
        return app.state.respond(request, "BusMetrics", data, view_id=args["view_id"])

    @app.get(prefix + "/sources")
    async def sources(ticker: str, request: Request):
        args = app.state.query(request, {"view_id", "limit", "cursor"})
        view = context(request, ticker, args)
        page = views.page(
            request.state.principal.user_id,
            "native:ticker_source_bindings",
            ticker,
            view=view,
            view_id=args["view_id"],
            live_bindings=True,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        page["items"] = [
            source_status(store, binding, view["seq"], view) for binding in page["items"]
        ]
        return app.state.respond(request, "SourceStatusPage", page, view_id=args["view_id"])

    @app.get(prefix + "/status")
    async def status(ticker: str, request: Request):
        args = app.state.query(request, {"view_id"})
        view = context(request, ticker, args)
        state = store.get("ticker", ticker, ticker, view["seq"])
        native = store.get("native:ticker_monitoring_states", ticker, ticker, view["seq"]) or {}
        started = native.get("continuous_run_started_at")
        counts, latency, samples = aggregates(store, ticker, view["seq"])
        running = state["run_state"] == "RUNNING" and native.get("status") == "running"
        data = {
            "run_state": state["run_state"],
            "source_counts": counts,
            "continuous_run_started_at": available(instant(datetime.fromisoformat(started)))
            if running and started
            else missing(),
            "continuous_run_seconds": available(
                max(
                    0,
                    (
                        datetime.fromisoformat(view["as_of"]) - datetime.fromisoformat(started)
                    ).total_seconds(),
                )
            )
            if running and started
            else missing(),
            "average_poll_latency_seconds": latency,
            "latency_sample_count": samples,
        }
        return app.state.respond(request, "BusStatus", data, view_id=args["view_id"])
