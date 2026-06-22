"""Finnhub provider tools."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, cast

from doxagent.models import EvidenceSourceType, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    _input_list,
    _input_str,
    _require,
)
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class FinnhubPeersClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.finnhub_api_key, "FINNHUB_API_KEY")
            symbol = _input_str(request, "symbol", request.ticker).upper()
            grouping = _input_str(request, "grouping", "industry")
            raw = self._get_json(
                self.settings.finnhub_base_url.rstrip("/") + "/stock/peers",
                params={"symbol": symbol, "grouping": grouping, "token": api_key},
                cache_ttl=self.settings.finnhub_cache_ttl_seconds,
            )
            return self._success(
                request,
                output={"provider": "finnhub", "symbol": symbol, "peers": raw},
                raw=raw,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"finnhub:peers:{symbol}",
                title=f"Finnhub 同业列表 - {symbol}",
                summary="已检索 Finnhub 公司同业列表。",
                citation_scope="finnhub_company_peers",
                confidence=0.7,
                metadata={"symbol": symbol, "grouping": grouping},
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
                raise ValueError(
                    f"duration_seconds must be between 0 and {max_duration}."
                )
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
            result = ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output={"provider": "finnhub", "symbols": symbols, "events": events},
                output_summary="已捕获 Finnhub 有界交易流。",
                raw=events,
            )
            evidence = result.to_evidence_ref(
                source_type=EvidenceSourceType.MARKET_DATA,
                source_id=f"finnhub:trade_stream:{','.join(symbols)}",
                title="Finnhub 交易流",
                citation_scope="finnhub_trade_stream",
                confidence=0.65,
            ).model_copy(
                update={
                    "retrieval_metadata": {
                        "tool_name": request.tool_name,
                        "symbols": symbols,
                        "duration_seconds": duration,
                        "max_events": max_events,
                        "bounded_capture": True,
                    }
                }
            )
            return result.model_copy(update={"evidence_refs": [evidence]}, deep=True)
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
