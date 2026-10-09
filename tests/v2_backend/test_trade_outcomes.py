"""Formal intent and effective ENTRY fill determine distinct graph outcomes."""

import json

from doxagent.v2_read.graph import project as graph_project
from doxagent.v2_read.maintenance import reproject_trade_outcomes
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.projectors import DomainProjectors
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
    for reason in ("RETRY_BUDGET_EXHAUSTED", "PORTFOLIO_CAPACITY_EXHAUSTED", "INSUFFICIENT_MARGIN"):
        terminal = execution(status="UNKNOWN", intake="EXECUTION_ACCEPTED", result="FAILED")
        terminal["entry_reason"] = reason
        assert classify_execution(terminal) == ("NOT_EXECUTED", [reason])
        rolled = rollup_case_trade(case(), [terminal])
        assert "TRADE_NOT_EXECUTED" in rolled["results"]
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


def test_delivery_failure_updates_case_without_invalid_trade_disposition(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("test", "seed", [
        {"kind": "business_provenance", "ticker": "MU", "id": "case-1",
         "data": {"basis": "TEST_FIXTURE"}},
        {"kind": "case", "ticker": "MU", "id": "case-1", "data": case()},
    ])
    intent = {
        "intent_id": "trade:case-1", "case_id": "case-1", "ticker": "MU",
        "status": "DELIVERY_FAILED", "trade": {"decision": "LONG"},
        "released_at": "2026-09-30T08:00:25+00:00",
        "delivery_failure": {"status": "FAILED", "code": "PROFILE_VALIDATION_FAILED",
                             "detail": "SHARED_CYCLE profile rejected by old delivery"},
    }
    records, _ = DomainProjectors(store)({
        "table_name": "runtime_values", "entity_id": "trade:case-1",
        "operation": "UPDATE", "seq": 12,
        "row": {"namespace": "trade_intents", "key": "trade:case-1",
                "payload": json.dumps(intent)},
    })
    summary = next(row["data"] for row in records if row["kind"] == "execution")
    updated_case = next(row["data"] for row in records if row["kind"] == "case")
    assert summary["execution_state"] == "NOT_EXECUTED"
    assert summary["entry_reason"].startswith("PROFILE_VALIDATION_FAILED")
    assert updated_case["trade_disposition"] == "READY"
    assert updated_case["trade"]["reason_codes"] == ["DELIVERY_FAILED"]


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


def test_terminal_failure_backfill_routes_without_waiting_for_day_end(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    failed = execution(status="UNKNOWN", intake="EXECUTION_ACCEPTED", result="FAILED")
    failed["entry_reason"] = "RETRY_BUDGET_EXHAUSTED"
    store.ingest("test", "failed", [
        {"kind": "case", "ticker": "MU", "id": "case-1", "data": case()},
        {"kind": "execution", "ticker": "MU", "id": "intent-1", "parent": "case-1", "data": failed},
    ])
    assert reproject_trade_outcomes(store, ticker="MU")["processed"] == 1
    assert store.get("case", "MU", "case-1")["trade"]["state"] == "NOT_EXECUTED"
    assert ["TRADE_INTENT", "TRADE_NOT_EXECUTED"] in store.get("graph_case", "MU", "case-1")["edges"]
    assert store.get("execution", "MU", "intent-1")["execution_state"] == "NOT_EXECUTED"


def test_policy_hit_filter_uses_final_decision_and_frozen_pagination(tmp_path):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.v2_control.repository import ControlRepository
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from tests.v2_backend.test_api import OfflineAuth

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records = [{"kind": "ticker", "ticker": "MU", "id": "MU", "data": {"removed": False}}]
    for index, hit in enumerate((True, True, False, None)):
        summary = rollup_case_trade(case(), [])
        summary.update(case_id=f"case-{index}", final_policy_hit=value(hit))
        records.append({"kind": "case", "ticker": "MU", "id": summary["case_id"],
                        "sort": f"2026-09-29T09:0{index}:00Z", "day": "2026-09-29",
                        "source_id": "news", "data": summary})
    store.ingest("test", "seed-hits", records)
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=store, control=control, auth=OfflineAuth())
    with TestClient(app) as client:
        view = app.state.views.create("developer", "RUNTIME", "MU", "ALL")["view_id"]
        params = {"view_id": view, "result": "POLICY_HIT", "source_id": "news", "limit": 1}
        url = PREFIX + "/tickers/MU/runtime/cases"
        first = client.get(url, params=params, headers={"Authorization": "Bearer offline"})
        assert first.status_code == 200, first.text
        page = first.json()["data"]["data"]
        assert [item["case_id"] for item in page["items"]] == ["case-1"]
        changed = {**records[1], "data": {**records[1]["data"], "final_policy_hit": value(False)}}
        store.ingest("test", "hit-changed", [changed])
        second = client.get(url, params={**params, "cursor": page["next_cursor"]},
                            headers={"Authorization": "Bearer offline"})
        assert second.status_code == 200, second.text
        page = second.json()["data"]["data"]
        assert [item["case_id"] for item in page["items"]] == ["case-0"]
        assert not page["has_more"]
        newest = app.state.views.create("developer", "RUNTIME", "MU", "ALL")["view_id"]
        filtered = client.get(url, params={**params, "view_id": newest, "limit": 20},
                              headers={"Authorization": "Bearer offline"})
        assert [item["case_id"] for item in filtered.json()["data"]["data"]["items"]] == ["case-1"]
        assert all("POLICY_HIT" not in row["data"]["results"] for row in records[1:])


def test_policy_recalled_filter_uses_r1_evidence_and_frozen_view(tmp_path):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.v2_control.repository import ControlRepository
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from tests.v2_backend.test_api import OfflineAuth

    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    records = [{'kind': 'ticker', 'ticker': 'MU', 'id': 'MU', 'data': {'removed': False}}]
    # Recalled but missed, recalled while pending, no recall, missing R1,
    # final hit without recorded recall, and a different source.
    for index, (ids, hit, source) in enumerate([
        (['policy-1'], False, 'news'), (['policy-2'], None, 'news'),
        ([], False, 'news'), (None, None, 'news'), ([], True, 'news'),
        (['policy-3'], True, 'other'),
    ]):
        identity = f'case-{index}'
        summary = rollup_case_trade(case(), [])
        summary.update(case_id=identity, final_policy_hit=value(hit))
        records.append({'kind': 'case', 'ticker': 'MU', 'id': identity,
                        'sort': f'2026-09-29T09:0{index}:00Z', 'day': '2026-09-29',
                        'source_id': source, 'data': summary})
        records.append({'kind': 'native:runtime_v2_cases', 'ticker': 'MU', 'id': identity,
                        'data': {'w2_round1': None if ids is None else {
                            'candidate_policy_ids': ids, 'reason': 'x' * 17000}}})
    store.ingest('test', 'seed-recalls', records)
    control = ControlRepository(RuntimeJournal(tmp_path / 'runtime.db'))
    control.migrate()
    app = create_app(store=store, control=control, auth=OfflineAuth())
    with TestClient(app) as client:
        view = app.state.views.create('developer', 'RUNTIME', 'MU', 'ALL')['view_id']
        params = {'view_id': view, 'result': 'POLICY_RECALLED', 'source_id': 'news', 'limit': 1}
        url = PREFIX + '/tickers/MU/runtime/cases'
        headers = {'Authorization': 'Bearer offline'}
        first = client.get(url, params=params, headers=headers)
        assert first.status_code == 200, first.text
        page = first.json()['data']['data']
        assert [item['case_id'] for item in page['items']] == ['case-1']
        store.ingest('test', 'recall-changed', [{
            'kind': 'native:runtime_v2_cases', 'ticker': 'MU', 'id': 'case-0',
            'data': {'w2_round1': {'candidate_policy_ids': []}},
        }])
        second = client.get(url, params={**params, 'cursor': page['next_cursor']}, headers=headers)
        assert second.status_code == 200, second.text
        page = second.json()['data']['data']
        assert [item['case_id'] for item in page['items']] == ['case-0']
        assert not page['has_more']
        newest = app.state.views.create('developer', 'RUNTIME', 'MU', 'ALL')['view_id']
        fresh = client.get(url, params={**params, 'view_id': newest, 'limit': 20}, headers=headers)
        assert [item['case_id'] for item in fresh.json()['data']['data']['items']] == ['case-1']
        assert all('POLICY_RECALLED' not in r['data']['results'] for r in records if r['kind'] == 'case')


def test_case_title_search_filters_before_pagination_and_keeps_frozen_scope(tmp_path):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.v2_control.repository import ControlRepository
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from tests.v2_backend.test_api import OfflineAuth

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records = [{"kind": "ticker", "ticker": "MU", "id": "MU", "data": {"removed": False}}]
    for index, (title, source, hit) in enumerate([
        ("HBM Straße 100%_ 产能", "news", True),
        ("hbm STRASSE 100%_ 产能", "news", True),
        ("HBM other source", "other", True),
        ("HBM no hit", "news", False),
        ("Unrelated title", "news", True),
    ]):
        summary = rollup_case_trade(case(), [])
        summary.update(case_id=f"search-{index}", title=value(title), final_policy_hit=value(hit))
        records.append({"kind": "case", "ticker": "MU", "id": summary["case_id"],
                        "sort": f"2026-09-29T09:0{index}:00Z", "day": "2026-09-29",
                        "source_id": source, "search": "HBM body-only text", "data": summary})
    store.ingest("test", "seed-search", records)
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=store, control=control, auth=OfflineAuth())
    with TestClient(app) as client:
        view = app.state.views.create("developer", "RUNTIME", "MU", "ALL")["view_id"]
        url = PREFIX + "/tickers/MU/runtime/cases"
        headers = {"Authorization": "Bearer offline"}
        params = {"view_id": view, "q": "  HBM  ", "result": "POLICY_HIT", "source_id": "news", "limit": 1}
        first = client.get(url, params=params, headers=headers)
        assert first.status_code == 200, first.text
        page = first.json()["data"]["data"]
        assert [row["case_id"] for row in page["items"]] == ["search-1"]
        cursor = page["next_cursor"]
        assert cursor
        changed = client.get(url, params={**params, "q": "other", "cursor": cursor}, headers=headers)
        assert changed.status_code == 400
        assert changed.json()["error"]["code"] == "INVALID_CURSOR"
        original = records[1]
        store.ingest("test", "rename-title", [{**original, "data": {**original["data"], "title": value("Renamed")}}])
        second = client.get(url, params={**params, "cursor": cursor}, headers=headers)
        assert second.status_code == 200, second.text
        assert [row["case_id"] for row in second.json()["data"]["data"]["items"]] == ["search-0"]
        for term, expected in [("strasse", ["search-1", "search-0"]), ("100%_", ["search-1", "search-0"]),
                               ("产能", ["search-1", "search-0"]), ("body-only", [])]:
            response = client.get(url, params={"view_id": view, "q": term}, headers=headers)
            assert response.status_code == 200, response.text
            assert [row["case_id"] for row in response.json()["data"]["data"]["items"]] == expected
        empty = client.get(url, params={"view_id": view, "q": "   "}, headers=headers)
        assert len(empty.json()["data"]["data"]["items"]) == 5
        assert client.get(url, params={"view_id": view, "q": "x" * 201}, headers=headers).status_code == 422
