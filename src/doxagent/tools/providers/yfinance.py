"""yfinance HK-only provider tool."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from doxagent.models import EvidenceSourceType, ResultStatus
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
                    message=(
                        "yfinance.hk_basic_snapshot 仅允许用于港股标的，不应用于美股标的。"
                    ),
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
                "market_cap": info.get("marketCap"),
                "trailing_pe": info.get("trailingPE"),
                "price_to_book": info.get("priceToBook"),
                "return_on_equity": info.get("returnOnEquity"),
                "dividend_yield": info.get("dividendYield"),
            }
            result = ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output=output,
                output_summary="已从 yfinance 检索港股基础快照。",
                raw={"info_keys": sorted(str(key) for key in info.keys())},
            )
            evidence = result.to_evidence_ref(
                source_type=EvidenceSourceType.MARKET_DATA,
                source_id=f"yfinance:hk_basic_snapshot:{symbol}",
                title=f"yfinance 港股基础快照 - {symbol}",
                citation_scope="yfinance_hk_basic_snapshot",
                confidence=0.45,
            ).model_copy(
                update={
                    "retrieval_metadata": {
                        "tool_name": request.tool_name,
                        "symbol": symbol,
                        "unofficial_source": True,
                        "hk_only": True,
                    }
                }
            )
            return result.model_copy(update={"evidence_refs": [evidence]}, deep=True)
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
    def call(self, request: ToolRequest) -> ToolResult:
        symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
        outputsize = _bounded_int(request.input.get("outputsize", 30), 1, 250)
        try:
            import importlib

            yf = cast(Any, importlib.import_module("yfinance"))
            _configure_yfinance_cache(yf)
            ticker = yf.Ticker(symbol)
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
                rows.append(
                    {
                        "datetime": str(row_date),
                        "open": _json_number(_row_value(row, "Open")),
                        "high": _json_number(_row_value(row, "High")),
                        "low": _json_number(_row_value(row, "Low")),
                        "close": _json_number(_row_value(row, "Close")),
                        "volume": _json_number(_row_value(row, "Volume")),
                    }
                )
            output = daily_ohlcv_output_with_snapshot(
                {
                    "provider": "yfinance",
                    "symbol": symbol,
                    "unofficial_source": True,
                    "fallback_for": "twelvedata.daily_ohlcv",
                    "interval": "1day",
                    "ohlcv": rows,
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
            evidence = result.to_evidence_ref(
                source_type=EvidenceSourceType.MARKET_DATA,
                source_id=f"yfinance:daily_ohlcv:{symbol}",
                title=f"yfinance 日线 OHLCV 备用数据 - {symbol}",
                citation_scope="yfinance_daily_ohlcv",
                confidence=0.48,
            ).model_copy(
                update={
                    "retrieval_metadata": {
                        "tool_name": request.tool_name,
                        "symbol": symbol,
                        "unofficial_source": True,
                        "fallback_for": "twelvedata.daily_ohlcv",
                        "market_evidence_snapshot": output.get("market_evidence_snapshot"),
                    }
                }
            )
            return result.model_copy(update={"evidence_refs": [evidence]}, deep=True)
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
