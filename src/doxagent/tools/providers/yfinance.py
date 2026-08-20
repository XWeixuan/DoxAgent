"""yfinance HK-only provider tool."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from doxagent.models import ResultStatus
from doxagent.tools.market_evidence import daily_ohlcv_output_with_snapshot
from doxagent.tools.providers.base import _input_str, _input_str_any
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class YFinanceHkBasicSnapshotClient:
    def call(self, request: ToolRequest) -> ToolResult:
        symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
        market = _input_str(request, "market", "")
        if market.upper() != "HK" and not symbol.endswith(".HK"):
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="market_not_allowed",
                    message=("yfinance.hk_basic_snapshot 仅允许用于港股标的，不应用于美股标的。"),
                    retryable=False,
                    details={"symbol": symbol, "market": market},
                ),
            )
        try:
            import importlib

            yf = cast(Any, importlib.import_module("yfinance"))
            _configure_yfinance_cache(yf)
            ticker = yf.Ticker(symbol)
            info = cast(Mapping[str, Any], getattr(ticker, "info", {}))
            output = {
                "provider": "yfinance",
                "symbol": symbol,
                "unofficial_source": True,
                "source_coordinates": {
                    "source_kind": "market_data",
                    "source_id": f"yfinance:hk_basic_snapshot:{symbol}",
                    "symbol": symbol,
                    "hk_only": True,
                },
                "market_cap": info.get("marketCap"),
                "trailing_pe": info.get("trailingPE"),
                "price_to_book": info.get("priceToBook"),
                "return_on_equity": info.get("returnOnEquity"),
                "dividend_yield": info.get("dividendYield"),
            }
            if all(
                output[key] is None
                for key in (
                    "market_cap",
                    "trailing_pe",
                    "price_to_book",
                    "return_on_equity",
                    "dividend_yield",
                )
            ):
                return ToolResult(
                    tool_name=request.tool_name,
                    status=ResultStatus.FAILED,
                    output_summary="yfinance returned no usable HK snapshot metrics.",
                    error=ToolError(
                        code="empty_result",
                        message="yfinance returned no usable HK snapshot metrics.",
                        retryable=True,
                        details={"provider": "yfinance", "symbol": symbol},
                    ),
                )
            result = ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output=output,
                output_summary="已从 yfinance 检索港股基础快照。",
                raw={"info_keys": sorted(str(key) for key in info.keys())},
            )
            return result
        except Exception as exc:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="tool_execution_failed",
                    message=str(exc),
                    retryable=True,
                    details={"provider": "yfinance", "symbol": symbol},
                ),
            )


class YFinanceDailyOhlcvClient:
    def __init__(self, *, adjusted: bool = False) -> None:
        self._adjusted = adjusted

    def call(self, request: ToolRequest) -> ToolResult:
        symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
        outputsize = _bounded_int(request.input.get("outputsize", 30), 1, 250)
        try:
            import importlib

            yf = cast(Any, importlib.import_module("yfinance"))
            _configure_yfinance_cache(yf)
            ticker = yf.Ticker(symbol)
            start_date = _input_str(request, "start_date", "") or None
            end_date = _input_str(request, "end_date", "") or None
            cutoff_date = str(request.metadata.get("cutoff_at") or "")[:10]
            if cutoff_date and (not end_date or end_date > cutoff_date):
                end_date = cutoff_date
            history_kwargs: dict[str, object] = {
                "period": None if start_date or end_date else "1y",
                "interval": "1d",
                "auto_adjust": self._adjusted,
                "actions": True,
            }
            if start_date:
                history_kwargs["start"] = start_date
            if end_date:
                # yfinance treats ``end`` as exclusive. Request the following
                # calendar day so the caller's inclusive end/cutoff date can
                # be returned without ever exposing a later daily bar.
                inclusive_end = date.fromisoformat(end_date)
                history_kwargs["end"] = (inclusive_end + timedelta(days=1)).isoformat()
            try:
                frame = ticker.history(**history_kwargs)
            except TypeError:
                # Preserve compatibility with older yfinance and deterministic test doubles.
                frame = ticker.history(period="1y", interval="1d")
            if getattr(frame, "empty", False):
                frame = yf.download(
                    symbol,
                    period="1mo",
                    interval="1d",
                    progress=False,
                    threads=False,
                    auto_adjust=False,
                )
            rows = []
            tail = frame.tail(outputsize)
            for index, row in tail.iterrows():
                row_date = index.date() if hasattr(index, "date") else index
                if end_date and str(row_date) > end_date:
                    continue
                rows.append(
                    {
                        "datetime": str(row_date),
                        "open": _json_number(_row_value(row, "Open")),
                        "high": _json_number(_row_value(row, "High")),
                        "low": _json_number(_row_value(row, "Low")),
                        "close": _json_number(_row_value(row, "Close")),
                        "volume": _json_number(_row_value(row, "Volume")),
                        "dividend": _json_number(_row_value(row, "Dividends")),
                        "stock_split": _json_number(_row_value(row, "Stock Splits")),
                    }
                )
            if not rows:
                return ToolResult(
                    tool_name=request.tool_name,
                    status=ResultStatus.FAILED,
                    output_summary="yfinance returned no OHLCV rows.",
                    error=ToolError(
                        code="empty_result",
                        message="yfinance returned no OHLCV rows.",
                        retryable=True,
                        details={"provider": "yfinance", "symbol": symbol},
                    ),
                )
            output = daily_ohlcv_output_with_snapshot(
                {
                    "provider": "yfinance",
                    "symbol": symbol,
                    "unofficial_source": True,
                    "source_coordinates": {
                        "source_kind": "market_data",
                        "source_id": f"yfinance:daily_ohlcv:{symbol}",
                        "symbol": symbol,
                        "fallback_for": "twelvedata.daily_ohlcv",
                    },
                    "fallback_for": "twelvedata.daily_ohlcv",
                    "interval": "1day",
                    "ohlcv": rows,
                    "requested_start_date": start_date,
                    "requested_end_date": end_date,
                    "adjustment_mode": (
                        "split_dividend_adjusted" if self._adjusted else "raw_unadjusted"
                    ),
                    "corporate_action_metadata": {
                        "splits_included": self._adjusted,
                        "dividends_included": self._adjusted,
                        "total_return": self._adjusted,
                    },
                },
                tool_name=request.tool_name,
            )
            result = ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output=output,
                output_summary="已从 yfinance 检索日线 OHLCV 备用数据。",
                raw={"row_count": len(rows), "unofficial_source": True},
            )
            return result
        except Exception as exc:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="tool_execution_failed",
                    message=str(exc),
                    retryable=True,
                    details={"provider": "yfinance", "symbol": symbol},
                ),
            )


class YFinanceSellSideConsensusClient:
    """Return a compact, explicitly unofficial analyst-consensus snapshot."""

    _METHODS = {
        "earnings_estimate": "get_earnings_estimate",
        "revenue_estimate": "get_revenue_estimate",
        "eps_trend": "get_eps_trend",
        "eps_revisions": "get_eps_revisions",
    }

    def call(self, request: ToolRequest) -> ToolResult:
        symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
        try:
            import importlib

            yf = cast(Any, importlib.import_module("yfinance"))
            _configure_yfinance_cache(yf)
            ticker = yf.Ticker(symbol)
            data: dict[str, Mapping[str, Any]] = {}
            errors: list[dict[str, str]] = []
            for output_key, method_name in self._METHODS.items():
                try:
                    raw = getattr(ticker, method_name)(as_dict=True)
                except Exception as exc:
                    errors.append(
                        {
                            "dataset": output_key,
                            "error": type(exc).__name__,
                            "message": str(exc)[:500],
                        }
                    )
                    continue
                if isinstance(raw, Mapping) and raw:
                    data[output_key] = raw
                else:
                    errors.append(
                        {
                            "dataset": output_key,
                            "error": "empty_result",
                            "message": "provider returned no rows",
                        }
                    )
            periods = _consensus_periods(data)
            if not periods:
                return ToolResult(
                    tool_name=request.tool_name,
                    status=ResultStatus.FAILED,
                    error=ToolError(
                        code="empty_result",
                        message="Yahoo Finance returned no usable sell-side consensus data.",
                        retryable=True,
                        details={"provider": "yfinance", "symbol": symbol, "errors": errors},
                    ),
                )
            retrieved_at = datetime.now(UTC).isoformat()
            cutoff_at = request.metadata.get("cutoff_at")
            output = {
                "provider": "yfinance",
                "symbol": symbol,
                "unofficial_source": True,
                "retrieved_at": retrieved_at,
                "as_of": retrieved_at,
                "periods": periods,
                "provider_errors": errors,
                "accounting_basis": (
                    "provider analyst consensus; EPS GAAP/non-GAAP basis is unspecified and "
                    "must not be labeled SEC GAAP without reconciliation"
                ),
                "period_code_legend": {
                    "0q": "current fiscal quarter",
                    "+1q": "next fiscal quarter",
                    "0y": "current fiscal year",
                    "+1y": "next fiscal year",
                },
                "requested_cutoff_at": cutoff_at,
                "vintage_policy": (
                    "retrieval-time current consensus only; the provider does not expose a "
                    "historical point-in-time vintage"
                ),
                "source_coordinates": {
                    "source_kind": "market_data",
                    "source_id": f"yfinance:sell_side_consensus:{symbol}",
                    "source_scope": "sell_side_consensus",
                    "symbol": symbol,
                    "retrieved_at": retrieved_at,
                    "unofficial_source": True,
                },
            }
            partial = bool(errors) or cutoff_at is not None
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.PARTIAL if partial else ResultStatus.SUCCEEDED,
                output=output,
                output_summary=(
                    "Retrieved a compact Yahoo Finance analyst-consensus snapshot."
                    if not partial
                    else "Retrieved current Yahoo Finance consensus with explicit vintage limits."
                ),
                error=(
                    ToolError(
                        code=(
                            "partial_consensus"
                            if errors
                            else "current_consensus_not_historical_vintage"
                        ),
                        message=(
                            "Some Yahoo Finance consensus datasets were unavailable."
                            if errors
                            else (
                                "Current consensus cannot establish the requested "
                                "historical vintage."
                            )
                        ),
                        retryable=bool(errors),
                        details={"provider_errors": errors, "requested_cutoff_at": cutoff_at},
                    )
                    if partial
                    else None
                ),
                raw={"datasets": sorted(data)},
            )
        except Exception as exc:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="tool_execution_failed",
                    message=str(exc),
                    retryable=True,
                    details={"provider": "yfinance", "symbol": symbol},
                ),
            )


def _consensus_periods(data: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    period_order = ("0q", "+1q", "0y", "+1y")
    rows: list[dict[str, Any]] = []
    for period in period_order:
        row: dict[str, Any] = {"period_code": period}
        for dataset, fields in data.items():
            projected = {
                str(field): _json_number(values.get(period))
                for field, values in fields.items()
                if isinstance(values, Mapping) and values.get(period) is not None
            }
            if projected:
                row[dataset] = projected
        if len(row) > 1:
            rows.append(row)
    return rows


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    bounded = max(minimum, min(maximum, parsed))
    return int(bounded)


def _json_number(value: object) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _row_value(row: Any, key: str) -> object:
    value = row.get(key)
    if value is not None:
        return value
    for column, candidate in row.items():
        if isinstance(column, tuple) and column and str(column[0]) == key:
            return candidate
    return None


def _configure_yfinance_cache(yf: Any) -> None:
    cache_dir = Path(tempfile.gettempdir()) / "doxagent-yfinance-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    set_cache_location = getattr(yf, "set_tz_cache_location", None)
    if callable(set_cache_location):
        set_cache_location(str(cache_dir))
