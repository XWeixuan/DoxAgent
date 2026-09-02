"""Initial source registry and default monitoring profile."""

from __future__ import annotations

from datetime import time

from doxagent.message_bus_v2.schema import (
    ActiveWindow,
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
        _source("stocktwits_messages", "Stocktwits Messages API"),
        _source(
            "tikhub_x_search",
            "TikHub X Search API",
            scheduler_group="tikhub",
            properties={"search_terms": {**string_array, "minItems": 1, "maxItems": 3}},
            required=["search_terms"],
        ),
        _source(
            "tikhub_x_user_posts",
            "TikHub X User Posts API",
            scheduler_group="tikhub",
            properties={"usernames": {**string_array, "minItems": 1, "maxItems": 2}},
            required=["usernames"],
        ),
        _source(
            "newswire_rss",
            "Newswire RSS",
            properties={"rss_urls": {**string_array, "minItems": 1, "maxItems": 3}},
            required=["rss_urls"],
        ),
    ]


def initial_default_profile() -> DefaultMonitoringProfile:
    window = ActiveWindow(
        timezone="America/New_York",
        weekdays=[0, 1, 2, 3, 4],
        start_time=time(7, 0),
        end_time=time(18, 0),
    )
    polling = PollingConfig(
        target_interval_seconds=60,
        tolerance_ratio=0.10,
        alert_after_seconds=1800,
        active_windows=[window],
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
        updated_reason="initial profile: Benzinga and Finnhub only",
    )


__all__ = ["initial_default_profile", "initial_sources"]
