"""Initial source registry and default monitoring profile."""

from __future__ import annotations

from doxagent.message_bus_v2.schema import (
    ContentEnrichmentMode,
    DefaultMonitoringProfile,
    DefaultProfileEntry,
    PollingConfig,
    SchedulerConstraints,
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
    required: list[str] | None = None,
    content_enrichment_mode: ContentEnrichmentMode = ContentEnrichmentMode.ENRICH,
) -> SourceDefinition:
    return SourceDefinition(
        source_id=source_id,
        display_name=display_name,
        kind=kind,
        adapter_ref=f"builtin:{source_id}",
        parameter_schema={
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
        scheduler_group=scheduler_group or source_id,
        scheduler_constraints=SchedulerConstraints(
            minimum_request_gap_seconds=1,
            max_concurrency=1,
        ),
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
        ],
        updated_by=UpdateActor.SYSTEM,
        updated_reason=(
            "initial profile: Benzinga and Finnhub; shared calendar owns continuous-session "
            "polling and the 02:00 ET closed-day sweep"
        ),
    )


__all__ = ["initial_default_profile", "initial_sources"]
