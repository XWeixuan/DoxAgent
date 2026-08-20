"""O4-specific market evidence tools.

These clients intentionally live beside, rather than alter, the C1/C3 provider
surfaces.  They return compact source observations and explicit quality limits;
they never promote a provider field into an Expectation state.
"""

from __future__ import annotations

import importlib
import math
from datetime import UTC, datetime
from statistics import median
from typing import Any, cast

from doxagent.models import ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.client import ToolClient
from doxagent.tools.market_evidence import build_daily_ohlcv_snapshot
from doxagent.tools.providers.alpha_vantage import _alpha_get_json, _alpha_issue
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    TTLCache,
    _input_list,
    _input_str_any,
    _require,
)
from doxagent.tools.providers.yfinance import _configure_yfinance_cache
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class MarketRelativePerformanceClient:
    """Fetch one governed basket and return compact relative-return evidence."""

    def __init__(self, daily_client: ToolClient) -> None:
        self._daily_client = daily_client

    def call(self, request: ToolRequest) -> ToolResult:
        target = request.ticker.upper()
        requested = _input_list(request, "symbols")
        symbols = _dedupe_symbols([target, *requested])[:10]
        if len(symbols) < 2:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="comparison_symbols_required",
                    message="Provide at least one benchmark or peer symbol in symbols.",
                ),
            )
        snapshots: list[JsonObject] = []
        failures: list[JsonObject] = []
        for symbol in symbols:
            child = self._daily_client.call(
                request.model_copy(
                    update={
                        "tool_name": "market.daily_ohlcv",
                        "input": {**request.input, "symbol": symbol, "ticker": symbol},
                    },
                    deep=True,
                )
            )
            snapshot = build_daily_ohlcv_snapshot(child.output, tool_name=request.tool_name)
            if snapshot is None:
                failures.append(
                    {
                        "symbol": symbol,
                        "status": child.status.value,
                        "error_code": child.error.code if child.error else "empty_result",
                    }
                )
                continue
            snapshots.append(snapshot)
        target_row = next(
            (item for item in snapshots if str(item.get("symbol", "")).upper() == target),
            None,
        )
        if target_row is None:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                output={"failed_symbols": failures},
                error=ToolError(
                    code="target_history_unavailable",
                    message=(
                        "The target history is unavailable, so relative returns cannot be computed."
                    ),
                    details={"failed_symbols": failures},
                ),
            )
        target_return = _number(target_row.get("total_return_pct"))
        comparisons: list[JsonObject] = []
        for row in snapshots:
            item_return = _number(row.get("total_return_pct"))
            comparisons.append(
                {
                    "symbol": row.get("symbol"),
                    "start_date": row.get("start_date"),
                    "end_date": row.get("end_date"),
                    "total_return_pct": item_return,
                    "relative_return_vs_target_pct": (
                        _round(item_return - target_return)
                        if item_return is not None
                        and target_return is not None
                        and row.get("symbol") != target
                        else None
                    ),
                    "bar_count": row.get("bar_count"),
                    "data_quality_flags": row.get("data_quality_flags", []),
                }
            )
        output = {
            "provider": "governed_market_route",
            "target_symbol": target,
            "comparisons": comparisons,
            "failed_symbols": failures,
            "method": {
                "method_id": "close_to_close_relative_return_v1",
                "formula": "comparison_return_pct - target_return_pct",
                "common_cutoff_at": request.metadata.get("cutoff_at"),
            },
            "source_coordinates": {
                "source_kind": "market_data",
                "source_id": f"market:relative_performance:{target}:{','.join(symbols)}",
                "symbols": symbols,
            },
        }
        partial = bool(failures) or any(item.get("data_quality_flags") for item in comparisons)
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.PARTIAL if partial else ResultStatus.SUCCEEDED,
            output=output,
            output_summary=f"Computed compact relative performance for {len(comparisons)} symbols.",
            error=(
                ToolError(
                    code="partial_relative_performance",
                    message="Some symbols or requested final dates were unavailable.",
                    details={"failed_symbols": failures},
                )
                if partial
                else None
            ),
            raw={"symbols": symbols, "snapshot_count": len(snapshots)},
        )


class MarketSellSideConsensusClient:
    """Stable current-consensus semantic route with explicit provider attempts."""

    def __init__(self, providers: list[tuple[str, ToolClient, dict[str, Any]]]) -> None:
        self._providers = providers

    def call(self, request: ToolRequest) -> ToolResult:
        attempts: list[JsonObject] = []
        for index, (tool_name, client, defaults) in enumerate(self._providers):
            child = client.call(
                request.model_copy(
                    update={
                        "tool_name": tool_name,
                        "input": {**defaults, **request.input},
                    },
                    deep=True,
                )
            )
            usable = child.status in {ResultStatus.SUCCEEDED, ResultStatus.PARTIAL} and bool(
                child.output
            )
            attempts.append(
                {
                    "tool_name": tool_name,
                    "status": child.status.value,
                    "usable": usable,
                    "error_code": child.error.code if child.error else None,
                }
            )
            if not usable:
                continue
            output = {
                **child.output,
                "provider_routing": {
                    "strategy": "current_consensus_fallback_v1",
                    "selected_tool": tool_name,
                    "fallback_used": index > 0,
                    "attempts": attempts,
                },
                "vintage_policy": (
                    "retrieval-time current consensus only; never use as a historical vintage"
                ),
            }
            return child.model_copy(
                update={
                    "tool_name": request.tool_name,
                    "output": output,
                    "output_summary": (
                        f"Selected {tool_name} for current sell-side consensus evidence."
                    ),
                },
                deep=True,
            )
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.FAILED,
            output={"provider_routing": {"attempts": attempts}},
            error=ToolError(
                code="all_consensus_providers_failed",
                message="No current consensus provider returned usable evidence.",
                retryable=any(item.get("error_code") == "rate_limited" for item in attempts),
                details={"provider_attempts": attempts},
            ),
        )


class AlphaVantageO4Client(BaseRealToolClient):
    """Compact Alpha Vantage O4 endpoints with entitlement-aware failures."""

    _FUNCTIONS = {
        "valuation": "OVERVIEW",
        "institutional_holdings": "INSTITUTIONAL_HOLDINGS",
        "insider_transactions": "INSIDER_TRANSACTIONS",
        "historical_options": "HISTORICAL_OPTIONS",
    }

    def __init__(
        self,
        settings: DoxAgentSettings,
        cache: TTLCache | None,
        mode: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, cache, **kwargs)
        self.mode = mode

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.alpha_vantage_api_key, "ALPHA_VANTAGE_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            function = self._FUNCTIONS[self.mode]
            params: dict[str, object] = {
                "function": function,
                "symbol": symbol,
                "apikey": api_key,
            }
            if self.mode == "historical_options" and request.input.get("date"):
                params["date"] = str(request.input["date"])
            raw = _alpha_get_json(self, params=params)
            issue = _alpha_issue(raw)
            if issue is not None:
                return self._failure(request, **issue, details={"function": function})
            default_limit = 10 if self.mode == "institutional_holdings" else 25
            row_limit = max(1, min(100, int(request.input.get("limit", default_limit))))
            data = _project_alpha_o4(
                self.mode,
                raw,
                cutoff_at=request.metadata.get("cutoff_at"),
                limit=row_limit,
            )
            if not data:
                return self._failure(
                    request,
                    code="empty_result",
                    message=f"Alpha Vantage {function} returned no usable O4 fields.",
                    details={"function": function, "symbol": symbol},
                )
            retrieved_at = datetime.now(UTC).isoformat()
            output = {
                "provider": "alpha_vantage",
                "function": function,
                "symbol": symbol,
                "retrieved_at": retrieved_at,
                "as_of": retrieved_at,
                self.mode: data,
            }
            return self._success(
                request,
                output=output,
                raw={
                    "function": function,
                    "returned_fields": sorted(data) if isinstance(data, dict) else len(data),
                },
                source_kind="market_data",
                source_id=f"alpha_vantage:{function}:{symbol}",
                title=f"Alpha Vantage {self.mode.replace('_', ' ')} - {symbol}",
                summary=f"Retrieved compact Alpha Vantage {self.mode.replace('_', ' ')} evidence.",
                source_scope=f"alpha_{self.mode}",
                confidence=0.72,
                metadata={"function": function, "symbol": symbol},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class YFinanceShortInterestClient:
    """Current listed short-interest snapshot fallback; explicitly unofficial."""

    _FIELDS = {
        "sharesShort": "short_interest_shares",
        "sharesShortPriorMonth": "previous_short_interest_shares",
        "shortRatio": "days_to_cover",
        "shortPercentOfFloat": "short_percent_of_float",
        "floatShares": "float_shares",
        "dateShortInterest": "settlement_timestamp",
        "sharesPercentSharesOut": "short_percent_of_shares_outstanding",
    }

    def call(self, request: ToolRequest) -> ToolResult:
        symbol = request.ticker.upper()
        try:
            yf = cast(Any, importlib.import_module("yfinance"))
            _configure_yfinance_cache(yf)
            info = getattr(yf.Ticker(symbol), "info", {})
            data = {
                target: _json_scalar(info.get(source))
                for source, target in self._FIELDS.items()
                if info.get(source) not in (None, "")
            }
            if not data:
                raise ValueError("Yahoo Finance returned no short-interest fields.")
            data["settlement_date"] = _unix_date(data.pop("settlement_timestamp", None))
            data["source_limit"] = (
                "Unofficial current snapshot; publication date and historical vintages "
                "are not guaranteed."
            )
            output = {
                "provider": "yfinance",
                "symbol": symbol,
                "unofficial_source": True,
                "short_interest": data,
                "as_of": data.get("settlement_date") or datetime.now(UTC).isoformat(),
                "source_coordinates": {
                    "source_kind": "market_data",
                    "source_id": f"yfinance:short_interest:{symbol}",
                    "unofficial_source": True,
                },
            }
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.PARTIAL,
                output=output,
                output_summary="Retrieved a compact unofficial listed short-interest snapshot.",
                error=ToolError(
                    code="unofficial_current_short_interest",
                    message="Use as a fallback and verify against an official published source.",
                ),
                raw={"returned_fields": sorted(data)},
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


class YFinancePeerRelativeValuationClient:
    """Governed explicit peer basket with compact current cross-sectional metrics."""

    _METRICS = {
        "marketCap": "market_cap",
        "enterpriseValue": "enterprise_value",
        "trailingPE": "trailing_pe",
        "forwardPE": "forward_pe",
        "priceToSalesTrailing12Months": "price_to_sales_ttm",
        "enterpriseToRevenue": "ev_to_revenue",
        "enterpriseToEbitda": "ev_to_ebitda",
    }

    def call(self, request: ToolRequest) -> ToolResult:
        target = request.ticker.upper()
        symbols = _dedupe_symbols([target, *_input_list(request, "symbols")])[:10]
        if len(symbols) < 2:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="peer_symbols_required",
                    message="Provide at least one explicit peer in symbols.",
                ),
            )
        try:
            yf = cast(Any, importlib.import_module("yfinance"))
            _configure_yfinance_cache(yf)
            rows: list[JsonObject] = []
            failures: list[str] = []
            for symbol in symbols:
                try:
                    info = getattr(yf.Ticker(symbol), "info", {})
                except Exception:
                    failures.append(symbol)
                    continue
                row = {
                    "symbol": symbol,
                    "currency": info.get("currency"),
                    "financial_currency": info.get("financialCurrency"),
                }
                row.update(
                    {
                        target_key: _json_scalar(info.get(source_key))
                        for source_key, target_key in self._METRICS.items()
                        if info.get(source_key) not in (None, "")
                    }
                )
                if len(row) == 1:
                    failures.append(symbol)
                else:
                    rows.append(row)
            if not any(row["symbol"] == target for row in rows):
                raise ValueError("Target valuation fields are unavailable.")
            target_row = next(row for row in rows if row["symbol"] == target)
            target_currency = target_row.get("currency")
            target_financial_currency = target_row.get("financial_currency")
            comparable_rows = [
                row
                for row in rows
                if row.get("currency") == target_currency
                and row.get("financial_currency") == target_financial_currency
            ]
            comparable_peers = [row for row in comparable_rows if row["symbol"] != target]
            relative: JsonObject = {}
            ratio_metrics = {
                "trailing_pe",
                "forward_pe",
                "price_to_sales_ttm",
                "ev_to_revenue",
                "ev_to_ebitda",
            }
            for metric in ratio_metrics:
                target_value = _number(target_row.get(metric))
                values = [_number(row.get(metric)) for row in comparable_peers]
                clean = [value for value in values if value is not None]
                if target_value is None or not clean:
                    continue
                relative[metric] = {
                    "target": target_value,
                    "peer_median": _round(median(clean)),
                    "premium_discount_pct": (
                        _round((target_value / median(clean) - 1) * 100)
                        if median(clean) != 0
                        else None
                    ),
                    "sample_size": len(clean),
                }
            output = {
                "provider": "yfinance",
                "target_symbol": target,
                "peer_symbols": [symbol for symbol in symbols if symbol != target],
                "unofficial_source": True,
                "rows": rows,
                "relative_valuation": relative,
                "failed_symbols": failures,
                "method": {
                    "method_id": "explicit_peer_cross_section_v1",
                    "peer_governance": "caller_supplied_explicit_basket",
                    "currency_normalization": "not_performed",
                    "relative_metric_policy": (
                        "Only dimensionless valuation ratios are compared; absolute market-cap "
                        "and enterprise-value amounts are returned with currencies but excluded. "
                        "Rows whose quote or financial currency differs from the target are also "
                        "excluded because provider ratios can mix currency bases."
                    ),
                    "relative_included_symbols": [row["symbol"] for row in comparable_peers],
                    "relative_excluded_currency_mismatch": [
                        row["symbol"] for row in rows if row not in comparable_rows
                    ],
                },
                "source_coordinates": {
                    "source_kind": "market_data",
                    "source_id": f"yfinance:peer_relative_valuation:{target}",
                    "symbols": symbols,
                    "unofficial_source": True,
                },
            }
            partial = bool(failures) or not relative
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.PARTIAL if partial else ResultStatus.SUCCEEDED,
                output=output,
                output_summary=(
                    f"Retrieved current valuation fields for {len(rows)} explicit basket symbols."
                ),
                error=(
                    ToolError(
                        code="partial_peer_valuation",
                        message="Some symbols or comparable valuation metrics were unavailable.",
                        details={"failed_symbols": failures},
                    )
                    if partial
                    else None
                ),
                raw={"symbols": symbols, "row_count": len(rows)},
            )
        except Exception as exc:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="tool_execution_failed",
                    message=str(exc),
                    retryable=True,
                    details={"provider": "yfinance", "target": target},
                ),
            )


def _project_alpha_o4(
    mode: str,
    raw: JsonObject,
    *,
    cutoff_at: object,
    limit: int = 25,
) -> Any:
    if mode == "valuation":
        keys = (
            "Symbol",
            "Name",
            "Currency",
            "Country",
            "FiscalYearEnd",
            "LatestQuarter",
            "MarketCapitalization",
            "EBITDA",
            "PERatio",
            "PEGRatio",
            "BookValue",
            "DividendPerShare",
            "DividendYield",
            "EPS",
            "RevenuePerShareTTM",
            "ProfitMargin",
            "OperatingMarginTTM",
            "ReturnOnAssetsTTM",
            "ReturnOnEquityTTM",
            "RevenueTTM",
            "GrossProfitTTM",
            "DilutedEPSTTM",
            "QuarterlyEarningsGrowthYOY",
            "QuarterlyRevenueGrowthYOY",
            "AnalystTargetPrice",
            "TrailingPE",
            "ForwardPE",
            "PriceToSalesRatioTTM",
            "PriceToBookRatio",
            "EVToRevenue",
            "EVToEBITDA",
            "Beta",
            "52WeekHigh",
            "52WeekLow",
            "SharesOutstanding",
        )
        return {
            key: _json_scalar(raw.get(key))
            for key in keys
            if raw.get(key) not in (None, "", "None", "-")
        }
    if mode == "institutional_holdings":
        summary_keys = (
            "symbol",
            "total_institutional_holders",
            "total_institutional_shares",
            "holders_with_increased_holdings",
            "shares_with_increased_holdings",
            "holders_with_decreased_holdings",
            "shares_with_decreased_holdings",
            "holders_with_unchanged_holdings",
            "shares_with_unchanged_holdings",
            "total_institutional_ownership_percentage",
        )
        cutoff_date = str(cutoff_at or "")[:10]
        holdings = []
        for row in raw.get("holdings", [])[:limit]:
            if not isinstance(row, dict):
                continue
            reported = str(row.get("last_reported") or "")[:10]
            if cutoff_date and reported and reported > cutoff_date:
                continue
            holdings.append(
                {
                    key: _json_scalar(value)
                    for key, value in row.items()
                    if key
                    in {
                        "holder_name",
                        "shares_held",
                        "shares_changed",
                        "shares_changed_percentage",
                        "change_type",
                        "last_reported",
                    }
                    and value not in (None, "", "None", "-")
                }
            )
        return {
            "summary": {
                key: _json_scalar(raw.get(key))
                for key in summary_keys
                if raw.get(key) not in (None, "", "None", "-")
            },
            "top_holdings": holdings,
            "projection_limit": limit,
        }
    candidates = next(
        (
            value
            for key, value in raw.items()
            if isinstance(value, list) and key not in {"Meta Data"}
        ),
        [],
    )
    cutoff_date = str(cutoff_at or "")[:10]
    rows: list[JsonObject] = []
    allowed = {
        "institutional_holdings": {
            "holder",
            "owner",
            "name",
            "cik",
            "shares",
            "value",
            "weight",
            "date_reported",
            "reportDate",
            "filingDate",
            "change",
            "changePercent",
        },
        "insider_transactions": {
            "transaction_date",
            "transactionDate",
            "filing_date",
            "filingDate",
            "executive",
            "name",
            "executive_title",
            "security_type",
            "acquisition_or_disposal",
            "shares",
            "share_price",
            "transaction_value",
            "shares_owned_following_transaction",
        },
        "historical_options": {
            "contractID",
            "symbol",
            "expiration",
            "strike",
            "type",
            "date",
            "last",
            "mark",
            "bid",
            "ask",
            "volume",
            "open_interest",
            "implied_volatility",
            "delta",
            "gamma",
            "theta",
            "vega",
            "rho",
        },
    }[mode]
    for row in candidates[:limit]:
        if not isinstance(row, dict):
            continue
        row_date = str(
            row.get("filingDate")
            or row.get("filing_date")
            or row.get("transactionDate")
            or row.get("transaction_date")
            or row.get("date")
            or ""
        )[:10]
        if cutoff_date and row_date and row_date > cutoff_date:
            continue
        projected = {
            key: _json_scalar(value)
            for key, value in row.items()
            if key in allowed and value not in (None, "", "None", "-")
        }
        if projected:
            rows.append(projected)
    return rows


def _dedupe_symbols(values: list[object]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        symbol = str(value or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        if not symbol.replace(".", "").replace("-", "").isalnum():
            continue
        seen.add(symbol)
        output.append(symbol)
    return output


def _number(value: object) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float) -> float | int:
    rounded = round(value, 4)
    return int(rounded) if rounded.is_integer() else rounded


def _json_scalar(value: object) -> object:
    number = _number(value)
    if number is not None:
        return int(number) if number.is_integer() else number
    return value


def _unix_date(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return str(value)[:10] if value else None
    try:
        return datetime.fromtimestamp(number, tz=UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None
