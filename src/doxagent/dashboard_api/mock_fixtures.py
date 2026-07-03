"""Fixture-backed Dashboard State API data for frontend mock mode."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, cast

JsonObject = dict[str, Any]
ENABLED_MONITOR_MODES = {"message_monitoring", "paper_trading"}

MOCK_GENERATED_AT = "2026-06-30T12:00:00Z"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("ticker is required.")
    return normalized


def parse_limit(limit: int | None) -> int:
    if limit is None:
        return 50
    return max(1, min(limit, 200))


def paginate_items(
    items: list[JsonObject],
    *,
    limit: int | None,
    cursor: str | None,
) -> JsonObject:
    resolved_limit = parse_limit(limit)
    offset = _parse_cursor(cursor)
    page_items = items[offset : offset + resolved_limit]
    next_offset = offset + resolved_limit
    has_more = next_offset < len(items)
    return {
        "items": deepcopy(page_items),
        "page": {
            "limit": resolved_limit,
            "next_cursor": f"cur_{next_offset}" if has_more else None,
            "has_more": has_more,
        },
    }


def sort_items(items: list[JsonObject], sort: str | None, *, default: str) -> list[JsonObject]:
    sort_key = sort or default
    reverse = sort_key.startswith("-")
    key = sort_key.removeprefix("-")
    return sorted(items, key=lambda item: str(item.get(key) or ""), reverse=reverse)


def _parse_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    if not cursor.startswith("cur_"):
        return 0
    try:
        return max(0, int(cursor.removeprefix("cur_")))
    except ValueError:
        return 0


class MockDashboardStore:
    """In-memory fixture store for the Dashboard State API mock.

    The store is intentionally detached from runtime_scheduler, monitoring,
    persistent_runtime, Blackboard, and any external database. Mutations only
    adjust this fixture state so frontend flows can be exercised safely.
    """

    def __init__(self) -> None:
        seed = _build_seed()
        self.tickers: dict[str, JsonObject] = seed["tickers"]
        self.ticker_details: dict[str, JsonObject] = seed["ticker_details"]
        self._documents_current: dict[str, JsonObject] = seed["documents_current"]
        self.document_versions: dict[str, dict[str, list[JsonObject]]] = seed["document_versions"]
        self.document_version_details: dict[str, JsonObject] = seed["document_version_details"]
        self.known_events: dict[str, list[JsonObject]] = seed["known_events"]
        self.policies: dict[str, list[JsonObject]] = seed["policies"]
        self.message_bus_overview: dict[str, JsonObject] = seed["message_bus_overview"]
        self.message_bus_messages: dict[str, list[JsonObject]] = seed["message_bus_messages"]
        self.message_bus_config: dict[str, JsonObject] = seed["message_bus_config"]
        self.runtime_overview: dict[str, JsonObject] = seed["runtime_overview"]
        self.runtime_graph: dict[str, JsonObject] = seed["runtime_graph"]
        self.runtime_nodes: dict[str, dict[str, JsonObject]] = seed["runtime_nodes"]
        self.runtime_executions: dict[str, list[JsonObject]] = seed["runtime_executions"]
        self.runtime_execution_details: dict[str, JsonObject] = seed["runtime_execution_details"]
        self.revenue_audit: dict[str, JsonObject] = seed["revenue_audit"]
        self.cost_audit: dict[str, JsonObject] = seed["cost_audit"]
        self.cost_details: dict[str, list[JsonObject]] = seed["cost_details"]
        self.sse_events: list[JsonObject] = seed["sse_events"]

    def overview(self, *, date: str | None = None, tz: str | None = None) -> JsonObject:
        del date, tz
        tickers = self._ticker_cards()
        return {
            "generated_at": MOCK_GENERATED_AT,
            "system": {
                "container_status": "normal",
                "dashboard_api_status": "normal",
                "message_bus_status": "degraded",
                "status_color": "yellow",
            },
            "kpis": {
                "running_ticker_count": sum(
                    1 for item in tickers if item["status"] in {"running", "degraded"}
                ),
                "today_message_count": 143,
                "today_dtc_count": 4,
                "today_token_cost_usd": 3.2842,
                "exception_count": 2,
            },
            "tickers": tickers,
        }

    def list_tickers(
        self,
        *,
        status: str | None,
        health: str | None,
        limit: int | None,
        cursor: str | None,
        sort: str | None,
    ) -> JsonObject:
        items = self._ticker_cards()
        if status:
            items = [item for item in items if item["status"] == status]
        if health:
            items = [item for item in items if item["health"] == health]
        return paginate_items(sort_items(items, sort, default="ticker"), limit=limit, cursor=cursor)

    def get_ticker(self, ticker: str) -> JsonObject | None:
        return deepcopy(self.ticker_details.get(normalize_ticker(ticker)))

    def start_ticker(
        self,
        ticker: str,
        *,
        force_initialize: bool = False,
        monitor_mode: str = "message_monitoring",
    ) -> JsonObject:
        normalized = normalize_ticker(ticker)
        if monitor_mode not in ENABLED_MONITOR_MODES:
            raise ValueError("unsupported_monitor_mode")
        if normalized in self.tickers and self.tickers[normalized]["status"] != "stopped":
            raise ValueError("already_running")
        base = _new_ticker_fixture(normalized, monitor_mode=monitor_mode)
        self.tickers[normalized] = base
        self.ticker_details[normalized] = _new_ticker_detail_fixture(base)
        self._documents_current[normalized] = _empty_documents_fixture(normalized)
        self.document_versions[normalized] = _empty_document_versions_fixture()
        self.known_events[normalized] = []
        self.policies[normalized] = []
        self.message_bus_overview[normalized] = _empty_message_bus_overview(normalized)
        self.message_bus_messages[normalized] = []
        self.message_bus_config[normalized] = _empty_message_bus_config(normalized)
        self.runtime_overview[normalized] = _empty_runtime_overview(normalized)
        self.runtime_graph[normalized] = _default_runtime_graph(empty=True)
        self.runtime_nodes[normalized] = _empty_runtime_nodes()
        self.runtime_executions[normalized] = []
        self.revenue_audit[normalized] = _empty_revenue_audit(normalized)
        self.cost_audit[normalized] = _empty_cost_audit(normalized)
        self.cost_details[normalized] = []
        return {
            "operation": "start",
            "status": "accepted",
            "ticker": normalized,
            "ticker_state": {
                "status": "running",
                "health": "normal",
                "force_initialize": force_initialize,
                "monitor_mode": monitor_mode,
            },
            "audit_id": f"audit_mock_start_{normalized.lower()}",
        }

    def set_monitor_mode(self, ticker: str, *, monitor_mode: str) -> JsonObject | None:
        if monitor_mode not in ENABLED_MONITOR_MODES:
            raise ValueError("unsupported_monitor_mode")
        state = self._ticker_state(ticker)
        if state is None:
            return None
        previous = state.get("monitor_mode", "message_monitoring")
        state["monitor_mode"] = monitor_mode
        state["updated_at"] = utc_now_iso()
        self._sync_ticker_detail_state(state)
        return {
            "operation": "monitor_mode",
            "status": "accepted",
            "ticker": state["ticker"],
            "ticker_state": {
                "status": state["status"],
                "health": state["health"],
                "monitor_mode": monitor_mode,
            },
            "audit_id": f"audit_mock_monitor_mode_{state['ticker'].lower()}",
            "previous_monitor_mode": previous,
        }

    def pause_ticker(self, ticker: str) -> JsonObject | None:
        state = self._ticker_state(ticker)
        if state is None:
            return None
        state["status"] = "paused"
        state["status_label"] = "暂停"
        state["health"] = "normal"
        state["updated_at"] = utc_now_iso()
        self._sync_ticker_detail_state(state)
        return {
            "operation": "pause",
            "status": "accepted",
            "ticker": state["ticker"],
            "ticker_state": {"status": "paused", "health": "normal"},
        }

    def delete_ticker(self, ticker: str, *, delete_history: bool = False) -> JsonObject | None:
        state = self._ticker_state(ticker)
        if state is None:
            return None
        state["status"] = "stopped"
        state["status_label"] = "已停止"
        state["health"] = "normal"
        state["updated_at"] = utc_now_iso()
        self._sync_ticker_detail_state(state)
        config = self.message_bus_config.get(state["ticker"], {"sources": []})
        binding_count = len(config.get("sources", []))
        return {
            "operation": "delete",
            "status": "accepted",
            "ticker": state["ticker"],
            "disabled_binding_count": binding_count,
            "deleted_binding_count": binding_count,
            "history_deleted": bool(delete_history),
        }

    def restart_ticker(self, ticker: str, *, force_initialize: bool = False) -> JsonObject | None:
        state = self._ticker_state(ticker)
        if state is None:
            return None
        state["status"] = "running"
        state["status_label"] = "运行中"
        state["health"] = "normal"
        state["updated_at"] = utc_now_iso()
        self._sync_ticker_detail_state(state)
        return {
            "operation": "restart",
            "status": "accepted",
            "ticker": state["ticker"],
            "ticker_state": {
                "status": "running",
                "health": "normal",
                "force_initialize": force_initialize,
            },
        }

    def documents_current(
        self,
        ticker: str,
        *,
        types: str | None,
        include_raw: bool,
    ) -> JsonObject | None:
        payload = deepcopy(self._documents_current.get(normalize_ticker(ticker)))
        if payload is None:
            return None
        requested_types = _split_csv(types)
        if requested_types:
            payload["documents"] = [
                item for item in payload["documents"] if item["document_type"] in requested_types
            ]
        if not include_raw:
            for document in payload["documents"]:
                document.pop("raw", None)
        return payload

    def versions(
        self,
        ticker: str,
        document_type: str,
        *,
        limit: int | None,
        cursor: str | None,
    ) -> JsonObject | None:
        ticker_versions = self.document_versions.get(normalize_ticker(ticker))
        if ticker_versions is None:
            return None
        items = ticker_versions.get(document_type)
        if items is None:
            return None
        return paginate_items(items, limit=limit, cursor=cursor)

    def version_detail(
        self,
        ticker: str,
        document_type: str,
        version_id: str,
    ) -> JsonObject | None:
        del ticker, document_type
        return deepcopy(self.document_version_details.get(version_id))

    def list_known_events(
        self,
        ticker: str,
        *,
        expectation_id: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> JsonObject | None:
        items = deepcopy(self.known_events.get(normalize_ticker(ticker)))
        if items is None:
            return None
        if expectation_id:
            items = [
                item
                for item in items
                if expectation_id in item.get("related_expectation_ids", [])
            ]
        items = sort_items(items, "-updated_at", default="-updated_at")
        return paginate_items(items, limit=limit, cursor=cursor)

    def list_policies(
        self,
        ticker: str,
        *,
        action_type: str | None,
        expectation_id: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> JsonObject | None:
        items = deepcopy(self.policies.get(normalize_ticker(ticker)))
        if items is None:
            return None
        if action_type:
            items = [item for item in items if item["action_type"] == action_type]
        if expectation_id:
            items = [item for item in items if item.get("expectation_id") == expectation_id]
        items = sort_items(items, "-updated_at", default="-updated_at")
        return paginate_items(items, limit=limit, cursor=cursor)

    def get_message_bus_overview(self, ticker: str) -> JsonObject | None:
        return deepcopy(self.message_bus_overview.get(normalize_ticker(ticker)))

    def list_messages(
        self,
        ticker: str,
        *,
        source_id: str | None,
        processing_status: str | None,
        q: str | None,
        from_time: str | None,
        to_time: str | None,
        limit: int | None,
        cursor: str | None,
        sort: str | None,
    ) -> JsonObject | None:
        items = deepcopy(self.message_bus_messages.get(normalize_ticker(ticker)))
        if items is None:
            return None
        if source_id:
            items = [item for item in items if item["source_id"] == source_id]
        if processing_status:
            items = [
                item for item in items if item["processing_status"] == processing_status
            ]
        if q:
            lowered = q.lower()
            items = [
                item
                for item in items
                if lowered in str(item.get("title") or "").lower()
                or lowered in str(item.get("summary") or "").lower()
                or lowered in str(item.get("body") or "").lower()
            ]
        items = _filter_time(items, "collected_at", from_time=from_time, to_time=to_time)
        items = sort_items(items, sort, default="-collected_at")
        return paginate_items(items, limit=limit, cursor=cursor)

    def get_message_bus_config(self, ticker: str) -> JsonObject | None:
        return deepcopy(self.message_bus_config.get(normalize_ticker(ticker)))

    def patch_source_config(
        self,
        ticker: str,
        source_id: str,
        payload: JsonObject,
    ) -> JsonObject | None:
        config = self.message_bus_config.get(normalize_ticker(ticker))
        if config is None:
            return None
        source = _find_source(config, source_id)
        if source is None:
            return None
        if "enabled" in payload:
            source["enabled"] = bool(payload["enabled"])
            source.setdefault("binding", {})["enabled"] = bool(payload["enabled"])
            if not payload["enabled"]:
                source.setdefault("poll_state", {})["status"] = "disabled"
            elif source.setdefault("poll_state", {}).get("status") == "disabled":
                source.setdefault("poll_state", {})["status"] = "never_polled"
        binding = source.setdefault("binding", {})
        parameters = binding.setdefault("parameters", {})
        for key in ("keywords", "usernames", "search_terms", "rss_urls", "source_filters"):
            if key in payload:
                parameters[key] = list(payload[key] or [])
        return {
            "ticker": normalize_ticker(ticker),
            "source_id": source_id,
            "binding": deepcopy(binding),
            "config": {
                "enabled": source.get("enabled"),
                "display_name": source.get("display_name"),
                "poll_interval_seconds": source.get("poll_interval_seconds"),
            },
        }

    def delete_source_config(self, ticker: str, source_id: str) -> JsonObject | None:
        config = self.message_bus_config.get(normalize_ticker(ticker))
        if config is None:
            return None
        source = _find_source(config, source_id)
        if source is None:
            return None
        source["enabled"] = False
        source.setdefault("binding", {})["enabled"] = False
        source.setdefault("poll_state", {})["status"] = "disabled"
        return {"ticker": normalize_ticker(ticker), "source_id": source_id, "removed": True}

    def get_runtime_overview(self, ticker: str) -> JsonObject | None:
        return deepcopy(self.runtime_overview.get(normalize_ticker(ticker)))

    def get_runtime_graph(self, ticker: str) -> JsonObject | None:
        return deepcopy(self.runtime_graph.get(normalize_ticker(ticker)))

    def get_runtime_node(
        self,
        ticker: str,
        node_id: str,
        *,
        limit: int | None,
        cursor: str | None,
    ) -> JsonObject | None:
        nodes = self.runtime_nodes.get(normalize_ticker(ticker))
        if nodes is None:
            return None
        node = deepcopy(nodes.get(node_id))
        if node is None:
            return None
        records = node.pop("recent_records", [])
        page = paginate_items(records, limit=limit, cursor=cursor)
        return {"node": node, "recent_records": page["items"], "page": page["page"]}

    def list_runtime_executions(
        self,
        ticker: str,
        *,
        route: str | None,
        status: str | None,
        source_type: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> JsonObject | None:
        items = deepcopy(self.runtime_executions.get(normalize_ticker(ticker)))
        if items is None:
            return None
        if route:
            items = [item for item in items if item["final_route"] == route]
        if status:
            items = [item for item in items if item["status"] == status]
        if source_type:
            items = [item for item in items if item["source_type"] == source_type]
        items = sort_items(items, "-created_at", default="-created_at")
        return paginate_items(items, limit=limit, cursor=cursor)

    def get_runtime_execution(self, ticker: str, execution_id: str) -> JsonObject | None:
        normalized = normalize_ticker(ticker)
        detail = self.runtime_execution_details.get(execution_id)
        if detail is None or detail.get("source_message", {}).get("ticker") != normalized:
            return None
        return deepcopy(detail)

    def get_revenue_audit(self, ticker: str, *, period: str | None) -> JsonObject | None:
        payload = deepcopy(self.revenue_audit.get(normalize_ticker(ticker)))
        if payload is None:
            return None
        resolved_period = period or str(payload.get("period") or "today")
        payload["period"] = resolved_period
        trend = _filter_period_items(
            cast(list[JsonObject], payload.get("trend") or []),
            resolved_period,
            "date",
        )
        trade_intents = _filter_period_items(
            cast(list[JsonObject], payload.get("trade_intents") or []),
            resolved_period,
            "time",
        )
        payload["trend"] = trend
        payload["trade_intents"] = trade_intents
        _apply_revenue_period_kpis(payload, trend, trade_intents, resolved_period)
        return payload

    def run_revenue_audit(self, ticker: str, *, date: str | None) -> JsonObject | None:
        if normalize_ticker(ticker) not in self.tickers:
            return None
        return {
            "audit_run_id": f"rev_audit_mock_{normalize_ticker(ticker).lower()}",
            "ticker": normalize_ticker(ticker),
            "date": date or "2026-06-30",
            "status": "calculating",
        }

    def get_cost_audit(
        self,
        ticker: str,
        *,
        period: str | None,
        group_by: str | None,
    ) -> JsonObject | None:
        normalized = normalize_ticker(ticker)
        payload = deepcopy(self.cost_audit.get(normalized))
        if payload is None:
            return None
        resolved_period = period or str(payload.get("period") or "today")
        payload["period"] = resolved_period
        if group_by:
            payload["group_by"] = group_by
        trend = _filter_period_items(
            cast(list[JsonObject], payload.get("trend") or []),
            resolved_period,
            "date",
        )
        detail_items = _filter_period_items(
            deepcopy(self.cost_details.get(normalized) or []),
            resolved_period,
            "time",
        )
        if not trend and detail_items:
            trend = _cost_trend_from_records(detail_items)
        payload["trend"] = trend
        _apply_cost_period_payload(payload, trend, detail_items)
        return payload

    def list_cost_details(
        self,
        ticker: str,
        *,
        node: str | None,
        model: str | None,
        status: str | None,
        from_time: str | None,
        to_time: str | None,
        limit: int | None,
        cursor: str | None,
    ) -> JsonObject | None:
        items = deepcopy(self.cost_details.get(normalize_ticker(ticker)))
        if items is None:
            return None
        if node:
            items = [item for item in items if item["node"].lower() == node.lower()]
        if model:
            items = [item for item in items if item["model"] == model]
        if status:
            items = [item for item in items if item["status"] == status]
        items = _filter_time(items, "time", from_time=from_time, to_time=to_time)
        items = sort_items(items, "-time", default="-time")
        return paginate_items(items, limit=limit, cursor=cursor)

    def filtered_sse_events(
        self,
        *,
        ticker: str | None,
        event_types: str | None,
        last_event_id: str | None,
    ) -> list[JsonObject]:
        events = deepcopy(self.sse_events)
        if ticker:
            normalized = normalize_ticker(ticker)
            events = [event for event in events if event["ticker"] == normalized]
        requested_types = _split_csv(event_types)
        if requested_types:
            events = [event for event in events if event["event_type"] in requested_types]
        if last_event_id:
            seen = False
            after_last: list[JsonObject] = []
            for event in events:
                if seen:
                    after_last.append(event)
                if event["event_id"] == last_event_id:
                    seen = True
            events = after_last if seen else events
        return events

    def _ticker_cards(self) -> list[JsonObject]:
        return [deepcopy(self.tickers[key]) for key in sorted(self.tickers)]

    def _ticker_state(self, ticker: str) -> JsonObject | None:
        return self.tickers.get(normalize_ticker(ticker))

    def _sync_ticker_detail_state(self, state: JsonObject) -> None:
        detail = self.ticker_details.get(state["ticker"])
        if detail is None:
            return
        detail_state = detail.setdefault("state", {})
        detail_state["status"] = state["status"]
        detail_state["health"] = state["health"]
        detail_state["monitor_mode"] = state.get("monitor_mode", "message_monitoring")
        detail_state["last_error"] = state.get("last_error")


def _split_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def _filter_time(
    items: list[JsonObject],
    key: str,
    *,
    from_time: str | None,
    to_time: str | None,
) -> list[JsonObject]:
    if from_time:
        items = [item for item in items if str(item.get(key) or "") >= from_time]
    if to_time:
        items = [item for item in items if str(item.get(key) or "") <= to_time]
    return items


def _filter_period_items(items: list[JsonObject], period: str | None, key: str) -> list[JsonObject]:
    if not items:
        return []
    dates = [_item_date(item, key) for item in items]
    known_dates = [date for date in dates if date is not None]
    if not known_dates:
        return items
    latest = max(known_dates)
    start = latest - timedelta(days=_period_day_count(period) - 1)
    return [
        item
        for item, item_date in zip(items, dates, strict=False)
        if item_date is not None and start <= item_date <= latest
    ]


def _period_day_count(period: str | None) -> int:
    if period == "30d":
        return 30
    if period == "7d":
        return 7
    return 1


def _item_date(item: JsonObject, key: str):
    raw = str(item.get(key) or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.fromisoformat(raw[:10]).date()
        except ValueError:
            return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _sum_optional_numbers(items: list[JsonObject], key: str) -> float | None:
    values = [_number_or_none(item.get(key)) for item in items]
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return round(sum(numbers), 4)


def _apply_revenue_period_kpis(
    payload: JsonObject,
    trend: list[JsonObject],
    trade_intents: list[JsonObject],
    period: str,
) -> None:
    kpis = cast(JsonObject, payload.get("kpis") or {})
    trend_trade_count = sum(
        int(item.get("trade_intent_count") or 0)
        for item in trend
        if isinstance(item.get("trade_intent_count"), int)
    )
    pnl_sum = _sum_optional_numbers(trend, "pnl_usd")
    audited_trades = [
        item
        for item in trade_intents
        if str(item.get("status") or "").lower() in {"audited", "completed"}
    ]
    kpis["today_trade_intent_count"] = trend_trade_count or len(trade_intents)
    kpis["audited_trade_count"] = len(audited_trades)
    kpis["today_pnl_usd"] = pnl_sum
    if period == "today":
        kpis["today_return_pct"] = kpis.get("today_return_pct")
    else:
        kpis["today_return_pct"] = round((pnl_sum or 0) / 10000 * 100, 2) if pnl_sum is not None else None
    kpis["win_rate"] = (
        len([item for item in audited_trades if _number_or_none(item.get("pnl_usd")) and float(item["pnl_usd"]) >= 0])
        / len(audited_trades)
        if audited_trades
        else None
    )
    payload["kpis"] = kpis


def _apply_cost_period_payload(
    payload: JsonObject,
    trend: list[JsonObject],
    detail_items: list[JsonObject],
) -> None:
    kpis = cast(JsonObject, payload.get("kpis") or {})
    trend_cost = _sum_optional_numbers(trend, "total_cost_usd")
    trend_tokens = _sum_optional_numbers(trend, "total_tokens")
    input_tokens = _sum_optional_numbers(detail_items, "input_tokens")
    output_tokens = _sum_optional_numbers(detail_items, "output_tokens")
    total_tokens = trend_tokens or _sum_optional_numbers(detail_items, "total_tokens")
    total_cost = trend_cost or _sum_optional_numbers(detail_items, "cost_usd")
    retry_items = [item for item in detail_items if item.get("is_retry")]
    by_node = _cost_breakdown_from_records(detail_items, "node")
    by_model = _cost_breakdown_from_records(detail_items, "model")
    highest_node = max(by_node, key=lambda item: float(item.get("cost_usd") or 0), default=None)
    kpis["today_input_tokens"] = int(input_tokens) if input_tokens is not None else None
    kpis["today_output_tokens"] = int(output_tokens) if output_tokens is not None else None
    kpis["today_total_tokens"] = int(total_tokens) if total_tokens is not None else None
    kpis["today_total_cost_usd"] = total_cost
    kpis["highest_cost_node"] = highest_node["label"] if highest_node else None
    kpis["retry_cost_usd"] = _sum_optional_numbers(retry_items, "cost_usd")
    payload["kpis"] = kpis
    payload["breakdown"] = {
        "by_node": by_node or cast(JsonObject, payload.get("breakdown") or {}).get("by_node", []),
        "by_model": by_model or cast(JsonObject, payload.get("breakdown") or {}).get("by_model", []),
    }


def _cost_breakdown_from_records(items: list[JsonObject], key: str) -> list[JsonObject]:
    buckets: dict[str, JsonObject] = {}
    for item in items:
        bucket_key = str(item.get(key) or "unknown")
        bucket = buckets.setdefault(
            bucket_key,
            {"key": bucket_key, "label": bucket_key, "cost_usd": 0.0, "total_tokens": 0},
        )
        bucket["cost_usd"] = round(float(bucket["cost_usd"]) + float(item.get("cost_usd") or 0), 4)
        bucket["total_tokens"] = int(bucket["total_tokens"]) + int(item.get("total_tokens") or 0)
    return sorted(buckets.values(), key=lambda item: float(item.get("cost_usd") or 0), reverse=True)


def _cost_trend_from_records(items: list[JsonObject]) -> list[JsonObject]:
    buckets: dict[str, JsonObject] = {}
    for item in items:
        item_date = _item_date(item, "time")
        if item_date is None:
            continue
        date_key = item_date.isoformat()
        bucket = buckets.setdefault(date_key, {"date": date_key, "total_cost_usd": 0.0, "total_tokens": 0})
        bucket["total_cost_usd"] = round(float(bucket["total_cost_usd"]) + float(item.get("cost_usd") or 0), 4)
        bucket["total_tokens"] = int(bucket["total_tokens"]) + int(item.get("total_tokens") or 0)
    return [buckets[key] for key in sorted(buckets)]


def _find_source(config: JsonObject, source_id: str) -> JsonObject | None:
    sources = config.get("sources", [])
    if not isinstance(sources, list):
        return None
    for source in sources:
        if isinstance(source, dict):
            source_payload = cast(JsonObject, source)
            if source_payload.get("source_id") == source_id:
                return source_payload
    return None


def _build_seed() -> JsonObject:
    tickers: dict[str, JsonObject] = {
        "ASTS": {
            "ticker": "ASTS",
            "status": "degraded",
            "status_label": "异常",
            "health": "degraded",
            "session_phase": "formal_monitoring",
            "monitor_mode": "message_monitoring",
            "started_at": "2026-06-30T11:20:00Z",
            "updated_at": "2026-06-30T12:04:40Z",
            "last_message_at": "2026-06-30T11:54:00Z",
            "last_worker_processed_at": "2026-06-30T11:58:20Z",
            "today_dtc_count": 2,
            "today_cost_usd": 1.3812,
            "last_error": "TikHub X Search 最近一次轮询超时，其他 source 正常。",
        },
        "EMPTY": {
            "ticker": "EMPTY",
            "status": "stopped",
            "status_label": "已停止",
            "health": "unknown",
            "session_phase": "off_hours_low_frequency",
            "monitor_mode": "message_monitoring",
            "started_at": "2026-06-29T12:00:00Z",
            "updated_at": "2026-06-30T10:00:00Z",
            "last_message_at": None,
            "last_worker_processed_at": None,
            "today_dtc_count": 0,
            "today_cost_usd": None,
            "last_error": None,
        },
        "MU": {
            "ticker": "MU",
            "status": "running",
            "status_label": "运行中",
            "health": "normal",
            "session_phase": "formal_monitoring",
            "monitor_mode": "message_monitoring",
            "started_at": "2026-06-30T11:30:00Z",
            "updated_at": "2026-06-30T12:05:00Z",
            "last_message_at": "2026-06-30T12:04:00Z",
            "last_worker_processed_at": "2026-06-30T12:04:20Z",
            "today_dtc_count": 1,
            "today_cost_usd": 0.7924,
            "last_error": None,
        },
        "NVDA": {
            "ticker": "NVDA",
            "status": "initializing",
            "status_label": "初始化中",
            "health": "unknown",
            "session_phase": "formal_monitoring",
            "monitor_mode": "message_monitoring",
            "started_at": "2026-06-30T12:03:00Z",
            "updated_at": "2026-06-30T12:05:00Z",
            "last_message_at": None,
            "last_worker_processed_at": None,
            "today_dtc_count": 0,
            "today_cost_usd": 0.2141,
            "last_error": None,
        },
    }
    return {
        "tickers": deepcopy(tickers),
        "ticker_details": _build_ticker_details(tickers),
        "documents_current": _build_documents_current(),
        "document_versions": _build_document_versions(),
        "document_version_details": _build_document_version_details(),
        "known_events": _build_known_events(),
        "policies": _build_policies(),
        "message_bus_overview": _build_message_bus_overview(),
        "message_bus_messages": _build_message_bus_messages(),
        "message_bus_config": _build_message_bus_config(),
        "runtime_overview": _build_runtime_overview(),
        "runtime_graph": _build_runtime_graph(),
        "runtime_nodes": _build_runtime_nodes(),
        "runtime_executions": _build_runtime_executions(),
        "runtime_execution_details": _build_runtime_execution_details(),
        "revenue_audit": _build_revenue_audit(),
        "cost_audit": _build_cost_audit(),
        "cost_details": _build_cost_details(),
        "sse_events": _build_sse_events(),
    }


def _build_ticker_details(tickers: dict[str, JsonObject]) -> dict[str, JsonObject]:
    details: dict[str, JsonObject] = {}
    for ticker, state in tickers.items():
        details[ticker] = {
            "ticker": ticker,
            "state": {
                "status": state["status"],
                "health": state["health"],
                "session_phase": state["session_phase"],
                "monitor_mode": state.get("monitor_mode", "message_monitoring"),
                "document_run_id": (
                    None if ticker == "EMPTY" else f"run_mock_{ticker.lower()}_current"
                ),
                "last_error": state.get("last_error"),
            },
            "document_status": {
                "usable": ticker != "EMPTY",
                "stale": ticker == "ASTS",
                "availability": "missing" if ticker == "EMPTY" else "available",
                "document_types": ["document1", "document2", "document3"],
            },
            "message_bus_status": {
                "pending_event_count": 0 if ticker == "EMPTY" else (4 if ticker == "ASTS" else 1),
                "recent_message_count": 0 if ticker == "EMPTY" else 18,
                "last_error_message": state.get("last_error"),
            },
            "runtime_status": {
                "queue_message_count": 0 if ticker == "EMPTY" else (6 if ticker == "ASTS" else 2),
                "failed_task_count": 0 if ticker != "ASTS" else 2,
                "last_execution_at": state.get("last_worker_processed_at"),
            },
            "audit_summary": {
                "today_dtc_count": state["today_dtc_count"],
                "today_revenue_audit_status": "completed" if ticker == "MU" else "not_started",
                "today_cost_audit_status": "completed" if ticker == "MU" else "missing",
            },
        }
    return details


def _build_documents_current() -> dict[str, JsonObject]:
    return {
        "MU": _documents_fixture("MU", availability="available"),
        "ASTS": _documents_fixture("ASTS", availability="stale"),
        "NVDA": _documents_fixture("NVDA", availability="available", initializing=True),
        "EMPTY": _empty_documents_fixture("EMPTY"),
    }


def _documents_fixture(
    ticker: str,
    *,
    availability: str,
    initializing: bool = False,
) -> JsonObject:
    run_id = f"run_mock_{ticker.lower()}_current"
    return {
        "ticker": ticker,
        "document_run_id": run_id,
        "documents": [
            {
                "document_type": "document1",
                "document_type_label": "Document 1：Global Research",
                "document_id": f"doc_global_research_{ticker.lower()}_v3",
                "generated_at": "2026-06-30T09:30:00Z",
                "updated_at": "2026-06-30T10:10:00Z",
                "version_status": "current",
                "availability": "missing" if initializing else availability,
                "cards": [
                    {
                        "card_id": "fundamental_report",
                        "title": "基本面研究",
                        "updated_at": "2026-06-30T10:10:00Z",
                        "summary": f"{ticker} 收入、毛利率、资本开支和库存周期摘要。",
                        "fields": [
                            {
                                "key": "revenue",
                                "label": "收入趋势",
                                "value": (
                                    "最近季度收入环比改善，管理层指引显示下半年需求更集中于 "
                                    "AI/高性能计算链条。"
                                ),
                            },
                            {
                                "key": "risk",
                                "label": "主要风险",
                                "value": "估值扩张、库存修正和宏观利率变化可能放大波动。",
                            },
                        ],
                    },
                    {
                        "card_id": "market_view",
                        "title": "市场观点",
                        "updated_at": "2026-06-30T10:12:00Z",
                        "summary": "消息面偏多，但需等待成交量确认。",
                        "fields": [
                            {
                                "key": "sentiment",
                                "label": "情绪摘要",
                                "value": "社媒热度升高，新闻源以财报和分析师上修为主。",
                            }
                        ],
                    },
                ],
                "raw": {"mock": True, "document_family": "global_research"},
            },
            {
                "document_type": "document2",
                "document_type_label": "Document 2：Expectation Units",
                "document_id": f"doc_expectation_units_{ticker.lower()}_v5",
                "generated_at": "2026-06-30T10:20:00Z",
                "updated_at": "2026-06-30T10:45:00Z",
                "version_status": "current",
                "availability": availability,
                "cards": [
                    {
                        "card_id": "eu_revenue_acceleration",
                        "title": "AI 需求拉动收入加速",
                        "updated_at": "2026-06-30T10:45:00Z",
                        "summary": "若高毛利产品出货继续超预期，市场可能重估未来两个季度 EPS。",
                        "fields": [
                            {
                                "key": "condition",
                                "label": "触发条件",
                                "value": "公司或主要客户确认订单/出货加速，且价格反应未完全兑现。",
                            },
                            {
                                "key": "contradiction",
                                "label": "失效信号",
                                "value": "库存重新积压或管理层下调出货指引。",
                            },
                        ],
                    }
                ],
                "raw": {"mock": True, "document_family": "expectation_units"},
            },
            {
                "document_type": "document3",
                "document_type_label": "Document 3：Runtime Strategy",
                "document_id": f"doc_runtime_strategy_{ticker.lower()}_v2",
                "generated_at": "2026-06-30T10:50:00Z",
                "updated_at": "2026-06-30T11:00:00Z",
                "version_status": "current",
                "availability": availability,
                "cards": [
                    {
                        "card_id": "known_events_and_policy",
                        "title": "Known Events 与执行策略摘要",
                        "updated_at": "2026-06-30T11:00:00Z",
                        "summary": "包含重复事件识别、DTC/EBA 触发策略和忽略条件。",
                        "fields": [
                            {
                                "key": "known_events_count",
                                "label": "Known Events 数量",
                                "value": 2,
                            },
                            {
                                "key": "policy_count",
                                "label": "Policy 数量",
                                "value": 4,
                            },
                        ],
                    }
                ],
                "raw": {"mock": True, "document_family": "runtime_strategy"},
            },
        ],
    }


def _empty_documents_fixture(ticker: str) -> JsonObject:
    return {
        "ticker": ticker,
        "document_run_id": None,
        "documents": [
            {
                "document_type": document_type,
                "document_type_label": label,
                "document_id": None,
                "generated_at": None,
                "updated_at": None,
                "version_status": "current",
                "availability": "missing",
                "cards": [],
            }
            for document_type, label in (
                ("document1", "Document 1：Global Research"),
                ("document2", "Document 2：Expectation Units"),
                ("document3", "Document 3：Runtime Strategy"),
            )
        ],
    }


def _build_document_versions() -> dict[str, dict[str, list[JsonObject]]]:
    versions: dict[str, dict[str, list[JsonObject]]] = {}
    for ticker in ("MU", "ASTS", "NVDA"):
        versions[ticker] = {
            "document1": _version_list(ticker, "document1"),
            "document2": _version_list(ticker, "document2"),
            "document3": _version_list(ticker, "document3"),
        }
    versions["EMPTY"] = _empty_document_versions_fixture()
    return versions


def _empty_document_versions_fixture() -> dict[str, list[JsonObject]]:
    return {"document1": [], "document2": [], "document3": []}


def _version_list(ticker: str, document_type: str) -> list[JsonObject]:
    return [
        {
            "version_id": f"{ticker.lower()}_{document_type}_v3",
            "document_id": f"doc_{document_type}_{ticker.lower()}_v3",
            "document_type": document_type,
            "generated_at": "2026-06-30T10:00:00Z",
            "updated_at": "2026-06-30T10:30:00Z",
            "version_status": "current",
            "summary": "当前前端 mock 版本。",
        },
        {
            "version_id": f"{ticker.lower()}_{document_type}_v2",
            "document_id": f"doc_{document_type}_{ticker.lower()}_v2",
            "document_type": document_type,
            "generated_at": "2026-06-28T10:00:00Z",
            "updated_at": "2026-06-28T10:30:00Z",
            "version_status": "historical",
            "summary": "历史版本，用于侧边栏切换。",
        },
    ]


def _build_document_version_details() -> dict[str, JsonObject]:
    details: dict[str, JsonObject] = {}
    for ticker in ("MU", "ASTS", "NVDA"):
        for document_type in ("document1", "document2", "document3"):
            for version in _version_list(ticker, document_type):
                details[version["version_id"]] = {
                    "ticker": ticker,
                    "version": version,
                    "document": {
                        **_documents_fixture(ticker, availability="available")["documents"][
                            {"document1": 0, "document2": 1, "document3": 2}[document_type]
                        ],
                        "version_status": version["version_status"],
                        "document_id": version["document_id"],
                    },
                }
    return details


def _build_known_events() -> dict[str, list[JsonObject]]:
    events = [
        {
            "event_id": "ke_mu_hbm_pricing",
            "event_name": "HBM 价格与订单更新",
            "event_time_or_window": "2026Q2-2026Q3",
            "description": "市场已多次讨论 HBM 价格上修，重复消息需去重，新增客户确认才算新事件。",
            "related_expectation_ids": ["EU_REVENUE_ACCELERATION"],
            "duplicate_detection_keys": ["HBM pricing", "customer allocation", "AI memory"],
            "source": "document3",
            "updated_at": "2026-06-30T10:55:00Z",
        },
        {
            "event_id": "ke_mu_earnings_setup",
            "event_name": "财报前预期抬升",
            "event_time_or_window": "2026-06",
            "description": "分析师上调预期已经进入价格，需要区分重复观点和新增数字。",
            "related_expectation_ids": ["EU_MARGIN_UPSIDE"],
            "duplicate_detection_keys": ["earnings setup", "estimate revision"],
            "source": "runtime_patch",
            "updated_at": "2026-06-30T11:25:00Z",
        },
    ]
    return {"MU": events, "ASTS": deepcopy(events[:1]), "NVDA": [], "EMPTY": []}


def _build_policies() -> dict[str, list[JsonObject]]:
    policies: list[JsonObject] = [
        {
            "policy_id": "POLICY_DTC_HBM_ORDER",
            "expectation_id": "EU_REVENUE_ACCELERATION",
            "action_type": "DTC",
            "title": "新增客户订单确认触发 DTC",
            "trigger_condition": (
                "若可靠来源确认新增 HBM 订单、出货加速或价格上修，"
                "且不是 Known Event recap，生成 Direct Trade Candidate。"
            ),
            "severity": "high",
            "updated_at": "2026-06-30T11:00:00Z",
        },
        {
            "policy_id": "POLICY_EBA_SUPPLY_CHAIN",
            "expectation_id": "EU_SUPPLY_CHAIN",
            "action_type": "EBA",
            "title": "供应链模糊消息委托背景 agent",
            "trigger_condition": "当消息涉及供应链但证据不足，推送给 O1/A2 进一步查证。",
            "severity": "medium",
            "updated_at": "2026-06-30T10:58:00Z",
        },
        {
            "policy_id": "POLICY_NULL_RECAP",
            "expectation_id": "EU_REVENUE_ACCELERATION",
            "action_type": "NULL",
            "title": "已知事件复述不触发交易",
            "trigger_condition": "若仅复述已记录的价格/订单观点，不进入交易记录。",
            "severity": "low",
            "updated_at": "2026-06-30T10:54:00Z",
        },
        {
            "policy_id": "POLICY_IRRELEVANT_MACRO",
            "expectation_id": None,
            "action_type": "Irrelevant",
            "title": "泛宏观噪音忽略",
            "trigger_condition": "无 ticker-specific 影响的宏观泛论直接忽略。",
            "severity": "low",
            "updated_at": "2026-06-30T10:50:00Z",
        },
    ]
    return {"MU": policies, "ASTS": deepcopy(policies[:2]), "NVDA": [], "EMPTY": []}


def _build_message_bus_overview() -> dict[str, JsonObject]:
    return {
        "MU": {
            "ticker": "MU",
            "uptime_seconds": 3600,
            "today_raw_message_count": 80,
            "today_event_count": 42,
            "media_enrichment_success_rate": 0.72,
            "healthy_channel_count": 5,
            "total_channel_count": 6,
            "last_error_message": None,
        },
        "ASTS": {
            "ticker": "ASTS",
            "uptime_seconds": 2800,
            "today_raw_message_count": 43,
            "today_event_count": 20,
            "media_enrichment_success_rate": 0.45,
            "healthy_channel_count": 4,
            "total_channel_count": 6,
            "last_error_message": "tikhub_x_search timeout",
        },
        "NVDA": _empty_message_bus_overview("NVDA") | {"uptime_seconds": 120},
        "EMPTY": _empty_message_bus_overview("EMPTY"),
    }


def _empty_message_bus_overview(ticker: str) -> JsonObject:
    return {
        "ticker": ticker,
        "uptime_seconds": 0,
        "today_raw_message_count": 0,
        "today_event_count": 0,
        "media_enrichment_success_rate": None,
        "healthy_channel_count": 0,
        "total_channel_count": 0,
        "last_error_message": None,
    }


def _build_message_bus_messages() -> dict[str, list[JsonObject]]:
    mu_messages = [
        {
            "message_id": "std_mu_001",
            "raw_message_id": "raw_mu_001",
            "ticker": "MU",
            "source_id": "benzinga_news",
            "source_label": "Benzinga",
            "source_type": "media",
            "collected_at": "2026-06-30T12:04:00Z",
            "published_at": "2026-06-30T12:02:00Z",
            "title": "Micron shares rise as AI memory demand stays firm",
            "summary": "分析师称 HBM 需求继续支撑下半年收入。",
            "body": "完整正文：该新闻为 mock 数据，用于前端展开消息详情、原文链接和处理状态展示。",
            "url": "https://example.com/mu-ai-memory",
            "processing_status": "routed_to_trading_records",
            "runtime_execution_id": "pre_mu_001",
        },
        {
            "message_id": "std_mu_002",
            "raw_message_id": "raw_mu_002",
            "ticker": "MU",
            "source_id": "stocktwits_messages",
            "source_label": "Stocktwits",
            "source_type": "social",
            "collected_at": "2026-06-30T12:01:00Z",
            "published_at": "2026-06-30T12:00:20Z",
            "title": "MU traders discuss earnings setup",
            "summary": "社媒讨论财报前预期，但大多属于已知观点复述。",
            "body": "Stocktwits mock body with ticker chatter and sentiment tags.",
            "url": "https://stocktwits.com/symbol/MU",
            "processing_status": "routed_to_archive",
            "runtime_execution_id": "pre_mu_002",
        },
        {
            "message_id": "std_mu_003",
            "raw_message_id": "raw_mu_003",
            "ticker": "MU",
            "source_id": "tikhub_x_search",
            "source_label": "X Search",
            "source_type": "social",
            "collected_at": "2026-06-30T11:59:00Z",
            "published_at": "2026-06-30T11:57:00Z",
            "title": "Rumor: new customer allocation",
            "summary": "待验证传闻，进入 A2/O3 处理链路。",
            "body": "Mock social post requiring verification.",
            "url": "https://example.com/x/mu-customer-allocation",
            "processing_status": "o3_running",
            "runtime_execution_id": "pre_mu_003",
        },
    ]
    asts_messages = [
        {
            "message_id": "std_asts_001",
            "raw_message_id": "raw_asts_001",
            "ticker": "ASTS",
            "source_id": "newswire_rss",
            "source_label": "Newswire RSS",
            "source_type": "media",
            "collected_at": "2026-06-30T11:54:00Z",
            "published_at": "2026-06-30T11:50:00Z",
            "title": "AST SpaceMobile launch window update",
            "summary": "发射窗口更新正在处理中。",
            "body": "Mock body for degraded/runtime in-progress state.",
            "url": "https://example.com/asts-launch-window",
            "processing_status": "w2_running",
            "runtime_execution_id": "pre_asts_001",
        }
    ]
    return {"MU": mu_messages, "ASTS": asts_messages, "NVDA": [], "EMPTY": []}


def _message_bus_sources_for(ticker: str) -> list[JsonObject]:
    normalized = ticker.upper()
    lower = normalized.lower()
    return [
        {
            "source_id": "stocktwits_messages",
            "display_name": "Stocktwits Messages API",
            "source_type": "social",
            "interface_type": "by_ticker",
            "enabled": True,
            "poll_interval_seconds": 300,
            "binding": {
                "binding_id": f"bind_{lower}_stocktwits",
                "ticker": normalized,
                "source_id": "stocktwits_messages",
                "enabled": True,
                "parameters": {},
            },
            "poll_state": {
                "status": "succeeded",
                "last_success_at": "2026-06-30T12:00:00Z",
                "last_error_message": None,
                "last_poll_new_message_count": 3,
                "last_latency_ms": 51000,
            },
            "user_only_fields": ["poll_interval_seconds"],
            "agent_mutable_fields": ["enabled"],
        },
        {
            "source_id": "benzinga_news",
            "display_name": "Benzinga News",
            "source_type": "media",
            "interface_type": "by_ticker",
            "enabled": True,
            "poll_interval_seconds": 300,
            "binding": {
                "binding_id": f"bind_{lower}_benzinga",
                "ticker": normalized,
                "source_id": "benzinga_news",
                "enabled": True,
                "parameters": {"search_terms": [f"{normalized} earnings", "AI memory"]},
            },
            "poll_state": {
                "status": "succeeded",
                "last_success_at": "2026-06-30T12:01:00Z",
                "last_error_message": None,
                "last_poll_new_message_count": 0,
                "last_latency_ms": 2200,
            },
            "user_only_fields": ["poll_interval_seconds"],
            "agent_mutable_fields": ["enabled", "search_terms"],
        },
        {
            "source_id": "finnhub_company_news",
            "display_name": "Finnhub Company News API",
            "source_type": "media",
            "interface_type": "by_ticker",
            "enabled": False,
            "poll_interval_seconds": 300,
            "binding": {
                "binding_id": f"bind_{lower}_finnhub",
                "ticker": normalized,
                "source_id": "finnhub_company_news",
                "enabled": False,
                "parameters": {},
            },
            "poll_state": {
                "status": "disabled",
                "last_success_at": None,
                "last_error_message": None,
                "last_poll_new_message_count": 0,
                "last_latency_ms": None,
            },
            "user_only_fields": ["poll_interval_seconds"],
            "agent_mutable_fields": ["enabled"],
        },
        {
            "source_id": "tikhub_x_search",
            "display_name": "TikHub X Search",
            "source_type": "social",
            "interface_type": "by_parameter",
            "enabled": True,
            "poll_interval_seconds": 600,
            "binding": {
                "binding_id": f"bind_{lower}_x_search",
                "ticker": normalized,
                "source_id": "tikhub_x_search",
                "enabled": True,
                "parameters": {"search_terms": [f"{normalized} HBM", "AI memory"]},
            },
            "poll_state": {
                "status": "failed",
                "last_success_at": "2026-06-30T11:30:00Z",
                "last_error_message": "Mock timeout for degraded channel state.",
                "last_poll_new_message_count": 2,
                "last_latency_ms": 8300,
            },
            "user_only_fields": ["poll_interval_seconds"],
            "agent_mutable_fields": ["enabled", "search_terms"],
        },
        {
            "source_id": "tikhub_x_user_posts",
            "display_name": "TikHub X User Posts API",
            "source_type": "social",
            "interface_type": "by_parameter",
            "enabled": False,
            "poll_interval_seconds": 600,
            "binding": {
                "binding_id": f"bind_{lower}_x_users",
                "ticker": normalized,
                "source_id": "tikhub_x_user_posts",
                "enabled": False,
                "parameters": {"usernames": []},
            },
            "poll_state": {
                "status": "disabled",
                "last_success_at": None,
                "last_error_message": None,
                "last_poll_new_message_count": 0,
                "last_latency_ms": None,
            },
            "user_only_fields": ["poll_interval_seconds"],
            "agent_mutable_fields": ["enabled", "usernames"],
        },
        {
            "source_id": "newswire_rss",
            "display_name": "Newswire RSS",
            "source_type": "media",
            "interface_type": "by_parameter",
            "enabled": True,
            "poll_interval_seconds": 300,
            "binding": {
                "binding_id": f"bind_{lower}_newswire_rss",
                "ticker": normalized,
                "source_id": "newswire_rss",
                "enabled": True,
                "parameters": {"rss_urls": [f"https://example.com/{lower}/news.xml"]},
            },
            "poll_state": {
                "status": "succeeded",
                "last_success_at": "2026-06-30T11:54:00Z",
                "last_error_message": None,
                "last_poll_new_message_count": 1,
                "last_latency_ms": 2900,
            },
            "user_only_fields": ["poll_interval_seconds"],
            "agent_mutable_fields": ["enabled", "rss_urls"],
        },
    ]


def _build_message_bus_config() -> dict[str, JsonObject]:
    return {
        "MU": {"ticker": "MU", "sources": _message_bus_sources_for("MU"), "missing_source_ids": []},
        "ASTS": {
            "ticker": "ASTS",
            "sources": _message_bus_sources_for("ASTS"),
            "missing_source_ids": [],
        },
        "NVDA": _empty_message_bus_config("NVDA"),
        "EMPTY": _empty_message_bus_config("EMPTY"),
    }


def _empty_message_bus_config(ticker: str) -> JsonObject:
    return {"ticker": ticker, "sources": [], "missing_source_ids": []}


def _build_runtime_overview() -> dict[str, JsonObject]:
    return {
        "MU": {
            "ticker": "MU",
            "queue_message_count": 8,
            "w1_today_count": 40,
            "w1_avg_latency_ms": 1200,
            "w2_today_count": 40,
            "w2_avg_latency_ms": 1300,
            "o3_today_count": 3,
            "o3_avg_latency_ms": 48000,
            "dtc_today_count": 1,
            "eba_today_count": 2,
            "failed_task_count": 0,
            "avg_processing_latency_ms": 2300,
        },
        "ASTS": {
            "ticker": "ASTS",
            "queue_message_count": 6,
            "w1_today_count": 18,
            "w1_avg_latency_ms": 1500,
            "w2_today_count": 15,
            "w2_avg_latency_ms": 1900,
            "o3_today_count": 2,
            "o3_avg_latency_ms": 76000,
            "dtc_today_count": 2,
            "eba_today_count": 1,
            "failed_task_count": 2,
            "avg_processing_latency_ms": 6100,
        },
        "NVDA": _empty_runtime_overview("NVDA") | {"queue_message_count": 1},
        "EMPTY": _empty_runtime_overview("EMPTY"),
    }


def _empty_runtime_overview(ticker: str) -> JsonObject:
    return {
        "ticker": ticker,
        "queue_message_count": 0,
        "w1_today_count": 0,
        "w1_avg_latency_ms": None,
        "w2_today_count": 0,
        "w2_avg_latency_ms": None,
        "o3_today_count": 0,
        "o3_avg_latency_ms": None,
        "dtc_today_count": 0,
        "eba_today_count": 0,
        "failed_task_count": 0,
        "avg_processing_latency_ms": None,
    }


def _build_runtime_graph() -> dict[str, JsonObject]:
    graph = _default_runtime_graph(empty=False)
    return {
        "MU": deepcopy(graph),
        "ASTS": _degraded_runtime_graph(),
        "NVDA": _default_runtime_graph(empty=True),
        "EMPTY": _default_runtime_graph(empty=True),
    }


def _default_runtime_graph(*, empty: bool) -> JsonObject:
    count = 0 if empty else 42
    joint_count = max(0, count - 2)
    return {
        "nodes": [
            {
                "node_id": "message_bus",
                "label": "Message Bus / 任务池",
                "status": "normal" if count else "unknown",
                "in_count": count,
                "out_count": count,
                "failed_count": 0,
            },
            {
                "node_id": "w1",
                "label": "W1 新旧判定",
                "status": "normal" if count else "unknown",
                "in_count": count,
                "out_count": joint_count,
                "failed_count": 0,
            },
            {
                "node_id": "w2",
                "label": "W2 Policy 判定",
                "status": "normal" if count else "unknown",
                "in_count": count,
                "out_count": joint_count,
                "failed_count": 0,
            },
            {
                "node_id": "route_engine",
                "label": "联合路由",
                "status": "normal" if count else "unknown",
                "in_count": joint_count,
                "out_count": joint_count,
                "failed_count": 0,
            },
            {
                "node_id": "o3",
                "label": "O3 值班专家",
                "status": "normal" if count else "unknown",
                "in_count": 3 if count else 0,
                "out_count": 3 if count else 0,
                "failed_count": 0,
            },
            {
                "node_id": "trading_records",
                "label": "交易记录",
                "status": "normal" if count else "unknown",
                "in_count": 2 if count else 0,
                "out_count": 0,
                "failed_count": 0,
            },
            {
                "node_id": "exception_queue",
                "label": "异常",
                "status": "normal" if count else "unknown",
                "in_count": 0,
                "out_count": 0,
                "failed_count": 0,
            },
            {
                "node_id": "objection",
                "label": "发起 Objection",
                "status": "normal" if count else "unknown",
                "in_count": 1 if count else 0,
                "out_count": 0,
                "failed_count": 0,
            },
            {
                "node_id": "known_event_patch",
                "label": "增补 Known Event",
                "status": "normal" if count else "unknown",
                "in_count": 1 if count else 0,
                "out_count": 0,
                "failed_count": 0,
            },
            {
                "node_id": "archive",
                "label": "归档池 Archive",
                "status": "normal" if count else "unknown",
                "in_count": max(0, count - 7),
                "out_count": 0,
                "failed_count": 0,
            },
            {
                "node_id": "ingest_queue",
                "label": "待入库队列 Ingest Queue",
                "status": "normal" if count else "unknown",
                "in_count": 2 if count else 0,
                "out_count": 0,
                "failed_count": 0,
            },
        ],
        "edges": [
            {
                "edge_id": "message_bus_to_w1",
                "from": "message_bus",
                "to": "w1",
                "label": "W1 novelty 输入",
                "count": count,
            },
            {
                "edge_id": "message_bus_to_w2",
                "from": "message_bus",
                "to": "w2",
                "label": "W2 policy 输入",
                "count": count,
            },
            {
                "edge_id": "w1_to_route_engine",
                "from": "w1",
                "to": "route_engine",
                "label": "novelty label",
                "count": joint_count,
            },
            {
                "edge_id": "w2_to_route_engine",
                "from": "w2",
                "to": "route_engine",
                "label": "policy type",
                "count": joint_count,
            },
            {
                "edge_id": "route_engine_to_trading",
                "from": "route_engine",
                "to": "trading_records",
                "label": "DTC 直接记录",
                "count": 1 if count else 0,
            },
            {
                "edge_id": "route_engine_to_o3",
                "from": "route_engine",
                "to": "o3",
                "label": "EBA / NULL / 低置信度",
                "count": 3 if count else 0,
            },
            {
                "edge_id": "route_engine_to_archive",
                "from": "route_engine",
                "to": "archive",
                "label": "old / irrelevant / duplicate",
                "count": max(0, count - 7),
            },
            {
                "edge_id": "route_engine_to_ingest_queue",
                "from": "route_engine",
                "to": "ingest_queue",
                "label": "preserve for review",
                "count": 2 if count else 0,
            },
            {
                "edge_id": "o3_to_trading",
                "from": "o3",
                "to": "trading_records",
                "label": "O3 trade action",
                "count": 1 if count else 0,
            },
            {
                "edge_id": "o3_to_objection",
                "from": "o3",
                "to": "objection",
                "label": "Document 2/3 objection",
                "count": 1 if count else 0,
            },
            {
                "edge_id": "o3_to_known_event_patch",
                "from": "o3",
                "to": "known_event_patch",
                "label": "known_events_update",
                "count": 1 if count else 0,
            },
            {
                "edge_id": "o3_to_ingest_queue",
                "from": "o3",
                "to": "ingest_queue",
                "label": "O3 fallback review",
                "count": 1 if count else 0,
            },
        ],
    }


def _degraded_runtime_graph() -> JsonObject:
    graph = _default_runtime_graph(empty=False)
    for node in graph["nodes"]:
        if node["node_id"] in {"o3", "exception_queue"}:
            node["status"] = "degraded"
            node["failed_count"] = 2
            if node["node_id"] == "exception_queue":
                node["in_count"] = 2
    graph["edges"].append(
        {
            "edge_id": "o3_to_exception_queue",
            "from": "o3",
            "to": "exception_queue",
            "label": "处理异常",
            "count": 2,
        }
    )
    return graph


def _build_runtime_nodes() -> dict[str, dict[str, JsonObject]]:
    return {
        "MU": _runtime_nodes_fixture(failed=False),
        "ASTS": _runtime_nodes_fixture(failed=True),
        "NVDA": _empty_runtime_nodes(),
        "EMPTY": _empty_runtime_nodes(),
    }


def _runtime_node(
    node_id: str,
    label: str,
    *,
    status: str = "normal",
    last_processed_at: str | None = "2026-06-30T12:04:20Z",
    today_count: int = 0,
    today_failed_count: int = 0,
    avg_latency_ms: int | None = None,
    last_error: str | None = None,
    recent_records: list[JsonObject] | None = None,
) -> JsonObject:
    return {
        "node_id": node_id,
        "label": label,
        "status": status,
        "last_processed_at": last_processed_at,
        "today_count": today_count,
        "today_failed_count": today_failed_count,
        "avg_latency_ms": avg_latency_ms,
        "last_error": last_error,
        "recent_records": recent_records or [],
    }


def _runtime_records(
    *,
    prefix: str,
    count: int,
    input_summary: str,
    output_summary: str,
    status: str = "completed",
    duration_ms: int = 1200,
) -> list[JsonObject]:
    return [
        {
            "execution_id": f"{prefix}_{index:03d}",
            "source_message_id": f"std_{prefix}_{index:03d}",
            "status": status,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "duration_ms": duration_ms + index * 350,
            "created_at": f"2026-06-30T11:{59 - min(index, 9):02d}:00Z",
        }
        for index in range(1, count + 1)
    ]


def _runtime_nodes_fixture(*, failed: bool) -> dict[str, JsonObject]:
    o3_records = _runtime_records(
        prefix="pre_o3",
        count=10,
        input_summary="W1/W2 联合路由进入 O3：需要值班专家判断 policy 外消息。",
        output_summary="O3 输出交易记录、Objection、Known Event patch 或 review queue。",
        status="failed" if failed else "completed",
        duration_ms=90000 if failed else 48000,
    )
    return {
        "message_bus": _runtime_node(
            "message_bus",
            "Message Bus / 任务池",
            today_count=42,
            avg_latency_ms=900,
            recent_records=_runtime_records(
                prefix="pre_bus",
                count=4,
                input_summary="标准消息进入 event_stream pending 队列。",
                output_summary="RuntimeSchedulerLoop 读取 pending_events 并交给 Persistent Runtime。",
                duration_ms=900,
            ),
        ),
        "w1": _runtime_node(
            "w1",
            "W1 新旧判定",
            today_count=42,
            avg_latency_ms=1200,
            recent_records=_runtime_records(
                prefix="pre_w1",
                count=4,
                input_summary="source_message + Known Events + runtime_clock。",
                output_summary="novelty_label / matched_known_event_ids / confidence。",
                duration_ms=1180,
            ),
        ),
        "w2": _runtime_node(
            "w2",
            "W2 Policy 判定",
            today_count=40,
            avg_latency_ms=1300,
            recent_records=_runtime_records(
                prefix="pre_w2",
                count=4,
                input_summary="source_message + Monitoring Execution Policy。",
                output_summary="W2Type: DTC / EBA / NULL / Irrelevant。",
                duration_ms=1310,
            ),
        ),
        "route_engine": _runtime_node(
            "route_engine",
            "联合路由",
            today_count=40,
            avg_latency_ms=320,
            recent_records=_runtime_records(
                prefix="pre_route",
                count=4,
                input_summary="W1Result + W2Result + source_type。",
                output_summary="RuntimeRoute: trading_record / o3 / ingest_queue / archive。",
                duration_ms=320,
            ),
        ),
        "o3": _runtime_node(
            "o3",
            "O3 值班专家",
            status="degraded" if failed else "normal",
            last_processed_at="2026-06-30T11:59:30Z",
            today_count=10,
            today_failed_count=2 if failed else 0,
            avg_latency_ms=76000 if failed else 48000,
            last_error="Mock O3 timeout for degraded state." if failed else None,
            recent_records=o3_records,
        ),
        "trading_records": _runtime_node(
            "trading_records",
            "交易记录",
            today_count=2,
            avg_latency_ms=240,
            recent_records=_runtime_records(
                prefix="pre_trade",
                count=2,
                input_summary="DTC 或 O3 trading_record action。",
                output_summary="TradingRecord recorded_only，未接真实 broker。",
                duration_ms=240,
            ),
        ),
        "exception_queue": _runtime_node(
            "exception_queue",
            "异常",
            status="degraded" if failed else "normal",
            today_count=2 if failed else 0,
            today_failed_count=2 if failed else 0,
            avg_latency_ms=None,
            last_error="RuntimeWorkerTimeout" if failed else None,
            recent_records=_runtime_records(
                prefix="pre_exception",
                count=2 if failed else 0,
                input_summary="worker timeout / runtime exception。",
                output_summary="ExecutionExceptionLog 持久化。",
                status="failed",
                duration_ms=90000,
            ),
        ),
        "objection": _runtime_node(
            "objection",
            "发起 Objection",
            today_count=1,
            avg_latency_ms=400,
            recent_records=_runtime_records(
                prefix="pre_obj",
                count=1,
                input_summary="O3 primary_action=objection。",
                output_summary="RuntimeObjectionRecord 写入 persistent_objections。",
                duration_ms=400,
            ),
        ),
        "known_event_patch": _runtime_node(
            "known_event_patch",
            "增补 Known Event",
            today_count=1,
            avg_latency_ms=520,
            recent_records=_runtime_records(
                prefix="pre_known",
                count=1,
                input_summary="O3 known_events_update side effect。",
                output_summary="KnownEventsPatchLog 写入 persistent_known_event_patch_logs。",
                duration_ms=520,
            ),
        ),
        "archive": _runtime_node(
            "archive",
            "归档池 Archive",
            today_count=35,
            avg_latency_ms=180,
            recent_records=_runtime_records(
                prefix="pre_arc",
                count=3,
                input_summary="old duplicate / irrelevant / A2 rejected。",
                output_summary="ArchiveItem 写入 persistent_archive。",
                duration_ms=180,
            ),
        ),
        "ingest_queue": _runtime_node(
            "ingest_queue",
            "待入库队列 Ingest Queue",
            today_count=2,
            avg_latency_ms=210,
            recent_records=_runtime_records(
                prefix="pre_inq",
                count=2,
                input_summary="保留供后续 review 或 DoxAtlas 入库。",
                output_summary="IngestQueueItem 写入 persistent_ingest_queue。",
                duration_ms=210,
            ),
        ),
    }


def _empty_runtime_nodes() -> dict[str, JsonObject]:
    nodes = _default_runtime_graph(empty=True)["nodes"]
    return {
        node["node_id"]: {
            "node_id": node["node_id"],
            "label": node["label"],
            "status": "unknown",
            "last_processed_at": None,
            "today_count": 0,
            "today_failed_count": 0,
            "avg_latency_ms": None,
            "last_error": None,
            "recent_records": [],
        }
        for node in nodes
    }


def _build_runtime_executions() -> dict[str, list[JsonObject]]:
    mu = [
        {
            "execution_id": "pre_mu_001",
            "source_message_id": "std_mu_001",
            "message_title": "Micron shares rise as AI memory demand stays firm",
            "ticker": "MU",
            "source_type": "media",
            "final_route": "trading_record",
            "status": "completed",
            "message_statuses": ["received", "workers_completed", "routed_to_trading_records"],
            "node_durations_ms": {"W1": 1180, "W2": 1310},
            "exception_types": [],
            "created_at": "2026-06-30T12:04:05Z",
        },
        {
            "execution_id": "pre_mu_003",
            "source_message_id": "std_mu_003",
            "message_title": "Rumor: new customer allocation",
            "ticker": "MU",
            "source_type": "social",
            "final_route": "o3",
            "status": "running",
            "message_statuses": ["received", "w1_running", "w2_running", "o3_running"],
            "node_durations_ms": {"W1": 1400, "W2": 1600, "O3": 48000},
            "exception_types": [],
            "created_at": "2026-06-30T11:59:00Z",
        },
    ]
    asts = [
        {
            "execution_id": "pre_asts_001",
            "source_message_id": "std_asts_001",
            "message_title": "AST SpaceMobile launch window update",
            "ticker": "ASTS",
            "source_type": "media",
            "final_route": "failed_with_exception",
            "status": "failed",
            "message_statuses": ["received", "workers_completed", "failed_with_exception"],
            "node_durations_ms": {"W1": 1600, "W2": 2100, "O3": 90000},
            "exception_types": ["RuntimeWorkerTimeout"],
            "created_at": "2026-06-30T11:54:00Z",
        }
    ]
    return {"MU": mu, "ASTS": asts, "NVDA": [], "EMPTY": []}


def _build_runtime_execution_details() -> dict[str, JsonObject]:
    return {
        "pre_mu_001": {
            "execution_id": "pre_mu_001",
            "source_message": {
                "source_message_id": "std_mu_001",
                "ticker": "MU",
                "title": "Micron shares rise as AI memory demand stays firm",
                "source_type": "media",
                "source_id": "benzinga_news",
            },
            "route_decision": {"final_route": "trading_record", "reason": "DTC policy matched."},
            "w1_result": {
                "is_new": True,
                "confidence": "high",
                "reasoning": "不是 Known Event recap。",
            },
            "w2_result": {
                "type": "DTC",
                "matched_policy_code": "POLICY_DTC_HBM_ORDER",
                "reasoning": "新增订单/需求信息匹配交易候选策略。",
            },
            "a2_result": None,
            "o3_result": None,
            "node_traces": [
                {"node": "W1", "status": "completed", "duration_ms": 1180},
                {"node": "W2", "status": "completed", "duration_ms": 1310},
            ],
            "exceptions": [],
            "created_at": "2026-06-30T12:04:05Z",
        },
        "pre_mu_003": {
            "execution_id": "pre_mu_003",
            "source_message": {
                "source_message_id": "std_mu_003",
                "ticker": "MU",
                "title": "Rumor: new customer allocation",
                "source_type": "social",
                "source_id": "tikhub_x_search",
            },
            "route_decision": {"final_route": "o3", "reason": "需要值班专家确认传闻可信度。"},
            "w1_result": {
                "is_new": True,
                "confidence": "medium",
                "reasoning": "可能是新增客户信息。",
            },
            "w2_result": {
                "type": "EBA",
                "matched_policy_code": "POLICY_EBA_SUPPLY_CHAIN",
                "reasoning": "证据不足，委托背景 agent。",
            },
            "a2_result": None,
            "o3_result": {"status": "running", "summary": "mock processing"},
            "node_traces": [
                {"node": "W1", "status": "completed", "duration_ms": 1400},
                {"node": "W2", "status": "completed", "duration_ms": 1600},
                {"node": "O3", "status": "running", "duration_ms": 48000},
            ],
            "exceptions": [],
            "created_at": "2026-06-30T11:59:00Z",
        },
        "pre_asts_001": {
            "execution_id": "pre_asts_001",
            "source_message": {
                "source_message_id": "std_asts_001",
                "ticker": "ASTS",
                "title": "AST SpaceMobile launch window update",
                "source_type": "media",
                "source_id": "newswire_rss",
            },
            "route_decision": {
                "final_route": "failed_with_exception",
                "reason": "O3 worker timeout.",
            },
            "w1_result": {
                "is_new": True,
                "confidence": "medium",
                "reasoning": "发射窗口消息为新更新。",
            },
            "w2_result": {"type": "DTC", "matched_policy_code": "POLICY_DTC_HBM_ORDER"},
            "a2_result": None,
            "o3_result": None,
            "node_traces": [{"node": "O3", "status": "failed", "duration_ms": 90000}],
            "exceptions": [{"type": "RuntimeWorkerTimeout", "message": "Mock timeout"}],
            "created_at": "2026-06-30T11:54:00Z",
        },
    }


def _build_revenue_audit() -> dict[str, JsonObject]:
    return {
        "MU": {
            "ticker": "MU",
            "audit_date": "2026-06-30",
            "period": "today",
            "status": "completed",
            "exit_rule": "close_minus_10min_full_exit",
            "kpis": {
                "today_trade_intent_count": 1,
                "audited_trade_count": 1,
                "today_pnl_usd": 183.42,
                "today_return_pct": 1.21,
                "win_rate": 1.0,
            },
            "trend": [
                {"date": "2026-06-28", "pnl_usd": -42.1, "trade_intent_count": 2},
                {"date": "2026-06-29", "pnl_usd": 88.0, "trade_intent_count": 1},
                {"date": "2026-06-30", "pnl_usd": 183.42, "trade_intent_count": 1},
            ],
            "trade_intents": [
                {
                    "record_id": "trd_mu_001",
                    "time": "2026-06-30T12:04:20Z",
                    "ticker": "MU",
                    "trigger_message_id": "std_mu_001",
                    "trigger_policy_id": "POLICY_DTC_HBM_ORDER",
                    "action": "long",
                    "theoretical_entry_price": 134.2,
                    "estimated_entry_price": 134.35,
                    "exit_price": 135.98,
                    "slippage_pct": 0.11,
                    "pnl_usd": 183.42,
                    "status": "audited",
                }
            ],
        },
        "ASTS": _empty_revenue_audit("ASTS") | {"status": "calculating"},
        "NVDA": _empty_revenue_audit("NVDA"),
        "EMPTY": _empty_revenue_audit("EMPTY"),
    }


def _empty_revenue_audit(ticker: str) -> JsonObject:
    return {
        "ticker": ticker,
        "audit_date": "2026-06-30",
        "period": "today",
        "status": "not_started",
        "exit_rule": "close_minus_10min_full_exit",
        "kpis": {
            "today_trade_intent_count": 0,
            "audited_trade_count": 0,
            "today_pnl_usd": None,
            "today_return_pct": None,
            "win_rate": None,
        },
        "trend": [{"date": "2026-06-30", "pnl_usd": None, "trade_intent_count": 0}],
        "trade_intents": [],
    }


def _build_cost_audit() -> dict[str, JsonObject]:
    return {
        "MU": {
            "ticker": "MU",
            "period": "today",
            "status": "completed",
            "kpis": {
                "today_input_tokens": 184000,
                "today_output_tokens": 28600,
                "today_total_tokens": 212600,
                "today_total_cost_usd": 0.7924,
                "highest_cost_node": "O3",
                "retry_cost_usd": 0.0431,
            },
            "trend": [
                {"date": "2026-06-28", "total_cost_usd": 0.58, "total_tokens": 150200},
                {"date": "2026-06-29", "total_cost_usd": 0.69, "total_tokens": 181400},
                {"date": "2026-06-30", "total_cost_usd": 0.7924, "total_tokens": 212600},
            ],
            "breakdown": {
                "by_node": [
                    {"key": "W1", "label": "W1", "cost_usd": 0.12, "total_tokens": 42000},
                    {"key": "W2", "label": "W2", "cost_usd": 0.18, "total_tokens": 51000},
                    {"key": "O3", "label": "O3", "cost_usd": 0.49, "total_tokens": 119600},
                ],
                "by_model": [
                    {"key": "qwen-plus", "label": "qwen-plus", "cost_usd": 0.31},
                    {"key": "deepseek-chat", "label": "deepseek-chat", "cost_usd": 0.48},
                ],
            },
        },
        "ASTS": _empty_cost_audit("ASTS") | {"status": "partial"},
        "NVDA": _empty_cost_audit("NVDA"),
        "EMPTY": _empty_cost_audit("EMPTY"),
    }


def _empty_cost_audit(ticker: str) -> JsonObject:
    return {
        "ticker": ticker,
        "period": "today",
        "status": "missing",
        "kpis": {
            "today_input_tokens": None,
            "today_output_tokens": None,
            "today_total_tokens": None,
            "today_total_cost_usd": None,
            "highest_cost_node": None,
            "retry_cost_usd": None,
        },
        "trend": [],
        "breakdown": {"by_node": [], "by_model": []},
    }


def _build_cost_details() -> dict[str, list[JsonObject]]:
    details = [
        {
            "cost_record_id": "cost_mu_001",
            "time": "2026-06-30T12:04:20Z",
            "ticker": "MU",
            "node": "W2",
            "model": "qwen-plus",
            "input_tokens": 2200,
            "output_tokens": 380,
            "total_tokens": 2580,
            "cost_usd": 0.0018,
            "is_retry": False,
            "status": "succeeded",
            "source_ref": {"execution_id": "pre_mu_001"},
        },
        {
            "cost_record_id": "cost_mu_002",
            "time": "2026-06-30T11:59:20Z",
            "ticker": "MU",
            "node": "O3",
            "model": "deepseek-chat",
            "input_tokens": 8800,
            "output_tokens": 1600,
            "total_tokens": 10400,
            "cost_usd": 0.0094,
            "is_retry": True,
            "status": "retried",
            "source_ref": {"execution_id": "pre_mu_003"},
        },
    ]
    asts = [
        {
            "cost_record_id": "cost_asts_001",
            "time": "2026-06-30T11:54:40Z",
            "ticker": "ASTS",
            "node": "O3",
            "model": "deepseek-chat",
            "input_tokens": 7600,
            "output_tokens": 0,
            "total_tokens": 7600,
            "cost_usd": 0.0069,
            "is_retry": False,
            "status": "failed",
            "source_ref": {"execution_id": "pre_asts_001"},
        }
    ]
    return {"MU": details, "ASTS": asts, "NVDA": [], "EMPTY": []}


def _build_sse_events() -> list[JsonObject]:
    return [
        {
            "event_id": "evt_mock_1001",
            "event_type": "message_bus.message.created",
            "ticker": "MU",
            "occurred_at": "2026-06-30T12:04:00Z",
            "payload": {"message_id": "std_mu_001", "source_id": "benzinga_news"},
        },
        {
            "event_id": "evt_mock_1002",
            "event_type": "runtime.execution.updated",
            "ticker": "MU",
            "occurred_at": "2026-06-30T12:04:20Z",
            "payload": {"execution_id": "pre_mu_001", "status": "completed"},
        },
        {
            "event_id": "evt_mock_1003",
            "event_type": "trade_intent.created",
            "ticker": "MU",
            "occurred_at": "2026-06-30T12:04:22Z",
            "payload": {"record_id": "trd_mu_001", "status": "recorded_only"},
        },
        {
            "event_id": "evt_mock_1004",
            "event_type": "runtime.execution.failed",
            "ticker": "ASTS",
            "occurred_at": "2026-06-30T11:56:00Z",
            "payload": {"execution_id": "pre_asts_001", "exception_type": "RuntimeWorkerTimeout"},
        },
        {
            "event_id": "evt_mock_1005",
            "event_type": "audit.cost.status_changed",
            "ticker": "MU",
            "occurred_at": "2026-06-30T12:05:00Z",
            "payload": {"status": "completed"},
        },
    ]


def _new_ticker_fixture(ticker: str, *, monitor_mode: str = "message_monitoring") -> JsonObject:
    now = utc_now_iso()
    return {
        "ticker": ticker,
        "status": "running",
        "status_label": "运行中",
        "health": "normal",
        "session_phase": "formal_monitoring",
        "monitor_mode": monitor_mode,
        "started_at": now,
        "updated_at": now,
        "last_message_at": None,
        "last_worker_processed_at": None,
        "today_dtc_count": 0,
        "today_cost_usd": None,
        "last_error": None,
    }


def _new_ticker_detail_fixture(state: JsonObject) -> JsonObject:
    ticker = state["ticker"]
    return {
        "ticker": ticker,
        "state": {
            "status": state["status"],
            "health": state["health"],
            "session_phase": state["session_phase"],
            "monitor_mode": state.get("monitor_mode", "message_monitoring"),
            "document_run_id": None,
            "last_error": None,
        },
        "document_status": {"usable": False, "stale": False, "availability": "missing"},
        "message_bus_status": {
            "pending_event_count": 0,
            "recent_message_count": 0,
            "last_error_message": None,
        },
        "runtime_status": {
            "queue_message_count": 0,
            "failed_task_count": 0,
            "last_execution_at": None,
        },
        "audit_summary": {
            "today_dtc_count": 0,
            "today_revenue_audit_status": "not_started",
            "today_cost_audit_status": "missing",
        },
    }
