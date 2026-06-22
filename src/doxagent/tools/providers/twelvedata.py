"""Twelve Data market-data provider tools."""

from __future__ import annotations

from doxagent.models import EvidenceSourceType
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
            output = daily_ohlcv_output_with_snapshot(
                {
                    "provider": "twelvedata",
                    "symbol": symbol,
                    "interval": "1day",
                    "ohlcv": values,
                    "meta": raw.get("meta", {}),
                    "fallback_tool": "yfinance.daily_ohlcv",
                },
                tool_name=request.tool_name,
            )
            return self._success(
                request,
                output=output,
                raw=raw,
                source_type=EvidenceSourceType.MARKET_DATA,
                source_id=f"twelvedata:daily_ohlcv:{symbol}",
                title=f"Twelve Data 日线 OHLCV - {symbol}",
                summary="已检索 Twelve Data 日线 OHLCV 数据。",
                citation_scope="twelvedata_daily_ohlcv",
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
