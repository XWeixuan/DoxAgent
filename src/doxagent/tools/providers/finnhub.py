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
                output={"provider": "finnhub", "symbol": symbol, "peers": raw},
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
        self, api_key: str, symbol: str, endpoints: dict[str, tuple[str, dict[str, object]]]
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
                    projected = _project_finnhub_payload(label, raw)
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
            return self._result(
                request,
                symbol=symbol,
                data=data,
                issues=issues,
                payload_key="insider_transactions",
                summary="Retrieved Finnhub insider-transaction records.",
            )
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
            data, issues = self._fetch_many(
                api_key,
                symbol,
                {
                    "company_news": ("/company-news", {"from": date_from, "to": date_to}),
                    "earnings": ("/stock/earnings", {}),
                },
            )
            return self._result(
                request,
                symbol=symbol,
                data=data,
                issues=issues,
                payload_key="company_news_events",
                summary="Retrieved Finnhub company-news and earnings-event records.",
            )
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


def _project_finnhub_payload(label: str, raw: JsonObject) -> JsonObject | list[JsonObject]:
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
    limit = 25 if label == "company_news" else 50
    fields = _FINNHUB_FIELDS.get(label, ())
    rows = [
        {key: row[key] for key in fields if row.get(key) not in (None, "", [], {})}
        for row in value[:limit]
        if isinstance(row, dict)
    ]
    return {"symbol": raw.get("symbol"), "records": rows}


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
