"""Ticker health follows acknowledged consumers and observed source failures."""


def project(store, ticker, records):
    import json

    from .projectors import state_wire

    dependencies = {"native:v2_ticker_control", "native:v2_control_ack", "native:ticker_monitoring_states",
                    "native:poll_states", "native:ticker_source_bindings", "native:source_definitions"}
    if not any(record["kind"] in dependencies for record in records):
        return

    # One bounded current-state snapshot, not a new connection for every ack,
    # binding and source on every poll event. Incoming deletions must win too.
    with store.connect() as db:
        current = {
            (r[0], r[1], r[2]): json.loads(r[3])
            for r in db.execute(
                "SELECT kind,ticker,id,payload FROM object_current "
                "WHERE ticker=? AND kind IN (SELECT value FROM json_each(?)) "
                "UNION ALL SELECT kind,ticker,id,payload FROM object_current "
                "WHERE ticker='' AND kind='native:source_definitions'",
                (ticker, json.dumps(sorted(dependencies - {"native:source_definitions"}))),
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
            polls = {
                identity: value for (kind, owner, identity), value in current.items()
                if kind == "native:poll_states" and owner == ticker
            }
            failed = False
            for identity, poll in polls.items():
                binding = latest("native:ticker_source_bindings", identity)
                if not poll or not binding or not binding.get("enabled"):
                    continue
                source = current.get(("native:source_definitions", "", binding["source_id"]))
                if source and source.get("enabled") and poll["status"] in {"partial", "failed"}:
                    failed = True
                    break
            wire.update(
                health="DEGRADED" if failed else "NORMAL",
                health_reasons=["SOURCE_POLL_FAILURE"] if failed else [],
            )
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
