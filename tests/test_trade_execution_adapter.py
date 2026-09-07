from decimal import Decimal
from types import SimpleNamespace

import pytest

from doxagent.trade_execution.ibkr_session import IbkrSession, execution_time
from tests.test_trade_execution import profile


def test_official_adapter_order_mapping_and_owner_fuse():
    pytest.importorskip("ibapi.order_cancel")
    calls = []
    session = IbkrSession(profile(), write_guard=lambda: calls.append("guard"))
    session.app = SimpleNamespace(
        isConnected=lambda: True,
        placeOrder=lambda *args: calls.append(args),
        cancelOrder=lambda *args: calls.append(args),
    )
    session.healthy = True
    session.accounts = ["DU_TEST"]
    attempt = dict(
        account="DU_TEST",
        order_id=21,
        client_id=82,
        order_ref="DA-UNIT",
        quantity=198,
        side="BUY",
        order_type="LMT",
        limit_price="101.01",
        session="OVERNIGHT",
        contract=dict(con_id=9939, symbol="MU", currency="USD", exchange="OVERNIGHT"),
    )
    session.submit(attempt)
    number, contract, order = calls[-1]
    assert number == 21 and contract.exchange == "OVERNIGHT"
    assert order.account == "DU_TEST" and order.totalQuantity == Decimal(198)
    assert order.lmtPrice == 101.01 and order.tif == "DAY"
    assert order.outsideRth and order.transmit
    session.cancel(attempt)
    assert calls[-1][0] == 21
    before = len(calls)
    session.accounts = ["U_LIVE"]
    with pytest.raises(ValueError, match="ACCOUNT_MISMATCH"):
        session.submit(attempt)
    assert len(calls) == before


def test_official_adapter_reject_callback_is_durable_and_time_normalized():
    pytest.importorskip("ibapi.order_cancel")
    events = []
    session = IbkrSession(profile(), event_sink=lambda *args: events.append(args))
    app = session._make_app()
    app.error(21, 0, 201, "insufficient funds")
    assert events[-1][0] == "order"
    assert events[-1][1]["status"] == "Rejected"
    assert events[-1][1]["order_id"] == 21
    assert execution_time("20260908 10:00:00 US/Eastern") == "2026-09-08T14:00:00+00:00"
