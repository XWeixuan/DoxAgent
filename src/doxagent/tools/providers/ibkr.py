"""Read-only IBKR semantic tools backed by the official local TWS API."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from doxagent.models import ResultStatus
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
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

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
                    "as_of": snapshot.get("captured_at"),
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


class IbkrHistoricalTicksClient(_IbkrClient):
    source_scope = "ibkr_historical_ticks"
    title = "IBKR TWS historical ticks"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            conid = _input_str(request, "conid", "")
            what_to_show = _input_str(request, "what_to_show", "TRADES").upper()
            number_of_ticks = max(1, min(1_000, int(request.input.get("number_of_ticks", 100))))
            start_datetime = _input_str(request, "start_datetime", "")
            end_datetime = _input_str(request, "end_datetime", "")
            if not start_datetime and not end_datetime:
                cutoff = _metadata_datetime(request.metadata.get("cutoff_at"))
                end_datetime = (cutoff or datetime.now(UTC)).strftime("%Y%m%d-%H:%M:%S")
            with self._session() as session:
                contract = _resolve_or_build_contract(session, symbol, conid)
                result = session.historical_ticks(
                    contract,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    number_of_ticks=number_of_ticks,
                    what_to_show=what_to_show,
                    use_rth=_bool_input(request.input.get("outside_rth"), inverted=True),
                )
            return self._success_result(
                request,
                symbol=symbol,
                con_id=contract.con_id,
                output={
                    **result,
                    "requested_start_datetime": start_datetime or None,
                    "requested_end_datetime": end_datetime or None,
                    "as_of": result["events"][-1].get("timestamp"),
                },
                summary=f"Retrieved {result['event_count']} IBKR historical tick(s) for {symbol}.",
            )
        except Exception as exc:
            return self._provider_failure(request, exc)


class IbkrShortabilitySnapshotClient(_IbkrClient):
    source_scope = "ibkr_shortability_snapshot"
    title = "IBKR TWS shortability snapshot"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            conid = _input_str(request, "conid", "")
            with self._session() as session:
                contract = _resolve_or_build_contract(session, symbol, conid)
                snapshot = session.market_snapshot(contract, generic_ticks="236")
            values = {
                key: value
                for key, value in snapshot["values"].items()
                if key in {"shortable_tier", "shortable_shares"}
            }
            if not values:
                raise IbkrTwsRequestError(
                    "TWS returned no shortability fields; check market-data entitlement."
                )
            return self._success_result(
                request,
                symbol=symbol,
                con_id=contract.con_id,
                output={
                    "con_id": contract.con_id,
                    "shortability": values,
                    "field_legend": {
                        "shortable_tier": (
                            "IBKR shortable classification; not listed short interest"
                        ),
                        "shortable_shares": (
                            "currently indicated borrowable shares; not settlement short interest"
                        ),
                    },
                    "market_data_type": snapshot["market_data_type"],
                },
                summary=f"Retrieved IBKR shortability fields for {symbol}.",
            )
        except Exception as exc:
            return self._provider_failure(request, exc)


class IbkrOptionSurfaceClient(_IbkrClient):
    source_scope = "ibkr_option_surface"
    title = "IBKR TWS bounded option surface"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            max_contracts = max(2, min(16, int(request.input.get("max_contracts", 8))))
            min_days = max(0, int(request.input.get("min_days", 7)))
            max_days = max(min_days + 1, min(365, int(request.input.get("max_days", 120))))
            with self._session() as session:
                underlying = _resolve_or_build_contract(
                    session, symbol, _input_str(request, "conid", "")
                )
                underlying_snapshot = session.market_snapshot(underlying)
                spot = _snapshot_price(underlying_snapshot["values"])
                if spot is None:
                    raise IbkrTwsRequestError(
                        "TWS returned no usable underlying price for option selection."
                    )
                parameter_sets = session.option_parameters(underlying)
                option_contracts = _select_option_contracts(
                    underlying,
                    parameter_sets,
                    spot=spot,
                    min_days=min_days,
                    max_days=max_days,
                    max_contracts=max_contracts,
                )
                rows: list[JsonObject] = []
                failures: list[JsonObject] = []
                for option in option_contracts:
                    try:
                        snap = session.market_snapshot(option, generic_ticks="100,101,106")
                    except Exception as exc:
                        failures.append(
                            {
                                "expiration": option.last_trade_date_or_contract_month,
                                "strike": option.strike,
                                "right": option.right,
                                "error": str(exc)[:300],
                            }
                        )
                        continue
                    rows.append(
                        {
                            "expiration": option.last_trade_date_or_contract_month,
                            "strike": option.strike,
                            "right": option.right,
                            "multiplier": option.multiplier,
                            "quotes": snap["values"],
                            "greeks": snap.get("option_computation", {}),
                        }
                    )
            if not rows:
                detail = failures[0].get("error") if failures else "no selected contracts"
                raise IbkrTwsRequestError(
                    f"No option contract returned a usable snapshot. First failure: {detail}"
                )
            derived = _derive_option_surface(rows, spot=spot)
            output = {
                "con_id": underlying.con_id,
                "underlying_price": spot,
                "contracts": rows,
                "failed_contracts": failures,
                "derived": derived,
                "method": {
                    "method_id": "bounded_ibkr_option_surface_v1",
                    "selection": "nearest strikes across expirations within requested day range",
                    "missing_values": "not imputed",
                },
                "as_of": datetime.now(UTC).isoformat(),
            }
            kwargs = dict(
                request=request,
                symbol=symbol,
                con_id=underlying.con_id,
                output=output,
                summary=f"Retrieved {len(rows)} bounded IBKR option snapshot(s) for {symbol}.",
            )
            result = self._success_result(**kwargs)
            if failures:
                return result.model_copy(
                    update={
                        "status": ResultStatus.PARTIAL,
                        "error": ToolError(
                            code="partial_option_surface",
                            message="Some selected option contracts lacked usable snapshots.",
                            details={"failed_contract_count": len(failures)},
                        ),
                    }
                )
            return result
        except Exception as exc:
            return self._provider_failure(request, exc)


class IbkrFedFundsCurveClient(_IbkrClient):
    source_scope = "ibkr_fed_funds_curve"
    title = "IBKR TWS Fed Funds futures curve"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            max_contracts = max(1, min(12, int(request.input.get("max_contracts", 8))))
            with self._session() as session:
                chain = session.resolve_future_chain("ZQ", exchange="CBOT")
                today_key = datetime.now(UTC).strftime("%Y%m")
                selected = sorted(
                    [
                        item
                        for item in chain
                        if str(item.last_trade_date_or_contract_month or "")[:6] >= today_key
                    ],
                    key=lambda item: str(item.last_trade_date_or_contract_month or ""),
                )[:max_contracts]
                rows: list[JsonObject] = []
                failures: list[JsonObject] = []
                for contract in selected:
                    try:
                        snap = session.market_snapshot(contract)
                        price = _snapshot_price(snap["values"])
                    except Exception as exc:
                        failures.append({"con_id": contract.con_id, "error": str(exc)[:300]})
                        continue
                    if price is None:
                        failures.append({"con_id": contract.con_id, "error": "no_price"})
                        continue
                    rows.append(
                        {
                            "con_id": contract.con_id,
                            "contract_month": contract.last_trade_date_or_contract_month,
                            "price": price,
                            "implied_average_effective_rate_pct": round(100 - price, 4),
                        }
                    )
            if not rows:
                raise IbkrTwsRequestError("No Fed Funds futures contract returned a usable price.")
            output = {
                "contracts": rows,
                "failed_contracts": failures,
                "method": {
                    "method_id": "fed_funds_futures_price_to_rate_v1",
                    "formula": "100 - futures_price",
                    "scope": "contract-month average effective rate; no meeting-day weighting",
                },
                "as_of": datetime.now(UTC).isoformat(),
            }
            result = self._success_result(
                request,
                symbol="ZQ",
                con_id=rows[0]["con_id"],
                output=output,
                summary=f"Retrieved {len(rows)} Fed Funds futures curve point(s).",
            )
            if failures:
                return result.model_copy(
                    update={
                        "status": ResultStatus.PARTIAL,
                        "error": ToolError(
                            code="partial_fed_funds_curve",
                            message="Some Fed Funds futures snapshots were unavailable.",
                            details={"failed_contracts": failures},
                        ),
                    }
                )
            return result
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
    accepted = normalized | {f"delayed_{field}" for field in normalized if field in delayed_capable}
    return {key: value for key, value in values.items() if key in accepted}


def _bool_input(value: object, *, inverted: bool = False) -> bool:
    enabled = value is True or str(value).strip().lower() in {"1", "true", "yes"}
    return not enabled if inverted else enabled


def _metadata_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _snapshot_price(values: JsonObject) -> float | None:
    for key in ("last", "delayed_last", "close", "delayed_close", "bid", "ask"):
        try:
            value = float(values[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _select_option_contracts(
    underlying: ResolvedContract,
    parameter_sets: list[dict[str, Any]],
    *,
    spot: float,
    min_days: int,
    max_days: int,
    max_contracts: int,
) -> list[ResolvedContract]:
    today = datetime.now(UTC).date()
    candidates: list[tuple[date, dict[str, Any]]] = []
    for parameters in parameter_sets:
        exchange = str(parameters.get("exchange") or "")
        if exchange not in {"SMART", "BOX", "CBOE", "ISE", "AMEX", "BATS", "PHLX"}:
            continue
        for expiration in parameters.get("expirations", []):
            try:
                parsed = datetime.strptime(str(expiration)[:8], "%Y%m%d").date()
            except ValueError:
                continue
            days = (parsed - today).days
            if min_days <= days <= max_days:
                candidates.append((parsed, parameters))
    candidates.sort(key=lambda item: item[0])
    selected: list[ResolvedContract] = []
    seen: set[tuple[str, float, str]] = set()
    for expiration, parameters in candidates:
        strikes = sorted(
            (float(item) for item in parameters.get("strikes", []) if float(item) > 0),
            key=lambda item: abs(item - spot),
        )[:2]
        for strike in strikes:
            for right in ("C", "P"):
                identity = (expiration.strftime("%Y%m%d"), strike, right)
                if identity in seen:
                    continue
                seen.add(identity)
                selected.append(
                    ResolvedContract(
                        con_id=0,
                        symbol=underlying.symbol,
                        security_type="OPT",
                        exchange="SMART",
                        currency=underlying.currency,
                        trading_class=str(parameters.get("trading_class") or underlying.symbol),
                        last_trade_date_or_contract_month=identity[0],
                        strike=strike,
                        right=right,
                        multiplier=str(parameters.get("multiplier") or "100"),
                    )
                )
                if len(selected) >= max_contracts:
                    return selected
    if not selected:
        raise IbkrTwsRequestError("No option expiration/strike matched the requested day range.")
    return selected


def _derive_option_surface(rows: list[JsonObject], *, spot: float) -> JsonObject:
    enriched: list[tuple[float, JsonObject]] = []
    for row in rows:
        strike = float(row.get("strike") or 0)
        greeks = row.get("greeks")
        if strike <= 0 or not isinstance(greeks, dict):
            continue
        try:
            iv = float(greeks["implied_volatility"])
        except (KeyError, TypeError, ValueError):
            continue
        enriched.append(
            (
                abs(strike - spot),
                {
                    "expiration": row.get("expiration"),
                    "strike": strike,
                    "right": row.get("right"),
                    "implied_volatility": iv,
                    "delta": greeks.get("delta"),
                },
            )
        )
    if not enriched:
        return {"status": "insufficient_greeks"}
    atm = min(enriched, key=lambda item: item[0])[1]
    by_expiry: dict[str, list[float]] = {}
    for _, item in enriched:
        by_expiry.setdefault(str(item["expiration"]), []).append(float(item["implied_volatility"]))
    return {
        "status": "partial_surface_metrics",
        "nearest_atm": atm,
        "term_atm_iv": [
            {"expiration": expiry, "median_iv": round(sum(values) / len(values), 6)}
            for expiry, values in sorted(by_expiry.items())
        ],
        "notice": (
            "25-delta skew and event implied move are omitted unless the bounded chain "
            "contains required deltas and paired marks."
        ),
    }
