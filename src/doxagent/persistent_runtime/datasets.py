"""Dataset helpers for Persistent Runtime Execution replay preparation."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from doxagent.monitoring.collectors import (
    CollectorError,
    MissingCredentialError,
    MonitoringCollectorRegistry,
)
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.schema import (
    EventStreamItem,
    IngestBatchResult,
    InterfaceType,
    JsonObject,
    MonitoringParameters,
    MonitoringSourceConfig,
    SourceType,
    TickerSourceBinding,
    UpdateActor,
)
from doxagent.monitoring.service import MonitoringBusService
from doxagent.settings import DoxAgentSettings

DATASET_SCHEMA_VERSION = 1
DEFAULT_SQLITE_PATH = Path(".tmp/monitoring_message_bus.sqlite3")


class RuntimeDatasetManifest(BaseModel):
    schema_version: int = DATASET_SCHEMA_VERSION
    dataset_id: str
    ticker: str
    source_types: list[SourceType]
    window_start: datetime | None = None
    window_end: datetime | None = None
    event_count: int = 0
    order: str = "event_time,published_at,stream_offset"
    source: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: list[str] = Field(default_factory=list)


class RuntimeReplayDataset(BaseModel):
    manifest: RuntimeDatasetManifest
    events: list[EventStreamItem]

    def source_type_counts(self) -> dict[str, int]:
        counts = {SourceType.MEDIA.value: 0, SourceType.SOCIAL.value: 0}
        for event in self.events:
            source_type = event_source_type(event).value
            counts[source_type] = counts.get(source_type, 0) + 1
        return counts


class RuntimeContextBundle(BaseModel):
    ticker: str | None = None
    source_run_id: str | None = None
    document3_run_id: str | None = None
    context: JsonObject
    missing: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @property
    def complete_for_runtime(self) -> bool:
        return not self.missing


@dataclass
class DatasetBuildResult:
    dataset: RuntimeReplayDataset
    ingest_results: list[IngestBatchResult] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def dataset_id_for(
    *,
    ticker: str,
    source_types: Iterable[SourceType],
    window_start: datetime | None,
    window_end: datetime | None,
) -> str:
    type_part = "-".join(sorted(source_type.value for source_type in source_types)) or "none"
    start_part = _compact_dt(window_start) if window_start else "unknown-start"
    end_part = _compact_dt(window_end) if window_end else "unknown-end"
    return f"{ticker.strip().upper()}-{type_part}-{start_part}-{end_part}"


def clean_events_for_dataset(
    events: Iterable[EventStreamItem],
    *,
    ticker: str | None = None,
    source_type: SourceType | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    reassign_offsets: bool = True,
) -> list[EventStreamItem]:
    """Filter, normalize, and sort monitoring events for deterministic replay."""

    normalized_ticker = ticker.strip().upper() if ticker else None
    start_utc = _as_utc(window_start)
    end_utc = _as_utc(window_end)
    cleaned: list[EventStreamItem] = []
    for event in events:
        if normalized_ticker is not None and event.ticker.strip().upper() != normalized_ticker:
            continue
        event_type = event_source_type(event)
        if source_type is not None and event_type is not source_type:
            continue
        event_dt = event_observed_at(event)
        if start_utc is not None and event_dt < start_utc:
            continue
        if end_utc is not None and event_dt > end_utc:
            continue
        cleaned.append(_normalized_event(event, ticker=normalized_ticker, source_type=event_type))
    cleaned.sort(key=event_sort_key)
    if reassign_offsets:
        cleaned = [
            event.model_copy(update={"stream_offset": index}, deep=True)
            for index, event in enumerate(cleaned, start=1)
        ]
    return cleaned


def build_dataset(
    events: Iterable[EventStreamItem],
    *,
    ticker: str,
    source_types: Iterable[SourceType] | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    source: JsonObject | None = None,
    notes: Iterable[str] = (),
) -> RuntimeReplayDataset:
    requested_types = list(source_types or [SourceType.MEDIA, SourceType.SOCIAL])
    event_list = list(events)
    cleaned: list[EventStreamItem] = []
    for requested_type in requested_types:
        cleaned.extend(
            clean_events_for_dataset(
                event_list,
                ticker=ticker,
                source_type=requested_type,
                window_start=window_start,
                window_end=window_end,
                reassign_offsets=False,
            )
        )
    cleaned.sort(key=event_sort_key)
    cleaned = [
        event.model_copy(update={"stream_offset": index}, deep=True)
        for index, event in enumerate(cleaned, start=1)
    ]
    observed_types = sorted(
        {event_source_type(event) for event in cleaned},
        key=lambda item: item.value,
    )
    if not observed_types:
        observed_types = sorted(set(requested_types), key=lambda item: item.value)
    manifest = RuntimeDatasetManifest(
        dataset_id=dataset_id_for(
            ticker=ticker,
            source_types=observed_types,
            window_start=window_start,
            window_end=window_end,
        ),
        ticker=ticker.strip().upper(),
        source_types=observed_types,
        window_start=_as_utc(window_start),
        window_end=_as_utc(window_end),
        event_count=len(cleaned),
        source=source or {},
        notes=list(notes),
    )
    return RuntimeReplayDataset(manifest=manifest, events=cleaned)


def write_dataset(dataset: RuntimeReplayDataset, events_path: str | Path) -> tuple[Path, Path]:
    event_file = Path(events_path)
    manifest_file = event_file.with_suffix(event_file.suffix + ".manifest.json")
    event_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        dataset.manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    with event_file.open("w", encoding="utf-8", newline="\n") as handle:
        for event in dataset.events:
            handle.write(event.model_dump_json())
            handle.write("\n")
    return event_file, manifest_file


def read_dataset(
    events_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> RuntimeReplayDataset:
    event_file = Path(events_path)
    resolved_manifest_path = (
        Path(manifest_path)
        if manifest_path is not None
        else event_file.with_suffix(event_file.suffix + ".manifest.json")
    )
    events: list[EventStreamItem] = []
    if event_file.exists():
        for line_number, line in enumerate(
            event_file.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(EventStreamItem.model_validate_json(stripped))
            except ValueError as exc:
                raise ValueError(f"Invalid dataset event JSONL at line {line_number}.") from exc
    if resolved_manifest_path.exists():
        manifest = RuntimeDatasetManifest.model_validate_json(
            resolved_manifest_path.read_text(encoding="utf-8")
        )
    else:
        inferred_types = sorted(
            {event_source_type(event) for event in events},
            key=lambda item: item.value,
        )
        manifest = RuntimeDatasetManifest(
            dataset_id=dataset_id_for(
                ticker=events[0].ticker if events else "UNKNOWN",
                source_types=inferred_types,
                window_start=None,
                window_end=None,
            ),
            ticker=events[0].ticker if events else "UNKNOWN",
            source_types=inferred_types,
            event_count=len(events),
            notes=["manifest inferred from JSONL; original manifest was absent."],
        )
    return RuntimeReplayDataset(manifest=manifest, events=clean_events_for_dataset(events))


def count_monitoring_sqlite_events(
    sqlite_path: str | Path = DEFAULT_SQLITE_PATH,
    *,
    ticker: str | None = None,
    days: int | None = None,
    source_type: SourceType | None = None,
) -> int:
    db_path = Path(sqlite_path)
    if not db_path.exists():
        return 0
    clauses, params = _event_query_filters(ticker=ticker, days=days, source_type=source_type)
    where = f"where {' and '.join(clauses)}" if clauses else ""
    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            f"""
            select count(*)
            from monitoring_event_stream events
            left join monitoring_standard_messages standard
                on events.standard_message_id = standard.standard_message_id
            {where}
            """,
            params,
        ).fetchone()[0]
    return int(count)


def export_monitoring_sqlite_dataset(
    sqlite_path: str | Path = DEFAULT_SQLITE_PATH,
    *,
    ticker: str,
    days: int = 7,
    source_type: SourceType | None = None,
    now: datetime | None = None,
) -> RuntimeReplayDataset:
    db_path = Path(sqlite_path)
    if not db_path.exists():
        return build_dataset(
            [],
            ticker=ticker,
            source_types=[source_type] if source_type else [SourceType.MEDIA, SourceType.SOCIAL],
            window_start=_window_start(days=days, now=now),
            window_end=_as_utc(now) or datetime.now(UTC),
            source={"type": "monitoring_sqlite", "path": str(db_path), "found": False},
            notes=["monitoring SQLite database was not found."],
        )
    clauses, params = _event_query_filters(
        ticker=ticker,
        days=days,
        source_type=source_type,
        now=now,
    )
    where = f"where {' and '.join(clauses)}" if clauses else ""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            select events.*
            from monitoring_event_stream events
            left join monitoring_standard_messages standard
                on events.standard_message_id = standard.standard_message_id
            {where}
            order by
                coalesce(standard.published_at, events.event_time) asc,
                events.stream_offset asc
            """,
            params,
        ).fetchall()
    events = [_event_from_sqlite_row(row) for row in rows]
    return build_dataset(
        events,
        ticker=ticker,
        source_types=[source_type] if source_type else [SourceType.MEDIA, SourceType.SOCIAL],
        window_start=_window_start(days=days, now=now),
        window_end=_as_utc(now) or datetime.now(UTC),
        source={"type": "monitoring_sqlite", "path": str(db_path), "found": True},
    )


def fetch_live_dataset(
    *,
    ticker: str,
    days: int = 7,
    source_type: SourceType | None = None,
    source_ids: Iterable[str] | None = None,
    search_terms: Iterable[str] = (),
    usernames: Iterable[str] = (),
    rss_urls: Iterable[str] = (),
    limit_per_source: int | None = None,
    settings: DoxAgentSettings | None = None,
    collectors: MonitoringCollectorRegistry | None = None,
    now: datetime | None = None,
) -> DatasetBuildResult:
    """Collect a compact live dataset through the existing Monitoring Bus path."""

    resolved_settings = settings or DoxAgentSettings()
    repository = InMemoryMonitoringRepository()
    collector_registry = collectors or MonitoringCollectorRegistry(resolved_settings)
    bus = MonitoringBusService(
        repository,
        collectors=collector_registry,
    )
    window_start = _window_start(days=days, now=now)
    window_end = _as_utc(now) or datetime.now(UTC)
    requested_sources = [
        source_id.strip().lower()
        for source_id in (source_ids or _default_source_ids(source_type))
        if source_id.strip()
    ]
    diagnostics: list[str] = []
    ingest_results: list[IngestBatchResult] = []
    for source_id in requested_sources:
        source = repository.get_source(source_id)
        if source is None:
            diagnostics.append(f"source {source_id} is not configured.")
            continue
        if source_type is not None and source.source_type is not source_type:
            diagnostics.append(
                f"source {source_id} skipped because it is {source.source_type.value}."
            )
            continue
        if source.interface_type is InterfaceType.BY_PARAMETER and not _has_required_parameters(
            source,
            search_terms=search_terms,
            usernames=usernames,
            rss_urls=rss_urls,
        ):
            diagnostics.append(f"source {source_id} skipped because required parameters are empty.")
            continue
        source = repository.upsert_source(
            _source_for_dataset(source, days=days, limit_per_source=limit_per_source)
        )
        binding = bus.configure_ticker_source(
            ticker,
            source.source_id,
            parameters=_parameters_for_source(
                source,
                search_terms=search_terms,
                usernames=usernames,
                rss_urls=rss_urls,
            ),
            updated_by=UpdateActor.USER,
            updated_reason="persistent runtime replay dataset build",
            merge=False,
        )
        binding = _move_in_memory_binding_watermark(
            repository,
            binding_id=binding.binding_id,
            watermark=window_start,
        )
        try:
            collector = collector_registry.collector_for(source)
            fetched = collector.collect(source=source, binding=binding)
        except MissingCredentialError as exc:
            diagnostics.append(f"source {source.source_id} skipped: {exc}")
            continue
        except CollectorError as exc:
            diagnostics.append(f"source {source.source_id} failed: {exc}")
            continue
        filtered = [
            item
            for item in fetched
            if item.source_published_at is None
            or item.source_published_at.astimezone(UTC) >= window_start
        ]
        if source.source_type is SourceType.SOCIAL:
            filtered = [_with_social_dataset_metadata(item) for item in filtered]
        result = bus.ingest_fetched(source=source, fetched=filtered)
        ingest_results.append(result)
        diagnostics.append(
            f"source {source.source_id}: fetched={len(fetched)} "
            f"kept={len(filtered)} events={result.event_count} duplicates={result.duplicate_count}"
        )
    events = repository.recent_events(ticker=ticker, limit=100_000)
    dataset = build_dataset(
        events,
        ticker=ticker,
        source_types=[source_type] if source_type else [SourceType.MEDIA, SourceType.SOCIAL],
        window_start=window_start,
        window_end=window_end,
        source={
            "type": "live_monitoring_collectors",
            "source_ids": requested_sources,
            "days": days,
        },
        notes=["dataset was collected into an in-memory Monitoring Bus repository."],
    )
    return DatasetBuildResult(
        dataset=dataset,
        ingest_results=ingest_results,
        diagnostics=diagnostics,
    )


def load_runtime_context_from_exports(
    *,
    source_run_export: str | Path,
    document3_export: str | Path | None = None,
) -> RuntimeContextBundle:
    """Load runtime context from Brief State exports without mutating Blackboard state."""

    source_path = Path(source_run_export)
    diagnostics: list[str] = []
    missing: list[str] = []
    if not source_path.exists():
        raise FileNotFoundError(f"source run export not found: {source_path}")
    source_data = _read_json(source_path)
    document3_data: JsonObject | None = None
    if document3_export is not None:
        document3_path = Path(document3_export)
        if document3_path.exists() and document3_path.stat().st_size > 0:
            document3_data = _read_json(document3_path)
        else:
            diagnostics.append(f"document3 export not found or empty: {document3_path}")
    source_run_id = _run_id_from_export(source_data)
    document3_run_id = _run_id_from_export(document3_data) if document3_data else None
    global_research = _first_document(source_data, "global_research")
    expectation_units = _documents(source_data, "expectation_unit") or _brief_list(
        source_data,
        "expectation_units",
    )
    doc3_source = document3_data or source_data
    known_events_document = _first_document(doc3_source, "known_events")
    monitoring_config = _first_document(doc3_source, "monitoring_config")
    monitoring_policy = _first_document(doc3_source, "monitoring_policy")
    if global_research is None:
        missing.append("global_research")
    if not expectation_units:
        missing.append("expectation_units")
    if known_events_document is None:
        missing.append("known_events")
    if monitoring_config is None:
        missing.append("monitoring_config")
    if monitoring_policy is None:
        missing.append("monitoring_policy")
    ticker = _first_text(
        global_research,
        known_events_document,
        monitoring_config,
        monitoring_policy,
        field="ticker",
    )
    policies = _runtime_policy_list(monitoring_policy)
    context: JsonObject = {
        "document_source_run_id": source_run_id,
        "document3_run_id": document3_run_id,
        "global_research": global_research or {},
        "expectation_units": expectation_units,
        "known_events_document": known_events_document or {},
        "known_events": _known_events_list(known_events_document),
        "monitoring_config": monitoring_config or {},
        "monitoring_policy": monitoring_policy or {},
        "monitoring_policies": policies,
    }
    if monitoring_policy and monitoring_policy.get("cache_rules"):
        diagnostics.append(
            "monitoring_policy contains legacy cache_rules; runtime context excludes them "
            "from W2 monitoring_policies."
        )
    if not policies:
        diagnostics.append("no direct_trade or push_to_agent monitoring policies were extracted.")
    return RuntimeContextBundle(
        ticker=ticker,
        source_run_id=source_run_id,
        document3_run_id=document3_run_id,
        context=context,
        missing=missing,
        diagnostics=diagnostics,
    )


def compact_runtime_context(context: JsonObject) -> JsonObject:
    """Reduce Brief State export context to the bounded runtime O3 surface."""

    compact: JsonObject = {
        "document_source_run_id": context.get("document_source_run_id"),
        "document3_run_id": context.get("document3_run_id"),
        "known_events": _compact_known_events(context.get("known_events")),
        "monitoring_policies": _compact_policies(context.get("monitoring_policies")),
        "source_confidence_policy": (
            "Use source credibility from the runtime system prompt; policies do not carry "
            "per-rule source_condition fields."
        ),
    }
    global_research = context.get("global_research")
    known_events_document = context.get("known_events_document")
    monitoring_config = context.get("monitoring_config")
    monitoring_policy = context.get("monitoring_policy")
    ticker = _first_text(
        global_research if isinstance(global_research, dict) else None,
        known_events_document if isinstance(known_events_document, dict) else None,
        monitoring_config if isinstance(monitoring_config, dict) else None,
        monitoring_policy if isinstance(monitoring_policy, dict) else None,
        field="ticker",
    )
    if ticker:
        compact["ticker"] = ticker
    expectation_summaries = _compact_expectations(context.get("expectation_units"))
    if expectation_summaries:
        compact["expectation_summaries"] = expectation_summaries
    return compact


def event_source_type(event: EventStreamItem) -> SourceType:
    raw = event.payload.get("source_type")
    if isinstance(raw, SourceType):
        return raw
    if isinstance(raw, str) and raw.strip():
        return SourceType(raw.strip().lower())
    source_id = event.source_id.lower()
    if any(token in source_id for token in ("stocktwits", "tikhub", "x_", "social")):
        return SourceType.SOCIAL
    return SourceType.MEDIA


def event_observed_at(event: EventStreamItem) -> datetime:
    for key in ("published_at", "source_published_at", "collected_at"):
        parsed = _parse_dt(event.payload.get(key))
        if parsed is not None:
            return parsed
    return event.event_time.astimezone(UTC)


def event_sort_key(event: EventStreamItem) -> tuple[datetime, int, str]:
    return (event_observed_at(event), event.stream_offset, event.event_id)


def _normalized_event(
    event: EventStreamItem,
    *,
    ticker: str | None,
    source_type: SourceType,
) -> EventStreamItem:
    normalized_ticker = (ticker or event.ticker).strip().upper()
    payload = dict(event.payload)
    payload["ticker"] = str(payload.get("ticker") or normalized_ticker).strip().upper()
    payload["source_id"] = str(payload.get("source_id") or event.source_id).strip().lower()
    payload["standard_message_id"] = str(
        payload.get("standard_message_id") or event.standard_message_id
    )
    payload["source_type"] = source_type.value
    metadata = dict(payload.get("metadata") or {})
    if source_type is SourceType.SOCIAL:
        metadata.setdefault("batch_window_id", _batch_window_for_event(event))
        metadata.setdefault(
            "item_id",
            str(
                payload.get("provider_message_id")
                or payload.get("id")
                or payload.get("message_id")
                or event.standard_message_id
            ),
        )
    payload["metadata"] = metadata
    return event.model_copy(
        update={
            "ticker": normalized_ticker,
            "source_id": payload["source_id"],
            "payload": payload,
            "consumed": False,
        },
        deep=True,
    )


def _event_query_filters(
    *,
    ticker: str | None,
    days: int | None,
    source_type: SourceType | None,
    now: datetime | None = None,
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if ticker is not None:
        clauses.append("events.ticker = ?")
        params.append(ticker.strip().upper())
    if days is not None:
        clauses.append("coalesce(standard.published_at, events.event_time) >= ?")
        params.append(_dt_json(_window_start(days=days, now=now)))
    if source_type is not None:
        clauses.append("standard.source_type = ?")
        params.append(source_type.value)
    return clauses, params


def _event_from_sqlite_row(row: sqlite3.Row) -> EventStreamItem:
    return EventStreamItem(
        event_id=str(row["event_id"]),
        stream_offset=int(row["stream_offset"]),
        standard_message_id=str(row["standard_message_id"]),
        event_type=str(row["event_type"]),
        event_time=_parse_dt(row["event_time"]) or datetime.now(UTC),
        ticker=str(row["ticker"]),
        source_id=str(row["source_id"]),
        payload=dict(json.loads(str(row["payload_json"]))),
        consumed=bool(row["consumed"]),
    )


def _default_source_ids(source_type: SourceType | None) -> list[str]:
    if source_type is SourceType.MEDIA:
        return ["benzinga_news", "finnhub_company_news"]
    if source_type is SourceType.SOCIAL:
        return ["stocktwits_messages"]
    return ["benzinga_news", "finnhub_company_news", "stocktwits_messages"]


def _source_for_dataset(
    source: MonitoringSourceConfig,
    *,
    days: int,
    limit_per_source: int | None,
) -> MonitoringSourceConfig:
    config = dict(source.config)
    if "lookback_days" in config:
        config["lookback_days"] = days
    if source.source_id == "finnhub_company_news":
        config["lookback_days"] = days
    if source.source_id == "stocktwits_messages":
        config["lookback_days"] = days
        if limit_per_source is not None:
            config["limit"] = min(max(1, limit_per_source), 500)
    if source.source_id == "benzinga_news" and limit_per_source is not None:
        config["page_size"] = min(max(1, limit_per_source), 100)
    return source.model_copy(update={"config": config}, deep=True)


def _parameters_for_source(
    source: MonitoringSourceConfig,
    *,
    search_terms: Iterable[str],
    usernames: Iterable[str],
    rss_urls: Iterable[str],
) -> MonitoringParameters:
    if source.source_id in {"benzinga_news", "tikhub_x_search"}:
        return MonitoringParameters(search_terms=list(search_terms))
    if source.source_id == "tikhub_x_user_posts":
        return MonitoringParameters(usernames=list(usernames))
    if source.source_id == "newswire_rss":
        return MonitoringParameters(rss_urls=list(rss_urls))
    return MonitoringParameters()


def _has_required_parameters(
    source: MonitoringSourceConfig,
    *,
    search_terms: Iterable[str],
    usernames: Iterable[str],
    rss_urls: Iterable[str],
) -> bool:
    if source.source_id == "tikhub_x_search":
        return any(item.strip() for item in search_terms)
    if source.source_id == "tikhub_x_user_posts":
        return any(item.strip() for item in usernames)
    if source.source_id == "newswire_rss":
        return any(item.strip() for item in rss_urls)
    return True


def _with_social_dataset_metadata(item: Any) -> Any:
    published = item.source_published_at or datetime.now(UTC)
    metadata = dict(item.metadata)
    metadata.setdefault("batch_window_id", published.astimezone(UTC).strftime("%Y%m%d%H%M"))
    metadata.setdefault(
        "item_id",
        str(item.provider_message_id or item.raw_payload.get("id") or item.raw_payload),
    )
    return item.model_copy(update={"metadata": metadata}, deep=True)


def _move_in_memory_binding_watermark(
    repository: InMemoryMonitoringRepository,
    *,
    binding_id: str,
    watermark: datetime,
) -> TickerSourceBinding:
    # Dataset backfills intentionally ingest historical rows inside the requested window.
    binding = repository._bindings[binding_id]
    updated = binding.model_copy(update={"updated_at": watermark}, deep=True)
    repository._bindings[binding_id] = updated
    return updated.model_copy(deep=True)


def _read_json(path: Path) -> JsonObject:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object JSON export: {path}")
    return value


def _run_id_from_export(data: JsonObject | None) -> str | None:
    if not data:
        return None
    export_meta = data.get("export_metadata")
    if isinstance(export_meta, dict) and export_meta.get("run_id"):
        return str(export_meta["run_id"])
    brief_state = data.get("brief_state")
    if isinstance(brief_state, dict):
        run = brief_state.get("run")
        if isinstance(run, dict) and run.get("run_id"):
            return str(run["run_id"])
    return None


def _documents(data: JsonObject, document_type: str) -> list[JsonObject]:
    stable_documents = data.get("stable_documents")
    if not isinstance(stable_documents, dict):
        return []
    block = stable_documents.get(document_type)
    if isinstance(block, dict):
        documents: list[JsonObject] = []
        for wrapper in block.values():
            if isinstance(wrapper, dict):
                document = wrapper.get("document")
                if isinstance(document, dict):
                    documents.append(dict(document))
        return documents
    if isinstance(block, list):
        return [dict(item) for item in block if isinstance(item, dict)]
    return []


def _first_document(data: JsonObject, document_type: str) -> JsonObject | None:
    docs = _documents(data, document_type)
    return docs[0] if docs else None


def _brief_list(data: JsonObject, key: str) -> list[JsonObject]:
    brief_state = data.get("brief_state")
    if not isinstance(brief_state, dict):
        return []
    raw = brief_state.get(key)
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    return []


def _known_events_list(document: JsonObject | None) -> list[JsonObject]:
    if not document:
        return []
    raw = document.get("events") or document.get("known_events") or document.get("items") or []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _runtime_policy_list(document: JsonObject | None) -> list[JsonObject]:
    if not document:
        return []
    policies: list[JsonObject] = []
    for key, policy_type in (
        ("direct_trade_rules", "direct_trade"),
        ("push_to_agent_rules", "push_to_agent"),
        ("escalation_rules", "escalate"),
        ("policies", None),
    ):
        raw = document.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            policy = dict(item)
            if policy_type and not (policy.get("policy_type") or policy.get("action_type")):
                policy["policy_type"] = policy_type
            policies.append(policy)
    return policies


def _compact_known_events(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    compact: list[JsonObject] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "event_id": item.get("event_id") or item.get("id"),
                "event_time": item.get("event_time"),
                "event_window": item.get("event_window"),
                "core_fact": _truncate(item.get("core_fact") or item.get("summary"), 500),
                "duplicate_detection_keys": list(item.get("duplicate_detection_keys") or [])[:12],
            }
        )
    return compact


def _compact_policies(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    compact: list[JsonObject] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        policy_type = str(item.get("policy_type") or item.get("action_type") or "").strip()
        if policy_type == "push_to_agent":
            policy_type = "escalate"
        if policy_type not in {"direct_trade", "escalate"}:
            continue
        trigger = item.get("trigger")
        action = item.get("action")
        compact.append(
            {
                "policy_id": item.get("policy_id") or item.get("rule_id") or item.get("id"),
                "policy_type": policy_type,
                "scope": item.get("scope"),
                "trigger": trigger if isinstance(trigger, dict) else None,
                "trigger_condition": _truncate(
                    item.get("trigger_condition")
                    or (trigger.get("condition") if isinstance(trigger, dict) else None)
                    or item.get("name"),
                    500,
                ),
                "confirmation": item.get("confirmation"),
                "action": action if isinstance(action, dict) else None,
                "risk_guard": item.get("risk_guard"),
                "reasoning": _truncate(item.get("reasoning"), 500),
            }
        )
    return compact


def _compact_expectations(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    compact: list[JsonObject] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "expectation_id": item.get("expectation_id") or item.get("document_id"),
                "expectation_name": _truncate(item.get("expectation_name"), 160),
                "direction": item.get("direction"),
                "market_view": _truncate(item.get("market_view"), 400),
                "realized_facts_summary": _truncate(item.get("realized_facts_summary"), 500),
                "event_monitoring_direction": _truncate(
                    item.get("event_monitoring_direction"),
                    400,
                ),
            }
        )
    return compact


def _first_text(*values: JsonObject | None, field: str) -> str | None:
    for value in values:
        if value is None:
            continue
        raw = value.get(field)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None


def _truncate(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _batch_window_for_event(event: EventStreamItem) -> str:
    metadata = event.payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("batch_window_id", "polling_window_id", "poll_window_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return event_observed_at(event).strftime("%Y%m%d%H%M")


def _window_start(*, days: int, now: datetime | None = None) -> datetime:
    return (_as_utc(now) or datetime.now(UTC)) - timedelta(days=days)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None


def _dt_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _compact_dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


__all__ = [
    "DATASET_SCHEMA_VERSION",
    "DEFAULT_SQLITE_PATH",
    "DatasetBuildResult",
    "RuntimeContextBundle",
    "RuntimeDatasetManifest",
    "RuntimeReplayDataset",
    "build_dataset",
    "clean_events_for_dataset",
    "compact_runtime_context",
    "count_monitoring_sqlite_events",
    "event_observed_at",
    "event_sort_key",
    "event_source_type",
    "export_monitoring_sqlite_dataset",
    "fetch_live_dataset",
    "load_runtime_context_from_exports",
    "read_dataset",
    "write_dataset",
]
