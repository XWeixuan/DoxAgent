"""Initial source registry and default monitoring profile."""

from __future__ import annotations

from doxagent.message_bus_v2.schema import (
    AcquisitionMode,
    ContentEnrichmentMode,
    DefaultMonitoringProfile,
    DefaultProfileEntry,
    DistributionPolicy,
    PollingConfig,
    SchedulerConstraints,
    SearchPolicy,
    SourceDefinition,
    SourceKind,
    StreamingConfig,
    UpdateActor,
)


def _source(
    source_id: str,
    display_name: str,
    *,
    kind: SourceKind = SourceKind.API,
    scheduler_group: str | None = None,
    properties: dict[str, object] | None = None,
    default_parameters: dict[str, object] | None = None,
    required: list[str] | None = None,
    content_enrichment_mode: ContentEnrichmentMode = ContentEnrichmentMode.ENRICH,
    minimum_request_gap_seconds: int = 1,
    default_polling: PollingConfig | None = None,
    acquisition_mode: AcquisitionMode = AcquisitionMode.BY_TICKER,
    content_language: str | None = None,
    entry_url: str | None = None,
    site_id: str | None = None,
    enabled: bool = True,
    search_policy: SearchPolicy | None = None,
    distribution_policy: DistributionPolicy | None = None,
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        display_name=display_name,
        kind=kind,
        adapter_ref=f"builtin:{source_id}",
        acquisition_mode=acquisition_mode,
        content_language=content_language,
        entry_url=entry_url,
        site_id=site_id,
        enabled=enabled,
        search_policy=search_policy,
        distribution_policy=distribution_policy,
        parameter_schema={
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
        default_parameters=default_parameters or {},
        scheduler_group=scheduler_group or source_id,
        scheduler_constraints=SchedulerConstraints(
            minimum_request_gap_seconds=minimum_request_gap_seconds,
            max_concurrency=1,
        ),
        default_polling_config=default_polling or PollingConfig(),
        content_enrichment_mode=content_enrichment_mode,
        updated_by=UpdateActor.SYSTEM,
        updated_reason="initial Message Bus v2 manifest",
    )


def initial_sources() -> list[SourceDefinition]:
    string_array = {"type": "array", "items": {"type": "string", "minLength": 1}}
    return [
        _source(
            "benzinga_news",
            "Benzinga News API",
            properties={"search_terms": {**string_array, "maxItems": 3}},
        ),
        _source("finnhub_company_news", "Finnhub Company News API"),
        _source(
            "stocktwits_messages",
            "Stocktwits Messages API",
            content_enrichment_mode=ContentEnrichmentMode.SKIP,
        ),
        _source(
            "tikhub_x_search",
            "TikHub X Search API",
            scheduler_group="tikhub",
            properties={"search_terms": {**string_array, "minItems": 1, "maxItems": 3}},
            required=["search_terms"],
            content_enrichment_mode=ContentEnrichmentMode.SKIP,
        ),
        _source(
            "tikhub_x_user_posts",
            "TikHub X User Posts API",
            scheduler_group="tikhub",
            properties={"usernames": {**string_array, "minItems": 1, "maxItems": 2}},
            required=["usernames"],
            content_enrichment_mode=ContentEnrichmentMode.SKIP,
        ),
        _source(
            "newswire_rss",
            "Newswire RSS",
            properties={"rss_urls": {**string_array, "minItems": 1, "maxItems": 3}},
            required=["rss_urls"],
        ),
        _source(
            "yahoo_finance_news",
            "Yahoo Finance News",
            properties={
                "snippet_count": {"type": "integer", "minimum": 10, "maximum": 200},
                "page_network_enabled": {"type": "boolean", "default": True},
            },
        ),
        _source("ibkr_news", "IBKR News API", scheduler_group="ibkr_news"),
        _source(
            "reuters_site_search",
            "Reuters Site Search",
            kind=SourceKind.CRAWLER,
            acquisition_mode=AcquisitionMode.BY_SEARCH,
            content_language="en",
            site_id="reuters",
            search_policy=SearchPolicy(mode="separate"),
            properties={
                "company_short_name": {"type": "string", "minLength": 1},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 10},
            },
        ),
        _source(
            "google_news_search_rss",
            "Google News Search RSS",
            acquisition_mode=AcquisitionMode.BY_SEARCH,
            content_language="en",
            search_policy=SearchPolicy(mode="or"),
            properties={
                "search_terms": {**string_array, "minItems": 1, "maxItems": 10},
                "domains": {**string_array, "maxItems": 10},
            },
            required=[],
        ),
        _source(
            "ctee_semiconductor",
            "工商时报半导体栏目",
            kind=SourceKind.CRAWLER,
            acquisition_mode=AcquisitionMode.BY_DISTRIBUTION,
            content_language="zh-Hant",
            entry_url="https://www.ctee.com.tw/industry/semi",
            site_id="ctee",
            enabled=False,
            distribution_policy=DistributionPolicy(jev_enabled=True),
            properties={"max_pages": {"type": "integer", "minimum": 1, "maximum": 20}},
            default_parameters={"max_pages": 8},
            default_polling=PollingConfig(target_interval_seconds=300),
            minimum_request_gap_seconds=3,
        ),
    ]


def initial_default_profile() -> DefaultMonitoringProfile:
    polling = PollingConfig(
        target_interval_seconds=60,
        tolerance_ratio=0.10,
        alert_after_seconds=1800,
        active_windows=[],
    )
    return DefaultMonitoringProfile(
        profile_id="default",
        entries=[
            DefaultProfileEntry(
                source_id="benzinga_news",
                polling=polling,
                streaming=StreamingConfig(),
            ),
            DefaultProfileEntry(
                source_id="finnhub_company_news",
                polling=polling,
                streaming=StreamingConfig(),
            ),
            DefaultProfileEntry(
                source_id="yahoo_finance_news",
                polling=polling,
                streaming=StreamingConfig(),
            ),
            DefaultProfileEntry(
                source_id="ibkr_news",
                polling=polling,
                streaming=StreamingConfig(),
            ),
            DefaultProfileEntry(
                source_id="reuters_site_search",
                polling=polling,
                streaming=StreamingConfig(),
            ),
        ],
        updated_by=UpdateActor.SYSTEM,
        updated_reason=(
            "initial profile: Benzinga, Finnhub, Yahoo, IBKR and Reuters; shared calendar owns "
            "continuous-session "
            "polling and the 02:00 ET closed-day sweep"
        ),
    )


__all__ = ["initial_default_profile", "initial_sources"]
