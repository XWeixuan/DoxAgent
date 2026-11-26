"""Ticker health reflects its acknowledged runtime, not individual source polls."""


def project(store, ticker, records):
    import json

    from .projectors import state_wire

    dependencies = {
        "native:v2_ticker_control", "native:v2_control_ack", "native:ticker_monitoring_states"
    }
    if not any(record["kind"] in dependencies for record in records):
        return

    # One small lifecycle snapshot. Source failures stay in source monitoring.
    with store.connect() as db:
        current = {
            (r[0], r[1], r[2]): json.loads(r[3])
            for r in db.execute(
                "SELECT kind,ticker,id,payload FROM object_current "
                "WHERE ticker=? AND kind IN (SELECT value FROM json_each(?))",
                (ticker, json.dumps(sorted(dependencies))),
            )
        }
    current.update(
        ((r["kind"], r["ticker"], r["id"]), r["data"])
        for r in records if r["kind"] in dependencies
    )

    def latest(kind, identity):
        return current.get((kind, ticker, identity))

    state = latest("native:v2_ticker_control", ticker)
    if not state:
        return
    wire = state_wire(state)
    if state["analysis_allowed"] and state["activation_id"]:
        acks = [
            latest("native:v2_control_ack", f"{state['epoch']}:{role}")
            for role in ("initializer", "bus", "runtime")
        ]
        bus = latest("native:ticker_monitoring_states", ticker)
        if all(acks) and bus and bus["status"] == "running":
            wire.update(health="NORMAL", health_reasons=[])
    if state.get("initialization_failed") and not state["activation_id"]:
        wire.update(health="BLOCKED", health_reasons=["NO_ACTIVE_REVISION"])
    records[:] = [
        r
        for r in records
        if not (
            (r["kind"] == "ticker" and r["ticker"] == ticker)
            or (r["kind"] == "navigation" and r["id"] == ticker)
        )
    ]
    records.append({"kind": "ticker", "ticker": ticker, "id": ticker, "data": wire})
    records.append(
        {
            "kind": "navigation",
            "ticker": "",
            "id": ticker,
            "data": {
                key: wire[key]
                for key in ("ticker", "run_state", "health", "initialization_incomplete", "removed")
            }
            if not wire["removed"] and not wire["initialization_incomplete"]
            else None,
        }
    )
