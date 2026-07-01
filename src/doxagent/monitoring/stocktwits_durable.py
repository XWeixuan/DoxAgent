"""Durable Stocktwits acquisition adapter for the Monitoring Message Bus."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from doxagent.monitoring.schema import (
    FetchedExternalMessage,
    IngestBatchResult,
    MonitoringSourceConfig,
    TickerSourceBinding,
)
from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.client import StocktwitsHTTPClient, StocktwitsPageClient
from doxagent.stocktwits.crawler import StocktwitsPollingCrawler
from doxagent.stocktwits.repository import StocktwitsRepository, repository_from_settings
from doxagent.stocktwits.schema import (
    BootstrapEventPolicy,
    CoverageStatus,
    CrawlRunStatus,
    StocktwitsCrawlerConfig,
    StocktwitsMessage,
    StocktwitsTickerState,
    TickerMode,
)


class StocktwitsDurableMonitoringAdapter:
    """Bridge Stocktwits durable polling into monitoring raw/standard/events."""

    def __init__(
        self,
        settings: DoxAgentSettings,
        *,
        repository: StocktwitsRepository | None = None,
        client: StocktwitsPageClient | None = None,
        now: Any | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or repository_from_settings(settings)
        self.client = client or StocktwitsHTTPClient(settings)
        self._now = now or (lambda: datetime.now(UTC))

    def is_due(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
        now: datetime,
    ) -> bool:
        state = self.repository.get_ticker_state(binding.ticker)
        if state is None:
            return True
        if not state.enabled or state.current_mode is TickerMode.PAUSED:
            return False
        return state.next_due_at <= now.astimezone(UTC)

    def ensure_state(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
        reset_schedule: bool = False,
    ) -> StocktwitsTickerState:
        self.repository.ensure_schema()
        existing = self.repository.get_ticker_state(binding.ticker)
        if existing is None:
            current = self._now()
            state = _new_state_from_source(
                source,
                binding,
                next_due_at=self._staggered_next_due_at(
                    source=source,
                    binding=binding,
                    now=current,
                ),
            )
            return self.repository.upsert_ticker_state(state)
        changed = False
        if reset_schedule:
            current = self._now()
            existing = existing.model_copy(
                update={
                    "next_due_at": self._staggered_next_due_at(
                        source=source,
                        binding=binding,
                        now=current,
                    )
                },
                deep=True,
            )
            changed = True
        if existing.enabled != binding.enabled:
            existing = existing.model_copy(update={"enabled": binding.enabled}, deep=True)
            changed = True
        if changed:
            return self.repository.upsert_ticker_state(existing)
        return existing

    def update_state(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
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
    ) -> StocktwitsTickerState:
        state = self.ensure_state(source=source, binding=binding)
        update: dict[str, object] = {}
        if enabled is not None:
            update["enabled"] = enabled
        if mode is not None:
            update["current_mode"] = mode
        if target_cadence_seconds is not None:
            update["target_cadence_seconds"] = target_cadence_seconds
        if hot_cadence_seconds is not None:
            update["hot_cadence_seconds"] = hot_cadence_seconds
        if page_size is not None:
            update["page_size"] = page_size
        if max_pages_per_crawl is not None:
            update["max_pages_per_crawl"] = max_pages_per_crawl
        if hot_message_threshold is not None:
            update["hot_message_threshold"] = hot_message_threshold
        if hot_cooldown_successes is not None:
            update["hot_cooldown_successes"] = hot_cooldown_successes
        if bootstrap_event_policy is not None:
            update["bootstrap_event_policy"] = bootstrap_event_policy
        updated_state = state.model_copy(update=update, deep=True) if update else state
        if reset_schedule:
            updated_state = updated_state.model_copy(
                update={
                    "next_due_at": self._staggered_next_due_at(
                        source=source,
                        binding=binding,
                        now=self._now(),
                        cadence_seconds=updated_state.target_cadence_seconds,
                    )
                },
                deep=True,
            )
        if not update and not reset_schedule:
            return state
        return self.repository.upsert_ticker_state(updated_state)

    def poll(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
        ingest: Any,
    ) -> IngestBatchResult:
        self.ensure_state(source=source, binding=binding)
        sink_result: IngestBatchResult | None = None

        def sink(
            requested_symbol: str,
            messages: list[StocktwitsMessage],
            coverage_status: CoverageStatus,
            crawl_state: StocktwitsTickerState,
        ) -> None:
            nonlocal sink_result
            selected = _messages_for_bus(messages, coverage_status, crawl_state)
            if not selected:
                sink_result = IngestBatchResult(
                    source_id=source.source_id,
                    binding_id=binding.binding_id,
                    ticker=binding.ticker,
                    collected_count=len(messages),
                    historical_skipped_count=len(messages),
                    metadata={
                        "stocktwits_bus_ingest_policy": crawl_state.bootstrap_event_policy.value,
                    },
                )
                return
            fetched = [
                _to_fetched_message(
                    source=source,
                    binding=binding,
                    message=message,
                    coverage_status=coverage_status,
                )
                for message in selected
            ]
            sink_result = ingest(source=source, fetched=fetched)

        crawler = StocktwitsPollingCrawler(
            repository=self.repository,
            client=self.client,
            config=_crawler_config_from_source(self.settings, source, binding.ticker),
            now=self._now,
            message_sink=sink,
        )
        run = crawler.crawl_symbol(binding.ticker)
        result = sink_result or IngestBatchResult(
            source_id=source.source_id,
            binding_id=binding.binding_id,
            ticker=binding.ticker,
        )
        metadata = _run_metadata(run)
        if result.metadata:
            metadata = {**result.metadata, **metadata}
        update: dict[str, object] = {
            "metadata": metadata,
            "latency_ms": _latency_ms(run),
        }
        if run.status is CrawlRunStatus.FAILED:
            update.update(
                {
                    "failed_count": 1,
                    "error_message": (
                        run.error_message or run.gap_reason or "Stocktwits crawl failed."
                    ),
                }
            )
        return result.model_copy(update=update, deep=True)

    def ticker_state_payload(
        self,
        *,
        symbol: str,
    ) -> dict[str, object] | None:
        state = self.repository.get_ticker_state(symbol)
        return state.model_dump(mode="json") if state is not None else None

    def _staggered_next_due_at(
        self,
        *,
        source: MonitoringSourceConfig,
        binding: TickerSourceBinding,
        now: datetime,
        cadence_seconds: int | None = None,
    ) -> datetime:
        cadence = max(
            30,
            cadence_seconds
            if cadence_seconds is not None
            else _config_int(
                source,
                "target_cadence_seconds",
                int(source.poll_interval_seconds),
            ),
        )
        slots = max(1, _config_int(source, "stagger_slots", 10))
        step_seconds = max(1, cadence // slots)
        states = [
            state
            for state in self.repository.list_ticker_states()
            if state.symbol != binding.ticker
            and state.enabled
            and state.current_mode is not TickerMode.PAUSED
        ]
        offset_seconds = (len(states) % slots) * step_seconds
        return now.astimezone(UTC) + timedelta(seconds=offset_seconds)


def _new_state_from_source(
    source: MonitoringSourceConfig,
    binding: TickerSourceBinding,
    *,
    next_due_at: datetime,
) -> StocktwitsTickerState:
    return StocktwitsTickerState(
        symbol=binding.ticker,
        enabled=binding.enabled,
        target_cadence_seconds=_config_int(
            source,
            "target_cadence_seconds",
            int(source.poll_interval_seconds),
        ),
        hot_cadence_seconds=_config_int(source, "hot_cadence_seconds", 90),
        page_size=_config_int(source, "page_size", 30),
        max_pages_per_crawl=_config_int(source, "max_pages_per_crawl", 10),
        hot_message_threshold=_config_int(source, "hot_message_threshold", 80),
        hot_cooldown_successes=_config_int(source, "hot_cooldown_successes", 3),
        bootstrap_event_policy=BootstrapEventPolicy(
            str(source.config.get("bootstrap_event_policy", "live_only"))
        ),
        next_due_at=next_due_at.astimezone(UTC),
    )


def _crawler_config_from_source(
    settings: DoxAgentSettings,
    source: MonitoringSourceConfig,
    symbol: str,
) -> StocktwitsCrawlerConfig:
    return StocktwitsCrawlerConfig(
        symbols=[symbol],
        target_cadence_seconds=_config_int(
            source,
            "target_cadence_seconds",
            int(source.poll_interval_seconds),
        ),
        hot_cadence_seconds=_config_int(
            source,
            "hot_cadence_seconds",
            settings.stocktwits_hot_cadence_seconds,
        ),
        scheduler_tick_seconds=settings.stocktwits_scheduler_tick_seconds,
        page_size=_config_int(source, "page_size", settings.stocktwits_page_size),
        max_pages_per_crawl=_config_int(
            source,
            "max_pages_per_crawl",
            settings.stocktwits_max_pages_per_crawl,
        ),
        hot_message_threshold=_config_int(
            source,
            "hot_message_threshold",
            settings.stocktwits_hot_message_threshold,
        ),
        hot_cooldown_successes=_config_int(
            source,
            "hot_cooldown_successes",
            settings.stocktwits_hot_cooldown_successes,
        ),
    )


def _messages_for_bus(
    messages: list[StocktwitsMessage],
    coverage_status: CoverageStatus,
    state: StocktwitsTickerState,
) -> list[StocktwitsMessage]:
    if state.last_seen_message_id is not None:
        return messages
    if coverage_status is not CoverageStatus.INCOMPLETE:
        return messages
    if state.bootstrap_event_policy is BootstrapEventPolicy.PUBLISH_ALL:
        return messages
    if state.bootstrap_event_policy is BootstrapEventPolicy.SUPPRESS_INITIAL:
        return []
    return messages


def _to_fetched_message(
    *,
    source: MonitoringSourceConfig,
    binding: TickerSourceBinding,
    message: StocktwitsMessage,
    coverage_status: CoverageStatus,
) -> FetchedExternalMessage:
    return FetchedExternalMessage(
        source_id=source.source_id,
        binding_id=binding.binding_id,
        ticker=binding.ticker,
        source_type=source.source_type,
        interface_type=source.interface_type,
        raw_payload=message.raw_payload,
        provider_message_id=message.message_id,
        source_url=message.source_url,
        source_published_at=message.created_at,
        metadata={
            "provider": "stocktwits",
            "stocktwits_coverage_status": coverage_status.value,
        },
    )


def _run_metadata(run: Any) -> dict[str, object]:
    return {
        "stocktwits_run_id": run.run_id,
        "stocktwits_status": run.status.value,
        "coverage_status": run.coverage_status.value,
        "checkpoint_found": run.checkpoint_found,
        "checkpoint_message_id": run.checkpoint_message_id,
        "newest_message_id": run.newest_message_id,
        "gap_reason": run.gap_reason,
        "current_mode": run.mode.value,
        "pages_fetched": run.pages_fetched,
        "request_count": run.request_count,
        "rate_limited": run.rate_limited,
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def _latency_ms(run: Any) -> int | None:
    if run.finished_at is None:
        return None
    return int((run.finished_at - run.started_at).total_seconds() * 1000)


def _config_int(source: MonitoringSourceConfig, key: str, default: int) -> int:
    value = source.config.get(key, default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def is_stocktwits_durable_source(source: MonitoringSourceConfig) -> bool:
    return (
        source.source_id == "stocktwits_messages"
        and str(source.config.get("mode", "durable_polling")) == "durable_polling"
    )


__all__ = [
    "StocktwitsDurableMonitoringAdapter",
    "is_stocktwits_durable_source",
]
