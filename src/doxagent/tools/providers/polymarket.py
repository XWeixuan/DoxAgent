"""Polymarket provider tools."""

from __future__ import annotations

import json
from typing import Any

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
                endpoint = "public-search"
                params = {
                    "q": query,
                    "limit_per_type": limit,
                    "events_status": "active",
                    "search_profiles": False,
                }
            raw = self._get_json(
                self.settings.polymarket_gamma_base_url.rstrip("/") + f"/{endpoint}",
                params=params,
                cache_ttl=self.settings.polymarket_cache_ttl_seconds,
            )
            markets = _compact_markets(raw, limit=limit)
            if not markets:
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
                    "markets": markets,
                    "probability_notice": (
                        "outcomes and outcome_probabilities are positionally paired; values are "
                        "market prices, not verified real-world probabilities"
                    ),
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


def _compact_markets(raw: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    candidates: list[tuple[dict[str, Any], str | None]] = []
    items = raw.get("items")
    if isinstance(items, list):
        candidates.extend((item, None) for item in items if isinstance(item, dict))
    events = raw.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            event_title = str(event.get("title") or "") or None
            event_markets = event.get("markets")
            if isinstance(event_markets, list):
                candidates.extend(
                    (market, event_title)
                    for market in event_markets
                    if isinstance(market, dict)
                )
    rows: list[dict[str, Any]] = []
    for market, event_title in candidates[:limit]:
        outcomes = _json_array(market.get("outcomes"))
        prices = _json_array(market.get("outcomePrices"))
        row = {
            "id": market.get("id"),
            "question": market.get("question"),
            "event_title": event_title,
            "slug": market.get("slug"),
            "end_date": market.get("endDate"),
            "active": market.get("active"),
            "closed": market.get("closed"),
            "liquidity": _number_or_value(market.get("liquidity")),
            "volume": _number_or_value(market.get("volume")),
            "outcomes": outcomes,
            "outcome_probabilities": [
                _number_or_value(value) for value in prices
            ],
            "last_trade_price": _number_or_value(market.get("lastTradePrice")),
            "best_bid": _number_or_value(market.get("bestBid")),
            "best_ask": _number_or_value(market.get("bestAsk")),
        }
        rows.append({key: value for key, value in row.items() if value not in (None, "", [])})
    return rows


def _json_array(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _number_or_value(value: object) -> object:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return value
