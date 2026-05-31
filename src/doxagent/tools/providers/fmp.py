"""Financial Modeling Prep provider tools."""

from __future__ import annotations

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import BaseRealToolClient, _input_str, _require
from doxagent.tools.schema import ToolRequest, ToolResult


class FmpPressReleasesClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fmp_api_key, "FMP_API_KEY")
            symbol = _input_str(request, "symbol", request.ticker).upper()
            raw = self._get_json(
                self.settings.fmp_base_url.rstrip("/") + "/stable/news/press-releases",
                params={
                    "symbols": symbol,
                    "limit": int(request.input.get("limit", 20)),
                    "apikey": api_key,
                },
                cache_ttl=self.settings.fmp_cache_ttl_seconds,
            )
            return self._success(
                request,
                output={"provider": "fmp", "symbol": symbol, "press_releases": raw},
                raw=raw,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"fmp:press_releases:{symbol}",
                title=f"FMP press releases for {symbol}",
                summary="FMP press releases were retrieved.",
                citation_scope="fmp_press_releases",
                confidence=0.72,
                metadata={"symbol": symbol},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class FmpSectorPerformanceClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fmp_api_key, "FMP_API_KEY")
            raw = self._get_json(
                self.settings.fmp_base_url.rstrip("/") + "/stable/sector-performance-snapshot",
                params={"apikey": api_key},
                cache_ttl=self.settings.fmp_cache_ttl_seconds,
            )
            return self._success(
                request,
                output={"provider": "fmp", "sector_performance": raw},
                raw=raw,
                source_type=EvidenceSourceType.MARKET_DATA,
                source_id="fmp:sector_performance",
                title="FMP sector performance snapshot",
                summary="FMP sector performance snapshot was retrieved.",
                citation_scope="fmp_sector_performance",
                confidence=0.7,
                metadata={},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
