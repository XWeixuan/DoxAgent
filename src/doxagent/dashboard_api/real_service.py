"""Real Dashboard State API assemblers for scheduler-backed dashboard views."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from doxagent.blackboard import BlackboardService
from doxagent.blackboard.state import BlackboardRun
from doxagent.models import DocumentType
from doxagent.models.documents import (
    DocumentBase,
    ExpectationUnitDocument,
    GlobalResearchDocument,
    KnownEvent,
    KnownEventsDocument,
    MonitoringPolicyDocument,
    MonitoringPolicyRule,
    ResearchSection,
)
from doxagent.monitoring.schema import (
    EventStreamItem,
    MonitoringParameters,
    PollState,
    StandardMessage,
    TickerSourceBinding,
    UpdateActor,
    binding_id_for,
    parameter_schema_for_source,
)
from doxagent.persistent_runtime.schema import (
    ArchiveItem,
    ExecutionExceptionLog,
    IngestQueueItem,
    KnownEventsPatchLog,
    RuntimeExecutionRecord,
    RuntimeKnownEvent,
    RuntimeNodeTrace,
    RuntimeObjectionRecord,
    TradingRecord,
)
from doxagent.runtime_scheduler.api import DashboardStateAPI
from doxagent.runtime_scheduler.schema import (
    AuditSeverity,
    MonitorMode,
    RuntimeAuditEvent,
    RuntimeHealth,
    TickerRunDetail,
    TickerRunState,
    TickerRunStatus,
)

JsonObject = dict[str, Any]
T = TypeVar("T")

DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_MONITOR_MODE = "message_monitoring"
ENABLED_MONITOR_MODES = {
    MonitorMode.MESSAGE_MONITORING.value,
    MonitorMode.PAPER_TRADING.value,
}
READ_AGGREGATION_LIMIT = 10_000
DOCUMENT_HISTORY_LIMIT = 100
MESSAGE_BUS_CONFIG_SOURCES = (
    "benzinga_news",
    "finnhub_company_news",
    "stocktwits_messages",
    "tikhub_x_search",
    "tikhub_x_user_posts",
    "newswire_rss",
)
RUNTIME_NODE_DEFINITIONS = (
    ("message_bus", "Message Bus"),
    ("w1", "W1 Novelty"),
    ("w2", "W2 Policy"),
    ("route_engine", "Route Engine"),
    ("o3", "O3 Duty Expert"),
    ("trading_records", "Trading Records"),
    ("exception_queue", "Exception Queue"),
    ("objection", "Objection"),
    ("known_event_patch", "Known Event Patch"),
    ("archive", "Archive"),
    ("ingest_queue", "Ingest Queue"),
)
RUNTIME_NODE_IDS = {node_id for node_id, _label in RUNTIME_NODE_DEFINITIONS}
AUDIT_PERIODS = {"today", "7d", "30d"}
AUDIT_GROUP_BYS = {"node", "model", "ticker"}
REVENUE_AUDIT_EVENT_TYPE = "audit.revenue.status_changed"
COST_AUDIT_EVENT_TYPE = "audit.cost.status_changed"
REVENUE_EXIT_RULE_NOT_INTEGRATED = "realized_exit_price_audit_not_integrated"


@dataclass(frozen=True)
class RuntimeDashboardContext:
    ticker: str
    executions: list[RuntimeExecutionRecord]
    messages_by_id: dict[str, StandardMessage]
    exceptions_by_source: dict[str, list[ExecutionExceptionLog]]
    trading_records_by_source: dict[str, list[TradingRecord]]
    ingest_queue_by_source: dict[str, list[IngestQueueItem]]
    archive_by_source: dict[str, list[ArchiveItem]]
    known_event_patch_by_source: dict[str, list[KnownEventsPatchLog]]
    objections_by_source: dict[str, list[RuntimeObjectionRecord]]

RUNNING_STATUSES = {TickerRunStatus.RUNNING, TickerRunStatus.DEGRADED}
FRONTEND_DOCUMENT_TYPES = ("document1", "document2", "document3")
DOCUMENT_TYPE_LABELS = {
    "document1": "Document 1：Global Research",
    "document2": "Document 2：Expectation Units",
    "document3": "Document 3：Runtime Strategy",
}
INTERNAL_DOCUMENT_TYPE_BY_FRONTEND = {
    "document1": DocumentType.GLOBAL_RESEARCH,
    "document2": DocumentType.EXPECTATION_UNIT,
    "known_events": DocumentType.KNOWN_EVENTS,
    "monitoring_policy": DocumentType.MONITORING_POLICY,
}
DOCUMENT_TYPE_LABELS.update(
    {
        "document1": "Document 1: Global Research",
        "document2": "Document 2: Expectation Units",
        "document3": "Document 3: Runtime Strategy",
    }
)


class RealDashboardOverviewService:
    """Adapter layer that hides scheduler/runtime internals from HTTP routes."""

    def __init__(self, dashboard_api: DashboardStateAPI | None = None) -> None:
        self.dashboard_api = dashboard_api or DashboardStateAPI.from_settings()

    def overview(self, *, date_text: str | None = None, tz: str | None = None) -> JsonObject:
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        states = self._states()
        cards = [self._ticker_card(state, target_date=target_date, zone=zone) for state in states]
        message_count = sum(card.pop("_today_message_count") for card in cards)
        exceptions = self._all_exceptions()
        system_status = self._system_status(states)
        return {
            "generated_at": _dt(datetime.now(UTC)),
            "system": system_status,
            "kpis": {
                "running_ticker_count": sum(
                    1 for state in states if state.status in RUNNING_STATUSES
                ),
                "today_message_count": message_count,
                "today_dtc_count": sum(int(card["today_dtc_count"]) for card in cards),
                "today_token_cost_usd": None,
                "exception_count": _count_on_day(
                    (item.created_at for item in exceptions),
                    target_date=target_date,
                    zone=zone,
                ),
            },
            "tickers": cards,
        }

    def list_tickers(
        self,
        *,
        status: str | None = None,
        health: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        sort: str | None = None,
        date_text: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        cards = [
            self._ticker_card(state, target_date=target_date, zone=zone)
            for state in self._states()
        ]
        for card in cards:
            card.pop("_today_message_count", None)
        if status and status != "all":
            cards = [card for card in cards if card["status"] == status]
        if health and health != "all":
            cards = [card for card in cards if card["health"] == health]
        return _paginate(_sort_cards(cards, sort), limit=limit, cursor=cursor)

    def get_ticker(
        self,
        ticker: str,
        *,
        date_text: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        detail = self.dashboard_api.get_ticker(_ticker(ticker))
        return self._ticker_detail(detail, target_date=target_date, zone=zone)

    def start_ticker(
        self,
        ticker: str,
        *,
        force_initialize: bool = False,
        monitor_mode: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        resolved_mode = _monitor_mode(monitor_mode)
        existing = self.dashboard_api.scheduler.repository.get_state(normalized)
        if existing is not None and existing.status in RUNNING_STATUSES and not force_initialize:
            raise TickerAlreadyRunning(normalized)
        detail = self.dashboard_api.start_ticker(
            normalized,
            force_initialize=force_initialize,
            monitor_mode=resolved_mode,
        )
        return self._operation_result("start", detail)

    def pause_ticker(self, ticker: str, *, reason: str | None = None) -> JsonObject:
        detail = self.dashboard_api.pause_ticker(_ticker(ticker), reason=reason)
        return self._operation_result("pause", detail)

    def set_monitor_mode(
        self,
        ticker: str,
        *,
        monitor_mode: str | None,
        reason: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        if self.dashboard_api.scheduler.repository.get_state(normalized) is None:
            raise TickerNotFound(normalized)
        resolved_mode = _monitor_mode(monitor_mode)
        detail = self.dashboard_api.set_monitor_mode(
            normalized,
            resolved_mode,
            reason=reason or "Dashboard monitor mode changed.",
        )
        return self._operation_result("monitor_mode", detail)

    def delete_ticker(
        self,
        ticker: str,
        *,
        reason: str | None = None,
        delete_history: bool = False,
    ) -> JsonObject:
        if delete_history:
            raise UnsupportedHistoryDelete(_ticker(ticker))
        normalized = _ticker(ticker)
        binding_count_before = len(
            self.dashboard_api.scheduler.monitoring_service.repository.list_bindings(
                ticker=normalized
            )
        )
        self.dashboard_api.stop_ticker(
            normalized,
            reason=reason,
            disable_bindings=True,
        )
        deleted_count = self.dashboard_api.scheduler.monitoring_service.delete_ticker_config(
            normalized
        )
        return {
            "operation": "delete",
            "status": "accepted",
            "ticker": normalized,
            "disabled_binding_count": binding_count_before,
            "deleted_binding_count": deleted_count,
            "history_deleted": False,
        }

    def restart_ticker(
        self,
        ticker: str,
        *,
        force_initialize: bool = False,
        keep_bindings: bool = True,
        reason: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        self.dashboard_api.stop_ticker(
            normalized,
            reason=reason or "Dashboard restart requested.",
            disable_bindings=not keep_bindings,
        )
        stopped_state = self.dashboard_api.scheduler.repository.get_state(normalized)
        detail = self.dashboard_api.start_ticker(
            normalized,
            force_initialize=force_initialize,
            monitor_mode=_state_monitor_mode(stopped_state) if stopped_state else None,
        )
        return self._operation_result("restart", detail)

    def documents_current(
        self,
        ticker: str,
        *,
        types: str | None = None,
        include_raw: bool = False,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        requested_types = _frontend_document_types(types)
        selected = self._current_document_run(normalized, requested_types)
        if selected is None:
            return {"ticker": normalized, "document_run_id": None, "documents": []}
        run, documents = selected
        return {
            "ticker": normalized,
            "document_run_id": run.run_id,
            "documents": [
                _without_raw(document, include_raw=include_raw) for document in documents
            ],
        }

    def document_versions(
        self,
        ticker: str,
        document_type: str,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        resolved_type = _frontend_document_type(document_type)
        versions = [
            version
            for version, _document in self._versioned_documents(
                normalized,
                resolved_type,
                include_raw=False,
            )
        ]
        return _paginate(versions, limit=limit, cursor=cursor)

    def document_version_detail(
        self,
        ticker: str,
        document_type: str,
        version_id: str,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        resolved_type = _frontend_document_type(document_type)
        requested = version_id.strip()
        for version, document in self._versioned_documents(
            normalized,
            resolved_type,
            include_raw=False,
        ):
            if requested in {version["version_id"], version["document_id"]}:
                return {
                    "ticker": normalized,
                    "version": version,
                    "document": document,
                }
        raise DocumentVersionNotFound(normalized, resolved_type, requested)

    def known_events(
        self,
        ticker: str,
        *,
        expectation_id: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        items = self._known_event_items(normalized)
        if expectation_id:
            items = [
                item
                for item in items
                if expectation_id in item.get("related_expectation_ids", [])
            ]
        if q:
            items = _search_items(items, q, fields=("event_name", "description"))
        items = _sort_by_updated(items)
        return _paginate(items, limit=limit, cursor=cursor)

    def policies(
        self,
        ticker: str,
        *,
        action_type: str | None = None,
        expectation_id: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        items = self._policy_items(normalized)
        if action_type:
            items = [item for item in items if item["action_type"] == action_type]
        if expectation_id:
            items = [item for item in items if item.get("expectation_id") == expectation_id]
        if q:
            items = _search_items(items, q, fields=("policy_id", "title", "trigger_condition"))
        items = _sort_by_updated(items)
        return _paginate(items, limit=limit, cursor=cursor)

    def message_bus_overview(
        self,
        ticker: str,
        *,
        date_text: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        messages = self._messages(normalized)
        events = self._events(normalized)
        config = self.message_bus_config(normalized)
        sources = config["sources"]
        healthy_sources = [
            source
            for source in sources
            if _message_source_health(source) in {"normal", "disabled", "never_polled"}
        ]
        last_error_message = None
        for source in sources:
            message = source["poll_state"].get("last_error_message")
            if isinstance(message, str) and message.strip():
                last_error_message = message
                break
        return {
            "ticker": normalized,
            "uptime_seconds": _ticker_uptime_seconds(
                self.dashboard_api.scheduler.repository.get_state(normalized)
            ),
            "today_raw_message_count": _count_on_day(
                (message.normalized_at for message in messages),
                target_date=target_date,
                zone=zone,
            ),
            "today_event_count": _count_on_day(
                (event.event_time for event in events),
                target_date=target_date,
                zone=zone,
            ),
            "media_enrichment_success_rate": _media_enrichment_success_rate(messages),
            "healthy_channel_count": len(healthy_sources),
            "total_channel_count": len(sources),
            "last_error_message": last_error_message,
        }

    def message_bus_messages(
        self,
        ticker: str,
        *,
        source_id: str | None = None,
        processing_status: str | None = None,
        q: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        runtime_by_source = {
            execution.source_message.source_message_id: execution
            for execution in self._executions(normalized)
        }
        events_by_message = {
            event.standard_message_id: event for event in self._events(normalized)
        }
        source_labels = {
            source.source_id: source.display_name
            for source in self.dashboard_api.scheduler.monitoring_service.repository.list_sources()
        }
        items = [
            _message_item(
                message,
                runtime=runtime_by_source.get(message.standard_message_id),
                event=events_by_message.get(message.standard_message_id),
                source_label=source_labels.get(message.source_id),
            )
            for message in self._messages(normalized)
        ]
        if source_id:
            normalized_source = source_id.strip().lower()
            items = [item for item in items if item["source_id"] == normalized_source]
        if processing_status:
            items = [
                item
                for item in items
                if item["processing_status"] == processing_status.strip()
            ]
        if q:
            items = _search_items(items, q, fields=("title", "summary", "body", "source_id"))
        items = _sort_messages(items, sort)
        return _paginate(items, limit=limit, cursor=cursor)

    def message_bus_config(self, ticker: str) -> JsonObject:
        normalized = _ticker(ticker)
        repository = self.dashboard_api.scheduler.monitoring_service.repository
        sources = {
            source.source_id: source
            for source in repository.list_sources()
            if source.source_id in MESSAGE_BUS_CONFIG_SOURCES
        }
        bindings = {
            binding.source_id: binding
            for binding in repository.list_bindings(ticker=normalized)
        }
        poll_states = {
            state.binding_id: state
            for state in repository.list_poll_states(ticker=normalized)
        }
        return {
            "ticker": normalized,
            "sources": [
                _message_source_config(
                    normalized,
                    source_id,
                    source=sources[source_id],
                    binding=bindings.get(source_id),
                    poll_state=poll_states.get(binding_id_for(normalized, source_id)),
                )
                for source_id in MESSAGE_BUS_CONFIG_SOURCES
                if source_id in sources
            ],
            "missing_source_ids": [
                source_id
                for source_id in MESSAGE_BUS_CONFIG_SOURCES
                if source_id not in bindings
            ],
        }

    def patch_message_source(
        self,
        ticker: str,
        source_id: str,
        payload: JsonObject,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        normalized_source = _source_id(source_id)
        source = self.dashboard_api.scheduler.monitoring_service.repository.get_source(
            normalized_source
        )
        if source is None:
            raise UnsupportedMessageSource(normalized_source)
        parameters, touched_parameters = _message_bus_parameters(normalized_source, payload)
        enabled = _optional_bool(payload.get("enabled"))
        existing = self.dashboard_api.scheduler.monitoring_service.repository.get_binding(
            normalized,
            normalized_source,
        )
        resolved_parameters = (
            parameters
            if touched_parameters or existing is None
            else existing.parameters
        )
        binding = self.dashboard_api.scheduler.monitoring_service.configure_ticker_source(
            normalized,
            normalized_source,
            parameters=resolved_parameters,
            enabled=enabled if enabled is not None else existing.enabled if existing else True,
            updated_by=UpdateActor.USER,
            updated_reason=_optional_text_value(payload.get("reason")),
            merge=False,
        )
        config = self.message_bus_config(normalized)
        return {
            **config,
            "source_id": normalized_source,
            "binding": binding.model_dump(mode="json"),
            "config": config,
        }

    def delete_message_source(self, ticker: str, source_id: str) -> JsonObject:
        normalized = _ticker(ticker)
        normalized_source = _source_id(source_id)
        if self.dashboard_api.scheduler.monitoring_service.repository.get_source(
            normalized_source
        ) is None:
            raise UnsupportedMessageSource(normalized_source)
        removed = self.dashboard_api.scheduler.monitoring_service.delete_ticker_source(
            normalized,
            normalized_source,
        )
        return {
            "ticker": normalized,
            "source_id": normalized_source,
            "removed": removed,
        }

    def runtime_overview(
        self,
        ticker: str,
        *,
        date_text: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        context = self._runtime_context(normalized)
        today_executions = [
            execution
            for execution in context.executions
            if _is_on_day(execution.created_at, target_date=target_date, zone=zone)
        ]
        exception_sources_today = {
            exception.source_message_id
            for exceptions in context.exceptions_by_source.values()
            for exception in exceptions
            if _is_on_day(exception.created_at, target_date=target_date, zone=zone)
        }
        failed_execution_sources_today = {
            execution.source_message.source_message_id
            for execution in today_executions
            if _runtime_execution_status(execution, context) == "failed"
        }
        pending_events = self.dashboard_api.scheduler.monitoring_service.pending_events(
            ticker=normalized,
            limit=READ_AGGREGATION_LIMIT,
        )
        return {
            "ticker": normalized,
            "queue_message_count": len(pending_events),
            "w1_today_count": _runtime_node_count(
                today_executions,
                "w1",
                context=context,
            ),
            "w1_avg_latency_ms": _runtime_node_avg_latency(
                today_executions,
                "w1",
            ),
            "w2_today_count": _runtime_node_count(
                today_executions,
                "w2",
                context=context,
            ),
            "w2_avg_latency_ms": _runtime_node_avg_latency(
                today_executions,
                "w2",
            ),
            "o3_today_count": _runtime_node_count(
                today_executions,
                "o3",
                context=context,
            ),
            "o3_avg_latency_ms": _runtime_node_avg_latency(
                today_executions,
                "o3",
            ),
            "dtc_today_count": sum(1 for execution in today_executions if _is_dtc(execution)),
            "eba_today_count": sum(1 for execution in today_executions if _is_eba(execution)),
            "failed_task_count": len(exception_sources_today | failed_execution_sources_today),
            "avg_processing_latency_ms": _runtime_avg_processing_latency(today_executions),
        }

    def runtime_graph(self, ticker: str) -> JsonObject:
        context = self._runtime_context(_ticker(ticker))
        nodes = [
            _runtime_graph_node(context, node_id=node_id, label=label)
            for node_id, label in RUNTIME_NODE_DEFINITIONS
        ]
        edges = _runtime_graph_edges(context)
        return {"nodes": nodes, "edges": edges}

    def runtime_node(
        self,
        ticker: str,
        node_id: str,
        *,
        date_text: str | None = None,
        tz: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        normalized_node = _runtime_node_id(node_id)
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        context = self._runtime_context(_ticker(ticker))
        records = _runtime_node_records(context, normalized_node)
        page = _paginate(records, limit=limit, cursor=cursor)
        return {
            "node": _runtime_node_detail(
                context,
                normalized_node,
                records=records,
                target_date=target_date,
                zone=zone,
            ),
            "recent_records": page["items"],
            "page": page["page"],
        }

    def runtime_executions(
        self,
        ticker: str,
        *,
        route: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        context = self._runtime_context(_ticker(ticker))
        items = [_runtime_execution_item(execution, context) for execution in context.executions]
        if route:
            route_filter = route.strip().lower()
            items = [item for item in items if item["final_route"] == route_filter]
        if status:
            status_filter = status.strip().lower()
            items = [item for item in items if item["status"] == status_filter]
        if source_type:
            source_type_filter = source_type.strip().lower()
            items = [item for item in items if item["source_type"] == source_type_filter]
        return _paginate(items, limit=limit, cursor=cursor)

    def runtime_execution_detail(self, ticker: str, execution_id: str) -> JsonObject:
        normalized = _ticker(ticker)
        context = self._runtime_context(normalized)
        for execution in context.executions:
            if execution.execution_id == execution_id:
                return _runtime_execution_detail(execution, context)
        raise RuntimeExecutionNotFound(normalized, execution_id)

    def revenue_audit(
        self,
        ticker: str,
        *,
        date_text: str | None = None,
        period: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        resolved_period = _audit_period(period)
        records = [
            record
            for record in self._trading_records(normalized)
            if _in_audit_period(record.created_at, resolved_period, target_date, zone)
        ]
        records.sort(key=lambda item: _aware(item.created_at), reverse=True)
        trade_intents = [_trade_intent_audit_item(record) for record in records]
        audited_trade_count = sum(
            1 for item in trade_intents if item["status"] == "audited"
        )
        status = _revenue_audit_status(
            trade_intents,
            latest_event=self._latest_scheduler_audit_event(
                normalized,
                REVENUE_AUDIT_EVENT_TYPE,
            ),
        )
        return {
            "ticker": normalized,
            "audit_date": target_date.isoformat(),
            "period": resolved_period,
            "status": status,
            "exit_rule": REVENUE_EXIT_RULE_NOT_INTEGRATED,
            "kpis": {
                "today_trade_intent_count": len(trade_intents),
                "audited_trade_count": audited_trade_count,
                "today_pnl_usd": _sum_optional(
                    item.get("pnl_usd") for item in trade_intents
                ),
                "today_return_pct": None,
                "win_rate": _win_rate(trade_intents),
            },
            "trend": _revenue_trend(records, resolved_period, target_date, zone),
            "trade_intents": trade_intents,
        }

    def run_revenue_audit(
        self,
        ticker: str,
        *,
        date_text: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        records = [
            record
            for record in self._trading_records(normalized)
            if _is_on_day(record.created_at, target_date=target_date, zone=zone)
        ]
        event = RuntimeAuditEvent(
            ticker=normalized,
            event_type=REVENUE_AUDIT_EVENT_TYPE,
            severity=AuditSeverity.WARNING,
            message=(
                "Revenue audit was requested, but realized exit-price/PnL audit "
                "worker is not integrated yet."
            ),
            payload={
                "ticker": normalized,
                "date": target_date.isoformat(),
                "status": "not_started",
                "trade_intent_count": len(records),
                "missing_capabilities": [
                    "entry_price_capture",
                    "exit_price_capture",
                    "slippage_calculation",
                    "realized_pnl_audit_worker",
                ],
            },
        )
        saved = self.dashboard_api.scheduler.repository.append_audit_event(event)
        return {
            "audit_run_id": saved.audit_id,
            "ticker": normalized,
            "date": target_date.isoformat(),
            "status": "not_started",
        }

    def cost_audit(
        self,
        ticker: str,
        *,
        date_text: str | None = None,
        period: str | None = None,
        group_by: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        resolved_period = _audit_period(period)
        resolved_group_by = _audit_group_by(group_by)
        records = [
            record
            for record in self._cost_records(normalized)
            if _in_audit_period(
                _parse_dt_text(record.get("time")),
                resolved_period,
                target_date,
                zone,
            )
        ]
        records.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        return _cost_audit_payload(
            normalized,
            records,
            period=resolved_period,
            group_by=resolved_group_by,
            target_date=target_date,
            zone=zone,
        )

    def cost_details(
        self,
        ticker: str,
        *,
        period: str | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        date_text: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        normalized = _ticker(ticker)
        zone = _zone(tz)
        target_date = _target_date(date_text, zone)
        resolved_period = _audit_period(period) if period else None
        start_time = _parse_dt_text(from_time)
        end_time = _parse_dt_text(to_time)
        if from_time and start_time is None:
            raise InvalidAuditParams("Invalid from timestamp.", details={"field": "from"})
        if to_time and end_time is None:
            raise InvalidAuditParams("Invalid to timestamp.", details={"field": "to"})
        node_filter = node.strip().lower() if node and node.strip() else None
        model_filter = model.strip() if model and model.strip() else None
        status_filter = status.strip().lower() if status and status.strip() else None
        items = self._cost_records(normalized)
        if resolved_period:
            items = [
                item
                for item in items
                if _in_audit_period(
                    _parse_dt_text(item.get("time")),
                    resolved_period,
                    target_date,
                    zone,
                )
            ]
        if start_time is not None:
            items = [
                item
                for item in items
                if (
                    (item_time := _parse_dt_text(item.get("time"))) is not None
                    and _aware(item_time) >= _aware(start_time)
                )
            ]
        if end_time is not None:
            items = [
                item
                for item in items
                if (
                    (item_time := _parse_dt_text(item.get("time"))) is not None
                    and _aware(item_time) <= _aware(end_time)
                )
            ]
        if node_filter:
            items = [
                item for item in items if str(item.get("node") or "").lower() == node_filter
            ]
        if model_filter:
            items = [item for item in items if item.get("model") == model_filter]
        if status_filter:
            items = [
                item
                for item in items
                if str(item.get("status") or "").lower() == status_filter
            ]
        items.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        return _paginate(items, limit=limit, cursor=cursor)

    def dashboard_events(
        self,
        *,
        ticker: str | None = None,
        event_types: str | None = None,
        last_event_id: str | None = None,
    ) -> list[JsonObject]:
        normalized = _ticker(ticker) if ticker else None
        requested_types = _csv_set(event_types)
        events: list[JsonObject] = []
        for event in self._events(normalized):
            events.append(
                {
                    "event_id": f"mb_{event.event_id}",
                    "event_type": "message_bus.message.created",
                    "ticker": event.ticker,
                    "occurred_at": _dt(event.event_time),
                    "payload": {
                        "source_id": event.source_id,
                        "standard_message_id": event.standard_message_id,
                        "stream_offset": event.stream_offset,
                    },
                }
            )
        repository = self.dashboard_api.scheduler.monitoring_service.repository
        for state in repository.list_poll_states(ticker=normalized):
            if str(state.status) != "failed":
                continue
            occurred_at = state.last_error_at or state.updated_at
            events.append(
                {
                    "event_id": f"mb_poll_failed_{state.binding_id}_{int(occurred_at.timestamp())}",
                    "event_type": "message_bus.poll.failed",
                    "ticker": state.ticker,
                    "occurred_at": _dt(occurred_at),
                    "payload": {
                        "source_id": state.source_id,
                        "binding_id": state.binding_id,
                        "last_error_message": state.last_error_message,
                    },
                }
            )
        runtime_context = self._runtime_context(normalized) if normalized else None
        runtime_executions = (
            runtime_context.executions if runtime_context is not None else self._executions()
        )
        if runtime_context is None:
            runtime_contexts: dict[str, RuntimeDashboardContext] = {}
        else:
            runtime_contexts = {runtime_context.ticker: runtime_context}
        for execution in runtime_executions:
            execution_ticker = execution.source_message.ticker
            context = runtime_contexts.get(execution_ticker)
            if context is None:
                context = self._runtime_context(execution_ticker)
                runtime_contexts[execution_ticker] = context
            status = _runtime_execution_status(execution, context)
            occurred_at = execution.updated_at or execution.created_at
            event_id = (
                f"rt_{execution.execution_id}_{int(_aware(occurred_at).timestamp())}"
            )
            events.append(
                {
                    "event_id": event_id,
                    "event_type": (
                        "runtime.execution.failed"
                        if status == "failed"
                        else "runtime.execution.updated"
                    ),
                    "ticker": execution_ticker,
                    "occurred_at": _dt(occurred_at),
                    "payload": {
                        "execution_id": execution.execution_id,
                        "source_message_id": execution.source_message.source_message_id,
                        "status": status,
                        "final_route": _runtime_final_route(execution, context),
                        "exception_types": _runtime_exception_types(execution, context),
                    },
                }
            )
        for audit_event in self.dashboard_api.scheduler.repository.list_audit_events(
            ticker=normalized,
            limit=READ_AGGREGATION_LIMIT,
        ):
            if audit_event.event_type not in {
                REVENUE_AUDIT_EVENT_TYPE,
                COST_AUDIT_EVENT_TYPE,
            }:
                continue
            events.append(_dashboard_event_from_scheduler_audit(audit_event))
        if normalized is not None:
            events.append(self._cost_status_event(normalized))
        if requested_types:
            events = [event for event in events if event["event_type"] in requested_types]
        events = sorted(events, key=lambda event: str(event.get("occurred_at") or ""))
        if last_event_id:
            events = _events_after(events, last_event_id)
        return events

    def _states(self) -> list[TickerRunState]:
        return self.dashboard_api.list_tickers().tickers

    def _blackboard(self) -> BlackboardService | None:
        provider = self.dashboard_api.scheduler.document_provider
        blackboard = getattr(provider, "blackboard", None)
        return blackboard if isinstance(blackboard, BlackboardService) else None

    def _blackboard_runs(
        self,
        ticker: str,
        *,
        limit: int = DOCUMENT_HISTORY_LIMIT,
    ) -> list[BlackboardRun]:
        blackboard = self._blackboard()
        if blackboard is None:
            return []
        return blackboard.list_runs_by_ticker(ticker, limit=limit)

    def _current_document_run(
        self,
        ticker: str,
        document_types: list[str],
    ) -> tuple[BlackboardRun, list[JsonObject]] | None:
        for run in self._blackboard_runs(ticker):
            documents = [
                document
                for document_type in document_types
                if (
                    document := _assemble_dashboard_document(
                        run,
                        document_type,
                        version_status="current",
                        include_raw=True,
                    )
                )
                is not None
            ]
            if documents:
                return run, documents
        return None

    def _versioned_documents(
        self,
        ticker: str,
        document_type: str,
        *,
        include_raw: bool,
    ) -> list[tuple[JsonObject, JsonObject]]:
        rows: list[tuple[BlackboardRun, JsonObject]] = []
        for run in self._blackboard_runs(ticker):
            document = _assemble_dashboard_document(
                run,
                document_type,
                version_status="historical",
                include_raw=include_raw,
            )
            if document is not None:
                rows.append((run, document))
        versioned: list[tuple[JsonObject, JsonObject]] = []
        for index, (run, document) in enumerate(rows):
            version_status = "current" if index == 0 else "historical"
            document["version_status"] = version_status
            version = {
                "version_id": _version_id(run.run_id, document_type, str(document["document_id"])),
                "document_id": document["document_id"],
                "document_type": document_type,
                "generated_at": document["generated_at"],
                "updated_at": document["updated_at"],
                "version_status": version_status,
                "summary": _document_summary(document),
            }
            versioned.append((version, document))
        return versioned

    def _known_event_items(self, ticker: str) -> list[JsonObject]:
        by_id: dict[str, JsonObject] = {}
        run = _first_run_with_documents(
            self._blackboard_runs(ticker),
            [DocumentType.KNOWN_EVENTS],
        )
        if run is not None:
            for document in _model_documents(
                run,
                DocumentType.KNOWN_EVENTS,
                KnownEventsDocument,
            ):
                for event in document.events:
                    by_id[event.event_id] = _known_event_item_from_document(
                        event,
                        document=document,
                    )
        for event in self.dashboard_api.scheduler.runtime_service.repository.list_known_events(
            ticker=ticker
        ):
            by_id[event.event_id] = _known_event_item_from_runtime(event)
        return list(by_id.values())

    def _policy_items(self, ticker: str) -> list[JsonObject]:
        run = _first_run_with_documents(
            self._blackboard_runs(ticker),
            [DocumentType.MONITORING_POLICY],
        )
        if run is None:
            return []
        items: list[JsonObject] = []
        for document in _model_documents(
            run,
            DocumentType.MONITORING_POLICY,
            MonitoringPolicyDocument,
        ):
            policies = (
                document.policies
                or [
                    *document.direct_trade_rules,
                    *document.push_to_agent_rules,
                    *document.cache_rules,
                ]
            )
            for policy in policies:
                items.append(_policy_item(policy, document=document))
        return items

    def _ticker_card(
        self,
        state: TickerRunState,
        *,
        target_date: date,
        zone: ZoneInfo,
    ) -> JsonObject:
        messages = self._messages(state.ticker)
        events = self._events(state.ticker)
        executions = self._executions(state.ticker)
        trades = self._trading_records(state.ticker)
        exceptions = self._exceptions(state.ticker)
        last_message_at = _latest_dt(
            [
                *(message.normalized_at for message in messages),
                *(event.event_time for event in events),
            ]
        )
        last_worker_processed_at = _latest_dt(execution.created_at for execution in executions)
        today_message_count = _count_on_day(
            (message.normalized_at for message in messages),
            target_date=target_date,
            zone=zone,
        )
        today_dtc_count = _count_on_day(
            (record.created_at for record in trades),
            target_date=target_date,
            zone=zone,
        )
        health = _health_with_exceptions(
            state.health,
            exceptions,
            target_date=target_date,
            zone=zone,
        )
        return {
            "ticker": state.ticker,
            "status": state.status.value,
            "status_label": _status_label(state.status),
            "health": health.value,
            "session_phase": state.session_phase.value,
            "monitor_mode": _state_monitor_mode(state),
            "started_at": _dt(state.started_at),
            "updated_at": _dt(state.updated_at),
            "last_message_at": _dt(last_message_at),
            "last_worker_processed_at": _dt(last_worker_processed_at),
            "today_dtc_count": today_dtc_count,
            "today_cost_usd": None,
            "last_error": state.last_error,
            "startup_progress": _startup_progress_payload(state),
            "_today_message_count": today_message_count,
        }

    def _ticker_detail(
        self,
        detail: TickerRunDetail,
        *,
        target_date: date,
        zone: ZoneInfo,
    ) -> JsonObject:
        state = detail.state
        return {
            "ticker": state.ticker,
            "state": {
                "status": state.status.value,
                "health": state.health.value,
                "session_phase": state.session_phase.value,
                "monitor_mode": _state_monitor_mode(state),
                "document_run_id": state.document_run_id,
                "last_error": state.last_error,
            },
            "document_status": {
                "usable": detail.document_status.usable,
                "stale": detail.document_status.stale,
                "availability": (
                    "available"
                    if detail.document_status.usable
                    else "missing"
                    if detail.document_status.missing_document_types
                    else "invalid"
                ),
                "blackboard_run_id": detail.document_status.blackboard_run_id,
                "missing_document_types": [
                    item.value for item in detail.document_status.missing_document_types
                ],
                "applied_config_version": detail.document_status.applied_config_version,
                "checked_at": _dt(detail.document_status.checked_at),
            },
            "message_bus_status": {
                "pending_event_count": detail.message_bus_status.pending_event_count,
                "recent_event_count": detail.message_bus_status.recent_event_count,
                "recent_message_count": detail.message_bus_status.recent_message_count,
                "configured_source_count": len(detail.message_bus_status.configured_sources),
                "last_success_at": _dt(detail.message_bus_status.last_success_at),
                "last_error_at": _dt(detail.message_bus_status.last_error_at),
                "last_error_message": detail.message_bus_status.last_error_message,
            },
            "runtime_status": {
                "pending_event_count": detail.runtime_status.pending_event_count,
                "consumed_event_count": detail.runtime_status.consumed_event_count,
                "runtime_execution_count": detail.runtime_status.runtime_execution_count,
                "exception_count": detail.runtime_status.exception_count,
                "last_execution_at": _dt(detail.runtime_status.last_execution_at),
            },
            "audit_summary": {
                "today_dtc_count": _count_on_day(
                    (record.created_at for record in self._trading_records(state.ticker)),
                    target_date=target_date,
                    zone=zone,
                ),
                "today_revenue_audit_status": "not_started",
                "today_cost_audit_status": "missing",
            },
        }

    def _operation_result(
        self,
        operation: str,
        detail: TickerRunDetail,
        *,
        monitor_mode: str | None = None,
    ) -> JsonObject:
        state = detail.state
        status = "blocked" if state.status is TickerRunStatus.BLOCKED else "accepted"
        return {
            "operation": operation,
            "status": status,
            "ticker": state.ticker,
            "ticker_state": {
                "status": state.status.value,
                "health": state.health.value,
                "monitor_mode": monitor_mode or _state_monitor_mode(state),
            },
            "audit_id": detail.audit_events[0].audit_id if detail.audit_events else None,
        }

    def _system_status(self, states: list[TickerRunState]) -> JsonObject:
        if not states:
            message_bus_status = RuntimeHealth.UNKNOWN
        elif any(state.health is RuntimeHealth.BLOCKED for state in states):
            message_bus_status = RuntimeHealth.BLOCKED
        elif any(
            state.health is RuntimeHealth.DEGRADED or state.last_error for state in states
        ):
            message_bus_status = RuntimeHealth.DEGRADED
        else:
            message_bus_status = RuntimeHealth.NORMAL
        return {
            "container_status": RuntimeHealth.NORMAL.value,
            "dashboard_api_status": RuntimeHealth.NORMAL.value,
            "message_bus_status": message_bus_status.value,
            "status_color": _status_color(message_bus_status),
        }

    def _messages(self, ticker: str | None = None) -> list[StandardMessage]:
        return self.dashboard_api.scheduler.monitoring_service.recent_messages(
            ticker=ticker,
            limit=READ_AGGREGATION_LIMIT,
        )

    def _events(self, ticker: str | None = None) -> list[EventStreamItem]:
        return self.dashboard_api.scheduler.monitoring_service.recent_events(
            ticker=ticker,
            limit=READ_AGGREGATION_LIMIT,
        )

    def _executions(self, ticker: str | None = None) -> list[RuntimeExecutionRecord]:
        return self.dashboard_api.scheduler.runtime_service.repository.list_executions(
            ticker=ticker
        )

    def _trading_records(self, ticker: str | None = None) -> list[TradingRecord]:
        return self.dashboard_api.scheduler.runtime_service.repository.list_trading_records(
            ticker=ticker
        )

    def _cost_records(self, ticker: str) -> list[JsonObject]:
        records: list[JsonObject] = []
        for execution in self._executions(ticker):
            records.extend(_cost_records_from_execution(execution))
        return records

    def _latest_scheduler_audit_event(
        self,
        ticker: str,
        event_type: str,
    ) -> RuntimeAuditEvent | None:
        for event in self.dashboard_api.scheduler.repository.list_audit_events(
            ticker=ticker,
            limit=READ_AGGREGATION_LIMIT,
        ):
            if event.event_type == event_type:
                return event
        return None

    def _cost_status_event(self, ticker: str) -> JsonObject:
        records = self._cost_records(ticker)
        status = _cost_audit_status(records)
        occurred_at = _latest_dt(
            _parse_dt_text(record.get("time")) for record in records
        ) or datetime.now(UTC)
        return {
            "event_id": f"audit_cost_status_{ticker}_{int(_aware(occurred_at).timestamp())}",
            "event_type": COST_AUDIT_EVENT_TYPE,
            "ticker": ticker,
            "occurred_at": _dt(occurred_at),
            "payload": {
                "ticker": ticker,
                "status": status,
                "cost_record_count": len(records),
                "missing_capabilities": (
                    []
                    if status == "completed"
                    else ["model_pricing_table", "unified_model_usage_persistence"]
                ),
            },
        }

    def _exceptions(self, ticker: str | None = None) -> list[ExecutionExceptionLog]:
        return self.dashboard_api.scheduler.runtime_service.repository.list_exceptions(
            ticker=ticker
        )

    def _runtime_context(self, ticker: str) -> RuntimeDashboardContext:
        normalized = _ticker(ticker)
        repository = self.dashboard_api.scheduler.runtime_service.repository
        executions = sorted(
            self._executions(normalized),
            key=lambda execution: _aware(execution.created_at),
            reverse=True,
        )
        messages_by_id = {
            message.standard_message_id: message for message in self._messages(normalized)
        }
        return RuntimeDashboardContext(
            ticker=normalized,
            executions=executions,
            messages_by_id=messages_by_id,
            exceptions_by_source=_group_by_source(
                repository.list_exceptions(ticker=normalized),
            ),
            trading_records_by_source=_group_by_source(
                repository.list_trading_records(ticker=normalized),
            ),
            ingest_queue_by_source=_group_by_source(
                repository.list_ingest_queue(ticker=normalized),
            ),
            archive_by_source=_group_by_source(
                repository.list_archive(ticker=normalized),
            ),
            known_event_patch_by_source=_group_by_source(
                repository.list_known_events_patch_logs(ticker=normalized),
            ),
            objections_by_source=_group_by_source(
                repository.list_objections(ticker=normalized),
            ),
        )

    def _all_exceptions(self) -> list[ExecutionExceptionLog]:
        return self._exceptions()


class DashboardRealServiceError(ValueError):
    """Base class for real Dashboard adapter validation errors."""


class TickerAlreadyRunning(DashboardRealServiceError):
    def __init__(self, ticker: str) -> None:
        super().__init__(ticker)
        self.ticker = ticker


class UnsupportedHistoryDelete(DashboardRealServiceError):
    def __init__(self, ticker: str) -> None:
        super().__init__(ticker)
        self.ticker = ticker


class TickerNotFound(DashboardRealServiceError):
    def __init__(self, ticker: str) -> None:
        super().__init__(ticker)
        self.ticker = ticker


class UnsupportedMonitorMode(DashboardRealServiceError):
    def __init__(self, monitor_mode: str) -> None:
        super().__init__(monitor_mode)
        self.monitor_mode = monitor_mode


class UnsupportedDocumentType(DashboardRealServiceError):
    def __init__(self, document_type: str) -> None:
        super().__init__(document_type)
        self.document_type = document_type


class DocumentVersionNotFound(DashboardRealServiceError):
    def __init__(self, ticker: str, document_type: str, version_id: str) -> None:
        super().__init__(version_id)
        self.ticker = ticker
        self.document_type = document_type
        self.version_id = version_id


class UnsupportedMessageSource(DashboardRealServiceError):
    def __init__(self, source_id: str) -> None:
        super().__init__(source_id)
        self.source_id = source_id


class InvalidMessageBusPatch(DashboardRealServiceError):
    def __init__(self, message: str, *, details: JsonObject | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class UnsupportedRuntimeNode(DashboardRealServiceError):
    def __init__(self, node_id: str) -> None:
        super().__init__(node_id)
        self.node_id = node_id


class RuntimeExecutionNotFound(DashboardRealServiceError):
    def __init__(self, ticker: str, execution_id: str) -> None:
        super().__init__(execution_id)
        self.ticker = ticker
        self.execution_id = execution_id


class InvalidAuditParams(DashboardRealServiceError):
    def __init__(self, message: str, *, details: JsonObject | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


def _ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("ticker is required.")
    return normalized


def _monitor_mode(value: str | None) -> str:
    resolved = (value or DEFAULT_MONITOR_MODE).strip()
    if resolved not in ENABLED_MONITOR_MODES:
        raise UnsupportedMonitorMode(resolved)
    return resolved


def _state_monitor_mode(state: TickerRunState | None) -> str:
    if state is None:
        return DEFAULT_MONITOR_MODE
    state_mode = getattr(state, "monitor_mode", None)
    if isinstance(state_mode, MonitorMode):
        return state_mode.value
    if isinstance(state_mode, str) and state_mode.strip():
        return state_mode
    value = state.metadata.get("monitor_mode")
    return str(value) if isinstance(value, str) and value.strip() else DEFAULT_MONITOR_MODE


def _startup_progress_payload(state: TickerRunState) -> JsonObject | None:
    raw = state.metadata.get("startup_progress")
    if not isinstance(raw, dict) or raw.get("visible") is not True:
        return None
    steps: list[JsonObject] = []
    for item in raw.get("steps") or []:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("step_id") or "").strip()
        label = str(item.get("label") or "").strip()
        status = str(item.get("status") or "pending").strip()
        if not step_id or not label:
            continue
        progress = item.get("progress")
        steps.append(
            {
                "step_id": step_id,
                "label": label,
                "status": status,
                "progress": int(progress) if isinstance(progress, int | float) else 0,
            }
        )
    if not steps:
        return None
    return {
        "status": str(raw.get("status") or "running"),
        "status_label": str(raw.get("status_label") or "启动中"),
        "current_step_id": raw.get("current_step_id"),
        "retryable": bool(raw.get("retryable")),
        "message": raw.get("message") if isinstance(raw.get("message"), str) else None,
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
        "steps": steps,
    }


def _source_id(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise InvalidMessageBusPatch("source_id is required.", details={"field": "source_id"})
    return normalized


def _message_item(
    message: StandardMessage,
    *,
    runtime: RuntimeExecutionRecord | None,
    event: EventStreamItem | None,
    source_label: str | None,
) -> JsonObject:
    body = message.body
    title = message.title or _truncate(body or message.standard_message_id, 120)
    summary = _message_summary(message)
    return {
        "message_id": message.standard_message_id,
        "raw_message_id": message.raw_message_id,
        "ticker": message.ticker,
        "source_id": message.source_id,
        "source_label": source_label or message.source_id,
        "source_type": str(message.source_type),
        "collected_at": _dt(message.collected_at),
        "published_at": _dt(message.published_at),
        "title": title,
        "summary": summary,
        "body": body,
        "url": message.url,
        "processing_status": _message_processing_status(runtime=runtime, event=event),
        "runtime_execution_id": runtime.execution_id if runtime is not None else None,
    }


def _message_summary(message: StandardMessage) -> str | None:
    for key in ("summary", "description", "excerpt"):
        value = message.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate(value, 240)
    if message.body:
        return _truncate(message.body, 240)
    return None


def _message_processing_status(
    *,
    runtime: RuntimeExecutionRecord | None,
    event: EventStreamItem | None,
) -> str:
    if runtime is not None:
        if runtime.message_statuses:
            return runtime.message_statuses[-1]
        return runtime.status
    if event is not None and event.consumed:
        return "event_consumed"
    return "message_bus_pending"


def _message_source_config(
    ticker: str,
    source_id: str,
    *,
    source: Any,
    binding: TickerSourceBinding | None,
    poll_state: PollState | None,
) -> JsonObject:
    parameters = binding.parameters.model_dump(mode="json") if binding is not None else {}
    binding_enabled = binding.enabled if binding is not None else False
    agent_fields = ["enabled", *parameter_schema_for_source(source_id).keys()]
    return {
        "source_id": source.source_id,
        "display_name": source.display_name,
        "source_type": str(source.source_type),
        "interface_type": str(source.interface_type),
        "enabled": bool(source.enabled and binding_enabled),
        "poll_interval_seconds": source.poll_interval_seconds,
        "parameter_schema": _parameter_schema(source_id),
        "binding": {
            "binding_id": (
                binding.binding_id
                if binding is not None
                else binding_id_for(ticker, source_id)
            ),
            "ticker": ticker,
            "source_id": source_id,
            "enabled": binding_enabled,
            "parameters": parameters,
        },
        "poll_state": _poll_state_payload(
            source_id=source_id,
            ticker=ticker,
            binding=binding,
            state=poll_state,
            source_enabled=source.enabled,
        ),
        "user_only_fields": _message_source_user_only_fields(source_id),
        "agent_mutable_fields": agent_fields,
    }


def _poll_state_payload(
    *,
    source_id: str,
    ticker: str,
    binding: TickerSourceBinding | None,
    state: PollState | None,
    source_enabled: bool,
) -> JsonObject:
    if binding is None or not binding.enabled or not source_enabled:
        status = "disabled"
    elif state is None:
        status = "never_polled"
    else:
        status = str(state.status)
    return {
        "binding_id": (
            binding.binding_id
            if binding is not None
            else binding_id_for(ticker, source_id)
        ),
        "source_id": source_id,
        "ticker": ticker,
        "status": status,
        "last_success_at": _dt(state.last_success_at) if state is not None else None,
        "last_error_message": state.last_error_message if state is not None else None,
        "last_poll_new_message_count": state.last_event_count if state is not None else None,
        "last_latency_ms": state.last_latency_ms if state is not None else None,
    }


def _parameter_schema(source_id: str) -> list[JsonObject]:
    return [
        {
            "key": key,
            "label": key,
            "max_items": max_items,
            "value_type": "string_list",
        }
        for key, max_items in parameter_schema_for_source(source_id).items()
    ]


def _message_source_user_only_fields(source_id: str) -> list[str]:
    fields = ["poll_interval_seconds", "global_source_enabled"]
    if source_id == "stocktwits_messages":
        fields.extend(
            [
                "target_cadence_seconds",
                "hot_cadence_seconds",
                "page_size",
                "max_pages_per_crawl",
                "hot_message_threshold",
                "hot_cooldown_successes",
                "bootstrap_event_policy",
                "current_mode",
            ]
        )
    return fields


def _message_source_health(source: JsonObject) -> str:
    poll_state = source.get("poll_state")
    if not isinstance(poll_state, dict):
        return "unknown"
    if not source.get("enabled") or poll_state.get("status") == "disabled":
        return "disabled"
    status = str(poll_state.get("status") or "unknown")
    if status == "succeeded":
        return "normal"
    return status


def _message_bus_parameters(
    source_id: str,
    payload: JsonObject,
) -> tuple[MonitoringParameters, bool]:
    allowed = set(parameter_schema_for_source(source_id))
    accepted_top_level = {"enabled", "reason", *allowed}
    unsupported = [
        key
        for key, value in payload.items()
        if key not in accepted_top_level and value not in (None, "", [])
    ]
    if unsupported:
        raise InvalidMessageBusPatch(
            "Unsupported Message Bus config field.",
            details={"fields": unsupported, "supported_fields": sorted(accepted_top_level)},
        )
    touched = any(key in payload for key in allowed)
    data: JsonObject = {}
    for key in allowed:
        if key in payload:
            data[key] = _coerce_string_list(payload[key], field=key)
    parameters = MonitoringParameters.model_validate(data)
    for key, max_items in parameter_schema_for_source(source_id).items():
        values = getattr(parameters, key)
        if len(values) > max_items:
            raise InvalidMessageBusPatch(
                f"{source_id}.{key} supports at most {max_items} item(s).",
                details={"source_id": source_id, "field": key, "max_items": max_items},
            )
    return parameters, touched


def _coerce_string_list(value: object, *, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.splitlines() if item.strip()]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise InvalidMessageBusPatch(
                    f"{field} must contain only strings.",
                    details={"field": field},
                )
            if item.strip():
                items.append(item.strip())
        return items
    raise InvalidMessageBusPatch(
        f"{field} must be a string list.",
        details={"field": field},
    )


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    raise InvalidMessageBusPatch("enabled must be boolean.", details={"field": "enabled"})


def _optional_text_value(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _ticker_uptime_seconds(state: TickerRunState | None) -> int:
    if state is None or state.started_at is None:
        return 0
    return max(0, int((datetime.now(UTC) - _aware(state.started_at)).total_seconds()))


def _media_enrichment_success_rate(messages: list[StandardMessage]) -> float | None:
    records: list[JsonObject] = []
    for message in messages:
        value = message.metadata.get("media_enrichment")
        if isinstance(value, dict):
            records.append(value)
    if not records:
        return None
    succeeded = sum(1 for record in records if record.get("succeeded") is True)
    return succeeded / len(records)


def _sort_messages(items: list[JsonObject], sort: str | None) -> list[JsonObject]:
    key = (sort or "-collected_at").strip()
    reverse = key.startswith("-")
    field = key.removeprefix("-")
    if field not in {"collected_at", "published_at", "source_id", "processing_status"}:
        field = "collected_at"
        reverse = True
    return sorted(items, key=lambda item: str(item.get(field) or ""), reverse=reverse)


def _csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def _events_after(events: list[JsonObject], last_event_id: str) -> list[JsonObject]:
    seen = False
    rows: list[JsonObject] = []
    for event in events:
        if seen:
            rows.append(event)
        if event.get("event_id") == last_event_id:
            seen = True
    return rows if seen else events


def _runtime_node_id(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in RUNTIME_NODE_IDS:
        raise UnsupportedRuntimeNode(normalized or value)
    return normalized


def _group_by_source(items: Iterable[T]) -> dict[str, list[T]]:
    grouped: dict[str, list[T]] = {}
    for item in items:
        source_message_id = getattr(item, "source_message_id", None)
        if isinstance(source_message_id, str) and source_message_id:
            grouped.setdefault(source_message_id, []).append(item)
    return grouped


def _runtime_graph_node(
    context: RuntimeDashboardContext,
    *,
    node_id: str,
    label: str,
) -> JsonObject:
    in_count = _runtime_node_in_count(context, node_id)
    out_count = _runtime_node_out_count(context, node_id)
    failed_count = _runtime_node_failed_count(context, node_id)
    return {
        "node_id": node_id,
        "label": label,
        "status": _runtime_health(
            in_count=in_count,
            out_count=out_count,
            failed_count=failed_count,
        ),
        "in_count": in_count,
        "out_count": out_count,
        "failed_count": failed_count,
    }


def _runtime_graph_edges(context: RuntimeDashboardContext) -> list[JsonObject]:
    direct_trading = sum(
        1
        for execution in context.executions
        if _runtime_route_value(execution) == "trading_record"
    )
    route_to_o3 = sum(
        1
        for execution in context.executions
        if _runtime_route_value(execution) in {"o3", "a2"}
        or _runtime_execution_in_node(execution, "o3", context)
    )
    return [
        _runtime_edge(
            "message_bus_to_w1",
            "message_bus",
            "w1",
            "W1 novelty input",
            _runtime_node_count(context.executions, "w1", context=context),
        ),
        _runtime_edge(
            "message_bus_to_w2",
            "message_bus",
            "w2",
            "W2 policy input",
            _runtime_node_count(context.executions, "w2", context=context),
        ),
        _runtime_edge(
            "w1_to_route_engine",
            "w1",
            "route_engine",
            "novelty result",
            sum(1 for execution in context.executions if execution.w1_result is not None),
        ),
        _runtime_edge(
            "w2_to_route_engine",
            "w2",
            "route_engine",
            "policy result",
            sum(1 for execution in context.executions if execution.w2_result is not None),
        ),
        _runtime_edge(
            "route_engine_to_trading",
            "route_engine",
            "trading_records",
            "direct trade route",
            direct_trading,
        ),
        _runtime_edge(
            "route_engine_to_o3",
            "route_engine",
            "o3",
            "O3 escalation",
            route_to_o3,
        ),
        _runtime_edge(
            "route_engine_to_archive",
            "route_engine",
            "archive",
            "archive route",
            _runtime_route_count(context, "archive"),
        ),
        _runtime_edge(
            "route_engine_to_ingest_queue",
            "route_engine",
            "ingest_queue",
            "review queue route",
            _runtime_route_count(context, "ingest_queue"),
        ),
        _runtime_edge(
            "route_engine_to_exception_queue",
            "route_engine",
            "exception_queue",
            "runtime exception",
            _runtime_route_count(context, "failed_with_exception"),
        ),
        _runtime_edge(
            "o3_to_trading",
            "o3",
            "trading_records",
            "O3 trade action",
            _runtime_o3_action_count(context, "trading_record"),
        ),
        _runtime_edge(
            "o3_to_objection",
            "o3",
            "objection",
            "O3 objection",
            _runtime_o3_action_count(context, "objection")
            + _runtime_o3_action_count(context, "objection_note"),
        ),
        _runtime_edge(
            "o3_to_known_event_patch",
            "o3",
            "known_event_patch",
            "known event update",
            sum(len(items) for items in context.known_event_patch_by_source.values()),
        ),
        _runtime_edge(
            "o3_to_ingest_queue",
            "o3",
            "ingest_queue",
            "O3 review queue",
            _runtime_o3_action_count(context, "ingest_queue"),
        ),
        _runtime_edge(
            "o3_to_exception_queue",
            "o3",
            "exception_queue",
            "O3 exception",
            _runtime_node_failed_count(context, "o3"),
        ),
    ]


def _runtime_edge(
    edge_id: str,
    from_node: str,
    to_node: str,
    label: str,
    count: int,
) -> JsonObject:
    return {
        "edge_id": edge_id,
        "from": from_node,
        "to": to_node,
        "label": label,
        "count": count,
    }


def _runtime_node_in_count(context: RuntimeDashboardContext, node_id: str) -> int:
    if node_id == "message_bus":
        return len(context.executions)
    if node_id in {"w1", "w2", "o3", "route_engine"}:
        return _runtime_node_count(context.executions, node_id, context=context)
    if node_id == "trading_records":
        return sum(len(items) for items in context.trading_records_by_source.values())
    if node_id == "exception_queue":
        return sum(len(items) for items in context.exceptions_by_source.values())
    if node_id == "objection":
        return sum(len(items) for items in context.objections_by_source.values())
    if node_id == "known_event_patch":
        return sum(len(items) for items in context.known_event_patch_by_source.values())
    if node_id == "archive":
        return sum(len(items) for items in context.archive_by_source.values())
    if node_id == "ingest_queue":
        return sum(len(items) for items in context.ingest_queue_by_source.values())
    return 0


def _runtime_node_out_count(context: RuntimeDashboardContext, node_id: str) -> int:
    if node_id == "message_bus":
        return _runtime_node_count(context.executions, "w1", context=context)
    if node_id in {"w1", "w2"}:
        return _runtime_node_count(context.executions, "route_engine", context=context)
    if node_id == "route_engine":
        return len(context.executions)
    if node_id == "o3":
        return sum(
            1
            for execution in context.executions
            if _runtime_route_value(execution) in {"o3", "a2"}
            and _runtime_final_route(execution, context) != "o3"
        )
    return 0


def _runtime_node_failed_count(context: RuntimeDashboardContext, node_id: str) -> int:
    if node_id == "exception_queue":
        return sum(len(items) for items in context.exceptions_by_source.values())
    if node_id in {"w1", "w2", "o3", "route_engine"}:
        failed_sources: set[str] = set()
        for execution in context.executions:
            source_id = execution.source_message.source_message_id
            trace = _runtime_trace(execution, node_id)
            if trace is not None and trace.status.lower() in {"failed", "error"}:
                failed_sources.add(source_id)
            if node_id == "o3" and any(
                exception.node.strip().lower() == "o3"
                for exception in context.exceptions_by_source.get(source_id, [])
            ):
                failed_sources.add(source_id)
            if (
                node_id == "route_engine"
                and _runtime_route_value(execution) == "failed_with_exception"
            ):
                failed_sources.add(source_id)
        return len(failed_sources)
    return 0


def _runtime_health(*, in_count: int, out_count: int, failed_count: int) -> str:
    if in_count == 0 and out_count == 0 and failed_count == 0:
        return "unknown"
    if failed_count and failed_count >= max(in_count, out_count, 1):
        return "failed"
    if failed_count:
        return "degraded"
    return "normal"


def _runtime_node_records(
    context: RuntimeDashboardContext,
    node_id: str,
) -> list[JsonObject]:
    records: list[JsonObject] = []
    executions_by_source = _runtime_executions_by_source(context)
    if node_id in {"message_bus", "w1", "w2", "route_engine", "o3"}:
        for execution in context.executions:
            if _runtime_execution_in_node(execution, node_id, context):
                records.append(_runtime_execution_node_record(execution, context, node_id))
    elif node_id == "trading_records":
        for trading_records in context.trading_records_by_source.values():
            for trading_record in trading_records:
                records.append(
                    _runtime_side_effect_record(
                        trading_record.source_message_id,
                        execution=executions_by_source.get(trading_record.source_message_id),
                        status=str(trading_record.status),
                        input_summary="Runtime route entered trading records.",
                        output_summary=f"trade_intent={trading_record.trade_intent.side}",
                        created_at=trading_record.created_at,
                    )
                )
    elif node_id == "exception_queue":
        for exceptions in context.exceptions_by_source.values():
            for exception in exceptions:
                records.append(
                    _runtime_side_effect_record(
                        exception.source_message_id,
                        execution=executions_by_source.get(exception.source_message_id),
                        status="failed",
                        input_summary=f"{exception.node} runtime exception.",
                        output_summary=(
                            f"{exception.exception_type}: "
                            f"{_truncate(exception.message, 120)}"
                        ),
                        created_at=exception.created_at,
                    )
                )
    elif node_id == "objection":
        for objections in context.objections_by_source.values():
            for objection in objections:
                records.append(
                    _runtime_side_effect_record(
                        objection.source_message_id,
                        execution=executions_by_source.get(objection.source_message_id),
                        status=str(objection.objection_type),
                        input_summary=f"target={objection.blackboard_target}",
                        output_summary=_truncate(objection.reason, 160),
                        created_at=objection.created_at,
                    )
                )
    elif node_id == "known_event_patch":
        for patch_logs in context.known_event_patch_by_source.values():
            for patch_log in patch_logs:
                records.append(
                    _runtime_side_effect_record(
                        patch_log.source_message_id,
                        execution=executions_by_source.get(patch_log.source_message_id),
                        status="completed",
                        input_summary=patch_log.source_ref,
                        output_summary=_truncate(patch_log.patch.core_fact, 160),
                        created_at=patch_log.changed_at,
                    )
                )
    elif node_id == "archive":
        for archive_items in context.archive_by_source.values():
            for archive_item in archive_items:
                records.append(
                    _runtime_side_effect_record(
                        archive_item.source_message_id,
                        execution=executions_by_source.get(archive_item.source_message_id),
                        status="completed",
                        input_summary="Runtime route entered archive.",
                        output_summary=_truncate(archive_item.reason, 160),
                        created_at=archive_item.created_at,
                    )
                )
    elif node_id == "ingest_queue":
        for queue_items in context.ingest_queue_by_source.values():
            for queue_item in queue_items:
                records.append(
                    _runtime_side_effect_record(
                        queue_item.source_message_id,
                        execution=executions_by_source.get(queue_item.source_message_id),
                        status="pending",
                        input_summary=f"queue_type={queue_item.queue_type}",
                        output_summary=_truncate(queue_item.reason, 160),
                        created_at=queue_item.created_at,
                    )
                )
    return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _runtime_node_detail(
    context: RuntimeDashboardContext,
    node_id: str,
    *,
    records: list[JsonObject],
    target_date: date,
    zone: ZoneInfo,
) -> JsonObject:
    today_records = [
        record
        for record in records
        if _is_on_day(_parse_dt_text(record.get("created_at")), target_date=target_date, zone=zone)
    ]
    durations = [
        int(record["duration_ms"])
        for record in today_records
        if isinstance(record.get("duration_ms"), int)
    ]
    return {
        "node_id": node_id,
        "label": _runtime_node_label(node_id),
        "status": _runtime_graph_node(
            context,
            node_id=node_id,
            label=_runtime_node_label(node_id),
        )["status"],
        "last_processed_at": records[0].get("created_at") if records else None,
        "today_count": len(today_records),
        "today_failed_count": sum(
            1
            for record in today_records
            if str(record.get("status") or "").lower() in {"failed", "error"}
        ),
        "avg_latency_ms": int(sum(durations) / len(durations)) if durations else None,
        "last_error": _runtime_node_last_error(context, node_id),
    }


def _runtime_execution_node_record(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
    node_id: str,
) -> JsonObject:
    trace = _runtime_trace(execution, node_id)
    return {
        "execution_id": execution.execution_id,
        "source_message_id": execution.source_message.source_message_id,
        "status": (
            _runtime_trace_status(trace)
            if trace is not None
            else _runtime_execution_status(execution, context)
        ),
        "input_summary": _runtime_node_input_summary(execution, context, node_id),
        "output_summary": _runtime_node_output_summary(execution, context, node_id),
        "duration_ms": trace.duration_ms if trace is not None else None,
        "created_at": _dt(trace.started_at if trace is not None else execution.created_at),
    }


def _runtime_side_effect_record(
    source_message_id: str,
    *,
    execution: RuntimeExecutionRecord | None,
    status: str,
    input_summary: str,
    output_summary: str,
    created_at: datetime,
) -> JsonObject:
    return {
        "execution_id": execution.execution_id if execution is not None else source_message_id,
        "source_message_id": source_message_id,
        "status": status,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "duration_ms": None,
        "created_at": _dt(created_at),
    }


def _runtime_execution_item(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
) -> JsonObject:
    return {
        "execution_id": execution.execution_id,
        "source_message_id": execution.source_message.source_message_id,
        "message_title": _runtime_message_title(execution, context),
        "ticker": execution.source_message.ticker,
        "source_type": str(execution.source_message.source_type),
        "final_route": _runtime_final_route(execution, context),
        "status": _runtime_execution_status(execution, context),
        "message_statuses": list(execution.message_statuses),
        "node_durations_ms": _runtime_node_durations(execution),
        "exception_types": _runtime_exception_types(execution, context),
        "created_at": _dt(execution.created_at),
    }


def _runtime_execution_detail(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
) -> JsonObject:
    route_decision = execution.route_decision.model_dump(mode="json")
    route_decision["final_route"] = _runtime_final_route(execution, context)
    source_message = execution.source_message.model_dump(mode="json")
    source_message["title"] = _runtime_message_title(execution, context)
    return {
        "execution_id": execution.execution_id,
        "source_message": source_message,
        "route_decision": route_decision,
        "w1_result": _runtime_dump_optional(execution.w1_result),
        "w2_result": _runtime_dump_optional(execution.w2_result),
        "a2_result": _runtime_dump_optional(execution.a2_result),
        "o3_result": _runtime_dump_optional(execution.o3_result),
        "node_traces": [trace.model_dump(mode="json") for trace in execution.node_traces],
        "exceptions": [
            exception.model_dump(mode="json")
            for exception in context.exceptions_by_source.get(
                execution.source_message.source_message_id,
                [],
            )
        ],
        "exception_ids": list(execution.exception_ids),
        "message_statuses": list(execution.message_statuses),
        "status": _runtime_execution_status(execution, context),
        "final_route": _runtime_final_route(execution, context),
        "node_durations_ms": _runtime_node_durations(execution),
        "created_at": _dt(execution.created_at),
        "updated_at": _dt(execution.updated_at),
    }


def _runtime_dump_optional(value: Any) -> JsonObject | None:
    if value is None:
        return None
    dumped = value.model_dump(mode="json")
    return dict(dumped)


def _runtime_execution_in_node(
    execution: RuntimeExecutionRecord,
    node_id: str,
    context: RuntimeDashboardContext,
) -> bool:
    source_id = execution.source_message.source_message_id
    if node_id == "message_bus":
        return True
    if node_id == "w1":
        return execution.w1_result is not None or _runtime_trace(execution, "w1") is not None
    if node_id == "w2":
        return execution.w2_result is not None or _runtime_trace(execution, "w2") is not None
    if node_id == "route_engine":
        return True
    if node_id == "o3":
        return (
            execution.o3_result is not None
            or _runtime_trace(execution, "o3") is not None
            or _runtime_route_value(execution) in {"o3", "a2"}
        )
    if node_id == "trading_records":
        return source_id in context.trading_records_by_source
    if node_id == "exception_queue":
        return source_id in context.exceptions_by_source
    if node_id == "objection":
        return source_id in context.objections_by_source
    if node_id == "known_event_patch":
        return source_id in context.known_event_patch_by_source
    if node_id == "archive":
        return source_id in context.archive_by_source
    if node_id == "ingest_queue":
        return source_id in context.ingest_queue_by_source
    return False


def _runtime_node_count(
    executions: Iterable[RuntimeExecutionRecord],
    node_id: str,
    *,
    context: RuntimeDashboardContext,
) -> int:
    return sum(
        1
        for execution in executions
        if _runtime_execution_in_node(execution, node_id, context)
    )


def _runtime_route_count(context: RuntimeDashboardContext, route: str) -> int:
    return sum(
        1
        for execution in context.executions
        if _runtime_final_route(execution, context) == route
    )


def _runtime_o3_action_count(context: RuntimeDashboardContext, action: str) -> int:
    count = 0
    for execution in context.executions:
        if _runtime_route_value(execution) not in {"o3", "a2"} and execution.o3_result is None:
            continue
        if _runtime_final_route(execution, context) == action:
            count += 1
    return count


def _runtime_route_value(execution: RuntimeExecutionRecord) -> str:
    return str(execution.route_decision.route).strip().lower()


def _runtime_final_route(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
) -> str:
    source_id = execution.source_message.source_message_id
    if (
        context.exceptions_by_source.get(source_id)
        or _runtime_route_value(execution) == "failed_with_exception"
    ):
        return "failed_with_exception"
    if context.trading_records_by_source.get(source_id):
        return "trading_record"
    if context.objections_by_source.get(source_id):
        objection = context.objections_by_source[source_id][0]
        return str(objection.objection_type)
    if context.ingest_queue_by_source.get(source_id):
        return "ingest_queue"
    if context.archive_by_source.get(source_id):
        return "archive"
    if execution.o3_result is not None:
        return str(execution.o3_result.primary_action)
    route = _runtime_route_value(execution)
    if route == "a2":
        return "o3"
    return route


def _runtime_execution_status(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
) -> str:
    source_id = execution.source_message.source_message_id
    raw_status = execution.status.strip().lower() if execution.status else "completed"
    if (
        context.exceptions_by_source.get(source_id)
        or _runtime_final_route(execution, context) == "failed_with_exception"
    ):
        return "failed"
    if raw_status in {"failed", "running", "completed"}:
        return raw_status
    if any("running" in status.lower() for status in execution.message_statuses):
        return "running"
    if any(trace.status.lower() in {"failed", "error"} for trace in execution.node_traces):
        return "failed"
    return "completed"


def _runtime_exception_types(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
) -> list[str]:
    source_id = execution.source_message.source_message_id
    types = [
        exception.exception_type
        for exception in context.exceptions_by_source.get(source_id, [])
    ]
    if types:
        return types
    return [
        trace.status
        for trace in execution.node_traces
        if trace.status.lower() in {"failed", "error"}
    ]


def _runtime_node_durations(execution: RuntimeExecutionRecord) -> dict[str, int]:
    durations: dict[str, int] = {}
    for trace in execution.node_traces:
        durations[_runtime_trace_node_label(trace.node)] = trace.duration_ms
    return durations


def _runtime_trace_node_label(value: str) -> str:
    normalized = value.strip()
    if normalized.lower() in {"w1", "w2", "o3"}:
        return normalized.upper()
    return normalized


def _runtime_trace(
    execution: RuntimeExecutionRecord,
    node_id: str,
) -> RuntimeNodeTrace | None:
    aliases = {
        "w1": {"w1"},
        "w2": {"w2"},
        "o3": {"o3"},
        "route_engine": {"route_engine", "router", "route"},
    }.get(node_id, {node_id})
    for trace in reversed(execution.node_traces):
        if trace.node.strip().lower() in aliases:
            return trace
    return None


def _runtime_trace_status(trace: RuntimeNodeTrace) -> str:
    status = trace.status.strip().lower()
    if status == "succeeded":
        return "completed"
    return status or "completed"


def _runtime_node_avg_latency(
    executions: Iterable[RuntimeExecutionRecord],
    node_id: str,
) -> int | None:
    durations = [
        trace.duration_ms
        for execution in executions
        if (trace := _runtime_trace(execution, node_id)) is not None
    ]
    return int(sum(durations) / len(durations)) if durations else None


def _runtime_avg_processing_latency(
    executions: Iterable[RuntimeExecutionRecord],
) -> int | None:
    totals = [
        sum(trace.duration_ms for trace in execution.node_traces)
        for execution in executions
        if execution.node_traces
    ]
    return int(sum(totals) / len(totals)) if totals else None


def _audit_period(value: str | None) -> str:
    resolved = (value or "today").strip().lower()
    if resolved not in AUDIT_PERIODS:
        raise InvalidAuditParams(
            "Unsupported audit period.",
            details={"period": resolved, "supported_periods": sorted(AUDIT_PERIODS)},
        )
    return resolved


def _audit_group_by(value: str | None) -> str:
    resolved = (value or "node").strip().lower()
    if resolved not in AUDIT_GROUP_BYS:
        raise InvalidAuditParams(
            "Unsupported audit group_by.",
            details={"group_by": resolved, "supported_group_by": sorted(AUDIT_GROUP_BYS)},
        )
    return resolved


def _audit_period_days(period: str) -> int:
    if period == "7d":
        return 7
    if period == "30d":
        return 30
    return 1


def _audit_period_dates(period: str, target_date: date) -> list[date]:
    days = _audit_period_days(period)
    start = target_date - timedelta(days=days - 1)
    return [start + timedelta(days=offset) for offset in range(days)]


def _in_audit_period(
    value: datetime | None,
    period: str,
    target_date: date,
    zone: ZoneInfo,
) -> bool:
    if value is None:
        return False
    item_date = _aware(value).astimezone(zone).date()
    dates = _audit_period_dates(period, target_date)
    return dates[0] <= item_date <= dates[-1]


def _trade_intent_audit_item(record: TradingRecord) -> JsonObject:
    return {
        "record_id": record.record_id,
        "time": _dt(record.created_at),
        "ticker": record.ticker,
        "trigger_message_id": record.source_message_id,
        "trigger_policy_id": record.matched_policy_code,
        "action": str(record.trade_intent.side),
        "theoretical_entry_price": None,
        "estimated_entry_price": None,
        "exit_price": None,
        "slippage_pct": None,
        "pnl_usd": None,
        "status": _trade_intent_audit_status(record),
    }


def _trade_intent_audit_status(record: TradingRecord) -> str:
    if record.exception_type:
        return "failed"
    if str(record.status).strip().lower() == "recorded_with_exception":
        return "failed"
    return "pending_audit"


def _revenue_audit_status(
    trade_intents: list[JsonObject],
    *,
    latest_event: RuntimeAuditEvent | None,
) -> str:
    if latest_event is not None:
        status = latest_event.payload.get("status")
        if status in {"not_started", "calculating", "completed", "failed"}:
            return str(status)
    if any(item.get("status") == "audited" for item in trade_intents):
        return "partial"
    return "not_started"


def _revenue_trend(
    records: list[TradingRecord],
    period: str,
    target_date: date,
    zone: ZoneInfo,
) -> list[JsonObject]:
    grouped: dict[date, list[TradingRecord]] = {}
    for record in records:
        grouped.setdefault(_aware(record.created_at).astimezone(zone).date(), []).append(record)
    return [
        {
            "date": item_date.isoformat(),
            "pnl_usd": None,
            "trade_intent_count": len(grouped.get(item_date, [])),
        }
        for item_date in _audit_period_dates(period, target_date)
    ]


def _win_rate(trade_intents: list[JsonObject]) -> float | None:
    audited = [
        item
        for item in trade_intents
        if item.get("status") == "audited" and isinstance(item.get("pnl_usd"), int | float)
    ]
    if not audited:
        return None
    wins = sum(1 for item in audited if float(item["pnl_usd"]) > 0)
    return wins / len(audited)


def _cost_records_from_execution(execution: RuntimeExecutionRecord) -> list[JsonObject]:
    records: list[JsonObject] = []
    for index, audit in enumerate(_extract_model_audits(execution), start=1):
        usage = _model_usage_payload(audit)
        input_tokens = _int_value(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or audit.get("input_tokens")
            or audit.get("prompt_tokens")
        )
        output_tokens = _int_value(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or audit.get("output_tokens")
            or audit.get("completion_tokens")
        )
        total_tokens = _int_value(
            usage.get("total_tokens")
            or audit.get("total_tokens")
        )
        resolved_input = input_tokens or 0
        resolved_output = output_tokens or 0
        resolved_total = (
            total_tokens if total_tokens is not None else resolved_input + resolved_output
        )
        retry_count = _int_value(audit.get("retry_count"))
        metadata = _dict_value(audit.get("metadata"))
        if retry_count is None:
            retry_count = _int_value(metadata.get("retry_count"))
        time_value = _model_audit_time(audit, execution)
        cost_record_id = _text_value(audit.get("cost_record_id")) or (
            f"cost_{execution.execution_id}_{index}"
        )
        records.append(
            {
                "cost_record_id": cost_record_id,
                "time": _dt(time_value),
                "ticker": execution.source_message.ticker,
                "node": _model_audit_node(audit, execution),
                "model": _model_audit_model(audit),
                "input_tokens": resolved_input,
                "output_tokens": resolved_output,
                "total_tokens": resolved_total,
                "cost_usd": _cost_from_audit(audit),
                "is_retry": bool(retry_count and retry_count > 0),
                "status": _model_audit_status(audit, retry_count=retry_count),
                "source_ref": {
                    "execution_id": execution.execution_id,
                    "source_message_id": execution.source_message.source_message_id,
                    "provider": _text_value(audit.get("provider")),
                    "retry_count": retry_count or 0,
                    "fallback_used": bool(audit.get("fallback_used", False)),
                },
            }
        )
    return records


def _extract_model_audits(execution: RuntimeExecutionRecord) -> list[JsonObject]:
    audits: list[JsonObject] = []
    seen: set[str] = set()
    _collect_model_audits(execution.timing, audits, seen=seen, depth=0)
    _collect_model_audits(execution.source_message.metadata, audits, seen=seen, depth=0)
    return audits


def _collect_model_audits(
    value: object,
    audits: list[JsonObject],
    *,
    seen: set[str],
    depth: int,
) -> None:
    if depth > 8:
        return
    if isinstance(value, dict):
        if _looks_like_model_audit(value):
            key = repr(sorted(value.items(), key=lambda item: str(item[0])))
            if key not in seen:
                audits.append(dict(value))
                seen.add(key)
        for child in value.values():
            _collect_model_audits(child, audits, seen=seen, depth=depth + 1)
        return
    if isinstance(value, list):
        for child in value:
            _collect_model_audits(child, audits, seen=seen, depth=depth + 1)


def _looks_like_model_audit(value: JsonObject) -> bool:
    model = value.get("model") or value.get("model_name")
    if not isinstance(model, str) or not model.strip():
        return False
    usage = value.get("usage")
    if isinstance(usage, dict):
        return True
    return any(
        key in value
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
            "cost_usd",
            "estimated_cost_usd",
        )
    )


def _model_usage_payload(audit: JsonObject) -> JsonObject:
    usage = audit.get("usage")
    return dict(usage) if isinstance(usage, dict) else {}


def _model_audit_model(audit: JsonObject) -> str:
    model = _text_value(audit.get("model")) or _text_value(audit.get("model_name"))
    provider = _text_value(audit.get("provider"))
    if model:
        return model
    if provider:
        return provider
    return "unknown"


def _model_audit_node(audit: JsonObject, execution: RuntimeExecutionRecord) -> str:
    metadata = _dict_value(audit.get("metadata"))
    for source in (audit, metadata):
        for key in ("node", "workflow_node", "runtime_node", "task_node"):
            value = _text_value(source.get(key))
            if value:
                return _runtime_trace_node_label(value)
        run_name = _text_value(source.get("run_name") or source.get("task_name"))
        if run_name:
            for token in ("W1", "W2", "O3"):
                if token.lower() in run_name.lower():
                    return token
    if execution.node_traces:
        return _runtime_trace_node_label(execution.node_traces[-1].node)
    return "unknown"


def _model_audit_time(
    audit: JsonObject,
    execution: RuntimeExecutionRecord,
) -> datetime | None:
    for key in ("time", "created_at", "completed_at", "started_at"):
        value = _parse_dt_text(audit.get(key))
        if value is not None:
            return value
    return execution.updated_at or execution.created_at


def _model_audit_status(audit: JsonObject, *, retry_count: int | None) -> str:
    raw_status = _text_value(audit.get("status"))
    if raw_status and raw_status.lower() in {"failed", "error"}:
        return "failed"
    if _text_value(audit.get("error")):
        return "failed"
    if retry_count and retry_count > 0:
        return "retried"
    return "succeeded"


def _cost_from_audit(audit: JsonObject) -> float | None:
    for key in ("cost_usd", "estimated_cost_usd", "total_cost_usd"):
        value = _float_value(audit.get(key))
        if value is not None:
            return value
    metadata = _dict_value(audit.get("metadata"))
    for key in ("cost_usd", "estimated_cost_usd", "total_cost_usd"):
        value = _float_value(metadata.get(key))
        if value is not None:
            return value
    return None


def _cost_audit_payload(
    ticker: str,
    records: list[JsonObject],
    *,
    period: str,
    group_by: str,
    target_date: date,
    zone: ZoneInfo,
) -> JsonObject:
    return {
        "ticker": ticker,
        "period": period,
        "status": _cost_audit_status(records),
        "group_by": group_by,
        "kpis": {
            "today_input_tokens": _sum_int(record.get("input_tokens") for record in records),
            "today_output_tokens": _sum_int(record.get("output_tokens") for record in records),
            "today_total_tokens": _sum_int(record.get("total_tokens") for record in records),
            "today_total_cost_usd": _sum_optional(
                record.get("cost_usd") for record in records
            ),
            "highest_cost_node": _highest_cost_node(records),
            "retry_cost_usd": _sum_optional(
                record.get("cost_usd") for record in records if record.get("is_retry") is True
            ),
        },
        "trend": _cost_trend(records, period, target_date, zone),
        "breakdown": {
            "by_node": _cost_breakdown(records, "node"),
            "by_model": _cost_breakdown(records, "model"),
        },
    }


def _cost_audit_status(records: list[JsonObject]) -> str:
    if not records:
        return "missing"
    if all(isinstance(record.get("cost_usd"), int | float) for record in records):
        return "completed"
    return "partial"


def _cost_trend(
    records: list[JsonObject],
    period: str,
    target_date: date,
    zone: ZoneInfo,
) -> list[JsonObject]:
    grouped: dict[date, list[JsonObject]] = {}
    for record in records:
        time_value = _parse_dt_text(record.get("time"))
        if time_value is None:
            continue
        grouped.setdefault(_aware(time_value).astimezone(zone).date(), []).append(record)
    rows: list[JsonObject] = []
    for item_date in _audit_period_dates(period, target_date):
        bucket = grouped.get(item_date, [])
        rows.append(
            {
                "date": item_date.isoformat(),
                "total_cost_usd": _sum_optional(record.get("cost_usd") for record in bucket),
                "total_tokens": _sum_int(record.get("total_tokens") for record in bucket),
            }
        )
    return rows


def _cost_breakdown(records: list[JsonObject], key: str) -> list[JsonObject]:
    grouped: dict[str, list[JsonObject]] = {}
    for record in records:
        value = _text_value(record.get(key)) or "unknown"
        grouped.setdefault(value, []).append(record)
    rows = [
        {
            "key": value,
            "label": value,
            "cost_usd": _sum_optional(record.get("cost_usd") for record in bucket),
            "total_tokens": _sum_int(record.get("total_tokens") for record in bucket),
        }
        for value, bucket in grouped.items()
    ]
    return sorted(
        rows,
        key=lambda item: (
            float(item["cost_usd"]) if isinstance(item.get("cost_usd"), int | float) else -1.0,
            int(item["total_tokens"]) if isinstance(item.get("total_tokens"), int) else 0,
            str(item["key"]),
        ),
        reverse=True,
    )


def _highest_cost_node(records: list[JsonObject]) -> str | None:
    buckets = _cost_breakdown(
        [record for record in records if isinstance(record.get("cost_usd"), int | float)],
        "node",
    )
    return str(buckets[0]["key"]) if buckets else None


def _dashboard_event_from_scheduler_audit(event: RuntimeAuditEvent) -> JsonObject:
    return {
        "event_id": event.audit_id,
        "event_type": event.event_type,
        "ticker": event.ticker,
        "occurred_at": _dt(event.created_at),
        "payload": {
            "severity": event.severity.value,
            "message": event.message,
            **event.payload,
        },
    }


def _sum_optional(values: Iterable[object]) -> float | None:
    numbers = [
        float(value)
        for value in values
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]
    if not numbers:
        return None
    return round(sum(numbers), 6)


def _sum_int(values: Iterable[object]) -> int | None:
    numbers = [
        int(value)
        for value in values
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]
    if not numbers:
        return None
    return int(sum(numbers))


def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _float_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _text_value(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _dict_value(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _runtime_message_title(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
) -> str | None:
    source_id = execution.source_message.source_message_id
    standard = context.messages_by_id.get(source_id)
    title = execution.source_message.title or (standard.title if standard is not None else None)
    if title:
        return _truncate(title, 180)
    body = execution.source_message.body or (standard.body if standard is not None else None)
    return _truncate(body, 180) if body else None


def _runtime_node_input_summary(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
    node_id: str,
) -> str | None:
    title = _runtime_message_title(execution, context)
    if node_id == "message_bus":
        return title or execution.source_message.source_message_id
    if node_id == "route_engine":
        return "W1/W2 runtime results."
    if node_id == "o3":
        return "Escalated runtime package."
    return title or execution.source_message.source_message_id


def _runtime_node_output_summary(
    execution: RuntimeExecutionRecord,
    context: RuntimeDashboardContext,
    node_id: str,
) -> str | None:
    if node_id == "message_bus":
        return "Runtime accepted source message."
    if node_id == "w1":
        if execution.w1_result is None:
            return None
        return (
            f"is_new={execution.w1_result.is_new}, "
            f"novelty={execution.w1_result.novelty_label}, "
            f"confidence={execution.w1_result.confidence}"
        )
    if node_id == "w2":
        if execution.w2_result is None:
            return None
        return (
            f"type={execution.w2_result.type}, "
            f"policy={execution.w2_result.matched_policy_code}"
        )
    if node_id == "route_engine":
        return f"route={_runtime_final_route(execution, context)}"
    if node_id == "o3":
        if execution.o3_result is not None:
            return (
                f"primary_action={execution.o3_result.primary_action}, "
                f"confidence={execution.o3_result.confidence}"
            )
        trace = _runtime_trace(execution, "o3")
        return f"O3 status={_runtime_trace_status(trace)}" if trace is not None else None
    return None


def _runtime_node_label(node_id: str) -> str:
    for current_id, label in RUNTIME_NODE_DEFINITIONS:
        if current_id == node_id:
            return label
    return node_id


def _runtime_node_last_error(
    context: RuntimeDashboardContext,
    node_id: str,
) -> str | None:
    exceptions: list[ExecutionExceptionLog] = []
    for items in context.exceptions_by_source.values():
        for exception in items:
            if node_id == "exception_queue" or exception.node.strip().lower() == node_id:
                exceptions.append(exception)
    if not exceptions:
        return None
    latest = max(exceptions, key=lambda exception: _aware(exception.created_at))
    return f"{latest.exception_type}: {_truncate(latest.message, 160)}"


def _runtime_executions_by_source(
    context: RuntimeDashboardContext,
) -> dict[str, RuntimeExecutionRecord]:
    return {
        execution.source_message.source_message_id: execution
        for execution in context.executions
    }


def _is_dtc(execution: RuntimeExecutionRecord) -> bool:
    if execution.w2_result is None:
        return False
    return str(execution.w2_result.type).strip().lower() == "direct trade candidate"


def _is_eba(execution: RuntimeExecutionRecord) -> bool:
    if execution.w2_result is None:
        return False
    return str(execution.w2_result.type).strip().lower() == "escalate to background agent"


def _is_on_day(value: datetime | None, *, target_date: date, zone: ZoneInfo) -> bool:
    if value is None:
        return False
    zoned = _aware(value).astimezone(zone)
    return zoned.date() == target_date


def _parse_dt_text(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _frontend_document_types(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return list(FRONTEND_DOCUMENT_TYPES)
    return [_frontend_document_type(item) for item in value.split(",") if item.strip()]


def _frontend_document_type(value: str) -> str:
    resolved = value.strip()
    if resolved not in FRONTEND_DOCUMENT_TYPES:
        raise UnsupportedDocumentType(resolved)
    return resolved


def _assemble_dashboard_document(
    run: BlackboardRun,
    document_type: str,
    *,
    version_status: str,
    include_raw: bool,
) -> JsonObject | None:
    if document_type == "document1":
        documents = _model_documents(
            run,
            DocumentType.GLOBAL_RESEARCH,
            GlobalResearchDocument,
        )
        document = _latest_document(documents)
        return (
            _global_research_view(
                document,
                version_status=version_status,
                include_raw=include_raw,
            )
            if document is not None
            else None
        )
    if document_type == "document2":
        documents = _model_documents(
            run,
            DocumentType.EXPECTATION_UNIT,
            ExpectationUnitDocument,
        )
        return (
            _expectation_units_view(
                run,
                documents,
                version_status=version_status,
                include_raw=include_raw,
            )
            if documents
            else None
        )
    if document_type == "document3":
        known_event_documents = _model_documents(
            run,
            DocumentType.KNOWN_EVENTS,
            KnownEventsDocument,
        )
        policy_documents = _model_documents(
            run,
            DocumentType.MONITORING_POLICY,
            MonitoringPolicyDocument,
        )
        return (
            _runtime_strategy_view(
                run,
                known_event_documents,
                policy_documents,
                version_status=version_status,
                include_raw=include_raw,
            )
            if known_event_documents or policy_documents
            else None
        )
    raise UnsupportedDocumentType(document_type)


def _global_research_view(
    document: GlobalResearchDocument,
    *,
    version_status: str,
    include_raw: bool,
) -> JsonObject:
    updated_at = _document_time(document)
    cards = [
        _research_section_card(
            "fundamental_report",
            "Fundamental Research",
            document.fundamental_report,
            updated_at=updated_at,
        ),
        _research_section_card(
            "macro_report",
            "Macro Research",
            document.macro_report,
            updated_at=updated_at,
        ),
        _research_section_card(
            "industry_report",
            "Industry Research",
            document.industry_report,
            updated_at=updated_at,
        ),
        _research_section_card(
            "market_trace_report",
            "Market Trace",
            document.market_trace_report,
            updated_at=updated_at,
        ),
    ]
    if document.market_narrative_report is not None:
        cards.append(
            _research_section_card(
                "market_narrative_report",
                "Market Narrative",
                document.market_narrative_report,
                updated_at=updated_at,
            )
        )
    payload = _dashboard_document_payload(
        document_type="document1",
        document_id=document.document_id,
        generated_at=document.created_at,
        updated_at=updated_at,
        version_status=version_status,
        cards=cards,
    )
    if include_raw:
        payload["raw"] = _json(document)
    return payload


def _expectation_units_view(
    run: BlackboardRun,
    documents: list[ExpectationUnitDocument],
    *,
    version_status: str,
    include_raw: bool,
) -> JsonObject:
    sorted_documents = sorted(documents, key=lambda item: item.expectation_id)
    updated_at = _max_dt(_document_time(document) for document in sorted_documents)
    generated_at = _min_dt(document.created_at for document in sorted_documents)
    if len(sorted_documents) == 1:
        document_id = sorted_documents[0].document_id
    else:
        document_id = f"expectation_units:{run.run_id}"
    payload = _dashboard_document_payload(
        document_type="document2",
        document_id=document_id,
        generated_at=generated_at,
        updated_at=updated_at,
        version_status=version_status,
        cards=[_expectation_card(document) for document in sorted_documents],
    )
    if include_raw:
        payload["raw"] = {"expectations": [_json(document) for document in sorted_documents]}
    return payload


def _runtime_strategy_view(
    run: BlackboardRun,
    known_event_documents: list[KnownEventsDocument],
    policy_documents: list[MonitoringPolicyDocument],
    *,
    version_status: str,
    include_raw: bool,
) -> JsonObject:
    known_event_document = _latest_document(known_event_documents)
    policy_document = _latest_document(policy_documents)
    source_documents = [
        document for document in (known_event_document, policy_document) if document is not None
    ]
    updated_at = _max_dt(_document_time(document) for document in source_documents)
    generated_at = _min_dt(document.created_at for document in source_documents)
    document_ids = "+".join(document.document_id for document in source_documents)
    payload = _dashboard_document_payload(
        document_type="document3",
        document_id=document_ids or f"runtime_strategy:{run.run_id}",
        generated_at=generated_at,
        updated_at=updated_at,
        version_status=version_status,
        cards=_runtime_strategy_cards(
            known_event_document,
            policy_document,
            updated_at=updated_at,
        ),
    )
    if include_raw:
        payload["raw"] = {
            "known_events": _json(known_event_document) if known_event_document else None,
            "monitoring_policy": _json(policy_document) if policy_document else None,
        }
    return payload


def _dashboard_document_payload(
    *,
    document_type: str,
    document_id: str,
    generated_at: datetime | None,
    updated_at: datetime | None,
    version_status: str,
    cards: list[JsonObject],
) -> JsonObject:
    return {
        "document_type": document_type,
        "document_type_label": DOCUMENT_TYPE_LABELS[document_type],
        "document_id": document_id,
        "generated_at": _dt(generated_at),
        "updated_at": _dt(updated_at or generated_at),
        "version_status": version_status,
        "availability": "available",
        "cards": cards,
    }


def _research_section_card(
    key: str,
    title: str,
    section: ResearchSection,
    *,
    updated_at: datetime | None,
) -> JsonObject:
    return {
        "card_id": key,
        "title": title,
        "updated_at": _dt(updated_at),
        "summary": section.summary,
        "fields": [
            {"key": "text", "label": "Research Text", "value": section.text},
            {
                "key": "author_agent",
                "label": "Author Agent",
                "value": str(section.author_agent),
            },
            {
                "key": "reviewer_agents",
                "label": "Reviewer Agents",
                "value": [str(agent) for agent in section.reviewer_agents],
            },
            {
                "key": "evidence_refs",
                "label": "Evidence Refs",
                "value": _json(section.evidence_refs),
            },
        ],
    }


def _expectation_card(document: ExpectationUnitDocument) -> JsonObject:
    updated_at = _document_time(document)
    return {
        "card_id": document.expectation_id,
        "title": document.expectation_name,
        "updated_at": _dt(updated_at),
        "summary": document.why_it_matters,
        "fields": [
            {"key": "direction", "label": "Direction", "value": str(document.direction)},
            {
                "key": "market_view_summary",
                "label": "Market View Summary",
                "value": document.market_view.summary,
            },
            {
                "key": "market_view_text",
                "label": "Market View",
                "value": document.market_view.text,
            },
            {
                "key": "realized_facts_summary",
                "label": "Realized Facts Summary",
                "value": document.realized_facts_summary,
            },
            {
                "key": "realized_facts",
                "label": "Realized Facts",
                "value": _json(document.realized_facts),
            },
            {
                "key": "key_variables",
                "label": "Key Variables",
                "value": _json(document.key_variables),
            },
            {
                "key": "event_monitoring_direction",
                "label": "Event Monitoring Direction",
                "value": _json(document.event_monitoring_direction),
            },
        ],
    }


def _runtime_strategy_cards(
    known_events: KnownEventsDocument | None,
    policies: MonitoringPolicyDocument | None,
    *,
    updated_at: datetime | None,
) -> list[JsonObject]:
    cards: list[JsonObject] = []
    if known_events is not None:
        cards.append(
            {
                "card_id": "known_events",
                "title": "Known Events",
                "updated_at": _dt(_document_time(known_events) or updated_at),
                "summary": (
                    f"{len(known_events.events)} known event(s) seed runtime novelty checks."
                ),
                "fields": [
                    {
                        "key": "events",
                        "label": "Events",
                        "value": [
                            _known_event_item_from_document(event, document=known_events)
                            for event in known_events.events
                        ],
                    }
                ],
            }
        )
    if policies is not None:
        policy_items = _policy_rules(policies)
        cards.append(
            {
                "card_id": "monitoring_policy",
                "title": "Monitoring Policy",
                "updated_at": _dt(_document_time(policies) or updated_at),
                "summary": f"{len(policy_items)} policy rule(s) route runtime execution.",
                "fields": [
                    {
                        "key": "policies",
                        "label": "Policies",
                        "value": [
                            _policy_item(policy, document=policies)
                            for policy in policy_items
                        ],
                    },
                    {
                        "key": "no_action_rationale",
                        "label": "No Action Rationale",
                        "value": policies.no_action_rationale,
                    },
                ],
            }
        )
    return cards


def _first_run_with_documents(
    runs: Iterable[BlackboardRun],
    internal_types: Iterable[DocumentType],
) -> BlackboardRun | None:
    for run in runs:
        if any(_document_bucket(run, internal_type) for internal_type in internal_types):
            return run
    return None


def _model_documents(
    run: BlackboardRun,
    internal_type: DocumentType,
    model: type[DocumentBase],
) -> list[Any]:
    documents: list[Any] = []
    for raw in _document_bucket(run, internal_type).values():
        candidate = _unwrap_document(raw)
        if isinstance(candidate, model):
            documents.append(candidate)
            continue
        try:
            documents.append(model.model_validate(candidate))
        except ValidationError:
            continue
    return documents


def _document_bucket(run: BlackboardRun, internal_type: DocumentType) -> dict[str, Any]:
    for key, bucket in run.belief_state.documents.items():
        if str(key) == internal_type.value and isinstance(bucket, dict):
            return bucket
    return {}


def _unwrap_document(raw: Any) -> Any:
    if isinstance(raw, dict) and "document" in raw:
        return raw["document"]
    return raw


def _latest_document(documents: Iterable[Any]) -> Any | None:
    resolved = list(documents)
    if not resolved:
        return None
    fallback = datetime.min.replace(tzinfo=UTC)
    return max(resolved, key=lambda document: _document_time(document) or fallback)


def _document_time(document: DocumentBase) -> datetime | None:
    return document.updated_at or document.created_at


def _without_raw(document: JsonObject, *, include_raw: bool) -> JsonObject:
    if include_raw:
        return document
    return {key: value for key, value in document.items() if key != "raw"}


def _known_event_item_from_document(
    event: KnownEvent,
    *,
    document: KnownEventsDocument,
) -> JsonObject:
    return {
        "event_id": event.event_id,
        "event_name": _truncate(event.core_fact, 96),
        "event_time_or_window": event.event_window or _dt(event.event_time),
        "description": event.description,
        "related_expectation_ids": [event.expectation_id] if event.expectation_id else [],
        "duplicate_detection_keys": list(event.duplicate_detection_keys),
        "source": event.source.title or event.source.source_id,
        "updated_at": _dt(_document_time(document)),
    }


def _known_event_item_from_runtime(event: RuntimeKnownEvent) -> JsonObject:
    return {
        "event_id": event.event_id,
        "event_name": _truncate(event.core_fact, 96),
        "event_time_or_window": event.event_time_or_window,
        "description": event.core_fact,
        "related_expectation_ids": [],
        "duplicate_detection_keys": list(event.duplicate_detection_keys),
        "source": event.source_ref,
        "updated_at": _dt(event.changed_at),
    }


def _policy_rules(document: MonitoringPolicyDocument) -> list[MonitoringPolicyRule]:
    return list(
        document.policies
        or [
            *document.direct_trade_rules,
            *document.push_to_agent_rules,
            *document.cache_rules,
        ]
    )


def _policy_item(
    policy: MonitoringPolicyRule,
    *,
    document: MonitoringPolicyDocument,
) -> JsonObject:
    action_type = _policy_action_type(policy)
    trigger_condition = _policy_trigger_condition(policy)
    return {
        "policy_id": policy.policy_id,
        "expectation_id": _policy_expectation_id(policy),
        "action_type": action_type,
        "title": f"{action_type}: {_truncate(trigger_condition or policy.policy_id, 80)}",
        "trigger_condition": trigger_condition,
        "severity": _policy_severity(policy),
        "updated_at": _dt(_document_time(document)),
    }


def _policy_action_type(policy: MonitoringPolicyRule) -> str:
    raw = str(policy.action_type)
    if raw == "direct_trade":
        return "DTC"
    if raw == "push_to_agent":
        return "EBA"
    if raw == "cache":
        return "NULL"
    return "Irrelevant"


def _policy_expectation_id(policy: MonitoringPolicyRule) -> str | None:
    if policy.expectation_id:
        return policy.expectation_id
    value = policy.scope.get("expectation_unit_id") or policy.scope.get("expectation_id")
    return str(value).strip() if value else None


def _policy_trigger_condition(policy: MonitoringPolicyRule) -> str | None:
    if policy.trigger_condition:
        return policy.trigger_condition
    value = policy.trigger.get("condition")
    return str(value).strip() if value else None


def _policy_severity(policy: MonitoringPolicyRule) -> str | None:
    for key in ("priority", "conviction", "severity"):
        value = policy.action.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _document_summary(document: JsonObject) -> str | None:
    cards = document.get("cards")
    if not isinstance(cards, list) or not cards:
        return None
    first = cards[0]
    if not isinstance(first, dict):
        return None
    summary = first.get("summary")
    if isinstance(summary, str) and summary.strip():
        return _truncate(summary.strip(), 160)
    title = first.get("title")
    return _truncate(str(title), 160) if title else None


def _version_id(run_id: str, document_type: str, document_id: str) -> str:
    return f"{document_type}:{run_id}:{document_id}"


def _search_items(
    items: list[JsonObject],
    q: str,
    *,
    fields: Iterable[str],
) -> list[JsonObject]:
    needle = q.strip().casefold()
    if not needle:
        return items
    return [
        item
        for item in items
        if any(needle in str(item.get(field) or "").casefold() for field in fields)
    ]


def _sort_by_updated(items: list[JsonObject]) -> list[JsonObject]:
    return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def _min_dt(values: Iterable[datetime | None]) -> datetime | None:
    resolved = [_aware(value) for value in values if value is not None]
    return min(resolved) if resolved else None


def _max_dt(values: Iterable[datetime | None]) -> datetime | None:
    resolved = [_aware(value) for value in values if value is not None]
    return max(resolved) if resolved else None


def _truncate(value: str, max_length: int) -> str:
    stripped = value.strip()
    if len(stripped) <= max_length:
        return stripped
    return f"{stripped[: max_length - 1]}..."


def _json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return _dt(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _status_label(status: TickerRunStatus) -> str:
    return {
        TickerRunStatus.INITIALIZING: "初始化中",
        TickerRunStatus.RUNNING: "运行中",
        TickerRunStatus.PAUSED: "暂停",
        TickerRunStatus.STOPPED: "已停止",
        TickerRunStatus.DEGRADED: "异常降级",
        TickerRunStatus.BLOCKED: "阻塞",
    }[status]


def _status_color(health: RuntimeHealth) -> str:
    return {
        RuntimeHealth.NORMAL: "green",
        RuntimeHealth.DEGRADED: "yellow",
        RuntimeHealth.BLOCKED: "red",
        RuntimeHealth.UNKNOWN: "gray",
    }[health]


def _health_with_exceptions(
    health: RuntimeHealth,
    exceptions: list[ExecutionExceptionLog],
    *,
    target_date: date,
    zone: ZoneInfo,
) -> RuntimeHealth:
    if health is RuntimeHealth.NORMAL and _count_on_day(
        (item.created_at for item in exceptions),
        target_date=target_date,
        zone=zone,
    ):
        return RuntimeHealth.DEGRADED
    return health


def _zone(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(value or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc


def _target_date(value: str | None, zone: ZoneInfo) -> date:
    if value is None or not value.strip():
        return datetime.now(zone).date()
    return date.fromisoformat(value)


def _day_window(target_date: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time.min, tzinfo=zone)
    end = datetime.combine(target_date, time.max, tzinfo=zone)
    return start, end


def _count_on_day(
    values: Iterable[datetime | None],
    *,
    target_date: date,
    zone: ZoneInfo,
) -> int:
    start, end = _day_window(target_date, zone)
    count = 0
    for value in values:
        if value is None:
            continue
        zoned = _aware(value).astimezone(zone)
        if start <= zoned <= end:
            count += 1
    return count


def _latest_dt(values: Iterable[datetime | None]) -> datetime | None:
    resolved = [_aware(value) for value in values if value is not None]
    return max(resolved) if resolved else None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).isoformat().replace("+00:00", "Z")


def _parse_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    if not cursor.startswith("cur_"):
        return 0
    try:
        return max(0, int(cursor.removeprefix("cur_")))
    except ValueError:
        return 0


def _limit(value: int | None) -> int:
    if value is None:
        return 50
    return max(1, min(value, 200))


def _paginate(items: list[JsonObject], *, limit: int | None, cursor: str | None) -> JsonObject:
    resolved_limit = _limit(limit)
    offset = _parse_cursor(cursor)
    page_items = items[offset : offset + resolved_limit]
    next_offset = offset + resolved_limit
    has_more = next_offset < len(items)
    return {
        "items": page_items,
        "page": {
            "limit": resolved_limit,
            "next_cursor": f"cur_{next_offset}" if has_more else None,
            "has_more": has_more,
        },
    }


def _sort_cards(items: list[JsonObject], sort: str | None) -> list[JsonObject]:
    sort_key = sort or "ticker"
    reverse = sort_key.startswith("-")
    key = sort_key.removeprefix("-")
    return sorted(items, key=lambda item: str(item.get(key) or ""), reverse=reverse)
