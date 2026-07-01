"""Contracts for the standalone Stocktwits polling crawler."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonObject = dict[str, Any]
DEFAULT_STOCKTWITS_SYMBOLS = (
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "PLTR",
    "MU",
)


class StocktwitsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TickerMode(StrEnum):
    NORMAL = "normal"
    HOT = "hot"
    PAUSED = "paused"


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    LIKELY_COMPLETE = "likely_complete"
    INCOMPLETE = "incomplete"
    GAP_DETECTED = "gap_detected"
    FAILED = "failed"


class BootstrapEventPolicy(StrEnum):
    LIVE_ONLY = "live_only"
    PUBLISH_ALL = "publish_all"
    SUPPRESS_INITIAL = "suppress_initial"


class CrawlRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class StocktwitsCrawlerConfig(StocktwitsModel):
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_STOCKTWITS_SYMBOLS))
    target_cadence_seconds: int = Field(default=300, ge=30)
    hot_cadence_seconds: int = Field(default=90, ge=30)
    scheduler_tick_seconds: int = Field(default=30, ge=1)
    page_size: int = Field(default=30, ge=1)
    max_pages_per_crawl: int = Field(default=10, ge=1)
    hot_message_threshold: int = Field(default=80, ge=1)
    hot_cooldown_successes: int = Field(default=3, ge=1)

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        symbols = normalize_symbols(value)
        if not symbols:
            raise ValueError("At least one Stocktwits symbol is required.")
        return symbols


class StocktwitsTickerState(StocktwitsModel):
    symbol: str
    enabled: bool = True
    target_cadence_seconds: int = Field(default=300, ge=30)
    hot_cadence_seconds: int = Field(default=90, ge=30)
    page_size: int = Field(default=30, ge=1)
    max_pages_per_crawl: int = Field(default=10, ge=1)
    hot_message_threshold: int = Field(default=80, ge=1)
    hot_cooldown_successes: int = Field(default=3, ge=1)
    bootstrap_event_policy: BootstrapEventPolicy = BootstrapEventPolicy.LIVE_ONLY
    next_due_at: datetime
    last_successful_crawl_at: datetime | None = None
    last_seen_message_id: str | None = None
    last_seen_message_created_at: datetime | None = None
    current_mode: TickerMode = TickerMode.NORMAL
    latest_coverage_status: CoverageStatus | None = None
    consecutive_gap_count: int = Field(default=0, ge=0)
    consecutive_complete_count: int = Field(default=0, ge=0)
    hot_started_at: datetime | None = None
    hot_until: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("symbol")
    @classmethod
    def _symbol_is_upper(cls, value: str) -> str:
        return normalize_symbol(value)


class StocktwitsUser(StocktwitsModel):
    user_id: str | None = None
    username: str | None = None
    name: str | None = None
    avatar_url: str | None = None


class StocktwitsMessage(StocktwitsModel):
    message_id: str
    body: str | None = None
    created_at: datetime | None = None
    user: StocktwitsUser = Field(default_factory=StocktwitsUser)
    sentiment: str | None = None
    symbols: list[str] = Field(default_factory=list)
    source_url: str | None = None
    raw_payload: JsonObject

    @field_validator("message_id")
    @classmethod
    def _message_id_required(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("Stocktwits message_id is required.")
        return cleaned

    @field_validator("symbols")
    @classmethod
    def _symbols_upper(cls, value: list[str]) -> list[str]:
        return normalize_symbols(value)


class StocktwitsPage(StocktwitsModel):
    messages: list[JsonObject]
    cursor_more: bool | None = None
    next_max_id: str | None = None
    raw_response: JsonObject = Field(default_factory=dict)


class StocktwitsCrawlRun(StocktwitsModel):
    run_id: str = Field(default_factory=lambda: f"st_run_{uuid4().hex}")
    symbol: str
    started_at: datetime
    finished_at: datetime | None = None
    status: CrawlRunStatus = CrawlRunStatus.FAILED
    fetched_count: int = 0
    inserted_count: int = 0
    duplicate_count: int = 0
    request_count: int = 0
    pages_fetched: int = 0
    newest_message_id: str | None = None
    newest_message_time: datetime | None = None
    oldest_message_time: datetime | None = None
    checkpoint_message_id: str | None = None
    checkpoint_found: bool = False
    coverage_status: CoverageStatus = CoverageStatus.FAILED
    gap_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    mode: TickerMode = TickerMode.NORMAL
    rate_limited: bool = False
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _run_symbol_is_upper(cls, value: str) -> str:
        return normalize_symbol(value)


class StocktwitsIngestResult(StocktwitsModel):
    inserted_count: int = 0
    duplicate_count: int = 0


class StocktwitsStatusSnapshot(StocktwitsModel):
    ticker_states: list[StocktwitsTickerState]
    recent_runs: list[StocktwitsCrawlRun]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("Stocktwits symbol is required.")
    return normalized


def normalize_symbols(symbols: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        cleaned = normalize_symbol(symbol)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def parse_symbol_csv(value: str | None) -> list[str]:
    if value is None:
        return list(DEFAULT_STOCKTWITS_SYMBOLS)
    return normalize_symbols([part for part in value.split(",") if part.strip()])


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
