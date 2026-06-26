"""Standalone low-frequency Stocktwits polling crawler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.client import (
    StocktwitsClientError,
    StocktwitsHTTPClient,
    StocktwitsPageClient,
    parse_stocktwits_datetime,
)
from doxagent.stocktwits.repository import StocktwitsRepository, repository_from_settings
from doxagent.stocktwits.schema import (
    CoverageStatus,
    CrawlRunStatus,
    JsonObject,
    StocktwitsCrawlerConfig,
    StocktwitsCrawlRun,
    StocktwitsMessage,
    StocktwitsStatusSnapshot,
    StocktwitsTickerState,
    StocktwitsUser,
    TickerMode,
    normalize_symbol,
    normalize_symbols,
    parse_symbol_csv,
)


@dataclass
class FetchTrace:
    messages: list[StocktwitsMessage] = field(default_factory=list)
    checkpoint_found: bool = False
    pages_fetched: int = 0
    request_count: int = 0
    page_limit_reached: bool = False
    stream_exhausted: bool = False
    suspected_truncation: bool = False
    stop_reason: str | None = None


class StocktwitsPollingCrawler:
    """Coordinates Stocktwits pagination, idempotent persistence, and checkpoints."""

    def __init__(
        self,
        *,
        repository: StocktwitsRepository,
        client: StocktwitsPageClient,
        config: StocktwitsCrawlerConfig,
        now: Any | None = None,
    ) -> None:
        self.repository = repository
        self.client = client
        self.config = config
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(
        cls,
        settings: DoxAgentSettings | None = None,
        *,
        storage_mode: str | None = None,
    ) -> StocktwitsPollingCrawler:
        resolved = settings or DoxAgentSettings()
        config = config_from_settings(resolved)
        return cls(
            repository=repository_from_settings(resolved, storage_mode=storage_mode),
            client=StocktwitsHTTPClient(resolved),
            config=config,
        )

    def initialize_tickers(
        self,
        symbols: list[str] | None = None,
        *,
        reset_schedule: bool = False,
        now: datetime | None = None,
    ) -> list[StocktwitsTickerState]:
        current = _utc(now or self._now())
        normalized_symbols = normalize_symbols(symbols or self.config.symbols)
        spacing_seconds = max(1, self.config.target_cadence_seconds // len(normalized_symbols))
        states: list[StocktwitsTickerState] = []
        for index, symbol in enumerate(normalized_symbols):
            existing = self.repository.get_ticker_state(symbol)
            scheduled_at = current + timedelta(seconds=index * spacing_seconds)
            if existing is None:
                state = StocktwitsTickerState(
                    symbol=symbol,
                    target_cadence_seconds=self.config.target_cadence_seconds,
                    hot_cadence_seconds=self.config.hot_cadence_seconds,
                    next_due_at=scheduled_at,
                )
            else:
                state = existing.model_copy(
                    update={
                        "enabled": True,
                        "target_cadence_seconds": self.config.target_cadence_seconds,
                        "hot_cadence_seconds": self.config.hot_cadence_seconds,
                        "next_due_at": scheduled_at if reset_schedule else existing.next_due_at,
                    },
                    deep=True,
                )
            states.append(self.repository.upsert_ticker_state(state))
        return states

    def poll_due_once(
        self,
        *,
        max_tickers: int = 1,
        now: datetime | None = None,
    ) -> list[StocktwitsCrawlRun]:
        current = _utc(now or self._now())
        due_states = [
            state
            for state in self.repository.list_ticker_states(enabled_only=True)
            if state.current_mode is not TickerMode.PAUSED and state.next_due_at <= current
        ]
        runs: list[StocktwitsCrawlRun] = []
        for state in due_states[: max(1, max_tickers)]:
            runs.append(self.crawl_symbol(state.symbol, now=current))
        return runs

    def crawl_symbol(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
    ) -> StocktwitsCrawlRun:
        normalized_symbol = normalize_symbol(symbol)
        started_at = _utc(now or self._now())
        state = self.repository.get_ticker_state(normalized_symbol)
        if state is None:
            state = self.repository.upsert_ticker_state(
                StocktwitsTickerState(
                    symbol=normalized_symbol,
                    target_cadence_seconds=self.config.target_cadence_seconds,
                    hot_cadence_seconds=self.config.hot_cadence_seconds,
                    next_due_at=started_at,
                )
            )
        run = StocktwitsCrawlRun(
            symbol=normalized_symbol,
            started_at=started_at,
            checkpoint_message_id=state.last_seen_message_id,
            mode=state.current_mode,
        )
        if not state.enabled or state.current_mode is TickerMode.PAUSED:
            skipped = run.model_copy(
                update={
                    "finished_at": self._now_utc(),
                    "status": CrawlRunStatus.SKIPPED,
                    "coverage_status": (
                        state.latest_coverage_status or CoverageStatus.LIKELY_COMPLETE
                    ),
                    "gap_reason": "ticker_disabled_or_paused",
                },
                deep=True,
            )
            self.repository.record_crawl_run(skipped)
            return skipped
        try:
            trace = self._fetch_until_checkpoint(state)
            ingest_result = self.repository.save_messages(
                requested_symbol=normalized_symbol,
                messages=trace.messages,
            )
            coverage_status, gap_reason = self._coverage_for_trace(state, trace)
            newest = _newest_message(trace.messages)
            oldest_time = _oldest_message_time(trace.messages)
            finished = self._now_utc()
            run = run.model_copy(
                update={
                    "finished_at": finished,
                    "status": CrawlRunStatus.SUCCEEDED,
                    "fetched_count": len(trace.messages),
                    "inserted_count": ingest_result.inserted_count,
                    "duplicate_count": ingest_result.duplicate_count,
                    "request_count": trace.request_count,
                    "pages_fetched": trace.pages_fetched,
                    "newest_message_id": newest.message_id if newest is not None else None,
                    "newest_message_time": newest.created_at if newest is not None else None,
                    "oldest_message_time": oldest_time,
                    "checkpoint_found": trace.checkpoint_found,
                    "coverage_status": coverage_status,
                    "gap_reason": gap_reason,
                    "metadata": {
                        "stop_reason": trace.stop_reason,
                        "page_limit_reached": trace.page_limit_reached,
                        "suspected_truncation": trace.suspected_truncation,
                        "hot_message_threshold": self.config.hot_message_threshold,
                    },
                },
                deep=True,
            )
            updated_state = self._state_after_completed_run(state, run, newest, finished)
            self.repository.upsert_ticker_state(updated_state)
            run = run.model_copy(update={"mode": updated_state.current_mode}, deep=True)
            return self.repository.record_crawl_run(run)
        except Exception as exc:
            finished = self._now_utc()
            code = _error_code(exc)
            failed = run.model_copy(
                update={
                    "finished_at": finished,
                    "status": CrawlRunStatus.FAILED,
                    "coverage_status": CoverageStatus.FAILED,
                    "error_code": code,
                    "error_message": str(exc)[:1000],
                    "rate_limited": _is_rate_limited(exc),
                    "gap_reason": "crawl_failed",
                    "metadata": {
                        "checkpoint_preserved": state.last_seen_message_id,
                        "coverage_window_exceeded": _coverage_window_exceeded(state, finished),
                    },
                },
                deep=True,
            )
            updated_state = self._state_after_failed_run(state, failed, finished)
            self.repository.upsert_ticker_state(updated_state)
            failed = failed.model_copy(update={"mode": updated_state.current_mode}, deep=True)
            return self.repository.record_crawl_run(failed)

    def status_snapshot(
        self,
        *,
        symbol: str | None = None,
        limit: int = 20,
    ) -> StocktwitsStatusSnapshot:
        return self.repository.status_snapshot(symbol=symbol, limit=limit)

    def _fetch_until_checkpoint(self, state: StocktwitsTickerState) -> FetchTrace:
        trace = FetchTrace()
        checkpoint_id = state.last_seen_message_id
        max_message_id: str | None = None
        seen_in_run: set[str] = set()
        for page_index in range(self.config.max_pages_per_crawl):
            page = self.client.fetch_symbol_page(
                symbol=state.symbol,
                max_message_id=max_message_id,
                page_size=self.config.page_size,
            )
            trace.pages_fetched += 1
            trace.request_count += 1
            if not page.messages:
                trace.stream_exhausted = True
                trace.stop_reason = "empty_page"
                return trace
            parsed_messages = [_parse_message(row) for row in page.messages]
            for message in parsed_messages:
                if message.message_id in seen_in_run:
                    continue
                seen_in_run.add(message.message_id)
                trace.messages.append(message)
                if checkpoint_id is not None and message.message_id == checkpoint_id:
                    trace.checkpoint_found = True
                    trace.stop_reason = "checkpoint_found"
                    return trace
            if page.cursor_more is False:
                trace.stream_exhausted = True
                trace.stop_reason = "cursor_exhausted"
                return trace
            next_max = page.next_max_id or _next_max_from_messages(parsed_messages)
            if not next_max or next_max == max_message_id:
                trace.suspected_truncation = len(page.messages) >= self.config.page_size
                trace.stop_reason = "no_pagination_cursor"
                return trace
            max_message_id = next_max
            if page_index == self.config.max_pages_per_crawl - 1:
                trace.page_limit_reached = True
                trace.stop_reason = "page_limit_reached"
        return trace

    def _coverage_for_trace(
        self,
        state: StocktwitsTickerState,
        trace: FetchTrace,
    ) -> tuple[CoverageStatus, str | None]:
        if trace.suspected_truncation:
            return CoverageStatus.INCOMPLETE, "pagination_cursor_missing_near_page_limit"
        if state.last_seen_message_id is None:
            if trace.page_limit_reached:
                return CoverageStatus.INCOMPLETE, "initial_history_exceeded_page_budget"
            return CoverageStatus.LIKELY_COMPLETE, None
        if trace.checkpoint_found:
            return CoverageStatus.COMPLETE, None
        if trace.page_limit_reached:
            return CoverageStatus.GAP_DETECTED, "checkpoint_not_found_before_page_limit"
        if trace.stream_exhausted:
            return CoverageStatus.GAP_DETECTED, "checkpoint_not_found_before_stream_exhausted"
        return CoverageStatus.GAP_DETECTED, "checkpoint_not_found"

    def _state_after_completed_run(
        self,
        state: StocktwitsTickerState,
        run: StocktwitsCrawlRun,
        newest: StocktwitsMessage | None,
        finished: datetime,
    ) -> StocktwitsTickerState:
        covered = run.coverage_status in {
            CoverageStatus.COMPLETE,
            CoverageStatus.LIKELY_COMPLETE,
        }
        gap_like = run.coverage_status in {
            CoverageStatus.GAP_DETECTED,
            CoverageStatus.INCOMPLETE,
            CoverageStatus.FAILED,
        }
        high_volume = run.fetched_count >= self.config.hot_message_threshold
        next_mode = state.current_mode
        consecutive_gap_count = state.consecutive_gap_count
        consecutive_complete_count = state.consecutive_complete_count
        hot_started_at = state.hot_started_at
        hot_until = state.hot_until
        if gap_like or high_volume:
            next_mode = TickerMode.HOT
            consecutive_gap_count = consecutive_gap_count + 1 if gap_like else consecutive_gap_count
            consecutive_complete_count = 0
            hot_started_at = hot_started_at or finished
            hot_until = finished + timedelta(
                seconds=state.hot_cadence_seconds * self.config.hot_cooldown_successes
            )
        elif covered:
            consecutive_gap_count = 0
            if state.current_mode is TickerMode.HOT:
                consecutive_complete_count += 1
                if consecutive_complete_count >= self.config.hot_cooldown_successes:
                    next_mode = TickerMode.NORMAL
                    consecutive_complete_count = 0
                    hot_started_at = None
                    hot_until = None
            else:
                consecutive_complete_count += 1
        cadence = (
            state.hot_cadence_seconds
            if next_mode is TickerMode.HOT
            else state.target_cadence_seconds
        )
        checkpoint_updates: dict[str, object] = {}
        can_bootstrap_checkpoint = state.last_seen_message_id is None and newest is not None
        if (covered or can_bootstrap_checkpoint) and newest is not None:
            checkpoint_updates = {
                "last_seen_message_id": newest.message_id,
                "last_seen_message_created_at": newest.created_at,
            }
        return state.model_copy(
            update={
                **checkpoint_updates,
                "last_successful_crawl_at": (
                    finished if covered else state.last_successful_crawl_at
                ),
                "latest_coverage_status": run.coverage_status,
                "current_mode": next_mode,
                "consecutive_gap_count": consecutive_gap_count,
                "consecutive_complete_count": consecutive_complete_count,
                "hot_started_at": hot_started_at,
                "hot_until": hot_until,
                "next_due_at": finished + timedelta(seconds=cadence),
            },
            deep=True,
        )

    def _state_after_failed_run(
        self,
        state: StocktwitsTickerState,
        run: StocktwitsCrawlRun,
        finished: datetime,
    ) -> StocktwitsTickerState:
        next_mode = state.current_mode
        if not run.rate_limited:
            next_mode = TickerMode.HOT
        cadence = (
            state.target_cadence_seconds if run.rate_limited else state.hot_cadence_seconds
        )
        return state.model_copy(
            update={
                "latest_coverage_status": CoverageStatus.FAILED,
                "current_mode": next_mode,
                "consecutive_gap_count": state.consecutive_gap_count + 1,
                "consecutive_complete_count": 0,
                "hot_started_at": (
                    state.hot_started_at or finished
                    if next_mode is TickerMode.HOT
                    else None
                ),
                "hot_until": (
                    finished
                    + timedelta(
                        seconds=(
                            state.hot_cadence_seconds
                            * self.config.hot_cooldown_successes
                        )
                    )
                    if next_mode is TickerMode.HOT
                    else state.hot_until
                ),
                "next_due_at": finished + timedelta(seconds=cadence),
            },
            deep=True,
        )

    def _now_utc(self) -> datetime:
        return _utc(self._now())


def config_from_settings(settings: DoxAgentSettings) -> StocktwitsCrawlerConfig:
    return StocktwitsCrawlerConfig(
        symbols=parse_symbol_csv(settings.stocktwits_default_symbols),
        target_cadence_seconds=settings.stocktwits_target_cadence_seconds,
        hot_cadence_seconds=settings.stocktwits_hot_cadence_seconds,
        scheduler_tick_seconds=settings.stocktwits_scheduler_tick_seconds,
        page_size=settings.stocktwits_page_size,
        max_pages_per_crawl=settings.stocktwits_max_pages_per_crawl,
        hot_message_threshold=settings.stocktwits_hot_message_threshold,
        hot_cooldown_successes=settings.stocktwits_hot_cooldown_successes,
    )


def _parse_message(row: JsonObject) -> StocktwitsMessage:
    message_id = _str_or_none(row.get("id") or row.get("message_id") or row.get("messageId"))
    if message_id is None:
        raise StocktwitsClientError(
            "Stocktwits response schema error: message is missing id.",
            code="schema_error",
            retryable=False,
        )
    user = row.get("user")
    user_payload = user if isinstance(user, dict) else {}
    body = _str_or_none(row.get("body") or row.get("text"))
    symbols = _symbols_from_message(row)
    return StocktwitsMessage(
        message_id=message_id,
        body=body,
        created_at=parse_stocktwits_datetime(row.get("created_at") or row.get("createdAt")),
        user=StocktwitsUser(
            user_id=_str_or_none(user_payload.get("id")),
            username=_str_or_none(user_payload.get("username")),
            name=_str_or_none(user_payload.get("name")),
            avatar_url=_str_or_none(
                user_payload.get("avatar_url") or user_payload.get("avatarUrl")
            ),
        ),
        sentiment=_sentiment_from_message(row),
        symbols=symbols,
        source_url=_str_or_none(row.get("url")),
        raw_payload=row,
    )


def _symbols_from_message(row: JsonObject) -> list[str]:
    symbols: list[str] = []
    for value in _symbol_items(row.get("symbols")):
        symbols.append(value)
    entities = row.get("entities")
    if isinstance(entities, dict):
        for value in _symbol_items(entities.get("symbols")):
            symbols.append(value)
    return normalize_symbols(symbols) if symbols else []


def _symbol_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    symbols: list[str] = []
    for item in value:
        if isinstance(item, dict):
            symbol = _str_or_none(item.get("symbol") or item.get("name"))
            if symbol is not None:
                symbols.append(symbol)
        elif isinstance(item, str):
            symbols.append(item)
    return symbols


def _sentiment_from_message(row: JsonObject) -> str | None:
    sentiment = row.get("sentiment")
    if isinstance(sentiment, dict):
        return _str_or_none(sentiment.get("basic") or sentiment.get("label"))
    if sentiment is not None:
        return _str_or_none(sentiment)
    entities = row.get("entities")
    if isinstance(entities, dict):
        entity_sentiment = entities.get("sentiment")
        if isinstance(entity_sentiment, dict):
            return _str_or_none(entity_sentiment.get("basic") or entity_sentiment.get("label"))
        return _str_or_none(entity_sentiment)
    return None


def _newest_message(messages: list[StocktwitsMessage]) -> StocktwitsMessage | None:
    if not messages:
        return None
    return max(messages, key=_message_sort_key)


def _oldest_message_time(messages: list[StocktwitsMessage]) -> datetime | None:
    times = [message.created_at for message in messages if message.created_at is not None]
    return min(times) if times else None


def _message_sort_key(message: StocktwitsMessage) -> tuple[datetime, int, str]:
    created_at = message.created_at or datetime.min.replace(tzinfo=UTC)
    numeric_id = _message_id_int(message.message_id) or 0
    return (created_at, numeric_id, message.message_id)


def _next_max_from_messages(messages: list[StocktwitsMessage]) -> str | None:
    numeric_ids = [
        value for value in (_message_id_int(message.message_id) for message in messages) if value
    ]
    if not numeric_ids:
        return None
    return str(max(0, min(numeric_ids) - 1))


def _message_id_int(value: str) -> int | None:
    text = value.strip()
    if not text.isdigit():
        return None
    return int(text)


def _coverage_window_exceeded(state: StocktwitsTickerState, now: datetime) -> bool:
    if state.last_successful_crawl_at is None:
        return False
    expected = (
        state.hot_cadence_seconds
        if state.current_mode is TickerMode.HOT
        else state.target_cadence_seconds
    )
    return state.last_successful_crawl_at + timedelta(seconds=expected * 2) < now


def _error_code(exc: Exception) -> str:
    if isinstance(exc, StocktwitsClientError):
        return exc.code
    return exc.__class__.__name__


def _is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, StocktwitsClientError) and exc.rate_limited


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "StocktwitsPollingCrawler",
    "config_from_settings",
]
