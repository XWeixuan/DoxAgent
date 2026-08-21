"""Twelve Data market-data provider tools."""

from __future__ import annotations

from doxagent.tools.market_evidence import daily_ohlcv_output_with_snapshot
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_str,
    _input_str_any,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult


class TwelveDataDailyOhlcvClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.twelvedata_api_key, "TWELVEDATA_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            outputsize = _bounded_int(request.input.get("outputsize", 30), 1, 500)
            params: dict[str, object] = {
                "symbol": symbol,
                "interval": "1day",
                "outputsize": outputsize,
                "apikey": api_key,
            }
            start_date = _input_str(request, "start_date", "")
            end_date = _input_str(request, "end_date", "")
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            raw = self._get_json(
                self.settings.twelvedata_base_url.rstrip("/") + "/time_series",
                params=params,
                cache_ttl=self.settings.twelvedata_cache_ttl_seconds,
            )
            _raise_twelvedata_error(raw)
            values = raw.get("values")
            if not isinstance(values, list):
                values = []
            if not values:
                return self._failure(
                    request,
                    code="empty_result",
                    message="Twelve Data returned no OHLCV rows for the requested range.",
                    details={"symbol": symbol, "start_date": start_date, "end_date": end_date},
                )
            numeric_values = [_normalize_ohlcv_row(row) for row in values if isinstance(row, dict)]
            output = daily_ohlcv_output_with_snapshot(
                {
                    "provider": "twelvedata",
                    "symbol": symbol,
                    "interval": "1day",
                    "ohlcv": numeric_values,
                    "meta": raw.get("meta", {}),
                    "fallback_tool": "yfinance.daily_ohlcv",
                    "requested_start_date": start_date or None,
                    "requested_end_date": end_date or None,
                    "adjustment_mode": "raw_unadjusted",
                    "corporate_action_metadata": {
                        "splits_included": False,
                        "dividends_included": False,
                        "total_return": False,
                    },
                },
                tool_name=request.tool_name,
            )
            return self._success(
                request,
                output=output,
                raw=raw,
                source_kind="market_data",
                source_id=f"twelvedata:daily_ohlcv:{symbol}",
                title=f"Twelve Data 日线 OHLCV - {symbol}",
                summary="已检索 Twelve Data 日线 OHLCV 数据。",
                source_scope="twelvedata_daily_ohlcv",
                confidence=0.76,
                metadata={
                    "symbol": symbol,
                    "interval": "1day",
                    "outputsize": outputsize,
                    "start_date": start_date or None,
                    "end_date": end_date or None,
                    "market_evidence_snapshot": output.get("market_evidence_snapshot"),
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class TwelveDataSellSideEstimatesClient(BaseRealToolClient):
    """Temporary consensus fallback; it returns source rows, never a derived value."""

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.twelvedata_api_key, "TWELVEDATA_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            data: JsonObject = {}
            issues: list[JsonObject] = []
            for label, endpoint in (
                ("earnings_estimate", "/earnings_estimate"),
                ("revenue_estimate", "/revenue_estimate"),
            ):
                try:
                    raw = self._get_json(
                        self.settings.twelvedata_base_url.rstrip("/") + endpoint,
                        params={"symbol": symbol, "apikey": api_key},
                        cache_ttl=self.settings.twelvedata_cache_ttl_seconds,
                        rate_limit_key="twelvedata",
                        min_interval_seconds=0.25,
                        max_rate_limit_retries=1,
                    )
                    _raise_twelvedata_error(raw)
                except ProviderHttpError as exc:
                    issues.append(
                        {
                            "endpoint": label,
                            "code": exc.code,
                            "message": exc.message,
                            "retryable": exc.retryable,
                        }
                    )
                    continue
                if _has_estimate_rows(raw):
                    rows = raw.get(label)
                    meta = raw.get("meta")
                    data[label] = {
                        "meta": {
                            key: meta[key]
                            for key in ("symbol", "currency", "exchange")
                            if isinstance(meta, dict) and meta.get(key) not in (None, "")
                        },
                        "estimates": [
                            {
                                key: row[key]
                                for key in (
                                    "date",
                                    "period",
                                    "number_of_analysts",
                                    "avg_estimate",
                                    "low_estimate",
                                    "high_estimate",
                                    "year_ago_eps",
                                    "year_ago_sales",
                                    "sales_growth",
                                )
                                if row.get(key) not in (None, "", [], {})
                            }
                            for row in rows or []
                            if isinstance(row, dict)
                        ],
                    }
                else:
                    issues.append(
                        {
                            "endpoint": label,
                            "code": "empty_result",
                            "message": "No usable estimate rows.",
                        }
                    )
            output = {
                "provider": "twelvedata",
                "symbol": symbol,
                "sell_side_estimates": data,
                "provider_errors": issues,
            }
            if not data:
                return self._failure(
                    request,
                    code="upstream_provider_error",
                    message="Twelve Data returned no usable estimate data.",
                    details={"provider_errors": issues},
                )
            kwargs = dict(
                output=output,
                raw=output,
                source_kind="external_report",
                source_id=f"twelvedata:sell_side_estimates:{symbol}",
                title=f"Twelve Data sell-side estimates - {symbol}",
                summary="Retrieved Twelve Data EPS and revenue estimate rows.",
                source_scope="twelvedata_sell_side_estimates",
                confidence=0.68,
                metadata={
                    "symbol": symbol,
                    "endpoints": list(data),
                    "failed_endpoints": [item["endpoint"] for item in issues],
                },
            )
            if issues:
                return self._partial(
                    request,
                    code="twelvedata_partial_subrequest_failure",
                    message="Some Twelve Data estimate requests failed or were empty.",
                    retryable=any(bool(item.get("retryable")) for item in issues),
                    details={"provider_errors": issues},
                    **kwargs,
                )
            return self._success(request, **kwargs)
        except Exception as exc:
            return self._handle_exception(request, exc)


def _raise_twelvedata_error(raw: JsonObject) -> None:
    if str(raw.get("status", "")).lower() == "error":
        message = str(raw.get("message") or "Twelve Data returned an error.")
        code = str(raw.get("code") or "upstream_provider_error")
        retryable = code == "429"
        raise ProviderHttpError(
            code="rate_limited" if retryable else "upstream_provider_error",
            message=message,
            retryable=retryable,
            details={"provider_code": code, "provider_status": raw.get("status")},
        )


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    bounded = max(minimum, min(maximum, parsed))
    return int(bounded)


def _normalize_ohlcv_row(row: JsonObject) -> JsonObject:
    normalized: JsonObject = {}
    for key in ("datetime", "date", "time"):
        if row.get(key) not in (None, ""):
            normalized["datetime"] = str(row[key])
            break
    for key in ("open", "high", "low", "close", "volume"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            number = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            continue
        normalized[key] = int(number) if number.is_integer() else number
    return normalized


def _has_estimate_rows(raw: JsonObject) -> bool:
    return any(
        value not in (None, "", [], {})
        for key, value in raw.items()
        if key not in {"status", "message", "code"}
    )
