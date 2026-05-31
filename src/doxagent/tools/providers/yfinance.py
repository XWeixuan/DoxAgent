"""yfinance HK-only provider tool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from doxagent.models import EvidenceSourceType, ResultStatus
from doxagent.tools.providers.base import _input_str
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class YFinanceHkBasicSnapshotClient:
    def call(self, request: ToolRequest) -> ToolResult:
        symbol = _input_str(request, "symbol", request.ticker).upper()
        market = _input_str(request, "market", "")
        if market.upper() != "HK" and not symbol.endswith(".HK"):
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="market_not_allowed",
                    message=(
                        "yfinance.hk_basic_snapshot is HK-only and must not be used "
                        "for US tickers."
                    ),
                    retryable=False,
                    details={"symbol": symbol, "market": market},
                ),
            )
        try:
            import importlib

            yf = cast(Any, importlib.import_module("yfinance"))
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
                output_summary="HK basic snapshot was retrieved from yfinance.",
                raw={"info_keys": sorted(str(key) for key in info.keys())},
            )
            evidence = result.to_evidence_ref(
                source_type=EvidenceSourceType.MARKET_DATA,
                source_id=f"yfinance:hk_basic_snapshot:{symbol}",
                title=f"yfinance HK basic snapshot for {symbol}",
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
