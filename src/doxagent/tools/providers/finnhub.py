"""Finnhub provider tools."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, cast

from doxagent.models import ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
    _input_str_any,
    _require,
)
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class FinnhubPeersClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.finnhub_api_key, "FINNHUB_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            grouping = _input_str(request, "grouping", "industry")
            raw = self._get_json(
                self.settings.finnhub_base_url.rstrip("/") + "/stock/peers",
                params={"symbol": symbol, "grouping": grouping, "token": api_key},
                cache_ttl=self.settings.finnhub_cache_ttl_seconds,
            )
            peers = raw.get("items")
            if not isinstance(peers, list) or not peers:
                return self._failure(
                    request,
                    code="empty_result",
                    message="Finnhub returned no company peers.",
                    details={"symbol": symbol, "grouping": grouping},
                )
            return self._success(
                request,
                output={
                    "provider": "finnhub",
                    "symbol": symbol,
                    "grouping": grouping,
                    "peers": raw,
                    "methodology_notice": (
                        "Provider-generated peer candidates for universe discovery; membership "
                        "does not imply a strict comparable-company methodology or hierarchy."
                    ),
                    "as_of": request.metadata.get("cutoff_at"),
                },
                raw=raw,
                source_kind="external_report",
                source_id=f"finnhub:peers:{symbol}",
                title=f"Finnhub 同业列表 - {symbol}",
                summary="已检索 Finnhub 公司同业列表。",
                source_scope="finnhub_company_peers",
                confidence=0.7,
                metadata={"symbol": symbol, "grouping": grouping},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class _FinnhubCompositeClient(BaseRealToolClient):
    source_scope = "finnhub"
    title = "Finnhub data"

    def _fetch_many(
        self,
        api_key: str,
        symbol: str,
        endpoints: dict[str, tuple[str, dict[str, object]]],
        *,
        row_limit: int = 25,
    ) -> tuple[JsonObject, list[JsonObject]]:
        data: JsonObject = {}
        issues: list[JsonObject] = []
        for label, (path, params) in endpoints.items():
            try:
                raw = self._get_json(
                    self.settings.finnhub_base_url.rstrip("/") + path,
                    params={"symbol": symbol, **params, "token": api_key},
                    cache_ttl=self.settings.finnhub_cache_ttl_seconds,
                    rate_limit_key="finnhub",
                    min_interval_seconds=0.25,
                    max_rate_limit_retries=1,
                )
                _raise_finnhub_issue(raw)
                if _has_finnhub_rows(raw):
                    projected = _project_finnhub_payload(
                        label,
                        raw,
                        symbol=symbol,
                        limit=row_limit,
                    )
                    if projected not in (None, "", [], {}):
                        data[label] = projected
                    else:
                        issues.append(
                            {
                                "endpoint": label,
                                "code": "empty_result",
                                "message": "No governed fields remained after projection.",
                            }
                        )
                else:
                    issues.append(
                        {"endpoint": label, "code": "empty_result", "message": "No usable rows."}
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

    def _result(
        self,
        request: ToolRequest,
        *,
        symbol: str,
        data: JsonObject,
        issues: list[JsonObject],
        payload_key: str,
        summary: str,
    ) -> ToolResult:
        payload: object = data
        if len(data) == 1 and payload_key in data:
            payload = data[payload_key]
        output = {
            "provider": "finnhub",
            "symbol": symbol,
            payload_key: payload,
            "provider_errors": issues,
        }
        if not data:
            return self._failure(
                request,
                code="upstream_provider_error",
                message="Finnhub returned no usable data.",
                details={"provider_errors": issues},
            )
        kwargs = dict(
            output=output,
            raw=output,
            source_kind="external_report",
            source_id=f"finnhub:{self.source_scope}:{symbol}",
            title=self.title,
            summary=summary,
            source_scope=self.source_scope,
            confidence=0.7,
            metadata={
                "symbol": symbol,
                "endpoints": list(data),
                "failed_endpoints": [item["endpoint"] for item in issues],
            },
        )
        if issues:
            return self._partial(
                request,
                code="finnhub_partial_subrequest_failure",
                message="Some Finnhub endpoint requests failed or were empty.",
                retryable=any(bool(item.get("retryable")) for item in issues),
                details={"provider_errors": issues},
                **kwargs,
            )
        return self._success(request, **kwargs)


class FinnhubInsiderTransactionsClient(_FinnhubCompositeClient):
    source_scope = "finnhub_insider_transactions"
    title = "Finnhub insider transactions"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.finnhub_api_key, "FINNHUB_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            params: dict[str, object] = {}
            date_from = _input_str(request, "from", _input_str(request, "date_from", ""))
            date_to = _input_str(request, "to", _input_str(request, "date_to", ""))
            if date_from:
                params["from"] = date_from
            if date_to:
                params["to"] = date_to
            data, issues = self._fetch_many(
                api_key,
                symbol,
                {
                    "insider_transactions": ("/stock/insider-transactions", params),
                },
            )
            result = self._result(
                request,
                symbol=symbol,
                data=data,
                issues=issues,
                payload_key="insider_transactions",
                summary="Retrieved Finnhub insider-transaction records.",
            )
            if result.output:
                result.output["transaction_code_notice"] = (
                    "Finnhub transactionCode is provider-native. Role, direct/indirect ownership, "
                    "Rule 10b5-1, gift and tax-withholding attribution are not supplied by this "
                    "endpoint; do not classify records as discretionary buys/sells without Form 4."
                )
            return result
        except Exception as exc:
            return self._handle_exception(request, exc)


class FinnhubCompanyNewsEventsClient(_FinnhubCompositeClient):
    source_scope = "finnhub_company_news_events"
    title = "Finnhub company news and events"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.finnhub_api_key, "FINNHUB_API_KEY")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            date_from = _input_str(request, "from", _input_str(request, "date_from", "2020-01-01"))
            date_to = _input_str(request, "to", _input_str(request, "date_to", "2030-01-01"))
            row_limit = _bounded_int(request.input.get("limit", 10), 1, 50)
            endpoints: dict[str, tuple[str, dict[str, object]]] = {
                "company_news": ("/company-news", {"from": date_from, "to": date_to}),
            }
            if bool(request.input.get("include_earnings", True)):
                endpoints["earnings"] = ("/stock/earnings", {})
            data, issues = self._fetch_many(
                api_key,
                symbol,
                endpoints,
                row_limit=row_limit,
            )
            result = self._result(
                request,
                symbol=symbol,
                data=data,
                issues=issues,
                payload_key="company_news_events",
                summary="Retrieved Finnhub company-news and earnings-event records.",
            )
            if result.output:
                result.output["applied_window"] = {"from": date_from, "to": date_to}
                result.output["record_limit_per_endpoint"] = row_limit
                result.output["usage_notice"] = (
                    "Finnhub news is a bounded discovery feed, not final-state evidence; verify "
                    "material claims against issuer, SEC, or regulator primary sources."
                )
            return result
        except Exception as exc:
            return self._handle_exception(request, exc)


class FinnhubTradeStreamClient:
    def __init__(self, settings: DoxAgentSettings) -> None:
        self.settings = settings

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.finnhub_api_key, "FINNHUB_API_KEY")
            symbols = _input_list(request, "symbols") or [
                _input_str(request, "symbol", request.ticker)
            ]
            duration = float(request.input.get("duration_seconds", 3))
            max_events = int(request.input.get("max_events", 25))
            if duration <= 0 or duration > self.settings.finnhub_max_stream_seconds:
                max_duration = self.settings.finnhub_max_stream_seconds
                raise ValueError(f"duration_seconds must be between 0 and {max_duration}.")
            if max_events <= 0 or max_events > self.settings.finnhub_max_stream_events:
                raise ValueError(
                    f"max_events must be between 1 and {self.settings.finnhub_max_stream_events}."
                )
            events = asyncio.run(
                _capture_finnhub_trades(
                    token=api_key,
                    ws_url=self.settings.finnhub_ws_url,
                    symbols=[symbol.upper() for symbol in symbols],
                    duration_seconds=duration,
                    max_events=max_events,
                )
            )
            if not events:
                return ToolResult(
                    tool_name=request.tool_name,
                    status=ResultStatus.PARTIAL,
                    output={
                        "provider": "finnhub",
                        "symbols": symbols,
                        "events": [],
                        "capture_status": "empty_sample",
                    },
                    output_summary="Finnhub capture completed but produced no trade events.",
                    raw=[],
                    error=ToolError(
                        code="empty_stream_sample",
                        message="The bounded Finnhub capture window contained no trade events.",
                        retryable=True,
                        details={
                            "symbols": symbols,
                            "duration_seconds": duration,
                            "max_events": max_events,
                        },
                    ),
                )
            result = ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output={
                    "provider": "finnhub",
                    "symbols": symbols,
                    "events": events,
                    "source_coordinates": {
                        "source_kind": "market_data",
                        "source_id": f"finnhub:trade_stream:{','.join(symbols)}",
                        "duration_seconds": duration,
                        "max_events": max_events,
                    },
                },
                output_summary="已捕获 Finnhub 有界交易流。",
                raw=events,
            )
            return result
        except Exception as exc:
            code = "tool_execution_failed"
            retryable = False
            if isinstance(exc, TimeoutError):
                code = "stream_timeout"
                retryable = True
            message = _trade_stream_error_message(exc)
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                output_summary=f"{code}: {message}",
                error=ToolError(
                    code=code,
                    message=message,
                    retryable=retryable,
                    details={
                        "provider": "finnhub",
                        "provider_error": type(exc).__name__,
                        "provider_error_repr": repr(exc),
                        "symbols": symbols if "symbols" in locals() else [],
                        "duration_seconds": duration if "duration" in locals() else None,
                        "max_events": max_events if "max_events" in locals() else None,
                    },
                ),
            )


def _trade_stream_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return f"Finnhub trade stream failed with {type(exc).__name__}: {repr(exc)}"


def _raise_finnhub_issue(raw: JsonObject) -> None:
    message = raw.get("error") or raw.get("message")
    if not message:
        return
    text = str(message)
    lowered = text.lower()
    if any(
        token in lowered
        for token in ("api key", "not authorized", "premium", "subscription", "limit", "rate")
    ):
        raise ProviderHttpError(
            code="rate_limited"
            if any(token in lowered for token in ("limit", "rate"))
            else "entitlement_or_permission_denied",
            message=text,
            retryable="limit" in lowered or "rate" in lowered,
            details={"provider_payload": raw},
        )


def _has_finnhub_rows(raw: JsonObject) -> bool:
    return any(
        value not in (None, "", [], {})
        for key, value in raw.items()
        if key not in {"error", "message", "status"}
    )


_FINNHUB_FIELDS: dict[str, tuple[str, ...]] = {
    "ownership": ("name", "share", "change", "filingDate", "portfolioPercent"),
    "insider_transactions": (
        "name",
        "share",
        "change",
        "filingDate",
        "transactionDate",
        "transactionPrice",
        "transactionCode",
    ),
    "company_news": (
        "id",
        "datetime",
        "headline",
        "summary",
        "source",
        "url",
        "category",
        "related",
    ),
    "earnings": (
        "period",
        "quarter",
        "year",
        "actual",
        "estimate",
        "surprise",
        "surprisePercent",
        "symbol",
    ),
}


def _project_finnhub_payload(
    label: str,
    raw: JsonObject,
    *,
    symbol: str = "",
    limit: int = 25,
) -> JsonObject | list[JsonObject]:
    container = {
        "ownership": "ownership",
        "insider_transactions": "data",
        "company_news": "items",
        "earnings": "data",
    }.get(label)
    value = raw.get(container) if container else None
    if label in {"company_news", "earnings"} and not isinstance(value, list):
        value = raw.get("items")
    if not isinstance(value, list):
        return {}
    fields = _FINNHUB_FIELDS.get(label, ())
    projected = [
        {
            key: _repair_common_mojibake(row[key])
            for key in fields
            if row.get(key) not in (None, "", [], {})
        }
        for row in value
        if isinstance(row, dict)
    ]
    if label == "company_news":
        projected = _select_company_news(projected, symbol=symbol, limit=limit)
    else:
        projected = projected[:limit]
    return {"symbol": raw.get("symbol") or symbol, "records": projected}


def _select_company_news(
    records: list[JsonObject], *, symbol: str, limit: int
) -> list[JsonObject]:
    selected: list[JsonObject] = []
    seen: set[str] = set()
    junk_phrases = (
        "prediction market",
        "daily roundup",
        "etf flows",
        "options corner",
        "top gainers and losers",
        "market movers",
        "dow jones index",
        "s&p 500 session",
        "nasdaq session",
    )
    material_terms = (
        "earnings",
        "financial results",
        "revenue",
        "guidance",
        "outlook",
        "launch",
        "product",
        "partnership",
        "acquisition",
        "regulatory",
        "export control",
        "data center",
        "gpu",
        "hbm",
        "cloud",
        "capital expenditure",
        "capex",
        "supply",
        "shipment",
        "customer",
    )
    for row in sorted(records, key=lambda item: int(item.get("datetime") or 0), reverse=True):
        headline = str(row.get("headline") or "")
        summary = str(row.get("summary") or "")
        related = str(row.get("related") or "").upper()
        lowered = f"{headline} {summary}".lower()
        company_named = symbol.lower() in lowered or "nvidia" in lowered
        if any(phrase in lowered for phrase in junk_phrases):
            continue
        if not company_named:
            continue
        if not any(term in lowered for term in material_terms):
            continue
        if related and symbol and symbol not in {item.strip() for item in related.split(",")}:
            continue
        identity = str(row.get("url") or headline).strip().lower()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        selected.append(row)
        if len(selected) >= limit:
            break
    if selected:
        return selected
    # A discovery tool should not turn a non-empty, symbol-scoped provider response into a
    # silent success with zero rows.  Keep a small fallback only when the high-signal filter
    # found nothing; downstream authoritative-source verification remains required.
    fallback = []
    for row in records:
        related = str(row.get("related") or "").upper()
        if related and symbol and symbol not in {item.strip() for item in related.split(",")}:
            continue
        fallback.append(row)
        if len(fallback) >= min(limit, 5):
            break
    return fallback


def _repair_common_mojibake(value: object) -> object:
    if not isinstance(value, str) or not any(
        marker in value for marker in ("Ã", "â€", "â\u0080", "Â")
    ):
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if repaired.count("�") <= value.count("�") else value


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


async def _capture_finnhub_trades(
    *,
    token: str,
    ws_url: str,
    symbols: list[str],
    duration_seconds: float,
    max_events: int,
) -> list[JsonObject]:
    import importlib

    websockets = cast(Any, importlib.import_module("websockets"))
    events: list[JsonObject] = []
    deadline = time.monotonic() + duration_seconds
    async with websockets.connect(f"{ws_url}?token={token}") as websocket:
        for symbol in symbols:
            await websocket.send(json.dumps({"type": "subscribe", "symbol": symbol}))
        while len(events) < max_events and time.monotonic() < deadline:
            timeout = max(0.01, deadline - time.monotonic())
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except TimeoutError:
                break
            data = json.loads(str(message))
            if isinstance(data, dict):
                events.append(cast(JsonObject, data))
        for symbol in symbols:
            await websocket.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))
    return events
