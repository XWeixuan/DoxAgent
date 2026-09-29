"""Formal intent and effective ENTRY fill determine distinct graph outcomes."""

from doxagent.v2_read.graph import project as graph_project
from doxagent.v2_read.maintenance import reproject_trade_outcomes
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.trade_outcomes import classify_execution, rollup_case_trade


def value(raw=None):
    return {"state": "AVAILABLE" if raw is not None else "NOT_PRODUCED", "value": raw,
            "reason": None if raw is not None else "NOT_RECORDED"}


def execution(identity="intent-1", *, status="READY", intake="NOT_RECEIVED", result=None, quantity="0"):
    return {
        "execution_id": identity, "intent_id": identity, "case_id": "case-1",
        "intent_status": status, "intake_status": intake, "entry_result": result,
        "entry_reason": None, "filled_quantity": value(quantity),
        "triggered_at": value("2026-09-29T10:00:00Z"), "first_fill_at": value(None),
    }


def case():
    return {
        "case_id": "case-1", "revision": 1, "semantic_day": "2026-09-29",
        "runtime_mode": "REALTIME", "first_round_shape": "PARALLEL", "status": "COMPLETED",
        "technical_status": "OK", "title": value("News"),
        "source": {"source_id": "news", "binding_id": "MU:news", "name": "News", "kind": "api"},
        "received_at": "2026-09-29T09:00:00Z", "completed_at": value(None),
        "duration_seconds": value(None), "initial_route": "TRADE", "resolved_route": None,
        "w3_status": None, "final_novelty": value("NEW"), "final_policy_hit": value(True),
        "results": ["EVENT_DISCOVERY"], "result_settled": True,
        "trade_disposition": "READY", "stream_item_id": "stream-1", "member_count": 1,
    }


def test_trade_state_priority_and_distinct_results():
    assert classify_execution(execution())[0] == "PENDING"
    assert classify_execution(execution(status="UNKNOWN", intake="UNKNOWN"))[0] == "UNKNOWN"
    assert classify_execution(execution(status="DUPLICATE_POLICY"))[0] == "NOT_EXECUTED"
    assert classify_execution(execution(result="FAILED"))[0] == "NOT_EXECUTED"
    assert classify_execution(execution(result="FILLED", quantity="0"))[0] == "UNKNOWN"
    assert classify_execution(execution(result="FAILED", quantity="2"))[0] == "EXECUTED"
    pending = rollup_case_trade(case(), [execution()])
    assert pending["results"] == ["EVENT_DISCOVERY", "TRADE_INTENT"]
    assert pending["result_settled"] is False
    executed = rollup_case_trade(case(), [execution(quantity="1"), execution("intent-2", result="FAILED")])
    assert executed["trade"]["intent_count"] == 2
    assert "TRADE_EXECUTION" in executed["results"]
    assert "TRADE_NOT_EXECUTED" not in executed["results"]
    assert rollup_case_trade(case(), [execution(status="OUTPUT_RECORDED")])["result_settled"] is True
    assert rollup_case_trade(case(), [])["trade"]["state"] == "NOT_APPLICABLE"


def test_graph_trade_execution_only_follows_intent_and_retracts(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = rollup_case_trade(case(), [execution(quantity="1")])
    records, counts = graph_project(store, "MU", summary, [])
    edges = next(item["data"]["edges"] for item in records if item["kind"] == "graph_case")
    assert ("TRADE_INTENT", "TRADE_EXECUTION") in edges
    assert ("W2", "TRADE_EXECUTION") not in edges
    store.ingest("test", "graph", [*records, {"kind": "case", "ticker": "MU", "id": "case-1", "data": summary}], contributions=counts)
    corrected = rollup_case_trade(summary, [execution(result="FILLED", quantity="0")])
    records, counts = graph_project(store, "MU", corrected, [])
    assert any(item["metric"] == "graph_edges" and item["dimensions"] == {"from": "TRADE_INTENT", "to": "TRADE_EXECUTION"} and item["value"] == 0 for item in counts)


def test_reprojection_updates_old_edges_idempotently(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    old = case()
    old["results"] = ["TRADE_EXECUTION"]
    store.ingest("test", "old", [
        {"kind": "case", "ticker": "MU", "id": "case-1", "data": old},
        {"kind": "execution", "ticker": "MU", "id": "intent-1", "parent": "case-1", "data": execution(quantity="1")},
        {"kind": "graph_case", "ticker": "MU", "id": "case-1", "data": {"nodes": ["SOURCE", "W2", "TRADE_EXECUTION"], "edges": [["SOURCE", "W2"], ["W2", "TRADE_EXECUTION"]]}},
        {"kind": "graph_member", "ticker": "MU", "id": "case-1:TRADE_EXECUTION", "parent": "TRADE_EXECUTION", "day": "2026-09-29", "data": old},
    ])
    assert reproject_trade_outcomes(store, ticker="MU", limit=1)["processed"] == 1
    updated = store.get("case", "MU", "case-1")
    assert updated["trade"]["state"] == "EXECUTED"
    assert ["W2", "TRADE_EXECUTION"] not in store.get("graph_case", "MU", "case-1")["edges"]
    assert ["TRADE_INTENT", "TRADE_EXECUTION"] in store.get("graph_case", "MU", "case-1")["edges"]
    from doxagent.api_v2.graph import Graphs
    with store.connect() as db:
        seq = store.highwater(db)
    paths = {edge["edge_id"] for edge in Graphs(store, None).paths("MU", seq, None, "TRADE_INTENT")}
    assert "TRADE_INTENT:TRADE_EXECUTION" in paths
    assert "SOURCE:TRADE_INTENT" in paths
    assert "W2:TRADE_EXECUTION" not in paths
    assert reproject_trade_outcomes(store, ticker="MU", limit=1)["processed"] == 0


def test_runtime_export_reads_one_frozen_view_for_candidates_and_executions(tmp_path):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.v2_control.repository import ControlRepository
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from tests.v2_backend.test_api import OfflineAuth

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("test", "seed", [
        {"kind": "ticker", "ticker": "MU", "id": "MU", "data": {"removed": False}},
        {"kind": "case", "ticker": "MU", "id": "case-1", "data": rollup_case_trade(case(), [])},
    ])
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=store, control=control, auth=OfflineAuth())
    headers = {"Authorization": "Bearer offline"}
    with TestClient(app) as client:
        first = app.state.views.create("developer", "RUNTIME", "MU", "ALL")["view_id"]
        candidate = {
            "candidate_id": "candidate-1", "dedupe_key": "key-1", "originating_node": "W1_R3",
            "proposition": "New fact", "assertion_state": "ACTUAL", "subject_time": None,
            "occurrence_date": None, "entities": ["MU"], "provisional_event_id": None,
            "created_at": "2026-09-29T10:00:00Z",
        }
        full_execution = {**execution(), "environment": value("PAPER"), "profile_revision": value("rev-1"),
                          "direction": "LONG", "has_actual_fill": False, "accepted_at": value(None),
                          "filled_notional_usd": value("0")}
        from doxagent.v2_read.trade_outcomes import with_execution_state
        full_execution = with_execution_state(full_execution)
        store.ingest("test", "new", [
            {"kind": "candidate", "ticker": "MU", "id": "candidate-1", "parent": "case-1", "data": candidate},
            {"kind": "execution", "ticker": "MU", "id": "intent-1", "parent": "case-1", "data": full_execution},
        ])
        second = app.state.views.create("developer", "RUNTIME", "MU", "ALL")["view_id"]
        prefix = PREFIX + "/tickers/MU/runtime/cases/case-1"
        for name in ("candidates", "executions"):
            old = client.get(prefix + "/" + name, params={"view_id": first}, headers=headers)
            current = client.get(prefix + "/" + name, params={"view_id": second}, headers=headers)
            assert old.status_code == 200, old.text
            assert current.status_code == 200, current.text
            assert old.json()["data"]["data"]["items"] == []
            assert len(current.json()["data"]["data"]["items"]) == 1
        assert client.get(PREFIX + "/tickers/INTC/executions/intent-1", params={"view_id": second}, headers=headers).status_code == 400
        assert client.get(PREFIX + "/tickers/MU/executions/intent-1", params={"view_id": first}, headers=headers).status_code == 404
        store.ingest("test", "fill", [
            {"kind": "order", "ticker": "MU", "id": "order-1", "parent": "intent-1", "data": {
                "order_attempt_id": "order-1", "execution_id": "intent-1", "leg": "ENTRY", "side": "BUY",
                "order_type": "LMT", "quantity": "1", "limit_price": "100", "state": "FILLED",
                "broker_status": "Filled", "sent_at": value("2026-09-29T10:01:00Z"),
                "settled_at": value("2026-09-29T10:02:00Z")}},
            {"kind": "fill", "ticker": "MU", "id": "fill-1", "parent": "intent-1", "data": {
                "fill_id": "fill-1", "execution_id": "intent-1", "order_attempt_id": "order-1",
                "correction_revision": 1, "leg": "ENTRY", "side": "BUY",
                "executed_at": "2026-09-29T10:02:00Z", "quantity": "1", "price": "100",
                "commission_usd": value("0.2")}},
        ])
        third = app.state.views.create("developer", "RUNTIME", "MU", "ALL")["view_id"]
        detail_path = PREFIX + "/tickers/MU/executions/intent-1"
        for suffix, key in (("", "orders"), ("/orders", "items"), ("/fills", "items")):
            old = client.get(detail_path + suffix, params={"view_id": second}, headers=headers)
            current = client.get(detail_path + suffix, params={"view_id": third}, headers=headers)
            assert old.status_code == 200 and current.status_code == 200
            if suffix:
                assert old.json()["data"]["data"]["items"] == []
                assert len(current.json()["data"]["data"]["items"]) == 1
            else:
                assert old.json()["data"]["data"]["orders"]["items"] == []
                assert len(current.json()["data"]["data"]["orders"]["items"]) == 1
