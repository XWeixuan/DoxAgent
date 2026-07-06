from __future__ import annotations

from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app


def test_dashboard_mock_api_serves_contract_snapshot_routes() -> None:
    client = TestClient(create_app())

    routes = [
        "/api/dashboard/v1/overview",
        "/api/dashboard/v1/tickers?status=running&limit=1",
        "/api/dashboard/v1/backtests",
        "/api/dashboard/v1/tickers/MU",
        "/api/dashboard/v1/tickers/MU/documents/current?types=document1,document3",
        "/api/dashboard/v1/tickers/MU/documents/document1/versions",
        "/api/dashboard/v1/tickers/MU/documents/document1/versions/mu_document1_v3",
        "/api/dashboard/v1/tickers/MU/known-events?expectation_id=EU_REVENUE_ACCELERATION",
        "/api/dashboard/v1/tickers/MU/policies?action_type=DTC",
        "/api/dashboard/v1/tickers/MU/message-bus/overview",
        "/api/dashboard/v1/tickers/MU/message-bus/messages?source_id=benzinga_news&q=memory",
        "/api/dashboard/v1/tickers/MU/message-bus/config",
        "/api/dashboard/v1/tickers/MU/runtime/overview",
        "/api/dashboard/v1/tickers/MU/runtime/graph",
        "/api/dashboard/v1/tickers/MU/runtime/nodes/w1?limit=1",
        "/api/dashboard/v1/tickers/MU/runtime/executions?route=trading_record",
        "/api/dashboard/v1/tickers/MU/runtime/executions/pre_mu_001",
        "/api/dashboard/v1/tickers/MU/audit/revenue?period=7d",
        "/api/dashboard/v1/tickers/MU/audit/cost?group_by=node",
        "/api/dashboard/v1/tickers/MU/audit/cost/details?node=O3&status=retried",
    ]

    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        payload = response.json()
        assert "data" in payload
        assert payload["meta"]["source"] == "dashboard_state_api"

    ticker_page = client.get("/api/dashboard/v1/tickers?limit=1").json()["data"]
    assert ticker_page["page"]["limit"] == 1
    assert ticker_page["page"]["has_more"] is True

    empty_messages = client.get("/api/dashboard/v1/tickers/EMPTY/message-bus/messages").json()
    assert empty_messages["data"]["items"] == []
    overview = client.get("/api/dashboard/v1/overview").json()["data"]
    assert overview["system"]["current_session_label"] == "运行时段"


def test_dashboard_static_shell_is_not_browser_cached(tmp_path, monkeypatch) -> None:
    static_dir = tmp_path / "dist"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text(
        '<script type="module" src="/assets/index-test.js"></script>',
        encoding="utf-8",
    )
    (assets_dir / "index-test.js").write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setenv("DOXAGENT_DASHBOARD_STATIC_DIR", str(static_dir))

    client = TestClient(create_app())

    index = client.get("/overview")
    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-store, max-age=0, must-revalidate"

    asset = client.get("/assets/index-test.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_dashboard_mock_api_mutations_are_fixture_only_and_contract_shaped() -> None:
    client = TestClient(create_app())

    created = client.post(
        "/api/dashboard/v1/tickers",
        json={
            "ticker": "AMD",
            "force_initialize": False,
            "monitor_mode": "paper_trading",
            "reason": "frontend smoke",
        },
    )
    assert created.status_code == 200
    assert created.json()["data"]["operation"] == "start"
    assert created.json()["data"]["ticker"] == "AMD"
    assert created.json()["data"]["ticker_state"]["monitor_mode"] == "paper_trading"

    switched = client.patch(
        "/api/dashboard/v1/tickers/AMD/monitor-mode",
        json={"monitor_mode": "message_monitoring"},
    )
    assert switched.status_code == 200
    assert switched.json()["data"]["operation"] == "monitor_mode"
    assert switched.json()["data"]["ticker_state"]["monitor_mode"] == "message_monitoring"

    broker = client.patch(
        "/api/dashboard/v1/tickers/AMD/monitor-mode",
        json={"monitor_mode": "broker_trading"},
    )
    assert broker.status_code == 422
    assert broker.json()["error"]["code"] == "INVALID_PARAMS"

    backtest = client.post(
        "/api/dashboard/v1/backtests",
        json={"ticker": "AMD", "period": "7d", "force_initialize": False},
    )
    assert backtest.status_code == 200
    assert backtest.json()["data"]["ticker"] == "AMD"
    assert backtest.json()["data"]["status"] == "completed"

    paused = client.post("/api/dashboard/v1/tickers/AMD/pause", json={"reason": "pause"})
    assert paused.status_code == 200
    assert paused.json()["data"]["ticker_state"]["status"] == "paused"

    restarted = client.post(
        "/api/dashboard/v1/tickers/AMD/restart",
        json={"keep_bindings": True, "reason": "restart"},
    )
    assert restarted.status_code == 200
    assert restarted.json()["data"]["ticker_state"]["status"] == "running"

    patched = client.patch(
        "/api/dashboard/v1/tickers/MU/message-bus/config/tikhub_x_search",
        json={"enabled": False, "search_terms": ["MU HBM"]},
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["source_id"] == "tikhub_x_search"

    deleted_source = client.delete(
        "/api/dashboard/v1/tickers/MU/message-bus/config/tikhub_x_search"
    )
    assert deleted_source.status_code == 200
    assert deleted_source.json()["data"]["removed"] is True

    deleted_ticker = client.request(
        "DELETE",
        "/api/dashboard/v1/tickers/AMD?delete_history=false",
        json={"reason": "cleanup"},
    )
    assert deleted_ticker.status_code == 200
    assert deleted_ticker.json()["data"]["history_deleted"] is False


def test_dashboard_mock_api_errors_and_mock_auth_follow_contract_shape() -> None:
    client = TestClient(create_app())

    duplicate = client.post("/api/dashboard/v1/tickers", json={"ticker": "MU"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "TICKER_ALREADY_RUNNING"
    assert duplicate.json()["error"]["details"]["ticker"] == "MU"

    missing = client.get("/api/dashboard/v1/tickers/NOPE/runtime/executions/pre_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert "request_id" in missing.json()

    invalid = client.get("/api/dashboard/v1/tickers/MU/documents/unknown/versions")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_PARAMS"

    auth_client = TestClient(create_app(auth_mode="mock-required"))
    unauthorized = auth_client.get("/api/dashboard/v1/overview")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"

    forbidden = auth_client.get(
        "/api/dashboard/v1/overview",
        headers={"Authorization": "Bearer forbidden"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "FORBIDDEN"


def test_dashboard_mock_sse_stream_is_frontend_connectable() -> None:
    client = TestClient(create_app())

    response = client.get(
        "/api/dashboard/v1/events?ticker=MU&event_types=runtime.execution.updated&once=true"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: runtime.execution.updated" in response.text
    assert '"event_type": "runtime.execution.updated"' in response.text
