"""Minimal read-only facade over the official IBKR TWS Python socket API.

This module deliberately stays below the ToolRegistry and MCP layers.  It is
the first-stage acceptance surface for proving that a local TWS socket, the
official ``ibapi`` package, contract resolution, and market-data requests work
before the existing semantic tools are migrated to TWS.

No order, account, portfolio, or execution request is implemented here.
"""

from __future__ import annotations

import importlib
import itertools
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib import metadata
from typing import Any

from doxagent.settings import DoxAgentSettings

_INFORMATIONAL_ERROR_CODES = {
    1101,
    1102,
    2104,
    2106,
    2107,
    2108,
    2158,
    # TWS will continue with delayed data after this notice when delayed
    # market-data mode is enabled; treating it as fatal discards usable ticks.
    10167,
}

_TICK_NAMES = {
    0: "bid_size",
    1: "bid",
    2: "ask",
    3: "ask_size",
    4: "last",
    5: "last_size",
    6: "high",
    7: "low",
    8: "volume",
    9: "close",
    14: "open",
    66: "delayed_bid",
    67: "delayed_ask",
    68: "delayed_last",
    69: "delayed_bid_size",
    70: "delayed_ask_size",
    71: "delayed_last_size",
    72: "delayed_high",
    73: "delayed_low",
    74: "delayed_volume",
    75: "delayed_close",
    76: "delayed_open",
    46: "shortable_tier",
    89: "shortable_shares",
    23: "option_historical_volatility",
    24: "option_implied_volatility",
    27: "option_call_open_interest",
    28: "option_put_open_interest",
    29: "option_call_volume",
    30: "option_put_volume",
}


class IbkrTwsError(RuntimeError):
    """Base error for the local TWS socket integration."""

    code = "ibkr_tws_error"


class IbkrTwsDependencyError(IbkrTwsError):
    """Raised when IBKR's official ``ibapi`` package is not installed."""

    code = "ibkr_tws_dependency_missing"


class IbkrTwsConnectionError(IbkrTwsError):
    """Raised when the configured TWS socket cannot complete its handshake."""

    code = "ibkr_tws_connection_failed"


class IbkrTwsRequestError(IbkrTwsError):
    """Raised when TWS rejects or times out a read-only request."""

    code = "ibkr_tws_request_failed"


@dataclass(frozen=True, slots=True)
class IbkrTwsConfig:
    host: str = "127.0.0.1"
    port: int = 7496
    client_id: int = 71
    timeout_seconds: float = 20.0
    market_data_type: int = 3

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "host.docker.internal"}:
            raise ValueError("IBKR TWS must use loopback or the configured Docker host gateway.")
        if not 1 <= self.port <= 65535:
            raise ValueError("IBKR TWS port must be between 1 and 65535.")
        if self.client_id < 0:
            raise ValueError("IBKR TWS client_id must be non-negative.")
        if not 0.5 <= self.timeout_seconds <= 120:
            raise ValueError("IBKR TWS timeout_seconds must be between 0.5 and 120.")
        if self.market_data_type not in {1, 2, 3, 4}:
            raise ValueError("IBKR market_data_type must be 1, 2, 3, or 4.")

    @classmethod
    def from_settings(cls, settings: DoxAgentSettings) -> IbkrTwsConfig:
        return cls(
            host=settings.ibkr_tws_host,
            port=settings.ibkr_tws_port,
            client_id=settings.ibkr_tws_client_id,
            timeout_seconds=float(settings.ibkr_tws_timeout_seconds),
            market_data_type=settings.ibkr_tws_market_data_type,
        )


@dataclass(frozen=True, slots=True)
class ResolvedContract:
    con_id: int
    symbol: str
    security_type: str
    exchange: str
    currency: str
    primary_exchange: str | None = None
    local_symbol: str | None = None
    trading_class: str | None = None
    long_name: str | None = None
    market_name: str | None = None
    valid_exchanges: tuple[str, ...] = ()
    time_zone_id: str | None = None
    min_tick: float | None = None
    last_trade_date_or_contract_month: str | None = None
    strike: float | None = None
    right: str | None = None
    multiplier: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "con_id": self.con_id,
            "symbol": self.symbol,
            "security_type": self.security_type,
            "exchange": self.exchange,
            "currency": self.currency,
            "primary_exchange": self.primary_exchange,
            "local_symbol": self.local_symbol,
            "trading_class": self.trading_class,
            "long_name": self.long_name,
            "market_name": self.market_name,
            "valid_exchanges": list(self.valid_exchanges),
            "time_zone_id": self.time_zone_id,
            "min_tick": self.min_tick,
            "last_trade_date_or_contract_month": self.last_trade_date_or_contract_month,
            "strike": self.strike,
            "right": self.right,
            "multiplier": self.multiplier,
        }


@dataclass(frozen=True, slots=True)
class IbkrApiError:
    request_id: int
    error_code: int
    message: str
    advanced_reject: str | None = None

    @property
    def informational(self) -> bool:
        return self.error_code in _INFORMATIONAL_ERROR_CODES


class IbkrTwsSession:
    """A small synchronous facade around the official asynchronous client."""

    def __init__(
        self,
        config: IbkrTwsConfig,
        *,
        app_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config
        self._app_factory = app_factory or _create_official_app
        self._app: Any | None = None
        self._thread: threading.Thread | None = None
        self._request_ids = itertools.count(1_000)

    def __enter__(self) -> IbkrTwsSession:
        self.connect()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def connect(self) -> dict[str, Any]:
        if self._app is not None:
            raise IbkrTwsConnectionError("This TWS session has already been started.")
        app = self._app_factory()
        self._app = app
        try:
            connected = app.connect(
                self.config.host,
                self.config.port,
                clientId=self.config.client_id,
            )
        except OSError as exc:
            self._app = None
            raise IbkrTwsConnectionError(
                f"Cannot connect to TWS at {self.config.host}:{self.config.port}: {exc}"
            ) from exc
        if connected is False:
            self._app = None
            raise IbkrTwsConnectionError(
                f"TWS rejected the socket connection at {self.config.host}:{self.config.port}."
            )
        self._thread = threading.Thread(target=app.run, name="ibkr-tws-api", daemon=True)
        self._thread.start()
        if not app.ready_event.wait(self.config.timeout_seconds):
            errors = _noninformational_errors(app.api_errors)
            self.close()
            suffix = f" Latest API error: {_format_error(errors[-1])}." if errors else ""
            raise IbkrTwsConnectionError(
                "TWS socket opened but the nextValidId handshake timed out."
                f"{suffix} Confirm API Settings and that client ID {self.config.client_id} is free."
            )
        return {
            "connected": bool(app.isConnected()),
            "host": self.config.host,
            "port": self.config.port,
            "client_id": self.config.client_id,
            "server_version": int(app.serverVersion()),
            "connection_time": _decode_connection_time(app.twsConnectionTime()),
            "ibapi_version": _installed_ibapi_version(),
        }

    def close(self) -> None:
        app = self._app
        self._app = None
        if app is None:
            return
        try:
            if app.isConnected():
                app.disconnect()
        finally:
            thread = self._thread
            self._thread = None
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)

    def current_time(self) -> str:
        app = self._require_app()
        app.current_time_value = None
        app.current_time_event.clear()
        app.reqCurrentTime()
        self._wait(app.current_time_event, operation="current time")
        if app.current_time_value is None:
            raise IbkrTwsRequestError("TWS returned no current-time value.")
        return datetime.fromtimestamp(int(app.current_time_value), tz=UTC).isoformat()

    def resolve_stock(
        self,
        symbol: str,
        *,
        currency: str = "USD",
        primary_exchange: str | None = None,
    ) -> list[ResolvedContract]:
        app = self._require_app()
        request_id = next(self._request_ids)
        contract = app.contract_type()
        contract.symbol = _required_upper(symbol, "symbol")
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = _required_upper(currency, "currency")
        if primary_exchange:
            contract.primaryExchange = primary_exchange.upper()
        app.contract_results[request_id] = []
        event = threading.Event()
        app.contract_events[request_id] = event
        try:
            app.reqContractDetails(request_id, contract)
            self._wait(event, request_id=request_id, operation="contract details")
            self._raise_request_error(request_id, operation="contract details")
            results = [_resolved_contract(details) for details in app.contract_results[request_id]]
        finally:
            app.contract_events.pop(request_id, None)
            app.contract_results.pop(request_id, None)
        if not results:
            raise IbkrTwsRequestError(
                f"TWS returned no SMART stock contract for {contract.symbol}/{contract.currency}."
            )
        return results

    def resolve_future_chain(
        self,
        symbol: str,
        *,
        exchange: str,
        currency: str = "USD",
    ) -> list[ResolvedContract]:
        app = self._require_app()
        request_id = next(self._request_ids)
        contract = app.contract_type()
        contract.symbol = _required_upper(symbol, "symbol")
        contract.secType = "FUT"
        contract.exchange = _required_upper(exchange, "exchange")
        contract.currency = _required_upper(currency, "currency")
        app.contract_results[request_id] = []
        event = threading.Event()
        app.contract_events[request_id] = event
        try:
            app.reqContractDetails(request_id, contract)
            self._wait(event, request_id=request_id, operation="future contract details")
            self._raise_request_error(request_id, operation="future contract details")
            results = [_resolved_contract(details) for details in app.contract_results[request_id]]
        finally:
            app.contract_events.pop(request_id, None)
            app.contract_results.pop(request_id, None)
        if not results:
            raise IbkrTwsRequestError(f"TWS returned no {exchange} future contracts for {symbol}.")
        return results

    def option_parameters(self, underlying: ResolvedContract) -> list[dict[str, Any]]:
        app = self._require_app()
        request_id = next(self._request_ids)
        event = threading.Event()
        app.option_parameter_events[request_id] = event
        app.option_parameter_results[request_id] = []
        try:
            app.reqSecDefOptParams(
                request_id,
                underlying.symbol,
                "",
                underlying.security_type,
                underlying.con_id,
            )
            self._wait(event, request_id=request_id, operation="option parameters")
            self._raise_request_error(request_id, operation="option parameters")
            rows = list(app.option_parameter_results[request_id])
        finally:
            app.option_parameter_events.pop(request_id, None)
            app.option_parameter_results.pop(request_id, None)
        if not rows:
            raise IbkrTwsRequestError("TWS returned no option-chain parameters.")
        return rows

    def market_snapshot(
        self,
        contract: ResolvedContract,
        *,
        generic_ticks: str = "",
    ) -> dict[str, Any]:
        app = self._require_app()
        request_id = next(self._request_ids)
        event = threading.Event()
        app.snapshot_events[request_id] = event
        app.snapshot_results[request_id] = {}
        app.snapshot_market_types[request_id] = None
        app.option_computation_results[request_id] = {}
        app.reqMarketDataType(self.config.market_data_type)
        streaming = bool(generic_ticks)
        try:
            app.reqMktData(
                request_id,
                _to_official_contract(app, contract),
                generic_ticks,
                not streaming,
                False,
                [],
            )
            if streaming:
                # IBKR rejects generic ticks on regulatory snapshots (error 321).
                # Use a short read-only streaming capture, then cancel it
                # deterministically after the requested generic fields arrive.
                deadline = time.monotonic() + min(self.config.timeout_seconds, 3.0)
                first_value_at: float | None = None
                while time.monotonic() < deadline:
                    self._raise_request_error(request_id, operation="market data capture")
                    has_values = bool(app.snapshot_results[request_id]) or bool(
                        app.option_computation_results[request_id]
                    )
                    if has_values and first_value_at is None:
                        first_value_at = time.monotonic()
                    if first_value_at is not None and time.monotonic() - first_value_at >= 0.75:
                        break
                    event.wait(0.1)
                    event.clear()
            else:
                self._wait(event, request_id=request_id, operation="market snapshot")
            self._raise_request_error(request_id, operation="market snapshot")
            values = dict(sorted(app.snapshot_results[request_id].items()))
            market_data_type = app.snapshot_market_types[request_id]
            option_computation = dict(app.option_computation_results[request_id])
        finally:
            if streaming:
                try:
                    app.cancelMktData(request_id)
                except Exception:
                    pass
            # A true reqMktData snapshot ends automatically at tickSnapshotEnd;
            # only the bounded generic-tick stream is cancelled explicitly.
            app.snapshot_events.pop(request_id, None)
            app.snapshot_results.pop(request_id, None)
            app.snapshot_market_types.pop(request_id, None)
            app.option_computation_results.pop(request_id, None)
        if not values:
            raise IbkrTwsRequestError(
                "TWS completed the snapshot but returned no usable ticks. "
                "Check account market-data permissions or TWS market-data configuration."
            )
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "captured_at": datetime.now(UTC).isoformat(),
            "market_data_type": market_data_type,
            "values": values,
            "option_computation": option_computation,
        }

    def historical_ticks(
        self,
        contract: ResolvedContract,
        *,
        start_datetime: str = "",
        end_datetime: str = "",
        number_of_ticks: int = 100,
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> dict[str, Any]:
        if not 1 <= number_of_ticks <= 1_000:
            raise ValueError("number_of_ticks must be between 1 and 1000.")
        if what_to_show not in {"TRADES", "BID_ASK", "MIDPOINT"}:
            raise ValueError("what_to_show must be TRADES, BID_ASK, or MIDPOINT.")
        if bool(start_datetime) == bool(end_datetime):
            raise ValueError("Provide exactly one of start_datetime or end_datetime.")
        app = self._require_app()
        request_id = next(self._request_ids)
        event = threading.Event()
        app.historical_tick_events[request_id] = event
        app.historical_tick_results[request_id] = []
        try:
            app.reqHistoricalTicks(
                request_id,
                _to_official_contract(app, contract),
                start_datetime,
                end_datetime,
                number_of_ticks,
                what_to_show,
                int(use_rth),
                False,
                [],
            )
            self._wait(event, request_id=request_id, operation="historical ticks")
            self._raise_request_error(request_id, operation="historical ticks")
            ticks = list(app.historical_tick_results[request_id])
        finally:
            app.historical_tick_events.pop(request_id, None)
            app.historical_tick_results.pop(request_id, None)
        if not ticks:
            raise IbkrTwsRequestError("TWS returned no historical ticks for the requested window.")
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "what_to_show": what_to_show,
            "use_rth": use_rth,
            "event_count": len(ticks),
            "events": ticks,
        }

    def historical_bars(
        self,
        contract: ResolvedContract,
        *,
        duration: str = "1 M",
        bar_size: str = "1 day",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> dict[str, Any]:
        app = self._require_app()
        request_id = next(self._request_ids)
        event = threading.Event()
        app.historical_events[request_id] = event
        app.historical_results[request_id] = []
        app.reqMarketDataType(self.config.market_data_type)
        try:
            app.reqHistoricalData(
                request_id,
                _to_official_contract(app, contract),
                "",
                duration,
                bar_size,
                what_to_show,
                int(use_rth),
                1,
                False,
                [],
            )
            self._wait(event, request_id=request_id, operation="historical bars")
            self._raise_request_error(request_id, operation="historical bars")
            bars = list(app.historical_results[request_id])
        finally:
            try:
                app.cancelHistoricalData(request_id)
            except Exception:
                pass
            app.historical_events.pop(request_id, None)
            app.historical_results.pop(request_id, None)
        if not bars:
            raise IbkrTwsRequestError(
                "TWS completed the historical request but returned no bars. "
                "Check the contract, session, and market-data entitlement."
            )
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "duration": duration,
            "bar_size": bar_size,
            "what_to_show": what_to_show,
            "use_rth": use_rth,
            "bars": bars,
        }

    def capture_trade_ticks(
        self,
        contract: ResolvedContract,
        *,
        duration_seconds: float = 5.0,
        max_events: int = 100,
    ) -> dict[str, Any]:
        if not 0.1 <= duration_seconds <= 30:
            raise ValueError("duration_seconds must be between 0.1 and 30.")
        if not 1 <= max_events <= 1_000:
            raise ValueError("max_events must be between 1 and 1000.")
        app = self._require_app()
        request_id = next(self._request_ids)
        event = threading.Event()
        app.tick_by_tick_events[request_id] = event
        app.tick_by_tick_results[request_id] = []
        app.reqMarketDataType(self.config.market_data_type)
        deadline = time.monotonic() + duration_seconds
        try:
            app.reqTickByTickData(
                request_id,
                _to_official_contract(app, contract),
                "AllLast",
                0,
                False,
            )
            while len(app.tick_by_tick_results[request_id]) < max_events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                event.wait(min(remaining, 0.25))
                event.clear()
                self._raise_request_error(request_id, operation="trade ticks")
            self._raise_request_error(request_id, operation="trade ticks")
            ticks = list(app.tick_by_tick_results[request_id][:max_events])
        finally:
            try:
                app.cancelTickByTickData(request_id)
            except Exception:
                pass
            app.tick_by_tick_events.pop(request_id, None)
            app.tick_by_tick_results.pop(request_id, None)
        if not ticks:
            raise IbkrTwsRequestError(
                "TWS returned no trade ticks during the bounded capture window. "
                "Check the session, market hours, and market-data entitlement."
            )
        return {
            "con_id": contract.con_id,
            "symbol": contract.symbol,
            "duration_seconds": duration_seconds,
            "event_count": len(ticks),
            "events": ticks,
        }

    def diagnostic_errors(self) -> list[dict[str, Any]]:
        app = self._app
        if app is None:
            return []
        return [
            {
                "request_id": error.request_id,
                "error_code": error.error_code,
                "message": error.message,
                "informational": error.informational,
            }
            for error in app.api_errors
        ]

    def _require_app(self) -> Any:
        if self._app is None or not self._app.isConnected():
            raise IbkrTwsConnectionError("TWS session is not connected.")
        return self._app

    def _wait(
        self,
        event: threading.Event,
        *,
        operation: str,
        request_id: int | None = None,
    ) -> None:
        if event.wait(self.config.timeout_seconds):
            return
        if request_id is not None:
            self._raise_request_error(request_id, operation=operation)
        raise IbkrTwsRequestError(
            f"Timed out after {self.config.timeout_seconds:g}s waiting for TWS {operation}."
        )

    def _raise_request_error(self, request_id: int, *, operation: str) -> None:
        app = self._require_app()
        errors = _noninformational_errors(app.request_errors.get(request_id, []))
        if errors:
            raise IbkrTwsRequestError(f"TWS {operation} failed: {_format_error(errors[-1])}")


def _create_official_app() -> Any:
    try:
        EClient = importlib.import_module("ibapi.client").EClient
        Contract = importlib.import_module("ibapi.contract").Contract
        EWrapper = importlib.import_module("ibapi.wrapper").EWrapper
    except ModuleNotFoundError as exc:
        raise IbkrTwsDependencyError(
            "IBKR's official ibapi package is not installed in this Python environment. "
            "Install TWS API Latest, then run its source/pythonclient/setup.py with the "
            "DoxAgent virtual-environment Python interpreter."
        ) from exc

    class OfficialTwsApp(EWrapper, EClient):  # type: ignore[misc,valid-type]
        def __init__(self) -> None:
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self.contract_type = Contract
            self.ready_event = threading.Event()
            self.current_time_event = threading.Event()
            self.current_time_value: int | None = None
            self.api_errors: list[IbkrApiError] = []
            self.request_errors: dict[int, list[IbkrApiError]] = {}
            self.contract_events: dict[int, threading.Event] = {}
            self.contract_results: dict[int, list[Any]] = {}
            self.snapshot_events: dict[int, threading.Event] = {}
            self.snapshot_results: dict[int, dict[str, int | float | str]] = {}
            self.snapshot_market_types: dict[int, int | None] = {}
            self.historical_events: dict[int, threading.Event] = {}
            self.historical_results: dict[int, list[dict[str, Any]]] = {}
            self.tick_by_tick_events: dict[int, threading.Event] = {}
            self.tick_by_tick_results: dict[int, list[dict[str, Any]]] = {}
            self.option_parameter_events: dict[int, threading.Event] = {}
            self.option_parameter_results: dict[int, list[dict[str, Any]]] = {}
            self.option_computation_results: dict[int, dict[str, Any]] = {}
            self.historical_tick_events: dict[int, threading.Event] = {}
            self.historical_tick_results: dict[int, list[dict[str, Any]]] = {}

        def nextValidId(self, _order_id: int) -> None:  # noqa: N802
            self.ready_event.set()

        def currentTime(self, time_value: int) -> None:  # noqa: N802
            self.current_time_value = time_value
            self.current_time_event.set()

        def error(self, request_id: int, *args: Any) -> None:
            parsed = _parse_ib_error(request_id, args)
            self.api_errors.append(parsed)
            self.request_errors.setdefault(request_id, []).append(parsed)
            if parsed.informational:
                return
            for events in (
                self.contract_events,
                self.snapshot_events,
                self.historical_events,
                self.tick_by_tick_events,
                self.option_parameter_events,
                self.historical_tick_events,
            ):
                event = events.get(request_id)
                if event is not None:
                    event.set()

        def contractDetails(self, request_id: int, details: Any) -> None:  # noqa: N802
            self.contract_results.setdefault(request_id, []).append(details)

        def contractDetailsEnd(self, request_id: int) -> None:  # noqa: N802
            event = self.contract_events.get(request_id)
            if event is not None:
                event.set()

        def marketDataType(self, request_id: int, market_data_type: int) -> None:  # noqa: N802
            if request_id in self.snapshot_market_types:
                self.snapshot_market_types[request_id] = market_data_type

        def tickPrice(self, request_id: int, tick_type: int, price: float, _attrib: Any) -> None:  # noqa: N802,E501
            self._record_tick(request_id, tick_type, price)

        def tickSize(self, request_id: int, tick_type: int, size: Decimal) -> None:  # noqa: N802
            self._record_tick(request_id, tick_type, size)

        def tickGeneric(self, request_id: int, tick_type: int, value: float) -> None:  # noqa: N802
            self._record_tick(request_id, tick_type, value)

        def tickString(self, request_id: int, tick_type: int, value: str) -> None:  # noqa: N802
            self._record_tick(request_id, tick_type, value)

        def tickSnapshotEnd(self, request_id: int) -> None:  # noqa: N802
            event = self.snapshot_events.get(request_id)
            if event is not None:
                event.set()

        def tickOptionComputation(  # noqa: N802
            self,
            request_id: int,
            tick_type: int,
            _tick_attrib: int,
            implied_vol: float,
            delta: float,
            opt_price: float,
            pv_dividend: float,
            gamma: float,
            vega: float,
            theta: float,
            underlying_price: float,
        ) -> None:
            target = self.option_computation_results.get(request_id)
            if target is None:
                return
            values = {
                "tick_type": tick_type,
                "implied_volatility": _json_number(implied_vol),
                "delta": _json_number(delta),
                "option_price": _json_number(opt_price),
                "pv_dividend": _json_number(pv_dividend),
                "gamma": _json_number(gamma),
                "vega": _json_number(vega),
                "theta": _json_number(theta),
                "underlying_price": _json_number(underlying_price),
            }
            target.update({key: value for key, value in values.items() if value is not None})

        def securityDefinitionOptionParameter(  # noqa: N802
            self,
            request_id: int,
            exchange: str,
            underlying_con_id: int,
            trading_class: str,
            multiplier: str,
            expirations: set[str],
            strikes: set[float],
        ) -> None:
            self.option_parameter_results.setdefault(request_id, []).append(
                {
                    "exchange": exchange,
                    "underlying_con_id": underlying_con_id,
                    "trading_class": trading_class,
                    "multiplier": multiplier,
                    "expirations": sorted(str(item) for item in expirations),
                    "strikes": sorted(float(item) for item in strikes),
                }
            )

        def securityDefinitionOptionParameterEnd(self, request_id: int) -> None:  # noqa: N802
            event = self.option_parameter_events.get(request_id)
            if event is not None:
                event.set()

        def historicalData(self, request_id: int, bar: Any) -> None:  # noqa: N802
            self.historical_results.setdefault(request_id, []).append(
                {
                    "date": str(bar.date),
                    "open": _json_number(bar.open),
                    "high": _json_number(bar.high),
                    "low": _json_number(bar.low),
                    "close": _json_number(bar.close),
                    "volume": _json_number(bar.volume),
                    "wap": _json_number(bar.wap),
                    "bar_count": int(bar.barCount),
                }
            )

        def historicalDataEnd(self, request_id: int, _start: str, _end: str) -> None:  # noqa: N802,E501
            event = self.historical_events.get(request_id)
            if event is not None:
                event.set()

        def tickByTickAllLast(  # noqa: N802
            self,
            request_id: int,
            tick_type: int,
            timestamp: int,
            price: float,
            size: Decimal,
            tick_attrib_last: Any,
            exchange: str,
            special_conditions: str,
        ) -> None:
            target = self.tick_by_tick_results.get(request_id)
            if target is None:
                return
            target.append(
                {
                    "timestamp": datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat(),
                    "tick_type": "last" if tick_type == 1 else "all_last",
                    "price": _json_number(price),
                    "size": _json_number(size),
                    "exchange": str(exchange) or None,
                    "special_conditions": str(special_conditions) or None,
                    "past_limit": bool(getattr(tick_attrib_last, "pastLimit", False)),
                    "unreported": bool(getattr(tick_attrib_last, "unreported", False)),
                }
            )
            event = self.tick_by_tick_events.get(request_id)
            if event is not None:
                event.set()

        def historicalTicksLast(self, request_id: int, ticks: list[Any], done: bool) -> None:  # noqa: N802,E501
            target = self.historical_tick_results.setdefault(request_id, [])
            for tick in ticks:
                attrib = getattr(tick, "tickAttribLast", None)
                target.append(
                    {
                        "timestamp": datetime.fromtimestamp(int(tick.time), tz=UTC).isoformat(),
                        "price": _json_number(tick.price),
                        "size": _json_number(tick.size),
                        "exchange": str(getattr(tick, "exchange", "")) or None,
                        "special_conditions": str(getattr(tick, "specialConditions", "")) or None,
                        "past_limit": bool(getattr(attrib, "pastLimit", False)),
                        "unreported": bool(getattr(attrib, "unreported", False)),
                    }
                )
            if done:
                event = self.historical_tick_events.get(request_id)
                if event is not None:
                    event.set()

        def historicalTicksBidAsk(self, request_id: int, ticks: list[Any], done: bool) -> None:  # noqa: N802,E501
            target = self.historical_tick_results.setdefault(request_id, [])
            for tick in ticks:
                target.append(
                    {
                        "timestamp": datetime.fromtimestamp(int(tick.time), tz=UTC).isoformat(),
                        "bid_price": _json_number(tick.priceBid),
                        "ask_price": _json_number(tick.priceAsk),
                        "bid_size": _json_number(tick.sizeBid),
                        "ask_size": _json_number(tick.sizeAsk),
                    }
                )
            if done:
                event = self.historical_tick_events.get(request_id)
                if event is not None:
                    event.set()

        def historicalTicks(self, request_id: int, ticks: list[Any], done: bool) -> None:  # noqa: N802,E501
            target = self.historical_tick_results.setdefault(request_id, [])
            for tick in ticks:
                target.append(
                    {
                        "timestamp": datetime.fromtimestamp(int(tick.time), tz=UTC).isoformat(),
                        "price": _json_number(tick.price),
                        "size": _json_number(tick.size),
                    }
                )
            if done:
                event = self.historical_tick_events.get(request_id)
                if event is not None:
                    event.set()

        def _record_tick(self, request_id: int, tick_type: int, value: object) -> None:
            compact = _json_tick_value(value)
            if compact is None:
                return
            target = self.snapshot_results.get(request_id)
            if target is not None:
                target[_TICK_NAMES.get(tick_type, f"tick_{tick_type}")] = compact

    return OfficialTwsApp()


def _resolved_contract(details: Any) -> ResolvedContract:
    contract = details.contract
    valid_exchanges = tuple(
        item.strip() for item in str(details.validExchanges or "").split(",") if item.strip()
    )
    return ResolvedContract(
        con_id=int(contract.conId),
        symbol=str(contract.symbol),
        security_type=str(contract.secType),
        exchange=str(contract.exchange or "SMART"),
        currency=str(contract.currency),
        primary_exchange=str(contract.primaryExchange) or None,
        local_symbol=str(contract.localSymbol) or None,
        trading_class=str(contract.tradingClass) or None,
        long_name=str(details.longName) or None,
        market_name=str(details.marketName) or None,
        valid_exchanges=valid_exchanges,
        time_zone_id=str(details.timeZoneId) or None,
        min_tick=_json_number(details.minTick),
        last_trade_date_or_contract_month=(str(contract.lastTradeDateOrContractMonth) or None),
        strike=_json_number(contract.strike),
        right=str(contract.right) or None,
        multiplier=str(contract.multiplier) or None,
    )


def _to_official_contract(app: Any, resolved: ResolvedContract) -> Any:
    contract = app.contract_type()
    contract.conId = resolved.con_id
    contract.symbol = resolved.symbol
    contract.secType = resolved.security_type
    contract.exchange = "SMART"
    contract.currency = resolved.currency
    if resolved.primary_exchange:
        contract.primaryExchange = resolved.primary_exchange
    if resolved.last_trade_date_or_contract_month:
        contract.lastTradeDateOrContractMonth = resolved.last_trade_date_or_contract_month
    if resolved.strike is not None:
        contract.strike = resolved.strike
    if resolved.right:
        contract.right = resolved.right
    if resolved.multiplier:
        contract.multiplier = resolved.multiplier
    if resolved.trading_class:
        contract.tradingClass = resolved.trading_class
    return contract


def _parse_ib_error(request_id: int, args: tuple[Any, ...]) -> IbkrApiError:
    if len(args) >= 3 and isinstance(args[0], int) and isinstance(args[1], int):
        # API 10.33+ adds errorTime before errorCode.
        error_code = int(args[1])
        message = str(args[2])
        advanced = str(args[3]) if len(args) > 3 and args[3] else None
    elif len(args) >= 2:
        error_code = int(args[0])
        message = str(args[1])
        advanced = str(args[2]) if len(args) > 2 and args[2] else None
    else:
        error_code = -1
        message = " ".join(str(item) for item in args) or "Unknown TWS API error"
        advanced = None
    return IbkrApiError(
        request_id=int(request_id),
        error_code=error_code,
        message=message,
        advanced_reject=advanced,
    )


def _noninformational_errors(errors: list[IbkrApiError]) -> list[IbkrApiError]:
    return [error for error in errors if not error.informational]


def _format_error(error: IbkrApiError) -> str:
    return f"[{error.error_code}] {error.message}"


def _json_tick_value(value: object) -> int | float | str | None:
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric) or abs(numeric) > 1e100 or numeric == -1:
            return None
        return int(numeric) if numeric.is_integer() else numeric
    text = str(value).strip()
    return text or None


def _json_number(value: object) -> int | float | None:
    compact = _json_tick_value(value)
    return compact if isinstance(compact, (int, float)) else None


def _required_upper(value: str, field: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError(f"{field} is required.")
    return normalized


def _installed_ibapi_version() -> str:
    try:
        return metadata.version("ibapi")
    except metadata.PackageNotFoundError:
        return "unknown"


def _decode_connection_time(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
