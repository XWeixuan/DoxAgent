"""Financial Modeling Prep provider tools."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_str,
    _input_str_any,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult

FMP_FREE_SECTOR_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "CBOE", "OTC", "PNK", "CNQ"}


class FmpSectorPerformanceClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fmp_api_key, "FMP_API_KEY")
            date, date_adjusted = _free_tier_sector_date(_input_str(request, "date", ""))
            exchange = _input_str(request, "exchange", "NASDAQ").upper()
            if exchange not in FMP_FREE_SECTOR_EXCHANGES:
                exchange = "NASDAQ"
            raw, resolved_date, resolved_exchange, fallback_used = self._fetch_sector_performance(
                api_key=api_key,
                date=date,
                exchange=exchange,
            )
            output = {
                "provider": "fmp",
                "sector_performance": raw,
                "request_resolution": {
                    "requested_date": _input_str(request, "date", "") or None,
                    "resolved_date": resolved_date,
                    "date_adjusted_to_free_tier_window": date_adjusted,
                    "requested_exchange": _input_str(request, "exchange", "NASDAQ").upper(),
                    "resolved_exchange": resolved_exchange,
                    "fallback_used": fallback_used,
                },
            }
            return self._success(
                request,
                output=output,
                raw=raw,
                source_kind="market_data",
                source_id="fmp:sector_performance",
                title="FMP 行业表现快照",
                summary="已检索 FMP 行业表现快照。",
                source_scope="fmp_sector_performance",
                confidence=0.7,
                metadata={
                    "date": resolved_date,
                    "date_adjusted_to_free_tier_window": date_adjusted,
                    "exchange": resolved_exchange,
                    "fallback_used": fallback_used,
                    "free_tier_constraints": {
                        "max_date_range": "1 month",
                        "allowed_exchanges": sorted(FMP_FREE_SECTOR_EXCHANGES),
                    },
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _fetch_sector_performance(
        self,
        *,
        api_key: str,
        date: str,
        exchange: str,
    ) -> tuple[object, str, str, bool]:
        last_error: Exception | None = None
        for index, (candidate_date, candidate_exchange) in enumerate(
            _sector_performance_candidates(date, exchange)
        ):
            try:
                raw = self._get_json(
                    self.settings.fmp_base_url.rstrip("/") + "/stable/sector-performance-snapshot",
                    params={
                        "date": candidate_date,
                        "exchange": candidate_exchange,
                        "apikey": api_key,
                    },
                    cache_ttl=self.settings.fmp_cache_ttl_seconds,
                )
            except httpx.RequestError as exc:
                last_error = exc
                continue
            if _has_items(raw):
                return raw, candidate_date, candidate_exchange, index > 0
            last_error = ValueError(
                f"FMP sector performance returned no rows for {candidate_date} "
                f"{candidate_exchange}."
            )
        if last_error is not None:
            raise last_error
        raise ValueError("FMP sector performance returned no candidate rows.")


def _free_tier_sector_date(raw_value: str) -> tuple[str, bool]:
    today = datetime.now(UTC).date()
    earliest = today - timedelta(days=30)
    adjusted = False
    if raw_value:
        try:
            parsed = datetime.fromisoformat(raw_value).date()
        except ValueError:
            parsed = today
            adjusted = True
    else:
        parsed = _previous_business_day(today)
        adjusted = parsed != today
    if parsed < earliest:
        parsed = earliest
        adjusted = True
    if parsed > today:
        parsed = today
        adjusted = True
    return parsed.isoformat(), adjusted


def _sector_performance_candidates(date: str, exchange: str) -> list[tuple[str, str]]:
    parsed = datetime.fromisoformat(date).date()
    previous = _previous_business_day(parsed)
    candidates = [
        (date, exchange),
        (previous.isoformat(), exchange),
        (date, "NYSE"),
        (previous.isoformat(), "NYSE"),
    ]
    unique: list[tuple[str, str]] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _previous_business_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _has_items(raw: object) -> bool:
    if isinstance(raw, list):
        return bool(raw)
    if isinstance(raw, dict):
        items = raw.get("items")
        if isinstance(items, list):
            return bool(items)
        return any(value not in (None, "", [], {}) for value in raw.values())
    return bool(raw)


class _FmpCompositeClient(BaseRealToolClient):
    """Base for bounded FMP business tools with per-endpoint partial success."""

    source_scope = "fmp"
    title = "FMP data"

    def _fetch_many(
        self, api_key: str, symbol: str, endpoints: dict[str, tuple[str, dict[str, object]]]
    ) -> tuple[JsonObject, list[JsonObject]]:
        data: JsonObject = {}
        issues: list[JsonObject] = []
        for label, (path, params) in endpoints.items():
            try:
                raw = self._get_json(
                    self.settings.fmp_base_url.rstrip("/") + path,
                    params={"symbol": symbol, **params, "apikey": api_key},
                    cache_ttl=self.settings.fmp_cache_ttl_seconds,
                    rate_limit_key="fmp",
                    min_interval_seconds=0.2,
                    max_rate_limit_retries=1,
                )
                _raise_fmp_issue(raw)
                if not _has_items(raw):
                    issues.append(
                        {"endpoint": label, "code": "empty_result", "message": "No usable rows."}
                    )
                else:
                    projected = _project_fmp_payload(label, raw)
                    if projected:
                        data[label] = projected
                    else:
                        issues.append(
                            {
                                "endpoint": label,
                                "code": "empty_result",
                                "message": "No governed fields remained after projection.",
                            }
                        )
            except ProviderHttpError as exc:
                issues.append(
                    {
                        "endpoint": label,
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                    }
                )
        return data, issues

    def _composite_result(
        self,
        request: ToolRequest,
        *,
        symbol: str,
        data: JsonObject,
        issues: list[JsonObject],
        payload_key: str,
        summary: str,
    ) -> ToolResult:
        output = {"provider": "fmp", "symbol": symbol, payload_key: data, "provider_errors": issues}
        if not data:
            return self._failure(
                request,
                code="upstream_provider_error",
                message="FMP returned no usable data.",
                details={"provider_errors": issues},
            )
        kwargs = dict(
            output=output,
            raw={payload_key: data, "provider_errors": issues},
            source_kind="external_report",
            source_id=f"fmp:{self.source_scope}:{symbol}",
            title=self.title,
            summary=summary,
            source_scope=self.source_scope,
            confidence=0.74,
            metadata={
                "symbol": symbol,
                "endpoints": list(data),
                "failed_endpoints": [item["endpoint"] for item in issues],
            },
        )
        if issues:
            return self._partial(
                request,
                code="fmp_partial_subrequest_failure",
                message="Some FMP endpoint requests failed or were empty.",
                retryable=any(bool(item.get("retryable")) for item in issues),
                details={"provider_errors": issues},
                **kwargs,
            )
        return self._success(request, **kwargs)


class FmpSellSideEstimatesClient(_FmpCompositeClient):
    source_scope = "fmp_sell_side_estimates"
    title = "FMP sell-side estimates"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fmp_api_key, "FMP_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            period = _input_str(request, "period", "annual")
            data, issues = self._fetch_many(
                api_key,
                symbol,
                {
                    "analyst_estimates": (
                        "/stable/analyst-estimates",
                        {"period": period, "page": 0, "limit": 10},
                    ),
                    "price_target_summary": ("/stable/price-target-summary", {}),
                    "price_target_consensus": ("/stable/price-target-consensus", {}),
                },
            )
            return self._composite_result(
                request,
                symbol=symbol,
                data=data,
                issues=issues,
                payload_key="sell_side_estimates",
                summary="Retrieved FMP current sell-side estimate and target snapshots.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class FmpValuationSnapshotClient(_FmpCompositeClient):
    source_scope = "fmp_valuation_snapshot"
    title = "FMP valuation snapshot"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fmp_api_key, "FMP_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            data, issues = self._fetch_many(
                api_key,
                symbol,
                {
                    "profile": ("/stable/profile", {}),
                    "enterprise_values": ("/stable/enterprise-values", {}),
                    "key_metrics_ttm": ("/stable/key-metrics-ttm", {}),
                    "ratios_ttm": ("/stable/ratios-ttm", {}),
                },
            )
            return self._composite_result(
                request,
                symbol=symbol,
                data=data,
                issues=issues,
                payload_key="valuation_snapshot",
                summary="Retrieved FMP trailing valuation inputs without deriving a multiple.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class FmpTranscriptFallbackClient(_FmpCompositeClient):
    source_scope = "fmp_transcript_fallback"
    title = "FMP earnings-call transcript"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fmp_api_key, "FMP_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            year = _input_str(request, "year", "")
            quarter = _input_str(request, "quarter", "")
            if not year or not quarter:
                data, issues = self._fetch_many(
                    api_key,
                    symbol,
                    {"available_dates": ("/stable/earning-call-transcript-dates", {})},
                )
            else:
                data, issues = self._fetch_many(
                    api_key,
                    symbol,
                    {
                        "transcript": (
                            "/stable/earning-call-transcript",
                            {"year": year, "quarter": quarter},
                        )
                    },
                )
            return self._composite_result(
                request,
                symbol=symbol,
                data=data,
                issues=issues,
                payload_key="transcript",
                summary="Retrieved FMP transcript detail or available transcript dates.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _raise_fmp_issue(raw: JsonObject) -> None:
    message = raw.get("Error Message") or raw.get("error") or raw.get("message")
    if not message:
        return
    text = str(message)
    lowered = text.lower()
    if any(
        token in lowered
        for token in (
            "invalid api key",
            "not available",
            "subscription",
            "not authorized",
            "limit",
            "rate",
        )
    ):
        raise ProviderHttpError(
            code="rate_limited"
            if any(token in lowered for token in ("limit", "rate"))
            else "entitlement_or_permission_denied",
            message=text,
            retryable="limit" in lowered or "rate" in lowered,
            details={"provider_payload": raw},
        )


_FMP_FIELDS: dict[str, tuple[str, ...]] = {
    "analyst_estimates": (
        "symbol",
        "date",
        "revenueLow",
        "revenueHigh",
        "revenueAvg",
        "ebitdaLow",
        "ebitdaHigh",
        "ebitdaAvg",
        "ebitLow",
        "ebitHigh",
        "ebitAvg",
        "netIncomeLow",
        "netIncomeHigh",
        "netIncomeAvg",
        "epsLow",
        "epsHigh",
        "epsAvg",
        "numAnalystsRevenue",
        "numAnalystsEps",
    ),
    "price_target_summary": (
        "symbol",
        "lastMonthCount",
        "lastMonthAvgPriceTarget",
        "lastQuarterCount",
        "lastQuarterAvgPriceTarget",
        "lastYearCount",
        "lastYearAvgPriceTarget",
    ),
    "price_target_consensus": (
        "symbol",
        "targetHigh",
        "targetLow",
        "targetConsensus",
        "targetMedian",
    ),
    "profile": ("symbol", "price", "marketCap", "currency", "exchange", "sector", "industry"),
    "enterprise_values": (
        "symbol",
        "date",
        "stockPrice",
        "numberOfShares",
        "marketCapitalization",
        "minusCashAndCashEquivalents",
        "addTotalDebt",
        "enterpriseValue",
    ),
    "key_metrics_ttm": (
        "symbol",
        "marketCap",
        "enterpriseValueTTM",
        "evToSalesTTM",
        "evToEBITDATTM",
        "evToOperatingCashFlowTTM",
        "evToFreeCashFlowTTM",
        "earningsYieldTTM",
        "freeCashFlowYieldTTM",
        "returnOnInvestedCapitalTTM",
    ),
    "ratios_ttm": (
        "symbol",
        "priceToEarningsRatioTTM",
        "priceToBookRatioTTM",
        "priceToSalesRatioTTM",
        "priceToFreeCashFlowRatioTTM",
        "priceToOperatingCashFlowRatioTTM",
        "enterpriseValueMultipleTTM",
        "grossProfitMarginTTM",
        "operatingProfitMarginTTM",
        "netProfitMarginTTM",
        "debtToEquityRatioTTM",
    ),
    "available_dates": ("symbol", "fiscalYear", "quarter", "date"),
    "transcript": ("symbol", "quarter", "year", "date", "content"),
}


def _project_fmp_payload(label: str, raw: JsonObject) -> list[JsonObject]:
    value = raw.get("items")
    if isinstance(value, list):
        rows = value
    else:
        rows = [raw]
    fields = _FMP_FIELDS.get(label, ())
    limit = 10 if label == "analyst_estimates" else 5
    return [
        {key: row[key] for key in fields if row.get(key) not in (None, "", [], {})}
        for row in rows[:limit]
        if isinstance(row, dict)
    ]
