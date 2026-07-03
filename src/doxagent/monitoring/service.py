"""Service layer for the Monitoring Message Bus."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from doxagent.monitoring.collectors import MonitoringCollectorRegistry
from doxagent.monitoring.media_enrichment import (
    Extractor,
    SessionFactory,
    enrich_media_records,
)
from doxagent.monitoring.normalizer import normalize_message
from doxagent.monitoring.repository import (
    InMemoryMonitoringRepository,
    MonitoringRepository,
    SQLiteMonitoringRepository,
)
from doxagent.monitoring.schema import (
    EndpointKind,
    EventStreamItem,
    FetchedExternalMessage,
    IngestBatchResult,
    IngestDecision,
    JsonObject,
    MonitoringParameters,
    MonitoringSnapshot,
    MonitoringSourceConfig,
    RawExternalMessage,
    StandardMessage,
    TickerSourceBinding,
    UpdateActor,
    dedupe_key_for,
    new_monitoring_id,
    parameter_schema_for_source,
    payload_hash,
)
from doxagent.monitoring.stocktwits_durable import (
    StocktwitsDurableMonitoringAdapter,
    is_stocktwits_durable_source,
)
from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.client import StocktwitsPageClient
from doxagent.stocktwits.repository import StocktwitsRepository
from doxagent.stocktwits.schema import BootstrapEventPolicy, TickerMode


class MonitoringPermissionError(PermissionError):
    """Raised when an actor attempts to update user-owned settings."""


class MonitoringBusService:
    """Coordinates source config, raw persistence, normalization, and event stream writes."""

    def __init__(
        self,
        repository: MonitoringRepository,
        *,
        collectors: MonitoringCollectorRegistry | None = None,
        settings: DoxAgentSettings | None = None,
        stocktwits_repository: StocktwitsRepository | None = None,
        stocktwits_client: StocktwitsPageClient | None = None,
    ) -> None:
        self.settings = settings or DoxAgentSettings()
        self.repository = repository
        self.collectors = collectors
        self._stocktwits_repository = stocktwits_repository
        self._stocktwits_client = stocktwits_client
        self._stocktwits_adapter: StocktwitsDurableMonitoringAdapter | None = None
        self.repository.ensure_defaults()

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
    ) -> MonitoringBusService:
        resolved = settings or DoxAgentSettings()
        if resolved.monitoring_storage_mode == "memory":
            repository: MonitoringRepository = InMemoryMonitoringRepository()
        else:
            repository = SQLiteMonitoringRepository(resolved.monitoring_sqlite_path)
        return cls(
            repository,
            collectors=MonitoringCollectorRegistry(resolved),
            settings=resolved,
        )

    def list_sources(self) -> list[MonitoringSourceConfig]:
        return self.repository.list_sources()

    def set_source_enabled(
        self,
        source_id: str,
        *,
        enabled: bool,
        updated_by: UpdateActor = UpdateActor.USER,
    ) -> MonitoringSourceConfig:
        if updated_by is not UpdateActor.USER:
            raise MonitoringPermissionError("Only users can globally enable or disable a source.")
        return self.repository.set_source_enabled(source_id, enabled)

    def set_source_poll_interval(
        self,
        source_id: str,
        *,
        seconds: int,
        updated_by: UpdateActor = UpdateActor.USER,
    ) -> MonitoringSourceConfig:
        if updated_by is not UpdateActor.USER:
            raise MonitoringPermissionError("Only users can modify API polling intervals.")
        return self.repository.set_source_poll_interval(source_id, seconds)

    def configure_ticker_source(
        self,
        ticker: str,
        source_id: str,
        *,
        parameters: MonitoringParameters | None = None,
        enabled: bool = True,
        updated_by: UpdateActor = UpdateActor.AGENT,
        updated_reason: str | None = None,
        merge: bool = True,
    ) -> TickerSourceBinding:
        return self.repository.upsert_binding(
            ticker=ticker,
            source_id=source_id,
            parameters=parameters or MonitoringParameters(),
            enabled=enabled,
            updated_by=updated_by,
            updated_reason=updated_reason,
            merge=merge,
        )

    def delete_ticker_source(self, ticker: str, source_id: str) -> bool:
        return self.repository.delete_binding(ticker, source_id)

    def delete_ticker_config(self, ticker: str) -> int:
        return self.repository.delete_ticker_bindings(ticker)

    def get_ticker_config(self, ticker: str) -> JsonObject:
        normalized_ticker = ticker.strip().upper()
        bindings = self.repository.list_bindings(ticker=normalized_ticker)
        sources = {source.source_id: source for source in self.repository.list_sources()}
        poll_states = {
            state.binding_id: state
            for state in self.repository.list_poll_states(ticker=normalized_ticker)
        }
        by_ticker: list[JsonObject] = []
        by_parameter: list[JsonObject] = []
        for binding in bindings:
            source = sources.get(binding.source_id)
            if source is None:
                continue
            poll_state = poll_states.get(binding.binding_id)
            user_only_fields = ["poll_interval_seconds", "global_source_enabled"]
            item: JsonObject = {
                "binding": binding.model_dump(mode="json"),
                "source": source.model_dump(mode="json"),
                "poll_state": poll_state.model_dump(mode="json") if poll_state else None,
                "agent_mutable_fields": [
                    "enabled",
                    *parameter_schema_for_source(source.source_id).keys(),
                ],
                "user_only_fields": user_only_fields,
            }
            if is_stocktwits_durable_source(source):
                item["stocktwits_state"] = self._stocktwits().ticker_state_payload(
                    symbol=binding.ticker
                )
                item["user_only_fields"] = [
                    *user_only_fields,
                    "target_cadence_seconds",
                    "hot_cadence_seconds",
                    "page_size",
                    "max_pages_per_crawl",
                    "hot_message_threshold",
                    "hot_cooldown_successes",
                    "bootstrap_event_policy",
                    "current_mode",
                ]
            if source.interface_type.value == "by_ticker":
                by_ticker.append(item)
            else:
                by_parameter.append(item)
        missing_sources = sorted(set(sources) - {binding.source_id for binding in bindings})
        return {
            "ticker": normalized_ticker,
            "by_ticker_sources": by_ticker,
            "by_parameter_sources": by_parameter,
            "missing_source_ids": missing_sources,
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def status_snapshot(self, *, ticker: str | None = None, limit: int = 20) -> MonitoringSnapshot:
        return self.repository.snapshot(ticker=ticker, limit=limit)

    def due_bindings(self, *, now: datetime | None = None) -> list[TickerSourceBinding]:
        current = now or datetime.now(UTC)
        sources = {source.source_id: source for source in self.repository.list_sources()}
        poll_states = {state.binding_id: state for state in self.repository.list_poll_states()}
        due: list[TickerSourceBinding] = []
        for binding in self.repository.list_bindings(enabled_only=True):
            source = sources.get(binding.source_id)
            if source is None or not source.enabled:
                continue
            if is_stocktwits_durable_source(source):
                if self._stocktwits().is_due(source=source, binding=binding, now=current):
                    due.append(binding)
                continue
            state = poll_states.get(binding.binding_id)
            last_attempt = state.last_attempt_at if state is not None else None
            if last_attempt is None:
                due.append(binding)
                continue
            if last_attempt + timedelta(seconds=source.poll_interval_seconds) <= current:
                due.append(binding)
        return due

    def poll_due_once(self, *, now: datetime | None = None) -> list[IngestBatchResult]:
        results: list[IngestBatchResult] = []
        for binding in self.due_bindings(now=now):
            try:
                results.append(self.poll_binding(binding.ticker, binding.source_id))
            except Exception as exc:
                results.append(
                    IngestBatchResult(
                        source_id=binding.source_id,
                        binding_id=binding.binding_id,
                        ticker=binding.ticker,
                        failed_count=1,
                        error_message=str(exc)[:500],
                    )
                )
        return results

    def poll_binding(self, ticker: str, source_id: str) -> IngestBatchResult:
        started = time.monotonic()
        source = _require_source(self.repository.get_source(source_id), source_id)
        binding = _require_binding(self.repository.get_binding(ticker, source.source_id), ticker)
        self.repository.record_poll_attempt(
            binding_id=binding.binding_id,
            source_id=source.source_id,
            ticker=binding.ticker,
        )
        if not source.enabled or not binding.enabled:
            result = IngestBatchResult(
                source_id=source.source_id,
                binding_id=binding.binding_id,
                ticker=binding.ticker,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            self.repository.record_poll_success(result)
            return result
        try:
            if is_stocktwits_durable_source(source):
                result = self._stocktwits().poll(
                    source=source,
                    binding=binding,
                    ingest=self.ingest_fetched,
                )
            else:
                if self.collectors is None:
                    raise RuntimeError("No monitoring collectors are configured.")
                collector = self.collectors.collector_for(source)
                fetched = collector.collect(source=source, binding=binding)
                result = (
                    self.ingest_fetched(source=source, fetched=fetched)
                    if fetched
                    else IngestBatchResult(
                        source_id=source.source_id,
                        binding_id=binding.binding_id,
                        ticker=binding.ticker,
                    )
                )
            result.latency_ms = int((time.monotonic() - started) * 1000)
            if result.failed_count:
                self.repository.record_poll_failure(
                    binding_id=binding.binding_id,
                    source_id=source.source_id,
                    ticker=binding.ticker,
                    message=result.error_message or "Polling failed.",
                    latency_ms=result.latency_ms,
                    metadata=result.metadata,
                )
            else:
                self.repository.record_poll_success(result)
            return result
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            self.repository.record_poll_failure(
                binding_id=binding.binding_id,
                source_id=source.source_id,
                ticker=binding.ticker,
                message=str(exc),
                latency_ms=latency_ms,
            )
            raise

    def configure_stocktwits_persistence(
        self,
        ticker: str,
        *,
        enabled: bool | None = None,
        mode: TickerMode | None = None,
        target_cadence_seconds: int | None = None,
        hot_cadence_seconds: int | None = None,
        page_size: int | None = None,
        max_pages_per_crawl: int | None = None,
        hot_message_threshold: int | None = None,
        hot_cooldown_successes: int | None = None,
        bootstrap_event_policy: BootstrapEventPolicy | None = None,
        reset_schedule: bool = False,
        ensure_binding: bool = True,
        updated_reason: str | None = None,
    ) -> JsonObject:
        source = _require_source(
            self.repository.get_source("stocktwits_messages"),
            "stocktwits_messages",
        )
        if source.endpoint_kind is not EndpointKind.STOCKTWITS_MESSAGES:
            raise ValueError("stocktwits_messages source is not configured as Stocktwits.")
        if ensure_binding:
            binding = self.configure_ticker_source(
                ticker,
                source.source_id,
                enabled=True if enabled is None else enabled,
                updated_by=UpdateActor.USER,
                updated_reason=updated_reason,
            )
        else:
            binding = _require_binding(
                self.repository.get_binding(ticker, source.source_id),
                ticker,
            )
        state = self._stocktwits().update_state(
            source=source,
            binding=binding,
            enabled=enabled,
            mode=mode,
            target_cadence_seconds=target_cadence_seconds,
            hot_cadence_seconds=hot_cadence_seconds,
            page_size=page_size,
            max_pages_per_crawl=max_pages_per_crawl,
            hot_message_threshold=hot_message_threshold,
            hot_cooldown_successes=hot_cooldown_successes,
            bootstrap_event_policy=bootstrap_event_policy,
            reset_schedule=reset_schedule,
        )
        return {
            "binding": binding.model_dump(mode="json"),
            "stocktwits_state": state.model_dump(mode="json"),
            "ticker_config": self.get_ticker_config(ticker),
        }

    def ingest_fetched(
        self,
        *,
        source: MonitoringSourceConfig,
        fetched: list[FetchedExternalMessage],
    ) -> IngestBatchResult:
        if not fetched:
            return IngestBatchResult(
                source_id=source.source_id,
                binding_id="",
                ticker="",
            )
        first = fetched[0]
        result = IngestBatchResult(
            source_id=source.source_id,
            binding_id=first.binding_id,
            ticker=first.ticker,
            collected_count=len(fetched),
        )
        for item in fetched:
            binding = self.repository.get_binding(item.ticker, item.source_id)
            if binding is not None and _is_before_binding_watermark(item, binding):
                result.historical_skipped_count += 1
                continue
            save_result = self.repository.save_raw_message(self._to_raw_message(item))
            if save_result.decision is IngestDecision.DUPLICATE:
                result.duplicate_count += 1
                continue
            result.raw_inserted_count += 1
            standard = normalize_message(save_result.message, source)
            self.repository.save_standard_message(standard)
            result.standardized_count += 1
            self.repository.append_event(standard)
            result.event_count += 1
        return result

    def recent_events(self, *, ticker: str | None = None, limit: int = 20) -> list[EventStreamItem]:
        return self.repository.recent_events(ticker=ticker, limit=limit)

    def pending_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[EventStreamItem]:
        return self.repository.pending_events(ticker=ticker, limit=limit)

    def mark_event_consumed(self, event_id: str) -> EventStreamItem | None:
        return self.repository.mark_event_consumed(event_id)

    def recent_messages(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[StandardMessage]:
        return self.repository.recent_standard_messages(ticker=ticker, limit=limit)

    def enrich_recent_media(
        self,
        *,
        ticker: str | None = None,
        limit: int = 50,
        concurrency: int = 6,
        dry_run: bool = False,
        incomplete_only: bool = True,
        reader_fallback: bool = True,
    ) -> JsonObject:
        return asyncio.run(
            self.enrich_recent_media_async(
                ticker=ticker,
                limit=limit,
                concurrency=concurrency,
                dry_run=dry_run,
                incomplete_only=incomplete_only,
                reader_fallback=reader_fallback,
            )
        )

    async def enrich_recent_media_async(
        self,
        *,
        ticker: str | None = None,
        limit: int = 50,
        concurrency: int = 6,
        dry_run: bool = False,
        incomplete_only: bool = True,
        session_factory: SessionFactory | None = None,
        extractor: Extractor | None = None,
        reader_fallback: bool = True,
    ) -> JsonObject:
        records = self.repository.list_media_enrichment_records(
            ticker=ticker,
            limit=limit,
            incomplete_only=incomplete_only,
        )
        stats, results = await enrich_media_records(
            records,
            session_factory=session_factory,
            extractor=extractor,
            concurrency=concurrency,
            dry_run=dry_run,
            reader_fallback=reader_fallback,
        )
        if not dry_run:
            stats.written_count = self.repository.apply_media_enrichment_results(results)
        return {
            "stats": stats.to_payload(),
            "results": [result.to_payload() for result in results],
        }

    def _to_raw_message(self, item: FetchedExternalMessage) -> RawExternalMessage:
        return RawExternalMessage(
            raw_message_id=new_monitoring_id("raw"),
            dedupe_key=dedupe_key_for(
                source_id=item.source_id,
                provider_message_id=item.provider_message_id,
                source_url=item.source_url,
                raw_payload=item.raw_payload,
            ),
            source_id=item.source_id,
            binding_id=item.binding_id,
            ticker=item.ticker,
            source_type=item.source_type,
            interface_type=item.interface_type,
            provider_message_id=item.provider_message_id,
            payload_hash=payload_hash(item.raw_payload),
            source_url=item.source_url,
            source_published_at=item.source_published_at,
            collected_at=datetime.now(UTC),
            raw_payload=item.raw_payload,
            metadata=item.metadata,
        )

    def _stocktwits(self) -> StocktwitsDurableMonitoringAdapter:
        if self._stocktwits_adapter is None:
            self._stocktwits_adapter = StocktwitsDurableMonitoringAdapter(
                self.settings,
                repository=self._stocktwits_repository,
                client=self._stocktwits_client,
            )
        return self._stocktwits_adapter


def snapshot_to_agent_payload(snapshot: MonitoringSnapshot) -> JsonObject:
    return {
        "sources": [source.model_dump(mode="json") for source in snapshot.sources],
        "bindings": [binding.model_dump(mode="json") for binding in snapshot.bindings],
        "poll_states": [state.model_dump(mode="json") for state in snapshot.poll_states],
        "recent_raw_messages": [
            message.model_dump(mode="json") for message in snapshot.recent_raw_messages
        ],
        "recent_standard_messages": [
            message.model_dump(mode="json") for message in snapshot.recent_standard_messages
        ],
        "recent_events": [event.model_dump(mode="json") for event in snapshot.recent_events],
    }


def _require_source(
    source: MonitoringSourceConfig | None,
    source_id: str,
) -> MonitoringSourceConfig:
    if source is None:
        raise KeyError(f"Unknown monitoring source: {source_id}")
    return source


def _require_binding(binding: TickerSourceBinding | None, ticker: str) -> TickerSourceBinding:
    if binding is None:
        raise KeyError(f"No monitoring binding configured for {ticker}.")
    return binding


def _is_before_binding_watermark(
    item: FetchedExternalMessage,
    binding: TickerSourceBinding,
) -> bool:
    if item.source_published_at is None:
        return False
    return item.source_published_at.astimezone(UTC) < binding.updated_at.astimezone(UTC)


def _coerce_parameters(payload: dict[str, Any]) -> MonitoringParameters:
    return MonitoringParameters(
        keywords=list(payload.get("keywords") or []),
        usernames=list(payload.get("usernames") or []),
        search_terms=list(payload.get("search_terms") or []),
        rss_urls=list(payload.get("rss_urls") or []),
        source_filters=list(payload.get("source_filters") or []),
        extra=dict(payload.get("extra") or {}),
    )


__all__ = [
    "MonitoringBusService",
    "MonitoringPermissionError",
    "snapshot_to_agent_payload",
]
