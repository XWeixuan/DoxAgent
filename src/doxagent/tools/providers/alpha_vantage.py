"""Alpha Vantage provider tools with HTTP-200 business-error handling."""

from __future__ import annotations

import csv
import json
import time
from io import StringIO

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

ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS = 1.3
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
            raw = self._get_json(
                self.settings.alpha_vantage_base_url,
                params=params,
                cache_ttl=self.settings.alpha_cache_ttl_seconds,
            )
            issue = _alpha_issue(raw)
            if issue is not None:
                return self._failure(request, **issue, details={"provider_payload": raw})
            if not _alpha_payload_has_data(raw, self.function_name):
                return self._failure(
                    request,
                    code="empty_result",
                    message=f"Alpha Vantage {self.function_name} returned no usable data.",
                    details={"provider_payload": raw},
                )
            source_type = (
                "market_data"
                if self.function_name == "TIME_SERIES_DAILY"
                else "external_report"
            )
            return self._success(
                request,
                output={
                    "provider": "alpha_vantage",
                    "function": self.function_name,
                    "symbol": symbol,
                    "data": raw,
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
        for index, function_name in enumerate(functions):
            if index:
                time.sleep(ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS)
            raw = self._get_json(
                self.settings.alpha_vantage_base_url,
                params={"function": function_name, "symbol": symbol, "apikey": api_key},
                cache_ttl=self.settings.alpha_cache_ttl_seconds,
            )
            issue = _alpha_issue(raw)
            if issue is not None:
                issues.append({"function": function_name, **issue, "provider_payload": raw})
            elif _alpha_payload_has_data(raw, function_name):
                data[function_name] = raw
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
            for index, function_name in enumerate(json_functions):
                if index:
                    time.sleep(ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS)
                raw = self._get_json(
                    self.settings.alpha_vantage_base_url,
                    params={"function": function_name, "symbol": symbol, "apikey": api_key},
                    cache_ttl=self.settings.alpha_cache_ttl_seconds,
                )
                issue = _alpha_issue(raw)
                if issue is not None:
                    issues.append({"function": function_name, **issue, "provider_payload": raw})
                elif _alpha_payload_has_data(raw, function_name):
                    data[function_name] = raw
                else:
                    issues.append(_empty_alpha_issue(function_name, raw))
            if event_type in {"all", "calendar"}:
                if json_functions:
                    time.sleep(ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS)
                csv_text = self._get_text(
                    self.settings.alpha_vantage_base_url,
                    params={"function": "EARNINGS_CALENDAR", "symbol": symbol, "apikey": api_key},
                    cache_ttl=self.settings.alpha_cache_ttl_seconds,
                )
                calendar_rows, calendar_issue = _parse_alpha_calendar(csv_text)
                if calendar_issue is not None:
                    issues.append({"function": "EARNINGS_CALENDAR", **calendar_issue})
                elif calendar_rows:
                    data["EARNINGS_CALENDAR"] = calendar_rows
                else:
                    issues.append(_empty_alpha_issue("EARNINGS_CALENDAR", {}))
            output = {
                "provider": "alpha_vantage",
                "symbol": symbol,
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
