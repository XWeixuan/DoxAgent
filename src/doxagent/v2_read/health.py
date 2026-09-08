"""Ticker health follows acknowledged consumers and observed source failures."""


def project(store, ticker, records):
    from .projectors import state_wire

    def latest(kind, identity):
        return next(
            (
                r["data"]
                for r in reversed(records)
                if r["kind"] == kind and r["ticker"] == ticker and r["id"] == identity
            ),
            store.get(kind, ticker, identity),
        )

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
            import json

            with store.connect() as db:
                polls = {
                    r[0]: json.loads(r[1])
                    for r in db.execute(
                        "SELECT id,payload FROM objects WHERE kind='native:poll_states' "
                        "AND ticker=? AND valid_to IS NULL",
                        (ticker,),
                    )
                }
            for record in records:
                if record["kind"] == "native:poll_states" and record["ticker"] == ticker:
                    polls[record["id"]] = record["data"]
            failed = False
            for identity, poll in polls.items():
                binding = latest("native:ticker_source_bindings", identity)
                if not poll or not binding or not binding.get("enabled"):
                    continue
                source = store.get("native:source_definitions", "", binding["source_id"])
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
