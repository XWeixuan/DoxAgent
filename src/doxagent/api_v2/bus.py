"""Read-only Bus status joins, with no inference from cumulative poll counters."""

from __future__ import annotations

from datetime import datetime

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


def aggregates(store, ticker, seq):
    with store.connect() as db:
        row = db.execute(
            """
            WITH scoped AS (
                SELECT s.payload AS source,
                    json_extract(b.payload,'$.enabled')
                        AND json_extract(b.payload,'$.polling.enabled')
                        AND json_extract(s.payload,'$.enabled') AS enabled,
                    coalesce(json_extract(p.payload,'$.status'),'never_polled') AS status,
                    json_extract(p.payload,'$.last_latency_ms') AS latency
                FROM objects b LEFT JOIN objects s
                    ON s.kind='native:source_definitions' AND s.ticker='' AND s.id=b.source_id
                    AND s.valid_from<=? AND (s.valid_to IS NULL OR s.valid_to>?)
                LEFT JOIN objects p ON p.kind='native:poll_states'
                    AND p.ticker=b.ticker AND p.id=b.id
                    AND p.valid_from<=? AND (p.valid_to IS NULL OR p.valid_to>?)
                WHERE b.kind='native:ticker_source_bindings' AND b.ticker=?
                    AND b.valid_from<=? AND (b.valid_to IS NULL OR b.valid_to>?)
                    AND json_extract(b.payload,'$.tombstoned_at') IS NULL
            ) SELECT coalesce(sum(source IS NULL),0),
                coalesce(sum(enabled AND status='succeeded'),0),
                coalesce(sum(enabled AND status IN ('partial','failed')),0),
                avg(CASE WHEN enabled AND status IN ('succeeded','partial','failed')
                    THEN latency END),
                count(CASE WHEN enabled AND status IN ('succeeded','partial','failed')
                    THEN latency END)
            FROM scoped
        """,
            (seq, seq, seq, seq, ticker, seq, seq),
        ).fetchone()
    counts = {
        "normal": available(row[1]) if not row[0] else missing("SOURCE_GAP"),
        "abnormal": available(row[2]) if not row[0] else missing("SOURCE_GAP"),
    }
    return counts, available(row[3] / 1000) if row[3] is not None else missing(), row[4]


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
