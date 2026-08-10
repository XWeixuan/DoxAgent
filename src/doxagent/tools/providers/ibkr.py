"""Read-only IBKR semantic tools backed by the official local TWS API."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    TTLCache,
    _input_list,
    _input_str,
    _input_str_any,
)
from doxagent.tools.providers.ibkr_tws import (
    IbkrTwsConfig,
    IbkrTwsConnectionError,
    IbkrTwsDependencyError,
    IbkrTwsRequestError,
    IbkrTwsSession,
    ResolvedContract,
)
from doxagent.tools.schema import ToolRequest, ToolResult

SessionFactory = Callable[[IbkrTwsConfig], IbkrTwsSession]

_PERIODS = {
    "1d": "1 D",
    "1w": "1 W",
    "1m": "1 M",
    "3m": "3 M",
    "6m": "6 M",
    "1y": "1 Y",
    "2y": "2 Y",
    "5y": "5 Y",
    "10y": "10 Y",
}
_BARS = {
    "1min": "1 min",
    "5min": "5 mins",
    "15min": "15 mins",
    "30min": "30 mins",
    "1h": "1 hour",
    "1d": "1 day",
    "1w": "1 week",
}
_CLIENT_PORTAL_FIELD_NAMES = {
    "31": "last",
    "84": "bid",
    "85": "ask_size",
    "86": "ask",
    "87": "volume",
}


class _IbkrClient(BaseRealToolClient):
    source_scope = "ibkr"
    title = "IBKR TWS market data"
    _session_lock = threading.Lock()

    def __init__(
        self,
        settings: Any,
        cache: TTLCache | None = None,
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        super().__init__(settings, cache)
        self._session_factory = session_factory or IbkrTwsSession

    @contextmanager
    def _session(self) -> Iterator[IbkrTwsSession]:
        if not self.settings.ibkr_tws_enabled:
            raise IbkrTwsDependencyError(
                "IBKR TWS tools are disabled; set IBKR_TWS_ENABLED=true after local smoke passes."
            )
        config = IbkrTwsConfig.from_settings(self.settings)
        with self._session_lock:
            session = self._session_factory(config)
            try:
                session.connect()
                yield session
            finally:
                session.close()

    def _provider_failure(self, request: ToolRequest, exc: Exception) -> ToolResult:
        if isinstance(exc, IbkrTwsDependencyError):
            return self._failure(
                request,
                code="provider_not_configured",
                message=str(exc),
            )
        if isinstance(exc, IbkrTwsConnectionError):
            return self._failure(
                request,
                code="ibkr_gateway_unavailable",
                message=str(exc),
                retryable=True,
            )
        if isinstance(exc, IbkrTwsRequestError):
            return self._failure(
                request,
                code="market_data_unavailable",
                message=str(exc),
                retryable=False,
            )
        return self._handle_exception(request, exc)

    def _success_result(
        self,
        request: ToolRequest,
        *,
        symbol: str,
        output: JsonObject,
        summary: str,
        con_id: int | None = None,
    ) -> ToolResult:
        source_suffix = str(con_id) if con_id is not None else symbol
        return self._success(
            request,
            output={
                "provider": "ibkr",
                "transport": "official_tws_socket",
                "symbol": symbol,
                **output,
            },
            raw=output,
            source_kind="market_data",
            source_id=f"ibkr:{self.source_scope}:{source_suffix}",
            title=self.title,
            summary=summary,
            source_scope=self.source_scope,
            confidence=0.9,
            metadata={
                "symbol": symbol,
                "con_id": con_id,
                "transport": "official_tws_socket",
            },
        )


class IbkrContractSearchClient(_IbkrClient):
    source_scope = "ibkr_contract_search"
    title = "IBKR TWS contract search"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            currency = _input_str(request, "currency", "USD").upper()
            primary_exchange = _input_str(request, "primary_exchange", "") or None
            with self._session() as session:
                contracts = session.resolve_stock(
                    symbol,
                    currency=currency,
                    primary_exchange=primary_exchange,
                )
            compact = [_compact_contract(item) for item in contracts[:10]]
            return self._success_result(
                request,
                symbol=symbol,
                con_id=contracts[0].con_id,
                output={"contracts": compact, "contract_count": len(compact)},
                summary=f"Resolved {len(compact)} IBKR TWS stock contract(s) for {symbol}.",
            )
        except Exception as exc:
            return self._provider_failure(request, exc)


class IbkrMarketSnapshotClient(_IbkrClient):
    source_scope = "ibkr_market_snapshot"
    title = "IBKR TWS market snapshot"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            conids = _input_list(request, "conids")
            conid = _input_str(request, "conid", "") or (conids[0] if conids else "")
            if len(conids) > 1:
                raise ValueError("The local TWS snapshot tool accepts one conid per call.")
            with self._session() as session:
                contract = _resolve_or_build_contract(session, symbol, conid)
                snapshot = session.market_snapshot(contract)
            requested_fields = _input_list(request, "fields")
            values = _filter_snapshot_fields(snapshot["values"], requested_fields)
            if not values:
                raise IbkrTwsRequestError(
                    "TWS returned no requested snapshot fields for this contract."
                )
            return self._success_result(
                request,
                symbol=symbol,
                con_id=contract.con_id,
                output={
                    "con_id": contract.con_id,
                    "market_data_type": snapshot["market_data_type"],
                    "snapshot": values,
                },
                summary=f"Retrieved {len(values)} bounded IBKR TWS snapshot field(s) for {symbol}.",
            )
        except Exception as exc:
            return self._provider_failure(request, exc)


class IbkrMarketHistoryClient(_IbkrClient):
    source_scope = "ibkr_market_history"
    title = "IBKR TWS market history"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            conid = _input_str(request, "conid", "")
            period = _input_str(request, "period", "1m").lower()
            bar = _input_str(request, "bar", "1d").lower()
            if period not in _PERIODS:
                raise ValueError("period is not an allowed IBKR TWS history period.")
            if bar not in _BARS:
                raise ValueError("bar is not an allowed IBKR TWS bar size.")
            with self._session() as session:
                contract = _resolve_or_build_contract(session, symbol, conid)
                history = session.historical_bars(
                    contract,
                    duration=_PERIODS[period],
                    bar_size=_BARS[bar],
                    use_rth=_bool_input(request.input.get("outside_rth"), inverted=True),
                )
            bars = history["bars"]
            return self._success_result(
                request,
                symbol=symbol,
                con_id=contract.con_id,
                output={
                    "con_id": contract.con_id,
                    "period": period,
                    "bar": bar,
                    "outside_rth": not history["use_rth"],
                    "as_of": bars[-1]["date"],
                    "bars": bars,
                },
                summary=f"Retrieved {len(bars)} IBKR TWS historical {bar} bar(s) for {symbol}.",
            )
        except Exception as exc:
            return self._provider_failure(request, exc)


class IbkrTradeTapeClient(_IbkrClient):
    source_scope = "ibkr_trade_tape"
    title = "IBKR TWS trade tape"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            conid = _input_str(request, "conid", "")
            duration_seconds = float(request.input.get("duration_seconds", 5.0))
            max_events = int(request.input.get("max_events", 100))
            with self._session() as session:
                contract = _resolve_or_build_contract(session, symbol, conid)
                tape = session.capture_trade_ticks(
                    contract,
                    duration_seconds=duration_seconds,
                    max_events=max_events,
                )
            return self._success_result(
                request,
                symbol=symbol,
                con_id=contract.con_id,
                output=tape,
                summary=f"Captured {tape['event_count']} IBKR TWS trade tick(s) for {symbol}.",
            )
        except Exception as exc:
            return self._provider_failure(request, exc)


def _resolve_or_build_contract(
    session: IbkrTwsSession,
    symbol: str,
    conid: str,
) -> ResolvedContract:
    if conid:
        try:
            parsed = int(conid)
        except ValueError as exc:
            raise ValueError("conid must be an integer IBKR contract identifier.") from exc
        return ResolvedContract(
            con_id=parsed,
            symbol=symbol,
            security_type="STK",
            exchange="SMART",
            currency="USD",
        )
    return session.resolve_stock(symbol)[0]


def _compact_contract(contract: ResolvedContract) -> JsonObject:
    return {
        "con_id": contract.con_id,
        "symbol": contract.symbol,
        "security_type": contract.security_type,
        "exchange": contract.exchange,
        "primary_exchange": contract.primary_exchange,
        "currency": contract.currency,
        "local_symbol": contract.local_symbol,
        "trading_class": contract.trading_class,
        "long_name": contract.long_name,
        "time_zone_id": contract.time_zone_id,
        "min_tick": contract.min_tick,
    }


def _filter_snapshot_fields(
    values: dict[str, int | float | str],
    requested_fields: list[str],
) -> dict[str, int | float | str]:
    if not requested_fields:
        return values
    normalized = {
        _CLIENT_PORTAL_FIELD_NAMES.get(field, field.strip().lower()) for field in requested_fields
    }
    delayed_capable = {
        "bid",
        "ask",
        "last",
        "bid_size",
        "ask_size",
        "last_size",
        "high",
        "low",
        "volume",
        "close",
        "open",
    }
    accepted = normalized | {
        f"delayed_{field}" for field in normalized if field in delayed_capable
    }
    return {key: value for key, value in values.items() if key in accepted}


def _bool_input(value: object, *, inverted: bool = False) -> bool:
    enabled = value is True or str(value).strip().lower() in {"1", "true", "yes"}
    return not enabled if inverted else enabled
