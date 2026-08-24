"""Deterministic Step-3 quality metrics over Published consumer views."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import StrictModel
from doxagent.event_library.repository import EventLibraryRepository


class EventLibraryQualityReport(StrictModel):
    ticker: str
    version: int = Field(ge=0)
    active_event_count: int = Field(ge=0)
    active_fact_count: int = Field(ge=0)
    stable_event_id_retention: float = Field(ge=0, le=1)
    known_index_event_coverage: float = Field(ge=0, le=1)
    event_detail_fact_coverage: float = Field(ge=0, le=1)
    reference_important_event_recall: float = Field(ge=0, le=1)
    reference_to_full_payload_ratio: float = Field(ge=0)
    reference_duplicate_event_mentions: int = Field(ge=0)
    runtime_mention_count: Literal[0] = 0
    delta_total: int = Field(ge=0)
    delta_resolution_rate: float = Field(ge=0, le=1)
    pending_delta_ratio: float = Field(ge=0, le=1)
    o2_invalid_bundle_ratio: float = Field(ge=0, le=1)
    o2_repair_run_ratio: float = Field(ge=0, le=1)


def compile_quality_report(
    repository: EventLibraryRepository,
    *,
    ticker: str,
    version: int | None = None,
) -> EventLibraryQualityReport:
    selected = repository.published_version(ticker) if version is None else version
    events = repository.published_events(ticker, selected)
    prior = repository.published_events(ticker, selected - 1) if selected > 1 else []
    event_ids = {event.event_id for event in events}
    prior_ids = {event.event_id for event in prior}
    retained = len(event_ids & prior_ids) / len(prior_ids) if prior_ids else 1.0
    compiler = EventLibraryViewCompiler(repository)
    index_ids = {
        line.split(" | ", 1)[0]
        for line in compiler.known_event_index(ticker, selected).splitlines()
        if line.strip()
    }
    detail_fact_count = sum(
        len(detail.facts)
        for event_id in event_ids
        if (detail := compiler.event_detail(ticker, event_id, selected)) is not None
    )
    fact_count = sum(len(event.facts) for event in events)
    reference = compiler.reference_view(ticker, selected)
    reference_events = list(reference["events"])
    reference_ids = [str(item["event_id"]) for item in reference_events]
    important_ids = {event.event_id for event in events if event.is_important}
    reference_important = important_ids & set(reference_ids)
    full_bytes = len(
        json.dumps(
            [event.model_dump(mode="json") for event in events],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    reference_bytes = len(
        json.dumps(reference, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    operations = repository.operational_quality_counts(ticker)
    delta_total = operations["delta_total"]
    delta_pending = operations["delta_pending"]
    o2_runs = operations["o2_run_total"]
    return EventLibraryQualityReport(
        ticker=ticker.upper(),
        version=selected,
        active_event_count=len(events),
        active_fact_count=fact_count,
        stable_event_id_retention=retained,
        known_index_event_coverage=(len(index_ids & event_ids) / len(event_ids) if events else 1.0),
        event_detail_fact_coverage=(detail_fact_count / fact_count if fact_count else 1.0),
        reference_important_event_recall=(
            len(reference_important) / len(important_ids) if important_ids else 1.0
        ),
        reference_to_full_payload_ratio=(reference_bytes / full_bytes if full_bytes else 0.0),
        reference_duplicate_event_mentions=len(reference_ids) - len(set(reference_ids)),
        delta_total=delta_total,
        delta_resolution_rate=(
            (delta_total - delta_pending) / delta_total if delta_total else 1.0
        ),
        pending_delta_ratio=(delta_pending / delta_total if delta_total else 0.0),
        o2_invalid_bundle_ratio=(
            operations["o2_invalid_bundle_runs"] / o2_runs if o2_runs else 0.0
        ),
        o2_repair_run_ratio=(operations["o2_repair_runs"] / o2_runs if o2_runs else 0.0),
    )
