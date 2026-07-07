"""Polymarket provider tools."""

from __future__ import annotations

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import BaseRealToolClient, _input_str
from doxagent.tools.schema import ToolRequest, ToolResult


class PolymarketMarketProbabilityClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            market_id = _input_str(request, "market_id", "")
            slug = _input_str(request, "market_slug", "")
            query = _input_str(request, "query", "fed rate cut")
            if market_id or slug:
                endpoint = "markets"
                params: dict[str, object] = {"id": market_id} if market_id else {"slug": slug}
            else:
                endpoint = "markets"
                params = {"search": query, "limit": int(request.input.get("limit", 10))}
            raw = self._get_json(
                self.settings.polymarket_gamma_base_url.rstrip("/") + f"/{endpoint}",
                params=params,
                cache_ttl=self.settings.polymarket_cache_ttl_seconds,
            )
            return self._success(
                request,
                output={
                    "provider": "polymarket",
                    "query": query,
                    "market_id": market_id,
                    "market_slug": slug,
                    "data": raw,
                    "label": "prediction_market_implied",
                },
                raw=raw,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"polymarket:{market_id or slug or query}",
                title="Polymarket 市场隐含概率",
                summary="已检索 Polymarket 公开市场数据。",
                citation_scope="polymarket_market_probability",
                confidence=0.55,
                metadata={"query": query, "read_only": True},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
