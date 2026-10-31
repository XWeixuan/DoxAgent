import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.test_trade_execution import setup  # noqa: F401

from doxagent.api_v2.dto import validate
from doxagent.revenue_audit.ledger import fold_leg, unrealized
from doxagent.revenue_audit.market import causal_quote
from doxagent.revenue_audit.projection import account_key
from doxagent.revenue_audit.service import RevenueService
from doxagent.v2_read.repository import ReadStore

T0 = "2026-11-02T14:00:00+00:00"
T1 = "2026-11-02T15:00:00+00:00"
T2 = "2026-11-02T20:30:00+00:00"
NOW = "2026-11-03T16:00:00+00:00"


def leg(sign=1, closed=False, asset="MU", role="MAIN"):
    lot = dict(
        id="x" + role,
        sign=sign,
        ticker=asset,
        role=role,
        remaining_qty=0 if closed else 6,
        first_fill_at=T0,
        scheduled_exit=T2,
    )
    fills = {}
    fees = {}
    allocations = []
    for identity, at, qty, price, kind in [
        ("e", T0, 10, 100, "ENTRY"),
        ("x", T1, 10 if closed else 4, 110, "EXIT"),
    ]:
        fills[("DU1", identity)] = dict(
            account="DU1", exec_id=identity, time=at, quantity=str(qty), price=str(price)
        )
        fees[("DU1", identity)] = dict(commission="1", currency="USD")
        allocations.append(dict(account="DU1", fill_id=identity, qty=str(qty), kind=kind))
    return fold_leg(lot, allocations, fills, fees)


def record(identity="trade:a", env="PAPER", kind="ACTUAL", legs=None):
    return dict(
        id=identity,
        case_id="case-a",
        ticker="MU",
        direction="LONG",
        environment=env,
        account_key=account_key("DU1" if env == "PAPER" else "U1"),
        title="消息标题",
        intent_at=T0,
        kind=kind,
        status="FAILED" if kind == "UNEXECUTED" else "EXECUTION_ACCEPTED",
        reason="TEST",
        completed=bool(legs and all(l["qty"] == "0" for l in legs)),
        main_cap="1000",
        legs=legs or [],
    )


class Market:
    def __init__(self):
        self.calls = []

    def batch(self, requests):
        self.calls.append(requests)
        return {r: dict(bid="120", ask="121", at=r[1]) for r in requests}


def model_balances(service, frame):
    """Independent cash/stock fixture replacing the existing Runtime capital book."""
    result = {}
    for env, opening in frame["opening"].items():
        cash = Decimal(opening["capital"])
        for event in service.objects("revenue_capital", "", frame["seq"], opening["account_key"]):
            cash += Decimal(event["amount"])
        positions = []
        known = True
        for r in service.records(frame, env):
            if r["kind"] != "ACTUAL":
                continue
            for leg in r["legs"]:
                known &= leg["known"]
                cash -= leg["sign"] * Decimal(leg["notional"])
                cash += leg["sign"] * Decimal(leg["exit_price"] or "0") * Decimal(leg["exit_qty"])
                cash -= Decimal(leg["fees"] or "0")
                if Decimal(leg["qty"]):
                    positions.append(dict(asset=leg["asset"], qty=leg["qty"], sign=leg["sign"]))
        result[env] = dict(cash=str(cash) if known else None, positions=positions)
    return result


@pytest.fixture
def service(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    with store.connect(write=True) as db:
        db.execute(
            "INSERT INTO read_meta VALUES('revenue_audit_v1',?)",
            (
                json.dumps(
                    dict(
                        since="2026-11-01T00:00:00+00:00",
                        opening={
                            "PAPER": dict(account_key=account_key("DU1"), capital="4000"),
                            "LIVE": dict(account_key=account_key("U1"), capital="5000"),
                        },
                    )
                ),
            ),
        )
    result = RevenueService(store, market=Market(), clock=lambda: datetime.fromisoformat(NOW))
    result.balance_provider = lambda frame: model_balances(result, frame)
    return result


def put(service, *records):
    service.store.ingest(
        "fixture",
        str(len(records)) + records[0]["id"],
        [
            dict(
                kind="revenue_record",
                ticker=r["environment"],
                id=r["id"],
                parent=r["account_key"],
                sort=r["intent_at"],
                data=r,
            )
            for r in records
        ],
    )


def test_fifo_partial_entry_fee_not_double_counted():
    l = leg()
    assert Decimal(l["realized"]) == Decimal("38.6")
    assert unrealized(l, NOW, dict(bid="120")) == Decimal("119.4")
    assert Decimal(l["realized"]) + unrealized(l, NOW, dict(bid="120")) == 158
    assert Decimal(leg(sign=-1)["realized"]) == Decimal("-41.4")
    assert unrealized(leg(sign=-1), NOW, dict(ask="120")) == Decimal("-120.6")


def test_frozen_actual_default_no_hypothetical_reads_and_accounts_isolated(service, monkeypatch):
    put(
        service,
        record(legs=[leg()]),
        record("trade:b", kind="UNEXECUTED"),
        record("trade:live", env="LIVE", legs=[leg(closed=True)]),
    )
    snapshot = service.capture("owner")
    validate("RevenueSnapshot", snapshot)
    frame = snapshot["frame_id"]
    monkeypatch.setattr(service, "hypothesis", lambda r: pytest.fail("default loaded hypothesis"))
    report = service.report("owner", frame, "PAPER")
    validate("RevenueReport", report)
    assert report["kpis"]["net"] == "158.0"
    # Cash 3438 after BUY 10 @100, SELL 4 @110 and $2 fees, plus 6*120 stock value.
    assert Decimal(report["kpis"]["strategy_value"]) == 3438 + 6 * 120
    assert report["kpis"]["actual_count"] == 1
    assert report["kpis"]["intent_count"] == 2
    assert Decimal(report["kpis"]["fees"]) == 2
    assert len(report["holdings"]) == 1
    calls = len(service.market.calls)
    service.report("owner", frame, "PAPER", "PREVIOUS_TRADING_DAY")
    detail = service.detail("owner", frame, "PAPER", "trade:a")
    validate("RevenueDetail", detail)
    assert len(service.market.calls) == calls
    from doxagent.api_v2.errors import ApiFailure

    with pytest.raises(ApiFailure):
        service.frame("someone-else", frame)


def test_http_auth_schema_validation_and_quote_work_only_on_post(service, tmp_path):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import create_app, PREFIX
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_control.repository import ControlRepository
    from tests.v2_backend.test_api import OfflineAuth

    put(service, record(legs=[leg()]))
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=service.store, control=control, auth=OfflineAuth())
    app.state.revenue.market = service.market
    app.state.revenue.clock = service.clock
    # No account profile is required for read-side fixture data.
    app.state.revenue.journal = None
    app.state.revenue.balance_provider = service.balance_provider
    with TestClient(app) as client:
        assert client.post(PREFIX + "/audit/revenue/refresh", json={}).status_code == 401
        headers = {"Authorization": "Bearer offline"}
        response = client.post(PREFIX + "/audit/revenue/refresh", headers=headers, json={})
        assert response.status_code == 200, response.text
        frame = response.json()["data"]["frame_id"]
        count = len(service.market.calls)
        report = client.get(
            PREFIX + "/audit/revenue/report",
            headers=headers,
            params=dict(frame_id=frame, environment="PAPER"),
        )
        assert report.status_code == 200, report.text
        assert report.json()["data"]["data"]["kpis"]["actual_count"] == 1
        assert len(service.market.calls) == count
        assert client.get(PREFIX + "/audit/revenue/report", headers=headers).status_code == 422
        assert (
            client.get(
                PREFIX + "/audit/revenue/report",
                headers=headers,
                params=dict(frame_id=frame, environment="invalid"),
            ).status_code
            == 422
        )


def test_real_projection_late_fee_and_bust(setup, tmp_path, monkeypatch):
    from tests.v2_backend.test_execution_projection import (
        test_owned_fill_and_late_commission_are_projected_without_order_status_inference,
    )

    clock, repo, _, _, _ = setup
    original_migrate = ReadStore.migrate

    def migrate(store, *args, **kwargs):
        result = original_migrate(store, *args, **kwargs)
        with store.connect(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO read_meta VALUES('revenue_audit_v1',?)",
                (json.dumps(dict(since="2026-09-01T00:00:00+00:00", opening={})),),
            )
        return result

    monkeypatch.setattr(ReadStore, "migrate", migrate)
    admit = repo.admit

    def audited(intent, *args, **kwargs):
        intent["audit_context"] = dict(
            version=1,
            decision_at=clock().isoformat(),
            environment="PAPER",
            account="DU_TEST",
            title="News",
        )
        return admit(intent, *args, **kwargs)

    monkeypatch.setattr(repo, "admit", audited)
    ingest = ReadStore.ingest
    seen = []

    def observe(store, *args, **kwargs):
        for item in args[2]:
            if item["kind"] == "revenue_record" and item["data"]:
                seen.extend(l["fees"] for l in item["data"]["legs"] if l["fees"] is not None)
        return ingest(store, *args, **kwargs)

    monkeypatch.setattr(ReadStore, "ingest", observe)
    test_owned_fill_and_late_commission_are_projected_without_order_status_inference(
        setup, tmp_path
    )
    store = ReadStore(tmp_path / "read.db")
    summary = store.get("revenue_record", "PAPER", "trade:A")
    assert summary is not None
    assert summary["kind"] == "UNEXECUTED"
    assert any(Decimal(f) == Decimal("1.25") for f in seen)


def test_period_net_subtracts_opening_upl(service):
    put(service, record(legs=[leg()]))
    snapshot = service.capture("owner")
    report = service.report("owner", snapshot["frame_id"], "PAPER", "CURRENT_TRADING_DAY")
    assert report["kpis"]["realized"] == "0"
    assert Decimal(report["kpis"]["net"]) == 0
    assert Decimal(report["kpis"]["upl"]) == Decimal("119.4")
    assert Decimal(report["kpis"]["strategy_value"]) == 4158


def test_hypothetical_lazy_caches_and_excludes_late_fill(service):
    put(service, record("trade:b", kind="UNEXECUTED"))
    frame = service.capture("owner")["frame_id"]
    assert not service.market.calls
    assert service.evaluate("owner", frame, "PAPER", ["trade:b"])["evaluated"] == 1
    calls = len(service.market.calls)
    assert service.evaluate("owner", frame, "PAPER", ["trade:b"])["evaluated"] == 0
    report = service.report("owner", frame, "PAPER", mode="ALL")
    assert report["items"][0]["hypothesis"]["state"] == "PRICED"
    assert report["kpis"]["hypothetical_count"] == 1
    r = record("trade:b", legs=[leg(closed=True)])
    service.store.ingest(
        "fixture", "late", [dict(kind="revenue_record", ticker="PAPER", id=r["id"], data=r)]
    )
    report = service.report("owner", frame, "PAPER", mode="ALL")
    assert report["kpis"]["hypothetical_count"] == 0
    assert report["items"][0]["hypothesis"] is None
    assert len(service.market.calls) == calls


def test_causal_quote_never_uses_future_or_skips_invalid_latest():
    events = [
        dict(
            timestamp="2026-11-02T14:00:00+00:00",
            bid_price=100,
            ask_price=101,
            bid_size=1,
            ask_size=1,
        ),
        dict(
            timestamp="2026-11-02T14:00:01+00:00",
            bid_price=-1,
            ask_price=102,
            bid_size=0,
            ask_size=1,
        ),
    ]
    q = causal_quote(events, datetime(2026, 11, 2, 14, 0, 2, tzinfo=UTC))
    assert q["bid"] is None and q["ask"] == "102"
    assert causal_quote(events, datetime(2026, 11, 2, 14, 2, tzinfo=UTC)) is None
    events[1]["timestamp"] = events[0]["timestamp"]
    q = causal_quote(events, datetime(2026, 11, 2, 14, 0, 1, tzinfo=UTC))
    assert q["bid"] is None


def test_attributed_fees_and_allocations_are_separate_from_profit(service):
    put(service, record(legs=[leg(closed=True)]))
    service.store.ingest(
        "fixture",
        "capital",
        [
            dict(
                kind="revenue_capital",
                ticker="",
                id="funding",
                parent=account_key("DU1"),
                data=dict(kind="ALLOCATION", amount="1000", at=T1),
            ),
            dict(
                kind="revenue_capital",
                ticker="",
                id="borrow",
                parent=account_key("DU1"),
                data=dict(
                    kind="ADJUSTMENT", amount="-3", at=T2, fee_kind="BORROW", execution_id="trade:a"
                ),
            ),
        ],
    )
    frame = service.capture("owner")["frame_id"]
    data = service.report("owner", frame, "PAPER")
    assert Decimal(data["kpis"]["net"]) == 95
    assert Decimal(data["kpis"]["realized"]) == 95
    assert Decimal(data["kpis"]["fees"]) == 5
    assert Decimal(data["kpis"]["strategy_value"]) == 5095
    assert Decimal(data["items"][0]["net"]) == 95
    assert Decimal(data["curve"][-1]["net"]) == 95
    assert Decimal(data["kpis"]["roi"]) < Decimal(95) / 4000
    detail = service.detail("owner", frame, "PAPER", "trade:a")
    assert Decimal(detail["fees"]) == 5


def test_roi_only_hypothesis_is_not_fabricated_dollar_profit(service):
    r = record("trade:no-cap", kind="UNEXECUTED")
    r["main_cap"] = None
    put(service, r)
    frame = service.capture("owner")["frame_id"]
    service.evaluate("owner", frame, "PAPER", [r["id"]])
    data = service.report("owner", frame, "PAPER", mode="UNEXECUTED")
    assert data["kpis"]["net"] is None
    assert data["items"][0]["hypothesis"]["roi"] is not None
    assert data["items"][0]["hypothesis"]["amount"] is None
    assert all(p["net"] is None for p in data["curve"])


def test_pending_zero_fill_never_requests_hypothetical_quotes(service):
    put(service, record("trade:pending", kind="PENDING"))
    frame = service.capture("owner")["frame_id"]
    assert service.evaluate("owner", frame, "PAPER", ["trade:pending"])["evaluated"] == 0
    assert not service.market.calls


def test_unavailable_boundary_quote_does_not_invent_period_profit(service):
    put(service, record(legs=[leg()]))
    original = service.market.batch
    service.market.batch = lambda requests: {
        k: v for k, v in original(requests).items() if not k[2]
    }
    frame = service.capture("owner")["frame_id"]
    data = service.report("owner", frame, "PAPER", "CURRENT_TRADING_DAY")
    assert data["kpis"]["net"] is None
    assert data["kpis"]["realized"] == "0"
    assert Decimal(data["kpis"]["upl"]) == Decimal("119.4")


def test_exit_calendar_early_close_and_profile_offset(service):
    r = record(kind="UNEXECUTED")
    r["intent_at"] = "2026-11-27T15:00:00+00:00"
    assert service.expected_exit(r, {}) == "2026-11-27T17:30:00+00:00"
    r["exit_offset_minutes"] = 15
    assert service.expected_exit(r, {}) == "2026-11-27T17:45:00+00:00"
    r["intent_at"] = "2026-11-27T17:50:00+00:00"
    assert service.expected_exit(r, {}) == "2026-11-30T20:45:00+00:00"


def test_finished_after_selected_period_is_not_a_historical_win(service):
    l = leg(closed=True)
    l["timeline"][-1]["at"] = NOW
    put(service, record(legs=[l]))
    frame = service.capture("owner")["frame_id"]
    data = service.report("owner", frame, "PAPER", "PREVIOUS_TRADING_DAY")
    assert data["kpis"]["completed_count"] == 0
    assert data["kpis"]["win_rate"] is None


def test_pending_future_fee_does_not_erase_previous_period_profit(service):
    old = record(legs=[leg(closed=True)])
    later = record("trade:new", legs=[leg()])
    later["intent_at"] = "2026-11-03T15:00:00+00:00"
    for point in later["legs"][0]["timeline"]:
        point["at"] = "2026-11-03T15:00:00+00:00"
        point["known"] = False
        point["realized_known"] = False
    later["legs"][0]["known"] = False
    put(service, old, later)
    frame = service.capture("owner")["frame_id"]
    data = service.report("owner", frame, "PAPER", "PREVIOUS_TRADING_DAY")
    assert Decimal(data["kpis"]["realized"]) == 98
    assert Decimal(data["kpis"]["net"]) == 98


def test_short_total_asset_counts_short_proceeds_and_negative_stock_value(service):
    r = record(legs=[leg(sign=-1)])
    r["direction"] = "SHORT"
    put(service, r)
    frame = service.capture("owner")["frame_id"]
    data = service.report("owner", frame, "PAPER")
    # 4000 + 1000 - 440 - 2 fees cash; remaining 6 short shares marked at Ask 121.
    assert Decimal(data["kpis"]["strategy_value"]) == 4558 - 6 * 121


def test_activation_is_explicit_idempotent_and_keeps_existing_capital(setup, tmp_path):
    from doxagent.revenue_audit.cli import activate
    from doxagent.trade_execution.capital import CapitalLedger

    clock, repo, _, _, _ = setup
    store = ReadStore(tmp_path / "activation.db")
    store.migrate()
    first = activate(store, repo.journal, clock())
    assert first["opening"]["PAPER"]["capital"] == "20000"
    CapitalLedger(repo).allocate("DU_TEST", "25000", "later", "new allocation")
    assert activate(store, repo.journal) == first
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM object_current WHERE kind='revenue_record'"
            ).fetchone()[0]
            == 0
        )


def test_global_hypotheses_are_batched_independently_of_record_pagination(service):
    put(service, *[record(f"trade:global:{i}", kind="UNEXECUTED") for i in range(25)])
    frame = service.capture("owner")["frame_id"]
    batches = 0
    data = service.report("owner", frame, "PAPER", mode="ALL", limit=1)
    assert len(data["items"]) == 1
    assert data["hypothesis_pending_count"] == 25
    while data["hypothesis_pending_ids"]:
        assert len(data["hypothesis_pending_ids"]) <= 12
        service.evaluate("owner", frame, "PAPER", data["hypothesis_pending_ids"])
        batches += 1
        data = service.report("owner", frame, "PAPER", mode="ALL", limit=1)
    assert batches == 3
    assert data["kpis"]["hypothetical_count"] == 25
    assert data["hypothesis_total"] == 25
    assert len(service.market.calls) == 1  # shared asset/second prices reused across batches


def test_new_closed_selection_keeps_original_w3_time_across_launch_boundary(service):
    from doxagent.revenue_audit.projection import project

    intent = dict(
        intent_id="trade:closed-new",
        case_id="case-closed",
        ticker="MU",
        status="EXPIRED_SEMANTIC_DAY",
        released_at=T0,
        trade={"decision": "LONG"},
        audit_context=dict(
            version=1,
            decision_at="2026-10-30T15:00:00+00:00",
            environment="PAPER",
            account="DU1",
            title="New formal selection",
        ),
    )
    event = dict(
        table_name="runtime_values",
        row=dict(namespace="trade_intents", key=intent["intent_id"], payload=json.dumps(intent)),
    )
    projected = project(service.store, event, [])
    assert len(projected) == 1
    r = projected[0]["data"]
    assert r["intent_at"] == "2026-10-30T15:00:00+00:00"
    assert r["recorded_at"] == T0
    put(service, r)
    frame = service.capture("owner")["frame_id"]
    assert service.report("owner", frame, "PAPER")["kpis"]["intent_count"] == 1
    assert service.report("owner", frame, "PAPER", mode="ALL")["hypothesis_pending_count"] == 0
    del intent["audit_context"]
    event["row"]["payload"] = json.dumps(intent)
    assert project(service.store, event, []) == []


def test_current_asset_uses_existing_balance_without_importing_history(service):
    service.balance_provider = lambda frame: {"PAPER": dict(cash="3987.519931", positions=[])}
    frame = service.capture("owner")["frame_id"]
    data = service.report("owner", frame, "PAPER")
    assert data["kpis"]["strategy_value"] == "3987.519931"
    assert data["items"] == [] and data["holdings"] == []
    assert data["kpis"]["net"] == "0" and data["kpis"]["intent_count"] == 0
    assert not service.market.calls
