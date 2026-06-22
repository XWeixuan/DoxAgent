"""Alpha Vantage provider tools."""

from __future__ import annotations

import csv
import time
from io import StringIO

import httpx

from doxagent.models import EvidenceSourceType
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    TTLCache,
    _input_str,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult

ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS = 1.3


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
            symbol = _input_str(request, "symbol", request.ticker).upper()
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
            source_type = (
                EvidenceSourceType.MARKET_DATA
                if self.function_name == "TIME_SERIES_DAILY"
                else EvidenceSourceType.EXTERNAL_REPORT
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
                source_type=source_type,
                source_id=f"alpha_vantage:{self.function_name}:{symbol}",
                title=f"Alpha Vantage {self.function_name} - {symbol}",
                summary=f"已检索 Alpha Vantage {self.function_name} 数据。",
                citation_scope=f"alpha_{self.function_name.lower()}",
                confidence=0.78,
                metadata={"function": self.function_name, "symbol": symbol},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class AlphaVantageFinancialStatementsClient(BaseRealToolClient):
    FUNCTIONS = ("INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW")

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.alpha_vantage_api_key, "ALPHA_VANTAGE_API_KEY")
            symbol = _input_str(request, "symbol", request.ticker).upper()
            data: JsonObject = {}
            for index, function_name in enumerate(self.FUNCTIONS):
                if index:
                    time.sleep(ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS)
                data[function_name] = self._get_json(
                    self.settings.alpha_vantage_base_url,
                    params={"function": function_name, "symbol": symbol, "apikey": api_key},
                    cache_ttl=self.settings.alpha_cache_ttl_seconds,
                )
            return self._success(
                request,
                output={"provider": "alpha_vantage", "symbol": symbol, "statements": data},
                raw=data,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"alpha_vantage:financial_statements:{symbol}",
                title=f"Alpha Vantage 财务报表 - {symbol}",
                summary="已检索 Alpha Vantage 标准化财务报表数据。",
                citation_scope="alpha_financial_statements",
                confidence=0.76,
                metadata={"symbol": symbol, "functions": list(self.FUNCTIONS)},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class AlphaVantageEarningsClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.alpha_vantage_api_key, "ALPHA_VANTAGE_API_KEY")
            symbol = _input_str(request, "symbol", request.ticker).upper()
            data: JsonObject = {}
            for index, function_name in enumerate(("EARNINGS", "EARNINGS_ESTIMATES")):
                if index:
                    time.sleep(ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS)
                data[function_name] = self._get_json(
                    self.settings.alpha_vantage_base_url,
                    params={"function": function_name, "symbol": symbol, "apikey": api_key},
                    cache_ttl=self.settings.alpha_cache_ttl_seconds,
                )
            time.sleep(ALPHA_FREE_TIER_REQUEST_INTERVAL_SECONDS)
            csv_text = self._get_text(
                self.settings.alpha_vantage_base_url,
                params={"function": "EARNINGS_CALENDAR", "symbol": symbol, "apikey": api_key},
                cache_ttl=self.settings.alpha_cache_ttl_seconds,
            )
            data["EARNINGS_CALENDAR"] = list(csv.DictReader(StringIO(csv_text)))
            return self._success(
                request,
                output={"provider": "alpha_vantage", "symbol": symbol, "earnings": data},
                raw=data,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"alpha_vantage:earnings:{symbol}",
                title=f"Alpha Vantage 盈利事件 - {symbol}",
                summary="已检索 Alpha Vantage 盈利历史、预期与日历数据。",
                citation_scope="alpha_earnings_events",
                confidence=0.74,
                metadata={"symbol": symbol},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
