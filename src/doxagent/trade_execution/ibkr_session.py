"""Official ibapi long-lived socket. Read calls are bounded; writes require account fuse.

API reference: https://www.interactivebrokers.com/docs/tws-api/
No order methods are registered with Data MCP or any model tool registry.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .schema import ExecutionProfile, account_fuse

INFO_CODES = {1101, 1102, 2104, 2106, 2107, 2108, 2158, 10167, 2186}


def execution_time(value: str) -> str:
    pieces = value.split()
    if len(pieces) < 2:
        raise ValueError("invalid broker execution timestamp")
    zone = pieces[2] if len(pieces) > 2 else "America/New_York"
    zone = {"US/Eastern": "America/New_York", "EST": "America/New_York"}.get(zone, zone)
    return (
        datetime.strptime(" ".join(pieces[:2]), "%Y%m%d %H:%M:%S")
        .replace(tzinfo=ZoneInfo(zone))
        .astimezone(UTC)
        .isoformat()
    )


class IbkrSession:
    def __init__(
        self, profile: ExecutionProfile, *, event_sink: Any = None, write_guard: Any = None
    ) -> None:
        self.profile = profile
        self.event_sink = event_sink or (lambda kind, payload: None)
        self.write_guard = write_guard
        self.app: Any = None
        self.thread: threading.Thread | None = None
        self.connect_lock = threading.Lock()
        self.sync_lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.requests: dict[int, dict[str, Any]] = {}
        self.serial = 10000
        self.ready = threading.Event()
        self.accounts_ready = threading.Event()
        self.accounts: list[str] = []
        self.next_id = 0
        self.errors: list[dict[str, Any]] = []
        self.orders: dict[int, dict[str, Any]] = {}
        self.quotes: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.quote_ids: dict[int, tuple[Any, ...]] = {}
        self.contracts: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.positions: dict[int, str] = {}
        self.position_event = threading.Event()
        self.open_event = threading.Event()
        self.completed_event = threading.Event()
        self.last_sync = 0.0
        self.healthy = False

    def _emit(self, kind: Any, value: Any) -> Any:
        self.event_sink(kind, value)

    def connect(self) -> Any:
        with self.connect_lock:
            if self.app and self.app.isConnected() and self.healthy:
                return
            self.close()
            self.ready.clear()
            self.accounts_ready.clear()
            self.accounts = []
            self.quotes.clear()
            self.quote_ids.clear()
            self.orders.clear()
            self.last_sync = 0
            self.app = self._make_app()
            self.app.connect(self.profile.host, self.profile.port, self.profile.client_id)
            self.thread = threading.Thread(target=self.app.run, daemon=True, name="doxagent-ibkr")
            self.thread.start()
            timeout = self.profile.strategy.request_timeout_seconds
            if not self.ready.wait(timeout) or not self.accounts_ready.wait(timeout):
                self.close()
                raise ConnectionError("IBKR_HANDSHAKE_TIMEOUT")
            account_fuse(self.profile, self.accounts)
            self.healthy = True

    def close(self) -> Any:
        self.healthy = False
        if self.app:
            self.app.disconnect()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        self.app = None

    def _request(self) -> Any:
        with self.request_lock:
            self.serial += 1
            identity = self.serial
            self.requests[identity] = {"event": threading.Event(), "rows": [], "error": None}
            return identity

    def _wait(self, identity: Any) -> Any:
        request = self.requests[identity]
        if not request["event"].wait(self.profile.strategy.request_timeout_seconds):
            raise TimeoutError("IBKR_REQUEST_TIMEOUT")
        if request["error"]:
            raise RuntimeError(request["error"])
        return list(request["rows"])

    def _fuse(self) -> Any:
        if not self.app or not self.app.isConnected() or not self.healthy:
            raise ConnectionError("IBKR_DISCONNECTED")
        account_fuse(self.profile, self.accounts)
        if not self.write_guard:
            raise RuntimeError("ORDER_WRITER_NOT_OWNED")
        self.write_guard()

    def contract(self, ticker: str, exchange: Any = "SMART") -> dict[str, Any]:
        self.connect()
        key = (ticker, exchange, datetime.now(UTC).date().isoformat())
        if key in self.contracts:
            return self.contracts[key]
        from ibapi.contract import Contract  # type: ignore[import-untyped]

        request = self._request()
        contract = Contract()
        contract.symbol, contract.secType, contract.currency, contract.exchange = (
            ticker,
            "STK",
            "USD",
            exchange,
        )
        try:
            self.app.reqContractDetails(request, contract)
            values = self._wait(request)
        finally:
            self.requests.pop(request, None)
        candidates = {
            v["con_id"]: v for v in values if v["symbol"] == ticker and v["currency"] == "USD"
        }
        if len(candidates) != 1:
            raise ValueError("CONTRACT_NOT_UNIQUE")
        result: dict[str, Any] = next(iter(candidates.values()))
        result["exchange"] = exchange
        self.contracts[key] = result
        return result

    def market_rules(self, contract: dict[str, Any]) -> list[dict[str, Any]]:
        exchanges, ids = contract["valid_exchanges"], contract["market_rule_ids"]
        if contract["exchange"] not in exchanges:
            raise ValueError("VENUE_NOT_AVAILABLE")
        index = exchanges.index(contract["exchange"])
        if index >= len(ids) or not ids[index]:
            raise ValueError("PRICE_RULE_UNAVAILABLE")
        identity = int(ids[index])
        # Market rule callbacks use the rule ID, not a generated request ID.
        with self.request_lock:
            request = self.requests.setdefault(
                identity, {"event": threading.Event(), "rows": [], "error": None}
            )
        if not request["event"].is_set():
            self.app.reqMarketRule(identity)
        return list(self._wait(identity))

    def quote(self, contract: dict[str, Any], side: str, *, strategy: Any = None) -> dict[str, Any]:
        self.connect()
        strategy = strategy or self.profile.strategy
        key = (contract["con_id"], contract["exchange"])
        with self.request_lock:
            if key not in self.quotes:
                self.serial += 1
                request = self.serial
                self.quotes[key] = {"market_data_type": None, "venue": contract["exchange"]}
                self.quote_ids[request] = key
                self.app.reqMarketDataType(1)
                self.app.reqMktData(request, self._contract_object(contract), "", False, False, [])
        deadline = time.monotonic() + strategy.request_timeout_seconds
        required = "ask" if side == "BUY" else "bid"
        while time.monotonic() < deadline:
            value = dict(self.quotes[key])
            if value.get("error"):
                raise RuntimeError(value["error"])
            stamp = value.get(required + "_at", 0)
            if (
                value.get("market_data_type") == 1
                and value.get(required, 0) > 0
                and time.time() - stamp <= strategy.quote_max_age_seconds
            ):
                return {**value, "captured_at": datetime.now(UTC).isoformat()}
            time.sleep(0.02)
        raise TimeoutError("FRESH_QUOTE_UNAVAILABLE")

    def positions_snapshot(self) -> dict[str, Any]:
        self.connect()
        with self.sync_lock:
            self.position_event.clear()
            self.positions = {}
            self.app.reqPositions()
            try:
                if not self.position_event.wait(self.profile.strategy.request_timeout_seconds):
                    raise TimeoutError("BROKER_POSITION_SYNC_INCOMPLETE")
                return {
                    "positions": dict(self.positions),
                    "complete": True,
                    "at": datetime.now(UTC).isoformat(),
                }
            finally:
                self.app.cancelPositions()

    def sync(self) -> dict[str, Any]:
        self.connect()
        with self.sync_lock:
            self.open_event.clear()
            self.completed_event.clear()
            self.position_event.clear()
            self.app.reqOpenOrders()
            self.app.reqCompletedOrders(True)
            from ibapi.execution import ExecutionFilter  # type: ignore[import-untyped]

            request = self._request()
            filt = ExecutionFilter()
            filt.acctCode = self.profile.expected_account_id
            self.app.reqExecutions(request, filt)
            try:
                self._wait(request)
                timeout = self.profile.strategy.request_timeout_seconds
                if not self.open_event.wait(timeout) or not self.completed_event.wait(timeout):
                    raise TimeoutError("BROKER_ORDER_SYNC_INCOMPLETE")
                self.positions = {}
                self.app.reqPositions()
                if not self.position_event.wait(timeout):
                    raise TimeoutError("BROKER_POSITION_SYNC_INCOMPLETE")
                self.app.cancelPositions()
                self.last_sync = time.monotonic()
                return {
                    "complete": True,
                    "positions": dict(self.positions),
                    "at": datetime.now(UTC).isoformat(),
                    "history_scope": "BROKER_SESSION_WINDOW",
                }
            finally:
                self.requests.pop(request, None)

    def submit(self, attempt: dict[str, Any]) -> Any:
        self._fuse()
        if attempt["account"] != self.profile.expected_account_id:
            raise ValueError("ACCOUNT_MISMATCH")
        from decimal import Decimal

        from ibapi.order import Order  # type: ignore[import-untyped]

        order = Order()
        order.account, order.orderRef = attempt["account"], attempt["order_ref"]
        order.action, order.orderType = attempt["side"], attempt["order_type"]
        order.totalQuantity = Decimal(attempt["quantity"])
        order.tif, order.outsideRth, order.transmit = "DAY", attempt["session"] != "RTH", True
        if attempt["limit_price"] is not None:
            order.lmtPrice = float(attempt["limit_price"])
        self.app.placeOrder(attempt["order_id"], self._contract_object(attempt["contract"]), order)
        self.next_id = max(self.next_id, attempt["order_id"] + 1)

    def cancel(self, attempt: dict[str, Any]) -> Any:
        self._fuse()
        if (
            attempt["account"] != self.profile.expected_account_id
            or attempt["client_id"] != self.profile.client_id
        ):
            raise ValueError("ORDER_OWNER_MISMATCH")
        from ibapi.order_cancel import OrderCancel  # type: ignore[import-untyped]

        self.app.cancelOrder(attempt["order_id"], OrderCancel())

    def what_if(
        self, contract: dict[str, Any], order_id: int, *, side: Any = "BUY", price: Any = 1.0
    ) -> dict[str, Any]:
        # Broker validation only: whatIf=True cannot place a transmitted trading order.
        if self.profile.environment != "PAPER":
            raise ValueError("PAPER_ONLY_DIAGNOSTIC")
        self._fuse()
        from decimal import Decimal

        from ibapi.order import Order

        order = Order()
        order.account = self.profile.expected_account_id
        order.orderRef = f"DA-WHATIF-{order_id}"
        order.action, order.orderType, order.lmtPrice = side, "LMT", price
        order.totalQuantity, order.whatIf = Decimal(1), True
        order.tif = "DAY"
        request = {"event": threading.Event(), "rows": [], "error": None}
        self.requests[order_id] = request
        try:
            self.app.placeOrder(order_id, self._contract_object(contract), order)
            return {"status": "VALIDATED", "rows": self._wait(order_id)}
        finally:
            self.requests.pop(order_id, None)

    @staticmethod
    def _contract_object(value: Any) -> Any:
        from ibapi.contract import Contract

        result = Contract()
        result.conId = value["con_id"]
        result.symbol, result.secType, result.currency = value["symbol"], "STK", "USD"
        result.exchange = value["exchange"]
        result.primaryExchange = value.get("primary_exchange", "")
        return result

    def _make_app(self) -> Any:
        from ibapi.client import EClient  # type: ignore[import-untyped]
        from ibapi.wrapper import EWrapper  # type: ignore[import-untyped]

        session = self

        class App(EWrapper, EClient):  # type: ignore[misc]
            def __init__(self) -> None:
                EClient.__init__(self, self)

            def nextValidId(self, orderId: Any) -> Any:
                session.next_id = max(session.next_id, orderId)
                session.ready.set()

            def managedAccounts(self, accountsList: Any) -> Any:
                session.accounts = [a for a in accountsList.split(",") if a]
                session.accounts_ready.set()

            def connectionClosed(self) -> Any:
                session.healthy = False

            def error(
                self,
                reqId: Any,
                errorTime: Any,
                errorCode: Any,
                errorString: Any,
                advancedOrderRejectJson: Any = "",
            ) -> Any:
                value = {"request_id": reqId, "code": errorCode, "message": errorString}
                session.errors.append(value)
                if errorCode in {1100, 1300, 504}:
                    session.healthy = False
                if errorCode in INFO_CODES:
                    return
                request = session.requests.get(reqId)
                if request:
                    request["error"] = f"IBKR_{errorCode}: {errorString}"
                    request["event"].set()
                key = session.quote_ids.get(reqId)
                if key:
                    session.quotes[key]["error"] = f"IBKR_{errorCode}: {errorString}"
                if request or key:
                    return
                session._emit(
                    "error", {**value, "order_id": reqId, "client_id": session.profile.client_id}
                )
                if errorCode in {201, 203, 321, 387, 10052}:
                    session._emit(
                        "order",
                        {
                            "order_id": reqId,
                            "client_id": session.profile.client_id,
                            "status": "Rejected",
                            "error": value,
                        },
                    )

            def contractDetails(self, reqId: Any, details: Any) -> Any:
                c = details.contract
                request = session.requests.get(reqId)
                if request:
                    request["rows"].append(
                        {
                            "con_id": c.conId,
                            "symbol": c.symbol,
                            "currency": c.currency,
                            "exchange": c.exchange,
                            "primary_exchange": c.primaryExchange,
                            "min_tick": str(details.minTick),
                            "valid_exchanges": details.validExchanges.split(","),
                            "market_rule_ids": details.marketRuleIds.split(","),
                            "trading_hours": details.tradingHours,
                            "liquid_hours": details.liquidHours,
                            "time_zone": details.timeZoneId,
                            "order_types": details.orderTypes,
                        }
                    )

            def contractDetailsEnd(self, reqId: Any) -> Any:
                if reqId in session.requests:
                    session.requests[reqId]["event"].set()

            def marketRule(self, marketRuleId: Any, priceIncrements: Any) -> Any:
                if marketRuleId in session.requests:
                    request = session.requests[marketRuleId]
                    request["rows"] = [
                        {"low_edge": str(p.lowEdge), "increment": str(p.increment)}
                        for p in priceIncrements
                    ]
                    request["event"].set()

            def marketDataType(self, reqId: Any, marketDataType: Any) -> Any:
                if reqId in session.quote_ids:
                    session.quotes[session.quote_ids[reqId]]["market_data_type"] = marketDataType

            def tickPrice(self, reqId: Any, tickType: Any, price: Any, attrib: Any) -> Any:
                if reqId in session.quote_ids and tickType in {1, 2}:
                    value = session.quotes[session.quote_ids[reqId]]
                    field = "bid" if tickType == 1 else "ask"
                    value[field], value[field + "_at"] = price, time.time()

            def openOrder(self, orderId: Any, contract: Any, order: Any, orderState: Any) -> Any:
                if order.whatIf and orderId in session.requests:
                    session.requests[orderId]["rows"].append(
                        {
                            "status": orderState.status,
                            "initial_margin_change": orderState.initMarginChange,
                            "maintenance_margin_change": orderState.maintMarginChange,
                            "warning": orderState.warningText,
                        }
                    )
                    session.requests[orderId]["event"].set()
                    return
                if order.account != session.profile.expected_account_id:
                    return
                value = {
                    "order_id": orderId,
                    "order_ref": order.orderRef,
                    "client_id": order.clientId,
                    "perm_id": order.permId,
                    "status": orderState.status,
                }
                session.orders[orderId] = value
                session.next_id = max(session.next_id, orderId + 1)
                session._emit("order", value)

            def orderStatus(
                self,
                orderId: Any,
                status: Any,
                filled: Any,
                remaining: Any,
                avgFillPrice: Any,
                permId: Any,
                parentId: Any,
                lastFillPrice: Any,
                clientId: Any,
                whyHeld: Any,
                mktCapPrice: Any,
            ) -> Any:
                value = {
                    "order_id": orderId,
                    "client_id": clientId,
                    "status": status,
                    "filled": str(filled),
                    "remaining": str(remaining),
                    "perm_id": permId,
                }
                session.orders[orderId] = {**session.orders.get(orderId, {}), **value}
                session._emit("order", value)

            def openOrderEnd(self) -> Any:
                session.open_event.set()

            def completedOrder(self, contract: Any, order: Any, orderState: Any) -> Any:
                self.openOrder(order.orderId, contract, order, orderState)

            def completedOrdersEnd(self) -> Any:
                session.completed_event.set()

            def execDetails(self, reqId: Any, contract: Any, execution: Any) -> Any:
                if execution.acctNumber != session.profile.expected_account_id:
                    return
                session._emit(
                    "fill",
                    {
                        "account": execution.acctNumber,
                        "exec_id": execution.execId,
                        "client_id": execution.clientId,
                        "order_id": execution.orderId,
                        "order_ref": execution.orderRef,
                        "perm_id": execution.permId,
                        "con_id": contract.conId,
                        "quantity": str(execution.shares),
                        "cum_qty": str(execution.cumQty),
                        "price": str(execution.price),
                        "side": "BUY" if execution.side == "BOT" else "SELL",
                        "time": execution_time(execution.time),
                    },
                )

            def execDetailsEnd(self, reqId: Any) -> Any:
                if reqId in session.requests:
                    session.requests[reqId]["event"].set()

            def commissionAndFeesReport(self, report: Any) -> Any:
                session._emit(
                    "fees",
                    {
                        "exec_id": report.execId,
                        "commission": str(report.commissionAndFees),
                        "currency": report.currency,
                        "realized_pnl": str(report.realizedPNL),
                    },
                )

            def position(self, account: Any, contract: Any, pos: Any, avgCost: Any) -> Any:
                if account == session.profile.expected_account_id:
                    session.positions[contract.conId] = str(pos)

            def positionEnd(self) -> Any:
                session.position_event.set()

        return App()
