"""Contracts for the Monitoring Message Bus."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonObject = dict[str, Any]
PARAMETER_LIST_FIELDS = ("keywords", "usernames", "search_terms", "rss_urls", "source_filters")
SOURCE_PARAMETER_SCHEMAS: dict[str, dict[str, int]] = {
    "benzinga_news": {"search_terms": 3},
    "finnhub_company_news": {},
    "stocktwits_messages": {},
    "tikhub_x_search": {"search_terms": 3},
    "tikhub_x_user_posts": {"usernames": 2},
    "newswire_rss": {"rss_urls": 3},
}


class MonitoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceType(StrEnum):
    MEDIA = "media"
    SOCIAL = "social"


class InterfaceType(StrEnum):
    BY_TICKER = "by_ticker"
    BY_PARAMETER = "by_parameter"


class MonitoringProvider(StrEnum):
    BENZINGA = "benzinga"
    FINNHUB = "finnhub"
    STOCKTWITS = "stocktwits"
    TIKHUB = "tikhub"
    NEWSWIRE_RSS = "newswire_rss"


class EndpointKind(StrEnum):
    BENZINGA_NEWS = "benzinga_news"
    FINNHUB_COMPANY_NEWS = "finnhub_company_news"
    STOCKTWITS_MESSAGES = "stocktwits_messages"
    TIKHUB_X_SEARCH = "tikhub_x_search"
    TIKHUB_X_USER_POSTS = "tikhub_x_user_posts"
    RSS_FEED = "rss_feed"


class PollStatus(StrEnum):
    NEVER_POLLED = "never_polled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DISABLED = "disabled"


class UpdateActor(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class IngestDecision(StrEnum):
    INSERTED = "inserted"
    DUPLICATE = "duplicate"


class MonitoringSourceConfig(MonitoringModel):
    source_id: str
    provider: MonitoringProvider
    display_name: str
    source_type: SourceType
    interface_type: InterfaceType
    endpoint_kind: EndpointKind
    enabled: bool = True
    poll_interval_seconds: int = Field(ge=30)
    required_api_key_env: str | None = None
    config: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source_id")
    @classmethod
    def _source_id_is_normalized(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("source_id is required.")
        return normalized


class MonitoringParameters(MonitoringModel):
    keywords: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    rss_urls: list[str] = Field(default_factory=list)
    source_filters: list[str] = Field(default_factory=list)
    extra: JsonObject = Field(default_factory=dict)

    @field_validator("keywords", "usernames", "search_terms", "rss_urls", "source_filters")
    @classmethod
    def _normalize_list(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            cleaned = item.strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)
        return normalized

    def merged_with(self, patch: MonitoringParameters) -> MonitoringParameters:
        def merge(left: list[str], right: list[str]) -> list[str]:
            return self.model_validate({"keywords": left + right}).keywords

        return MonitoringParameters(
            keywords=merge(self.keywords, patch.keywords),
            usernames=merge(self.usernames, patch.usernames),
            search_terms=merge(self.search_terms, patch.search_terms),
            rss_urls=merge(self.rss_urls, patch.rss_urls),
            source_filters=merge(self.source_filters, patch.source_filters),
            extra={**self.extra, **patch.extra},
        )

    def non_empty_fields(self) -> list[str]:
        fields = [field for field in PARAMETER_LIST_FIELDS if getattr(self, field)]
        if self.extra:
            fields.append("extra")
        return fields


class TickerSourceBinding(MonitoringModel):
    binding_id: str
    ticker: str
    source_id: str
    enabled: bool = True
    parameters: MonitoringParameters = Field(default_factory=MonitoringParameters)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: UpdateActor = UpdateActor.USER
    updated_reason: str | None = None

    @field_validator("ticker")
    @classmethod
    def _ticker_is_upper(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("ticker is required.")
        return normalized

    @field_validator("source_id")
    @classmethod
    def _source_id_is_lower(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("source_id is required.")
        return normalized


class FetchedExternalMessage(MonitoringModel):
    source_id: str
    binding_id: str
    ticker: str
    source_type: SourceType
    interface_type: InterfaceType
    raw_payload: JsonObject
    provider_message_id: str | None = None
    source_url: str | None = None
    source_published_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class RawExternalMessage(MonitoringModel):
    raw_message_id: str
    dedupe_key: str
    source_id: str
    binding_id: str
    ticker: str
    source_type: SourceType
    interface_type: InterfaceType
    provider_message_id: str | None = None
    payload_hash: str
    source_url: str | None = None
    source_published_at: datetime | None = None
    collected_at: datetime
    raw_payload: JsonObject
    metadata: JsonObject = Field(default_factory=dict)
    duplicate_seen_count: int = 0
    last_seen_at: datetime | None = None


class StandardMessage(MonitoringModel):
    standard_message_id: str
    raw_message_id: str
    source_id: str
    binding_id: str
    ticker: str
    source_type: SourceType
    interface_type: InterfaceType
    title: str | None = None
    body: str | None = None
    url: str | None = None
    author: str | None = None
    symbols: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    username: str | None = None
    published_at: datetime | None = None
    collected_at: datetime
    normalized_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider_message_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class EventStreamItem(MonitoringModel):
    event_id: str
    stream_offset: int
    standard_message_id: str
    event_type: str = "monitoring.message.created"
    event_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ticker: str
    source_id: str
    payload: JsonObject
    consumed: bool = False


class PollState(MonitoringModel):
    binding_id: str
    source_id: str
    ticker: str
    status: PollStatus = PollStatus.NEVER_POLLED
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_message: str | None = None
    collected_count: int = 0
    historical_skipped_count: int = 0
    raw_inserted_count: int = 0
    duplicate_count: int = 0
    standardized_count: int = 0
    event_count: int = 0
    last_collected_count: int = 0
    last_historical_skipped_count: int = 0
    last_raw_inserted_count: int = 0
    last_duplicate_count: int = 0
    last_standardized_count: int = 0
    last_event_count: int = 0
    last_latency_ms: int | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RawMessageSaveResult(MonitoringModel):
    decision: IngestDecision
    message: RawExternalMessage


class IngestBatchResult(MonitoringModel):
    source_id: str
    binding_id: str
    ticker: str
    collected_count: int = 0
    historical_skipped_count: int = 0
    raw_inserted_count: int = 0
    duplicate_count: int = 0
    standardized_count: int = 0
    event_count: int = 0
    failed_count: int = 0
    error_message: str | None = None
    latency_ms: int | None = None


class MonitoringSnapshot(MonitoringModel):
    sources: list[MonitoringSourceConfig]
    bindings: list[TickerSourceBinding]
    poll_states: list[PollState]
    recent_raw_messages: list[RawExternalMessage]
    recent_standard_messages: list[StandardMessage]
    recent_events: list[EventStreamItem]


def binding_id_for(ticker: str, source_id: str) -> str:
    return f"{ticker.strip().upper()}:{source_id.strip().lower()}"


def new_monitoring_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def payload_hash(payload: JsonObject) -> str:
    return sha256_text(canonical_json(payload))


def dedupe_key_for(
    *,
    source_id: str,
    provider_message_id: str | None,
    source_url: str | None,
    raw_payload: JsonObject,
) -> str:
    if provider_message_id:
        return f"{source_id}:provider_id:{provider_message_id}"
    if source_url:
        return f"{source_id}:url:{source_url}"
    return f"{source_id}:payload:{payload_hash(raw_payload)}"


def parameter_schema_for_source(source_id: str) -> dict[str, int]:
    return SOURCE_PARAMETER_SCHEMAS.get(source_id.strip().lower(), {})


def validate_parameters_for_source(
    source_id: str,
    parameters: MonitoringParameters,
) -> MonitoringParameters:
    normalized_source = source_id.strip().lower()
    schema = parameter_schema_for_source(normalized_source)
    unsupported = [
        field
        for field in parameters.non_empty_fields()
        if field not in schema
    ]
    if unsupported:
        allowed = ", ".join(schema) if schema else "ticker only"
        raise ValueError(
            f"{normalized_source} does not support parameter fields: "
            f"{', '.join(unsupported)}. Allowed fields: {allowed}."
        )
    for field, max_items in schema.items():
        values = getattr(parameters, field)
        if len(values) > max_items:
            raise ValueError(
                f"{normalized_source}.{field} supports at most {max_items} "
                f"item(s), got {len(values)}."
            )
    return parameters


def default_source_configs() -> list[MonitoringSourceConfig]:
    now = datetime.now(UTC)
    return [
        MonitoringSourceConfig(
            source_id="benzinga_news",
            provider=MonitoringProvider.BENZINGA,
            display_name="Benzinga News API",
            source_type=SourceType.MEDIA,
            interface_type=InterfaceType.BY_TICKER,
            endpoint_kind=EndpointKind.BENZINGA_NEWS,
            poll_interval_seconds=60,
            required_api_key_env="BENZINGA_API_KEY",
            config={"page_size": 50, "display_output": "full"},
            created_at=now,
            updated_at=now,
        ),
        MonitoringSourceConfig(
            source_id="finnhub_company_news",
            provider=MonitoringProvider.FINNHUB,
            display_name="Finnhub Company News API",
            source_type=SourceType.MEDIA,
            interface_type=InterfaceType.BY_TICKER,
            endpoint_kind=EndpointKind.FINNHUB_COMPANY_NEWS,
            poll_interval_seconds=60,
            required_api_key_env="FINNHUB_API_KEY",
            config={"lookback_days": 3},
            created_at=now,
            updated_at=now,
        ),
        MonitoringSourceConfig(
            source_id="stocktwits_messages",
            provider=MonitoringProvider.STOCKTWITS,
            display_name="Stocktwits Messages API",
            source_type=SourceType.SOCIAL,
            interface_type=InterfaceType.BY_TICKER,
            endpoint_kind=EndpointKind.STOCKTWITS_MESSAGES,
            poll_interval_seconds=600,
            required_api_key_env="STOCKTWITS_RAPIDAPI_KEY",
            config={
                "mode": "rapidapi_or_public",
                "rapidapi_path": "/functions/v1/stocktwits-query",
                "public_path_template": "/streams/symbol/{symbol}.json",
                "action": "messages",
                "lookback_days": 2,
                "limit": 100,
                "primary_only": True,
                "force_refresh": False,
                "timeout_seconds": 45,
            },
            created_at=now,
            updated_at=now,
        ),
        MonitoringSourceConfig(
            source_id="tikhub_x_search",
            provider=MonitoringProvider.TIKHUB,
            display_name="TikHub X Search API",
            source_type=SourceType.SOCIAL,
            interface_type=InterfaceType.BY_PARAMETER,
            endpoint_kind=EndpointKind.TIKHUB_X_SEARCH,
            poll_interval_seconds=600,
            required_api_key_env="TIKHUB_API_KEY",
            config={"search_type": "Latest"},
            created_at=now,
            updated_at=now,
        ),
        MonitoringSourceConfig(
            source_id="tikhub_x_user_posts",
            provider=MonitoringProvider.TIKHUB,
            display_name="TikHub X User Posts API",
            source_type=SourceType.SOCIAL,
            interface_type=InterfaceType.BY_PARAMETER,
            endpoint_kind=EndpointKind.TIKHUB_X_USER_POSTS,
            poll_interval_seconds=600,
            required_api_key_env="TIKHUB_API_KEY",
            config={},
            created_at=now,
            updated_at=now,
        ),
        MonitoringSourceConfig(
            source_id="newswire_rss",
            provider=MonitoringProvider.NEWSWIRE_RSS,
            display_name="Newswire RSS",
            source_type=SourceType.MEDIA,
            interface_type=InterfaceType.BY_PARAMETER,
            endpoint_kind=EndpointKind.RSS_FEED,
            poll_interval_seconds=600,
            required_api_key_env=None,
            config={},
            created_at=now,
            updated_at=now,
        ),
    ]
