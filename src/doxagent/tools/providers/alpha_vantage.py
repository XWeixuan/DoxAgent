"""Alpha Vantage provider tools with HTTP-200 business-error handling."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    TTLCache,
    _input_str,
    _input_str_any,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult

ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS = 12.1
_ALPHA_ERROR_KEYS = ("Error Message", "Information", "Note")


class AlphaVantageClient(BaseRealToolClient):
    def __init__(
        self,
        settings: DoxAgentSettings,
        cache: TTLCache | None,
        function_name: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(settings, cache, client=client)
        self.function_name = function_name

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.alpha_vantage_api_key, "ALPHA_VANTAGE_API_KEY")
            symbol = _symbol(request)
            params: dict[str, object] = {
                "function": self.function_name,
                "symbol": symbol,
                "apikey": api_key,
            }
            if self.function_name == "TIME_SERIES_DAILY":
                params["outputsize"] = _input_str(request, "outputsize", "compact")
            raw = _alpha_get_json(
                self,
                params=params,
            )
            issue = _alpha_issue(raw)
            if issue is not None:
                if self.function_name == "SHARES_OUTSTANDING":
                    return self._shares_fallback(request, issue)
                return self._failure(request, **issue, details={"provider_payload": raw})
            if not _alpha_payload_has_data(raw, self.function_name):
                if self.function_name == "SHARES_OUTSTANDING":
                    return self._shares_fallback(
                        request,
                        {
                            "code": "empty_result",
                            "message": "Alpha Vantage returned no shares-outstanding rows.",
                            "retryable": False,
                        },
                    )
                return self._failure(
                    request,
                    code="empty_result",
                    message=f"Alpha Vantage {self.function_name} returned no usable data.",
                    details={"provider_payload": raw},
                )
            source_type = (
                "market_data" if self.function_name == "TIME_SERIES_DAILY" else "external_report"
            )
            normalized = (
                _normalize_alpha_overview(raw)
                if self.function_name == "OVERVIEW"
                else _clean_alpha_payload(raw)
            )
            return self._success(
                request,
                output={
                    "provider": "alpha_vantage",
                    "function": self.function_name,
                    "symbol": symbol,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "as_of": datetime.now(UTC).isoformat(),
                    "data": normalized,
                },
                raw=raw,
                source_kind=source_type,
                source_id=f"alpha_vantage:{self.function_name}:{symbol}",
                title=f"Alpha Vantage {self.function_name} - {symbol}",
                summary=f"Retrieved Alpha Vantage {self.function_name} data.",
                source_scope=f"alpha_{self.function_name.lower()}",
                confidence=0.78,
                metadata={"function": self.function_name, "symbol": symbol},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _shares_fallback(self, request: ToolRequest, alpha_issue: JsonObject) -> ToolResult:
        from doxagent.tools.providers.sec import SecCompanyFinancialsClient

        fallback_request = request.model_copy(
            update={
                "tool_name": "sec.company_financials",
                "input": {
                    "ticker": request.ticker,
                    "concepts": [
                        "CommonStockSharesOutstanding",
                        "WeightedAverageNumberOfDilutedSharesOutstanding",
                    ],
                },
            }
        )
        fallback = SecCompanyFinancialsClient(self.settings, self.cache, client=self.client).call(
            fallback_request
        )
        if not fallback.output:
            return self._failure(
                request,
                code=str(alpha_issue.get("code") or "empty_result"),
                message="Alpha Vantage shares were unavailable and SEC fallback also failed.",
                retryable=bool(alpha_issue.get("retryable")),
                details={"alpha_error": alpha_issue, "sec_error": fallback.error},
            )
        output = {
            **fallback.output,
            "fallback_for": "alpha.shares_outstanding",
            "alpha_error": alpha_issue,
        }
        coordinates = output.get("source_coordinates")
        if isinstance(coordinates, dict):
            coordinates["tool_name"] = request.tool_name
            coordinates["source_scope"] = "alpha_shares_outstanding_sec_fallback"
        return fallback.model_copy(
            update={
                "tool_name": request.tool_name,
                "output": output,
                "output_summary": (
                    "Alpha Vantage shares were unavailable; retrieved exact SEC share concepts."
                ),
            }
        )


class AlphaVantageFinancialStatementsClient(BaseRealToolClient):
    FUNCTIONS = ("INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW")
    FUNCTION_ALIASES = {
        "income": "INCOME_STATEMENT",
        "income_statement": "INCOME_STATEMENT",
        "balance": "BALANCE_SHEET",
        "balance_sheet": "BALANCE_SHEET",
        "cash_flow": "CASH_FLOW",
        "cashflow": "CASH_FLOW",
    }

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.alpha_vantage_api_key, "ALPHA_VANTAGE_API_KEY")
            symbol = _symbol(request)
            requested = _input_str(request, "statement_type", "all").strip().lower()
            functions: tuple[str, ...]
            if requested in {"", "all"}:
                functions = self.FUNCTIONS
            elif requested in self.FUNCTION_ALIASES:
                functions = (self.FUNCTION_ALIASES[requested],)
            else:
                raise ValueError(
                    "statement_type must be all, income_statement, balance_sheet, or cash_flow."
                )
            data, issues = self._fetch_functions(api_key, symbol, functions)
            output = {
                "provider": "alpha_vantage",
                "symbol": symbol,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "as_of": datetime.now(UTC).isoformat(),
                "statements": data,
                "provider_errors": issues,
            }
            if not data:
                return _alpha_all_failed(self, request, issues, "financial statements")
            if issues:
                return self._partial(
                    request,
                    output=output,
                    raw={"statements": data, "provider_errors": issues},
                    source_kind="external_report",
                    source_id=f"alpha_vantage:financial_statements:{symbol}",
                    title=f"Alpha Vantage financial statements - {symbol}",
                    summary="Alpha Vantage returned only some requested financial statements.",
                    source_scope="alpha_financial_statements",
                    confidence=0.62,
                    metadata={"symbol": symbol, "functions": list(functions)},
                    code="alpha_partial_subrequest_failure",
                    message="Some Alpha Vantage financial-statement subrequests failed.",
                    retryable=any(bool(item["retryable"]) for item in issues),
                    details={"provider_errors": issues},
                )
            return self._success(
                request,
                output=output,
                raw=data,
                source_kind="external_report",
                source_id=f"alpha_vantage:financial_statements:{symbol}",
                title=f"Alpha Vantage financial statements - {symbol}",
                summary="Retrieved Alpha Vantage standardized financial statements.",
                source_scope="alpha_financial_statements",
                confidence=0.76,
                metadata={"symbol": symbol, "functions": list(functions)},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _fetch_functions(
        self, api_key: str, symbol: str, functions: tuple[str, ...]
    ) -> tuple[JsonObject, list[JsonObject]]:
        data: JsonObject = {}
        issues: list[JsonObject] = []
        for function_name in functions:
            raw = _alpha_get_json(
                self,
                params={"function": function_name, "symbol": symbol, "apikey": api_key},
            )
            issue = _alpha_issue(raw)
            if issue is not None:
                issues.append({"function": function_name, **issue, "provider_payload": raw})
            elif _alpha_payload_has_data(raw, function_name):
                data[function_name] = _normalize_alpha_statement(raw, function_name)
            else:
                issues.append(_empty_alpha_issue(function_name, raw))
        return data, issues


class AlphaVantageEarningsClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.alpha_vantage_api_key, "ALPHA_VANTAGE_API_KEY")
            symbol = _symbol(request)
            event_type = _input_str(request, "event_type", "all").strip().lower()
            allowed = {"all", "history", "estimates", "calendar"}
            if event_type not in allowed:
                raise ValueError("event_type must be all, history, estimates, or calendar.")
            data: JsonObject = {}
            issues: list[JsonObject] = []
            json_functions: list[str] = []
            if event_type in {"all", "history"}:
                json_functions.append("EARNINGS")
            if event_type in {"all", "estimates"}:
                json_functions.append("EARNINGS_ESTIMATES")
            for function_name in json_functions:
                raw = _alpha_get_json(
                    self,
                    params={"function": function_name, "symbol": symbol, "apikey": api_key},
                )
                issue = _alpha_issue(raw)
                if issue is not None:
                    issues.append({"function": function_name, **issue, "provider_payload": raw})
                elif _alpha_payload_has_data(raw, function_name):
                    data[function_name] = (
                        _normalize_alpha_earnings(raw)
                        if function_name == "EARNINGS"
                        else _clean_alpha_payload(raw)
                    )
                else:
                    issues.append(_empty_alpha_issue(function_name, raw))
            if event_type in {"all", "calendar"}:
                csv_text = self._get_text(
                    self.settings.alpha_vantage_base_url,
                    params={"function": "EARNINGS_CALENDAR", "symbol": symbol, "apikey": api_key},
                    cache_ttl=None,
                    rate_limit_key="alpha_vantage",
                    min_interval_seconds=ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS,
                )
                calendar_rows, calendar_issue = _parse_alpha_calendar(csv_text)
                if calendar_issue is not None:
                    issues.append({"function": "EARNINGS_CALENDAR", **calendar_issue})
                elif calendar_rows:
                    data["EARNINGS_CALENDAR"] = [
                        _clean_alpha_payload(row) for row in calendar_rows[:20]
                    ]
                else:
                    issues.append(_empty_alpha_issue("EARNINGS_CALENDAR", {}))
            output = {
                "provider": "alpha_vantage",
                "symbol": symbol,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "as_of": datetime.now(UTC).isoformat(),
                "earnings": data,
                "provider_errors": issues,
            }
            if not data:
                return _alpha_all_failed(self, request, issues, "earnings data")
            if issues:
                return self._partial(
                    request,
                    output=output,
                    raw={"earnings": data, "provider_errors": issues},
                    source_kind="external_report",
                    source_id=f"alpha_vantage:earnings:{symbol}",
                    title=f"Alpha Vantage earnings events - {symbol}",
                    summary="Alpha Vantage returned only some requested earnings data.",
                    source_scope="alpha_earnings_events",
                    confidence=0.6,
                    metadata={"symbol": symbol, "event_type": event_type},
                    code="alpha_partial_subrequest_failure",
                    message="Some Alpha Vantage earnings subrequests failed.",
                    retryable=any(bool(item["retryable"]) for item in issues),
                    details={"provider_errors": issues},
                )
            return self._success(
                request,
                output=output,
                raw=data,
                source_kind="external_report",
                source_id=f"alpha_vantage:earnings:{symbol}",
                title=f"Alpha Vantage earnings events - {symbol}",
                summary="Retrieved Alpha Vantage earnings history, estimates, or calendar data.",
                source_scope="alpha_earnings_events",
                confidence=0.74,
                metadata={"symbol": symbol, "event_type": event_type},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _symbol(request: ToolRequest) -> str:
    return _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()


def _alpha_get_json(client: BaseRealToolClient, *, params: dict[str, object]) -> JsonObject:
    """Cache only valid Alpha payloads and retry one HTTP-200 rate-limit envelope."""

    cache_identity = {
        key: value for key, value in params.items() if key.lower() != "apikey"
    }
    cache_key = "alpha_valid:" + json.dumps(
        cache_identity, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    cached = client.cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    raw: JsonObject = {}
    transport = getattr(client.client, "_transport", None)
    min_interval = (
        0.0
        if isinstance(transport, httpx.MockTransport)
        else ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS
    )
    for attempt in range(2):
        raw = client._get_json(
            client.settings.alpha_vantage_base_url,
            params=params,
            cache_ttl=None,
            rate_limit_key="alpha_vantage",
            min_interval_seconds=min_interval,
        )
        issue = _alpha_issue(raw)
        if issue is None:
            client.cache.set(cache_key, raw, client.settings.alpha_cache_ttl_seconds)
            return raw
        if not issue.get("retryable") or attempt == 1:
            return raw
    return raw


def _alpha_issue(raw: JsonObject) -> JsonObject | None:
    for key in _ALPHA_ERROR_KEYS:
        message = raw.get(key)
        if not isinstance(message, str) or not message.strip():
            continue
        lowered = message.lower()
        retryable = key == "Note" or any(
            token in lowered for token in ("rate limit", "call frequency", "try again", "requests")
        )
        code = "rate_limited" if retryable else "upstream_provider_error"
        return {"code": code, "message": message.strip(), "retryable": retryable}
    return None


def _alpha_payload_has_data(raw: JsonObject, function_name: str) -> bool:
    expected = {
        "OVERVIEW": ("Symbol", "Name"),
        "SHARES_OUTSTANDING": ("annualSharesOutstanding", "quarterlySharesOutstanding"),
        "TIME_SERIES_DAILY": ("Time Series (Daily)",),
        "INCOME_STATEMENT": ("annualReports", "quarterlyReports"),
        "BALANCE_SHEET": ("annualReports", "quarterlyReports"),
        "CASH_FLOW": ("annualReports", "quarterlyReports"),
        "EARNINGS": ("annualEarnings", "quarterlyEarnings"),
        "EARNINGS_ESTIMATES": ("estimates",),
    }.get(function_name, ())
    if expected:
        return any(raw.get(key) not in (None, "", [], {}) for key in expected)
    return bool(raw)


def _empty_alpha_issue(function_name: str, raw: JsonObject) -> JsonObject:
    return {
        "function": function_name,
        "code": "empty_result",
        "message": f"Alpha Vantage {function_name} returned no usable data.",
        "retryable": False,
        "provider_payload": raw,
    }


def _parse_alpha_calendar(csv_text: str) -> tuple[list[dict[str, str]], JsonObject | None]:
    stripped = csv_text.strip()
    if not stripped:
        return [], None
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return [], {
                "code": "upstream_provider_error",
                "message": "Alpha Vantage returned malformed earnings-calendar JSON.",
                "retryable": False,
                "provider_payload_preview": stripped[:500],
            }
        if isinstance(payload, dict):
            issue = _alpha_issue(payload)
            if issue is not None:
                return [], {**issue, "provider_payload": payload}
    rows = [dict(row) for row in csv.DictReader(StringIO(csv_text))]
    return rows, None


def _alpha_all_failed(
    client: BaseRealToolClient,
    request: ToolRequest,
    issues: list[JsonObject],
    label: str,
) -> ToolResult:
    retryable = any(bool(item.get("retryable")) for item in issues)
    code = "rate_limited" if retryable else "upstream_provider_error"
    return client._failure(
        request,
        code=code,
        message=f"Alpha Vantage returned no usable {label}.",
        retryable=retryable,
        details={"provider_errors": issues},
    )


def _normalize_alpha_overview(raw: JsonObject) -> JsonObject:
    market_pricing_prefixes = (
        "Analyst",
        "52Week",
        "50Day",
        "200Day",
        "TrailingPE",
        "ForwardPE",
        "PriceTo",
        "EVTo",
        "MarketCapitalization",
        "Beta",
        "PEGRatio",
        "PERatio",
    )
    projected = {
        key: _clean_alpha_value(value, key=key)
        for key, value in raw.items()
        if not key.startswith(market_pricing_prefixes)
    }
    projected = {key: value for key, value in projected.items() if value is not None}
    projected["_basis"] = {
        "LatestQuarter": "provider period marker; not an exact SEC report-date substitute",
        "TTM_fields": "provider current trailing-twelve-month normalization",
        "excluded": "market pricing, technical, valuation, analyst rating and price-target fields",
    }
    return projected


def _normalize_alpha_statement(raw: JsonObject, function_name: str) -> JsonObject:
    return {
        "symbol": raw.get("symbol"),
        "annualReports": [
            _clean_alpha_payload(row)
            for row in raw.get("annualReports", [])[:8]
            if isinstance(row, dict)
        ],
        "quarterlyReports": [
            _clean_alpha_payload(row)
            for row in raw.get("quarterlyReports", [])[:12]
            if isinstance(row, dict)
        ],
        "mapping_basis": (
            f"Alpha Vantage {function_name} standardized provider mapping; "
            "use SEC concepts for filing-exact accounting semantics"
        ),
    }


def _normalize_alpha_earnings(raw: JsonObject) -> JsonObject:
    annual = [item for item in raw.get("annualEarnings", []) if isinstance(item, dict)]
    months = [
        str(item.get("fiscalDateEnding") or "")[5:7]
        for item in annual
        if len(str(item.get("fiscalDateEnding") or "")) >= 7
    ]
    fiscal_year_end_month = Counter(months).most_common(1)[0][0] if months else ""
    if fiscal_year_end_month:
        annual = [
            item
            for item in annual
            if str(item.get("fiscalDateEnding") or "")[5:7] == fiscal_year_end_month
        ]
    return {
        "symbol": raw.get("symbol"),
        "annualEarnings": [_clean_alpha_payload(row) for row in annual[:12]],
        "quarterlyEarnings": [
            _clean_alpha_payload(row)
            for row in raw.get("quarterlyEarnings", [])[:20]
            if isinstance(row, dict)
        ],
        "eps_basis": (
            "provider normalized/unspecified; do not label reportedEPS as SEC GAAP EPS "
            "without filing reconciliation"
        ),
        "annual_period_filter": (
            f"kept modal fiscal-year-end month {fiscal_year_end_month}"
            if fiscal_year_end_month
            else "no fiscal-year-end month inferred"
        ),
    }


def _clean_alpha_payload(value: object) -> Any:
    if isinstance(value, dict):
        return {
            str(key): cleaned
            for key, item in value.items()
            if (cleaned := _clean_alpha_value(item, key=str(key))) is not None
        }
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := _clean_alpha_payload(item)) is not None]
    return _clean_alpha_value(value)


def _clean_alpha_value(value: object, *, key: str = "") -> Any:
    if isinstance(value, (dict, list)):
        return _clean_alpha_payload(value)
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.lower() in {"", "none", "null", "n/a", "na"}:
        return None
    if key not in {"symbol", "reportedCurrency", "fiscalDateEnding", "reportedDate"}:
        try:
            return int(stripped)
        except ValueError:
            try:
                return float(stripped)
            except ValueError:
                pass
    return stripped
