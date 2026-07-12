"""Polymarket provider tools."""

from __future__ import annotations

from doxagent.tools.providers.base import BaseRealToolClient, _input_str
from doxagent.tools.schema import ToolRequest, ToolResult


class PolymarketMarketProbabilityClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            market_id = _input_str(request, "market_id", "")
            slug = _input_str(request, "market_slug", _input_str(request, "slug", ""))
            query = _input_str(request, "query", "fed rate cut")
            limit = _bounded_int(request.input.get("limit", 10), 1, 50)
            if market_id or slug:
                endpoint = "markets"
                params: dict[str, object] = {"id": market_id} if market_id else {"slug": slug}
            else:
                endpoint = "markets"
                params = {"search": query, "limit": limit}
            raw = self._get_json(
                self.settings.polymarket_gamma_base_url.rstrip("/") + f"/{endpoint}",
                params=params,
                cache_ttl=self.settings.polymarket_cache_ttl_seconds,
            )
            items = raw.get("items")
            if not isinstance(items, list) or not items:
                return self._failure(
                    request,
                    code="empty_result",
                    message="Polymarket returned no matching markets.",
                    details={"query": query, "market_id": market_id, "market_slug": slug},
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
                source_kind="external_report",
                source_id=f"polymarket:{market_id or slug or query}",
                title="Polymarket 市场隐含概率",
                summary="已检索 Polymarket 公开市场数据。",
                source_scope="polymarket_market_probability",
                confidence=0.55,
                metadata={"query": query, "limit": limit, "read_only": True},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))
