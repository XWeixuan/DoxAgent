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

from .recovery import DELAYS
from .schema import ExecutionProfile, account_fuse

INFO_CODES = {2100, 1101, 1102, 2104, 2106, 2107, 2108, 2158, 10167, 2186}


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
        self.informational_counts: dict[int, int] = {}
        self.orders: dict[int, dict[str, Any]] = {}
        self.quotes: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.quote_ids: dict[int, tuple[Any, ...]] = {}
        self.quote_lock = threading.RLock()
        self.quote_owners: dict[tuple, set[str]] = {}
        self.quote_refresh_after: dict[tuple, float] = {}
        self.contracts: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.positions: dict[int, str] = {}
        self.account_values: dict[str, Any] = {}
        self.portfolio: dict[int, dict[str, Any]] = {}
        self.account_event = threading.Event()
        self.position_event = threading.Event()
        self.open_event = threading.Event()
        self.completed_event = threading.Event()
        self.last_sync = 0.0
        self.healthy = False
        self.connection_generation = 0
        self.retry_after = 0.0
        self.connection_failures = 0

    def _emit(self, kind: Any, value: Any) -> Any:
        self.event_sink(kind, value)

    def connect(self) -> Any:
        with self.connect_lock:
            if self.app and self.app.isConnected() and self.healthy:
                return
            if time.monotonic() < self.retry_after:
                raise ConnectionError("IBKR_RECONNECT_BACKOFF")
            self.close()
            if self.thread and self.thread.is_alive():
                raise ConnectionError("IBKR_PREVIOUS_SOCKET_STILL_RUNNING")
            self.ready.clear()
            self.accounts_ready.clear()
            self.accounts = []
            with self.quote_lock:
                self.quotes.clear()
                self.quote_ids.clear()
            self.orders.clear()
            self.account_values.clear()
            self.portfolio.clear()
            self.last_sync = 0
            self.app = self._make_app()
            try:
                self.app.connect(self.profile.host, self.profile.port, self.profile.client_id)
            except Exception:
                self.connection_failures += 1
                self.retry_after = time.monotonic() + DELAYS[min(self.connection_failures - 1, 4)]
                raise
            self.thread = threading.Thread(target=self.app.run, daemon=True, name="doxagent-ibkr")
            self.thread.start()
            timeout = self.profile.strategy.request_timeout_seconds
            if not self.ready.wait(timeout) or not self.accounts_ready.wait(timeout):
                self.close()
                self.connection_failures += 1
                self.retry_after = time.monotonic() + DELAYS[min(self.connection_failures - 1, 4)]
                raise ConnectionError("IBKR_HANDSHAKE_TIMEOUT")
            account_fuse(self.profile, self.accounts)
            self.healthy = True
            self.connection_generation += 1
            self.connection_failures = 0
            self.retry_after = 0.0
            self._emit(
                "connection",
                {
                    "generation": self.connection_generation,
                    "status": "CONNECTED",
                    "client_id": self.profile.client_id,
                },
            )

    def close(self) -> Any:
        self.healthy = False
        if self.app:
            self.app.disconnect()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
            if not self.thread.is_alive():
                self.thread = None
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

    def subscribe_quote(self, contract, owner, *, refresh=False):
        """Shared streaming L1; only socket setup runs on a background thread."""
        self.connect()
        key = (contract["con_id"], contract["exchange"])
        with self.quote_lock:
            self.quote_owners.setdefault(key, set()).add(owner)
            if refresh and time.monotonic() >= self.quote_refresh_after.get(key, 0):
                self._cancel_quote(key)
                self.quote_refresh_after[key] = time.monotonic() + 30
            if key in self.quotes:
                return
            with self.request_lock:
                self.serial += 1
                request = self.serial
            self.quotes[key] = dict(
                market_data_type=None,
                venue=contract["exchange"],
                generation=(self.connection_generation, request),
                request_id=request,
            )
            self.quote_ids[request] = key
            self.app.reqMarketDataType(1)
            self.app.reqMktData(request, self._contract_object(contract), "", False, False, [])

    def _cancel_quote(self, key):
        value = self.quotes.pop(key, {})
        request = value.get("request_id")
        if request is not None:
            self.quote_ids.pop(request, None)
            if self.app and self.app.isConnected():
                self.app.cancelMktData(request)

    def release_quote(self, contract, owner):
        key = (contract["con_id"], contract["exchange"])
        with self.quote_lock:
            owners = self.quote_owners.get(key, set())
            owners.discard(owner)
            if not owners:
                self.quote_owners.pop(key, None)
                self._cancel_quote(key)
                self.quote_refresh_after.pop(key, None)

    def quote_snapshot(self, contract):
        # Never reset received times on a cache read or account heartbeat.
        if not self.healthy:
            return None
        with self.quote_lock:
            value = self.quotes.get((contract["con_id"], contract["exchange"]))
            return dict(value) if value else None

    def quote(self, contract: dict[str, Any], side: str, *, strategy: Any = None) -> dict[str, Any]:
        strategy = strategy or self.profile.strategy
        owner = "order:" + str(threading.get_ident())
        self.subscribe_quote(contract, owner)
        try:
            deadline = time.monotonic() + strategy.request_timeout_seconds
            required = "ask" if side == "BUY" else "bid"
            while time.monotonic() < deadline:
                value = self.quote_snapshot(contract) or {}
                if value.get("error"):
                    raise RuntimeError(value["error"])
                stamp = value.get(required + "_received_mono", float("-inf"))
                if (
                    value.get("market_data_type") == 1
                    and value.get(required, 0) > 0
                    and float(value.get(required + "_size", 0)) > 0
                    and not value.get(required + "_pending")
                    and 0 <= time.monotonic() - stamp <= strategy.quote_max_age_seconds
                ):
                    return {**value, "captured_at": datetime.now(UTC).isoformat()}
                time.sleep(0.02)
            self._emit(
                "quote_gap",
                dict(con_id=contract["con_id"], side=side, reason="FRESH_QUOTE_UNAVAILABLE"),
            )
            raise TimeoutError("FRESH_QUOTE_UNAVAILABLE")
        finally:
            self.release_quote(contract, owner)

    def atr_seed(self, contract, side, end, bar_size="1 min"):
        """One bounded historical request; bars seed ATR, never historical MFE."""
        self.connect()
        request = self._request()
        try:
            self.app.reqHistoricalData(
                request,
                self._contract_object(contract),
                end,
                "1800 S",
                bar_size,
                side,
                0,
                2,
                False,
                [],
            )
            return self._wait(request)
        finally:
            self.app.cancelHistoricalData(request)
            self.requests.pop(request, None)

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
            started = time.monotonic()
            stages = {}

            def wait_end(event, stage, code):
                before = time.monotonic()
                if not event.wait(self.profile.strategy.request_timeout_seconds):
                    raise TimeoutError(
                        f"{code}: stage={stage} generation={self.connection_generation} "
                        f"elapsed={time.monotonic() - started:.3f}s"
                    )
                stages[stage] = round(time.monotonic() - before, 3)

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
                before = time.monotonic()
                try:
                    self._wait(request)
                except TimeoutError as exc:
                    raise TimeoutError(
                        "IBKR_REQUEST_TIMEOUT: stage=executions "
                        f"generation={self.connection_generation}"
                    ) from exc
                stages["executions"] = round(time.monotonic() - before, 3)
                wait_end(self.open_event, "open_orders", "BROKER_ORDER_SYNC_INCOMPLETE")
                wait_end(self.completed_event, "completed_orders", "BROKER_ORDER_SYNC_INCOMPLETE")
                self.positions = {}
                self.app.reqPositions()
                wait_end(self.position_event, "positions", "BROKER_POSITION_SYNC_INCOMPLETE")
                self.app.cancelPositions()
                self.account_event.clear()
                self.account_values = {}
                self.portfolio = {}
                self.app.reqAccountUpdates(True, self.profile.expected_account_id)
                try:
                    wait_end(self.account_event, "account", "BROKER_ACCOUNT_SYNC_INCOMPLETE")
                finally:
                    self.app.reqAccountUpdates(False, self.profile.expected_account_id)
                self.last_sync = time.monotonic()
                return {
                    "complete": True,
                    "positions": dict(self.positions),
                    "account_values": dict(self.account_values),
                    "portfolio": list(self.portfolio.values()),
                    "at": datetime.now(UTC).isoformat(),
                    "history_scope": "BROKER_SESSION_WINDOW",
                    "connection_generation": self.connection_generation,
                    "stage_seconds": stages,
                    "informational_counts": dict(self.informational_counts),
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
        self,
        contract: dict[str, Any],
        order_id: int,
        *,
        side: Any = "BUY",
        price: Any = 1.0,
        quantity: int = 1,
        order_type: str = "LMT",
        session: str = "RTH",
    ) -> dict[str, Any]:
        # Broker validation only: whatIf=True cannot place a transmitted trading order.
        self._fuse()
        from decimal import Decimal

        from ibapi.order import Order

        order = Order()
        order.account = self.profile.expected_account_id
        order.orderRef = f"DA-WHATIF-{order_id}"
        order.action, order.orderType = side, order_type
        if order_type == "LMT":
            order.lmtPrice = float(price)
        order.totalQuantity, order.whatIf = Decimal(quantity), True
        order.tif, order.outsideRth = "DAY", session != "RTH"
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
                value = {
                    "request_id": reqId,
                    "code": errorCode,
                    "message": errorString,
                    "advanced_order_reject_json": advancedOrderRejectJson,
                    "connection_generation": session.connection_generation,
                }
                session.errors.append(value)
                del session.errors[:-100]
                if errorCode in {1100, 1300, 504, 326}:
                    session.healthy = False
                    session.connection_failures += 1
                    session.retry_after = (
                        time.monotonic() + DELAYS[min(session.connection_failures - 1, 4)]
                    )
                if errorCode == 1101:
                    # Market data was lost; old cached sides cannot survive this generation.
                    with session.quote_lock:
                        for key in list(session.quotes):
                            session._cancel_quote(key)
                if errorCode in INFO_CODES:
                    session.informational_counts[errorCode] = (
                        session.informational_counts.get(errorCode, 0) + 1
                    )
                    return
                request = session.requests.get(reqId)
                if request:
                    request["error"] = f"IBKR_{errorCode}: {errorString}"
                    request["event"].set()
                with session.quote_lock:
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
                with session.quote_lock:
                    key = session.quote_ids.get(reqId)
                    if key:
                        session.quotes[key]["market_data_type"] = marketDataType

            def tickPrice(self, reqId: Any, tickType: Any, price: Any, attrib: Any) -> Any:
                with session.quote_lock:
                    key = session.quote_ids.get(reqId)
                    if key and tickType in {1, 2}:
                        value = session.quotes[key]
                        field = "bid" if tickType == 1 else "ask"
                        value[field] = price
                        value[field + "_at"] = time.time()
                        value[field + "_pending"] = True

            def tickSize(self, reqId, tickType, size):
                with session.quote_lock:
                    key = session.quote_ids.get(reqId)
                    if key and tickType in {0, 3}:
                        value = session.quotes[key]
                        field = "bid" if tickType == 0 else "ask"
                        value[field + "_size"] = str(size)
                        if field in value:
                            value[field + "_pending"] = False
                            value[field + "_received_mono"] = time.monotonic()
                            value[field + "_received_at"] = time.time()

            def historicalData(self, reqId, bar):
                request = session.requests.get(reqId)
                if request:
                    request["rows"].append(
                        dict(date=bar.date, high=bar.high, low=bar.low, close=bar.close)
                    )

            def historicalDataEnd(self, reqId, start, end):
                request = session.requests.get(reqId)
                if request:
                    request["event"].set()

            def openOrder(self, orderId: Any, contract: Any, order: Any, orderState: Any) -> Any:
                if order.whatIf and orderId in session.requests:
                    session.requests[orderId]["rows"].append(
                        {
                            "status": orderState.status,
                            "initial_margin_change": orderState.initMarginChange,
                            "maintenance_margin_change": orderState.maintMarginChange,
                            "warning": orderState.warningText,
                            "reject_reason": getattr(orderState, "rejectReason", ""),
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

            def updateAccountValue(
                self, key: Any, value: Any, currency: Any, accountName: Any
            ) -> Any:
                if accountName != session.profile.expected_account_id:
                    return
                if str(key).lower() == "accountready":
                    session.account_values["AccountReady"] = value
                elif key in {"NetLiquidation", "AvailableFunds", "LookAheadAvailableFunds"}:
                    session.account_values[key] = {"value": value, "currency": currency}

            def updatePortfolio(
                self,
                contract: Any,
                position: Any,
                marketPrice: Any,
                marketValue: Any,
                averageCost: Any,
                unrealizedPNL: Any,
                realizedPNL: Any,
                accountName: Any,
            ) -> Any:
                if accountName != session.profile.expected_account_id:
                    return
                session.portfolio[contract.conId] = {
                    "con_id": contract.conId,
                    "symbol": contract.symbol,
                    "security_type": contract.secType,
                    "currency": contract.currency,
                    "quantity": str(position),
                    "market_value": str(marketValue),
                    "market_price": str(marketPrice),
                }

            def accountDownloadEnd(self, accountName: Any) -> Any:
                if accountName == session.profile.expected_account_id:
                    session.account_event.set()

        return App()
