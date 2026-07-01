"""Persistence backends for the Monitoring Message Bus."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from doxagent.monitoring.media_enrichment import (
    MediaEnrichmentRecord,
    MediaExtractionResult,
    assess_media_body,
    choose_media_fetch_url,
    media_enrichment_metadata,
)
from doxagent.monitoring.schema import (
    EndpointKind,
    EventStreamItem,
    IngestBatchResult,
    IngestDecision,
    InterfaceType,
    MonitoringParameters,
    MonitoringProvider,
    MonitoringSnapshot,
    MonitoringSourceConfig,
    PollState,
    PollStatus,
    RawExternalMessage,
    RawMessageSaveResult,
    SourceType,
    StandardMessage,
    TickerSourceBinding,
    UpdateActor,
    binding_id_for,
    canonical_json,
    default_source_configs,
    new_monitoring_id,
    validate_parameters_for_source,
)


class MonitoringRepository(Protocol):
    def ensure_defaults(self, sources: Iterable[MonitoringSourceConfig] | None = None) -> None:
        ...

    def upsert_source(self, source: MonitoringSourceConfig) -> MonitoringSourceConfig:
        ...

    def get_source(self, source_id: str) -> MonitoringSourceConfig | None:
        ...

    def list_sources(self) -> list[MonitoringSourceConfig]:
        ...

    def set_source_enabled(self, source_id: str, enabled: bool) -> MonitoringSourceConfig:
        ...

    def set_source_poll_interval(self, source_id: str, seconds: int) -> MonitoringSourceConfig:
        ...

    def upsert_binding(
        self,
        *,
        ticker: str,
        source_id: str,
        parameters: MonitoringParameters,
        enabled: bool,
        updated_by: UpdateActor,
        updated_reason: str | None = None,
        merge: bool = True,
    ) -> TickerSourceBinding:
        ...

    def get_binding(self, ticker: str, source_id: str) -> TickerSourceBinding | None:
        ...

    def list_bindings(
        self,
        *,
        ticker: str | None = None,
        source_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[TickerSourceBinding]:
        ...

    def delete_binding(self, ticker: str, source_id: str) -> bool:
        ...

    def delete_ticker_bindings(self, ticker: str) -> int:
        ...

    def save_raw_message(self, message: RawExternalMessage) -> RawMessageSaveResult:
        ...

    def save_standard_message(self, message: StandardMessage) -> StandardMessage:
        ...

    def append_event(self, message: StandardMessage) -> EventStreamItem:
        ...

    def mark_event_consumed(self, event_id: str) -> EventStreamItem | None:
        ...

    def record_poll_attempt(self, *, binding_id: str, source_id: str, ticker: str) -> PollState:
        ...

    def record_poll_success(self, result: IngestBatchResult) -> PollState:
        ...

    def record_poll_failure(
        self,
        *,
        binding_id: str,
        source_id: str,
        ticker: str,
        message: str,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PollState:
        ...

    def list_poll_states(
        self,
        *,
        ticker: str | None = None,
        source_id: str | None = None,
    ) -> list[PollState]:
        ...

    def recent_raw_messages(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[RawExternalMessage]:
        ...

    def recent_standard_messages(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[StandardMessage]:
        ...

    def recent_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[EventStreamItem]:
        ...

    def pending_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[EventStreamItem]:
        ...

    def list_media_enrichment_records(
        self,
        *,
        ticker: str | None = None,
        limit: int = 50,
        incomplete_only: bool = True,
    ) -> list[MediaEnrichmentRecord]:
        ...

    def apply_media_enrichment_results(
        self,
        results: Iterable[MediaExtractionResult],
    ) -> int:
        ...

    def snapshot(self, *, ticker: str | None = None, limit: int = 20) -> MonitoringSnapshot:
        ...


class InMemoryMonitoringRepository:
    """In-process repository for tests and isolated agent runs."""

    def __init__(self) -> None:
        self._sources: dict[str, MonitoringSourceConfig] = {}
        self._bindings: dict[str, TickerSourceBinding] = {}
        self._raw_by_dedupe: dict[str, RawExternalMessage] = {}
        self._standard_by_raw: dict[str, StandardMessage] = {}
        self._events: list[EventStreamItem] = []
        self._poll_states: dict[str, PollState] = {}
        self.ensure_defaults()

    def ensure_defaults(self, sources: Iterable[MonitoringSourceConfig] | None = None) -> None:
        for source in sources or default_source_configs():
            existing = self._sources.get(source.source_id)
            if existing is None:
                self._sources[source.source_id] = source.model_copy(deep=True)
            else:
                self._sources[source.source_id] = _merge_default_source(
                    existing,
                    source,
                )

    def upsert_source(self, source: MonitoringSourceConfig) -> MonitoringSourceConfig:
        updated = source.model_copy(update={"updated_at": datetime.now(UTC)}, deep=True)
        self._sources[updated.source_id] = updated
        return updated.model_copy(deep=True)

    def get_source(self, source_id: str) -> MonitoringSourceConfig | None:
        source = self._sources.get(source_id.strip().lower())
        return source.model_copy(deep=True) if source is not None else None

    def list_sources(self) -> list[MonitoringSourceConfig]:
        return [self._sources[key].model_copy(deep=True) for key in sorted(self._sources)]

    def set_source_enabled(self, source_id: str, enabled: bool) -> MonitoringSourceConfig:
        source = _require_source(self.get_source(source_id), source_id)
        return self.upsert_source(source.model_copy(update={"enabled": enabled}, deep=True))

    def set_source_poll_interval(self, source_id: str, seconds: int) -> MonitoringSourceConfig:
        if seconds < 30:
            raise ValueError("poll interval must be at least 30 seconds.")
        source = _require_source(self.get_source(source_id), source_id)
        return self.upsert_source(
            source.model_copy(update={"poll_interval_seconds": seconds}, deep=True)
        )

    def upsert_binding(
        self,
        *,
        ticker: str,
        source_id: str,
        parameters: MonitoringParameters,
        enabled: bool,
        updated_by: UpdateActor,
        updated_reason: str | None = None,
        merge: bool = True,
    ) -> TickerSourceBinding:
        source = _require_source(self.get_source(source_id), source_id)
        parameters = validate_parameters_for_source(source.source_id, parameters)
        binding_id = binding_id_for(ticker, source.source_id)
        existing = self._bindings.get(binding_id)
        now = datetime.now(UTC)
        if existing is None:
            binding = TickerSourceBinding(
                binding_id=binding_id,
                ticker=ticker,
                source_id=source.source_id,
                enabled=enabled,
                parameters=parameters,
                created_at=now,
                updated_at=now,
                updated_by=updated_by,
                updated_reason=updated_reason,
            )
        else:
            resolved_parameters = (
                existing.parameters.merged_with(parameters) if merge else parameters
            )
            resolved_parameters = validate_parameters_for_source(
                source.source_id,
                resolved_parameters,
            )
            binding = existing.model_copy(
                update={
                    "enabled": enabled,
                    "parameters": resolved_parameters,
                    "updated_at": now,
                    "updated_by": updated_by,
                    "updated_reason": updated_reason,
                },
                deep=True,
            )
        self._bindings[binding_id] = binding
        return binding.model_copy(deep=True)

    def get_binding(self, ticker: str, source_id: str) -> TickerSourceBinding | None:
        binding = self._bindings.get(binding_id_for(ticker, source_id))
        return binding.model_copy(deep=True) if binding is not None else None

    def list_bindings(
        self,
        *,
        ticker: str | None = None,
        source_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[TickerSourceBinding]:
        rows = list(self._bindings.values())
        if ticker is not None:
            normalized_ticker = ticker.strip().upper()
            rows = [row for row in rows if row.ticker == normalized_ticker]
        if source_id is not None:
            normalized_source = source_id.strip().lower()
            rows = [row for row in rows if row.source_id == normalized_source]
        if enabled_only:
            rows = [row for row in rows if row.enabled]
        return [row.model_copy(deep=True) for row in sorted(rows, key=lambda row: row.binding_id)]

    def delete_binding(self, ticker: str, source_id: str) -> bool:
        binding_id = binding_id_for(ticker, source_id)
        removed = self._bindings.pop(binding_id, None) is not None
        self._poll_states.pop(binding_id, None)
        return removed

    def delete_ticker_bindings(self, ticker: str) -> int:
        normalized_ticker = ticker.strip().upper()
        binding_ids = [
            binding.binding_id
            for binding in self._bindings.values()
            if binding.ticker == normalized_ticker
        ]
        for binding_id in binding_ids:
            self._bindings.pop(binding_id, None)
            self._poll_states.pop(binding_id, None)
        return len(binding_ids)

    def save_raw_message(self, message: RawExternalMessage) -> RawMessageSaveResult:
        existing = self._raw_by_dedupe.get(message.dedupe_key)
        now = datetime.now(UTC)
        if existing is not None:
            updated = existing.model_copy(
                update={
                    "duplicate_seen_count": existing.duplicate_seen_count + 1,
                    "last_seen_at": now,
                },
                deep=True,
            )
            self._raw_by_dedupe[message.dedupe_key] = updated
            return RawMessageSaveResult(
                decision=IngestDecision.DUPLICATE,
                message=updated.model_copy(deep=True),
            )
        self._raw_by_dedupe[message.dedupe_key] = message.model_copy(deep=True)
        return RawMessageSaveResult(
            decision=IngestDecision.INSERTED,
            message=message.model_copy(deep=True),
        )

    def save_standard_message(self, message: StandardMessage) -> StandardMessage:
        self._standard_by_raw[message.raw_message_id] = message.model_copy(deep=True)
        return message.model_copy(deep=True)

    def append_event(self, message: StandardMessage) -> EventStreamItem:
        existing = [
            event
            for event in self._events
            if event.standard_message_id == message.standard_message_id
        ]
        if existing:
            return existing[0].model_copy(deep=True)
        event = EventStreamItem(
            event_id=new_monitoring_id("evt"),
            stream_offset=len(self._events) + 1,
            standard_message_id=message.standard_message_id,
            ticker=message.ticker,
            source_id=message.source_id,
            payload=message.model_dump(mode="json"),
        )
        self._events.append(event)
        return event.model_copy(deep=True)

    def mark_event_consumed(self, event_id: str) -> EventStreamItem | None:
        for index, event in enumerate(self._events):
            if event.event_id != event_id:
                continue
            updated = event.model_copy(update={"consumed": True}, deep=True)
            self._events[index] = updated
            return updated.model_copy(deep=True)
        return None

    def record_poll_attempt(self, *, binding_id: str, source_id: str, ticker: str) -> PollState:
        existing = self._poll_states.get(binding_id)
        now = datetime.now(UTC)
        state = existing or PollState(binding_id=binding_id, source_id=source_id, ticker=ticker)
        updated = state.model_copy(
            update={"last_attempt_at": now, "updated_at": now},
            deep=True,
        )
        self._poll_states[binding_id] = updated
        return updated.model_copy(deep=True)

    def record_poll_success(self, result: IngestBatchResult) -> PollState:
        existing = self._poll_states.get(result.binding_id) or PollState(
            binding_id=result.binding_id,
            source_id=result.source_id,
            ticker=result.ticker,
        )
        now = datetime.now(UTC)
        updated = existing.model_copy(
            update={
                "status": PollStatus.SUCCEEDED,
                "last_success_at": now,
                "last_error_at": None,
                "last_error_message": None,
                "collected_count": existing.collected_count + result.collected_count,
                "historical_skipped_count": existing.historical_skipped_count
                + result.historical_skipped_count,
                "raw_inserted_count": existing.raw_inserted_count
                + result.raw_inserted_count,
                "duplicate_count": existing.duplicate_count + result.duplicate_count,
                "standardized_count": existing.standardized_count
                + result.standardized_count,
                "event_count": existing.event_count + result.event_count,
                "last_collected_count": result.collected_count,
                "last_historical_skipped_count": result.historical_skipped_count,
                "last_raw_inserted_count": result.raw_inserted_count,
                "last_duplicate_count": result.duplicate_count,
                "last_standardized_count": result.standardized_count,
                "last_event_count": result.event_count,
                "last_latency_ms": result.latency_ms,
                "metadata": result.metadata,
                "updated_at": now,
            },
            deep=True,
        )
        self._poll_states[result.binding_id] = updated
        return updated.model_copy(deep=True)

    def record_poll_failure(
        self,
        *,
        binding_id: str,
        source_id: str,
        ticker: str,
        message: str,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PollState:
        existing = self._poll_states.get(binding_id) or PollState(
            binding_id=binding_id,
            source_id=source_id,
            ticker=ticker,
        )
        now = datetime.now(UTC)
        updated = existing.model_copy(
            update={
                "status": PollStatus.FAILED,
                "last_error_at": now,
                "last_error_message": message[:500],
                "last_collected_count": 0,
                "last_historical_skipped_count": 0,
                "last_raw_inserted_count": 0,
                "last_duplicate_count": 0,
                "last_standardized_count": 0,
                "last_event_count": 0,
                "last_latency_ms": latency_ms,
                "metadata": metadata or {},
                "updated_at": now,
            },
            deep=True,
        )
        self._poll_states[binding_id] = updated
        return updated.model_copy(deep=True)

    def list_poll_states(
        self,
        *,
        ticker: str | None = None,
        source_id: str | None = None,
    ) -> list[PollState]:
        rows = list(self._poll_states.values())
        if ticker is not None:
            normalized_ticker = ticker.strip().upper()
            rows = [row for row in rows if row.ticker == normalized_ticker]
        if source_id is not None:
            normalized_source = source_id.strip().lower()
            rows = [row for row in rows if row.source_id == normalized_source]
        return [row.model_copy(deep=True) for row in sorted(rows, key=lambda row: row.binding_id)]

    def recent_raw_messages(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[RawExternalMessage]:
        rows = list(self._raw_by_dedupe.values())
        if ticker is not None:
            rows = [row for row in rows if row.ticker == ticker.strip().upper()]
        rows = sorted(rows, key=lambda row: row.collected_at, reverse=True)
        return [row.model_copy(deep=True) for row in rows[:limit]]

    def recent_standard_messages(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[StandardMessage]:
        rows = list(self._standard_by_raw.values())
        if ticker is not None:
            rows = [row for row in rows if row.ticker == ticker.strip().upper()]
        rows = [row for row in rows if self._is_live_standard_message(row)]
        rows = sorted(rows, key=lambda row: row.normalized_at, reverse=True)
        return [row.model_copy(deep=True) for row in rows[:limit]]

    def recent_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[EventStreamItem]:
        rows = self._events
        if ticker is not None:
            rows = [row for row in rows if row.ticker == ticker.strip().upper()]
        rows = [row for row in rows if self._is_live_event(row)]
        rows = sorted(rows, key=lambda row: row.stream_offset, reverse=True)
        return [row.model_copy(deep=True) for row in rows[:limit]]

    def pending_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[EventStreamItem]:
        rows = self._events
        if ticker is not None:
            rows = [row for row in rows if row.ticker == ticker.strip().upper()]
        rows = [
            row
            for row in rows
            if not row.consumed and self._is_live_event(row)
        ]
        rows = sorted(rows, key=lambda row: row.stream_offset)
        return [row.model_copy(deep=True) for row in rows[:limit]]

    def list_media_enrichment_records(
        self,
        *,
        ticker: str | None = None,
        limit: int = 50,
        incomplete_only: bool = True,
    ) -> list[MediaEnrichmentRecord]:
        resolved_limit = max(0, limit)
        if resolved_limit == 0:
            return []
        normalized_ticker = ticker.strip().upper() if ticker is not None else None
        rows = sorted(
            self._standard_by_raw.values(),
            key=lambda row: row.normalized_at,
            reverse=True,
        )
        records: list[MediaEnrichmentRecord] = []
        for message in rows:
            if message.source_type is not SourceType.MEDIA:
                continue
            if normalized_ticker is not None and message.ticker != normalized_ticker:
                continue
            if incomplete_only and assess_media_body(message.body, message.title).complete_like:
                continue
            raw = self._raw_for_standard(message)
            raw_payload = raw.raw_payload if raw is not None else {}
            records.append(
                MediaEnrichmentRecord(
                    standard_message_id=message.standard_message_id,
                    raw_message_id=message.raw_message_id,
                    source_id=message.source_id,
                    ticker=message.ticker,
                    title=message.title,
                    body=message.body,
                    url=choose_media_fetch_url(
                        standard_url=message.url,
                        raw_url=raw.source_url if raw is not None else None,
                        raw_payload=raw_payload,
                    ),
                    raw_url=raw.source_url if raw is not None else None,
                    source_name=_media_source_name(message, raw_payload),
                )
            )
            if len(records) >= resolved_limit:
                break
        return records

    def apply_media_enrichment_results(
        self,
        results: Iterable[MediaExtractionResult],
    ) -> int:
        result_by_id = {result.record.standard_message_id: result for result in results}
        if not result_by_id:
            return 0
        written = 0
        for raw_id, message in list(self._standard_by_raw.items()):
            result = result_by_id.get(message.standard_message_id)
            if result is None:
                continue
            metadata = media_enrichment_metadata(message.metadata, result)
            update: dict[str, Any] = {"metadata": metadata}
            if result.succeeded and result.content:
                update.update(
                    {
                        "body": result.content,
                        "url": result.final_url or message.url,
                        "author": result.source_name or message.author,
                    }
                )
                written += 1
            updated = message.model_copy(update=update, deep=True)
            self._standard_by_raw[raw_id] = updated
            for index, event in enumerate(self._events):
                if event.standard_message_id == updated.standard_message_id:
                    self._events[index] = event.model_copy(
                        update={"payload": updated.model_dump(mode="json")},
                        deep=True,
                    )
        return written

    def snapshot(self, *, ticker: str | None = None, limit: int = 20) -> MonitoringSnapshot:
        return MonitoringSnapshot(
            sources=self.list_sources(),
            bindings=self.list_bindings(ticker=ticker),
            poll_states=self.list_poll_states(ticker=ticker),
            recent_raw_messages=self.recent_raw_messages(ticker=ticker, limit=limit),
            recent_standard_messages=self.recent_standard_messages(ticker=ticker, limit=limit),
            recent_events=self.recent_events(ticker=ticker, limit=limit),
        )

    def _is_live_standard_message(self, message: StandardMessage) -> bool:
        binding = self._bindings.get(message.binding_id)
        if binding is None:
            return False
        if message.published_at is None:
            return True
        return message.published_at >= binding.updated_at

    def _is_live_event(self, event: EventStreamItem) -> bool:
        for message in self._standard_by_raw.values():
            if message.standard_message_id == event.standard_message_id:
                return self._is_live_standard_message(message)
        return False

    def _raw_for_standard(self, message: StandardMessage) -> RawExternalMessage | None:
        for raw in self._raw_by_dedupe.values():
            if raw.raw_message_id == message.raw_message_id:
                return raw
        return None


class SQLiteMonitoringRepository:
    """SQLite-backed durable repository for the local message bus."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.ensure_defaults()

    def ensure_defaults(self, sources: Iterable[MonitoringSourceConfig] | None = None) -> None:
        for source in sources or default_source_configs():
            existing = self.get_source(source.source_id)
            if existing is None:
                self.upsert_source(source)
            else:
                self.upsert_source(_merge_default_source(existing, source))

    def upsert_source(self, source: MonitoringSourceConfig) -> MonitoringSourceConfig:
        updated = source.model_copy(update={"updated_at": datetime.now(UTC)}, deep=True)
        with self._connect() as conn:
            conn.execute(
                """
                insert into monitoring_sources
                    (source_id, provider, display_name, source_type, interface_type,
                     endpoint_kind, enabled, poll_interval_seconds, required_api_key_env,
                     config_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source_id) do update set
                    provider = excluded.provider,
                    display_name = excluded.display_name,
                    source_type = excluded.source_type,
                    interface_type = excluded.interface_type,
                    endpoint_kind = excluded.endpoint_kind,
                    enabled = excluded.enabled,
                    poll_interval_seconds = excluded.poll_interval_seconds,
                    required_api_key_env = excluded.required_api_key_env,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                _source_row(updated),
            )
        return _require_source(self.get_source(updated.source_id), updated.source_id)

    def get_source(self, source_id: str) -> MonitoringSourceConfig | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from monitoring_sources where source_id = ?",
                (source_id.strip().lower(),),
            ).fetchone()
        return _source_from_row(row) if row is not None else None

    def list_sources(self) -> list[MonitoringSourceConfig]:
        with self._connect() as conn:
            rows = conn.execute("select * from monitoring_sources order by source_id").fetchall()
        return [_source_from_row(row) for row in rows]

    def set_source_enabled(self, source_id: str, enabled: bool) -> MonitoringSourceConfig:
        source = _require_source(self.get_source(source_id), source_id)
        return self.upsert_source(source.model_copy(update={"enabled": enabled}, deep=True))

    def set_source_poll_interval(self, source_id: str, seconds: int) -> MonitoringSourceConfig:
        if seconds < 30:
            raise ValueError("poll interval must be at least 30 seconds.")
        source = _require_source(self.get_source(source_id), source_id)
        return self.upsert_source(
            source.model_copy(update={"poll_interval_seconds": seconds}, deep=True)
        )

    def upsert_binding(
        self,
        *,
        ticker: str,
        source_id: str,
        parameters: MonitoringParameters,
        enabled: bool,
        updated_by: UpdateActor,
        updated_reason: str | None = None,
        merge: bool = True,
    ) -> TickerSourceBinding:
        source = _require_source(self.get_source(source_id), source_id)
        parameters = validate_parameters_for_source(source.source_id, parameters)
        normalized_ticker = ticker.strip().upper()
        binding_id = binding_id_for(normalized_ticker, source.source_id)
        now = datetime.now(UTC)
        existing = self.get_binding(normalized_ticker, source.source_id)
        if existing is not None and merge:
            parameters = existing.parameters.merged_with(parameters)
        parameters = validate_parameters_for_source(source.source_id, parameters)
        created_at = existing.created_at if existing is not None else now
        binding = TickerSourceBinding(
            binding_id=binding_id,
            ticker=normalized_ticker,
            source_id=source.source_id,
            enabled=enabled,
            parameters=parameters,
            created_at=created_at,
            updated_at=now,
            updated_by=updated_by,
            updated_reason=updated_reason,
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into monitoring_bindings
                    (binding_id, ticker, source_id, enabled, parameters_json,
                     created_at, updated_at, updated_by, updated_reason)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(binding_id) do update set
                    enabled = excluded.enabled,
                    parameters_json = excluded.parameters_json,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by,
                    updated_reason = excluded.updated_reason
                """,
                _binding_row(binding),
            )
        return _require_binding(self.get_binding(normalized_ticker, source.source_id), binding_id)

    def get_binding(self, ticker: str, source_id: str) -> TickerSourceBinding | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from monitoring_bindings where binding_id = ?",
                (binding_id_for(ticker, source_id),),
            ).fetchone()
        return _binding_from_row(row) if row is not None else None

    def list_bindings(
        self,
        *,
        ticker: str | None = None,
        source_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[TickerSourceBinding]:
        clauses: list[str] = []
        params: list[object] = []
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker.strip().upper())
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id.strip().lower())
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"select * from monitoring_bindings {where} order by ticker, source_id",
                params,
            ).fetchall()
        return [_binding_from_row(row) for row in rows]

    def delete_binding(self, ticker: str, source_id: str) -> bool:
        binding_id = binding_id_for(ticker, source_id)
        with self._connect() as conn:
            cursor = conn.execute(
                "delete from monitoring_bindings where binding_id = ?",
                (binding_id,),
            )
            conn.execute(
                "delete from monitoring_poll_states where binding_id = ?",
                (binding_id,),
            )
        return cursor.rowcount > 0

    def delete_ticker_bindings(self, ticker: str) -> int:
        normalized_ticker = ticker.strip().upper()
        with self._connect() as conn:
            rows = conn.execute(
                "select binding_id from monitoring_bindings where ticker = ?",
                (normalized_ticker,),
            ).fetchall()
            binding_ids = [str(row["binding_id"]) for row in rows]
            conn.execute(
                "delete from monitoring_bindings where ticker = ?",
                (normalized_ticker,),
            )
            if binding_ids:
                placeholders = ", ".join("?" for _ in binding_ids)
                conn.execute(
                    f"delete from monitoring_poll_states where binding_id in ({placeholders})",
                    binding_ids,
                )
        return len(binding_ids)

    def save_raw_message(self, message: RawExternalMessage) -> RawMessageSaveResult:
        with self._connect() as conn:
            existing = conn.execute(
                "select * from monitoring_raw_messages where dedupe_key = ?",
                (message.dedupe_key,),
            ).fetchone()
            if existing is not None:
                now = datetime.now(UTC)
                conn.execute(
                    """
                    update monitoring_raw_messages
                    set duplicate_seen_count = duplicate_seen_count + 1,
                        last_seen_at = ?
                    where dedupe_key = ?
                    """,
                    (_dt(now), message.dedupe_key),
                )
                updated = conn.execute(
                    "select * from monitoring_raw_messages where dedupe_key = ?",
                    (message.dedupe_key,),
                ).fetchone()
                return RawMessageSaveResult(
                    decision=IngestDecision.DUPLICATE,
                    message=_raw_from_row(updated),
                )
            conn.execute(
                """
                insert into monitoring_raw_messages
                    (raw_message_id, dedupe_key, source_id, binding_id, ticker,
                     source_type, interface_type, provider_message_id, payload_hash,
                     source_url, source_published_at, collected_at, raw_payload_json,
                     metadata_json, duplicate_seen_count, last_seen_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _raw_row(message),
            )
        return RawMessageSaveResult(decision=IngestDecision.INSERTED, message=message)

    def save_standard_message(self, message: StandardMessage) -> StandardMessage:
        with self._connect() as conn:
            conn.execute(
                """
                insert into monitoring_standard_messages
                    (standard_message_id, raw_message_id, source_id, binding_id, ticker,
                     source_type, interface_type, title, body, url, author, symbols_json,
                     keywords_json, username, published_at, collected_at, normalized_at,
                     provider_message_id, metadata_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(raw_message_id) do update set
                    title = excluded.title,
                    body = excluded.body,
                    metadata_json = excluded.metadata_json
                """,
                _standard_row(message),
            )
        return message

    def append_event(self, message: StandardMessage) -> EventStreamItem:
        with self._connect() as conn:
            existing = conn.execute(
                "select * from monitoring_event_stream where standard_message_id = ?",
                (message.standard_message_id,),
            ).fetchone()
            if existing is not None:
                return _event_from_row(existing)
            event_id = new_monitoring_id("evt")
            event_time = datetime.now(UTC)
            payload = message.model_dump(mode="json")
            cursor = conn.execute(
                """
                insert into monitoring_event_stream
                    (event_id, standard_message_id, event_type, event_time,
                     ticker, source_id, payload_json, consumed)
                values (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    event_id,
                    message.standard_message_id,
                    "monitoring.message.created",
                    _dt(event_time),
                    message.ticker,
                    message.source_id,
                    canonical_json(payload),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an event stream offset.")
            offset = int(cursor.lastrowid)
        return EventStreamItem(
            event_id=event_id,
            stream_offset=offset,
            standard_message_id=message.standard_message_id,
            event_time=event_time,
            ticker=message.ticker,
            source_id=message.source_id,
            payload=payload,
        )

    def mark_event_consumed(self, event_id: str) -> EventStreamItem | None:
        normalized_event_id = event_id.strip()
        with self._connect() as conn:
            row = conn.execute(
                "select * from monitoring_event_stream where event_id = ?",
                (normalized_event_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "update monitoring_event_stream set consumed = 1 where event_id = ?",
                (normalized_event_id,),
            )
            updated = conn.execute(
                "select * from monitoring_event_stream where event_id = ?",
                (normalized_event_id,),
            ).fetchone()
        return _event_from_row(updated) if updated is not None else None

    def record_poll_attempt(self, *, binding_id: str, source_id: str, ticker: str) -> PollState:
        return self._upsert_poll_state(
            PollState(
                binding_id=binding_id,
                source_id=source_id,
                ticker=ticker,
                last_attempt_at=datetime.now(UTC),
            ),
            merge_counts=True,
        )

    def record_poll_success(self, result: IngestBatchResult) -> PollState:
        existing = self._get_poll_state(result.binding_id)
        now = datetime.now(UTC)
        state = existing or PollState(
            binding_id=result.binding_id,
            source_id=result.source_id,
            ticker=result.ticker,
        )
        updated = state.model_copy(
            update={
                "status": PollStatus.SUCCEEDED,
                "last_success_at": now,
                "last_error_at": None,
                "last_error_message": None,
                "collected_count": state.collected_count + result.collected_count,
                "historical_skipped_count": state.historical_skipped_count
                + result.historical_skipped_count,
                "raw_inserted_count": state.raw_inserted_count + result.raw_inserted_count,
                "duplicate_count": state.duplicate_count + result.duplicate_count,
                "standardized_count": state.standardized_count + result.standardized_count,
                "event_count": state.event_count + result.event_count,
                "last_collected_count": result.collected_count,
                "last_historical_skipped_count": result.historical_skipped_count,
                "last_raw_inserted_count": result.raw_inserted_count,
                "last_duplicate_count": result.duplicate_count,
                "last_standardized_count": result.standardized_count,
                "last_event_count": result.event_count,
                "last_latency_ms": result.latency_ms,
                "metadata": result.metadata,
                "updated_at": now,
            },
            deep=True,
        )
        return self._upsert_poll_state(updated, merge_counts=False)

    def record_poll_failure(
        self,
        *,
        binding_id: str,
        source_id: str,
        ticker: str,
        message: str,
        latency_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PollState:
        existing = self._get_poll_state(binding_id)
        now = datetime.now(UTC)
        state = existing or PollState(binding_id=binding_id, source_id=source_id, ticker=ticker)
        updated = state.model_copy(
            update={
                "status": PollStatus.FAILED,
                "last_error_at": now,
                "last_error_message": message[:500],
                "last_collected_count": 0,
                "last_historical_skipped_count": 0,
                "last_raw_inserted_count": 0,
                "last_duplicate_count": 0,
                "last_standardized_count": 0,
                "last_event_count": 0,
                "last_latency_ms": latency_ms,
                "metadata": metadata or {},
                "updated_at": now,
            },
            deep=True,
        )
        return self._upsert_poll_state(updated, merge_counts=False)

    def list_poll_states(
        self,
        *,
        ticker: str | None = None,
        source_id: str | None = None,
    ) -> list[PollState]:
        clauses: list[str] = []
        params: list[object] = []
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker.strip().upper())
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id.strip().lower())
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"select * from monitoring_poll_states {where} order by ticker, source_id",
                params,
            ).fetchall()
        return [_poll_state_from_row(row) for row in rows]

    def recent_raw_messages(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[RawExternalMessage]:
        params: list[object] = []
        where = ""
        if ticker is not None:
            where = "where ticker = ?"
            params.append(ticker.strip().upper())
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select * from monitoring_raw_messages
                {where}
                order by collected_at desc
                limit ?
                """,
                params,
            ).fetchall()
        return [_raw_from_row(row) for row in rows]

    def recent_standard_messages(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[StandardMessage]:
        clauses = [
            "monitoring_bindings.binding_id is not null",
            "(published_at is null or published_at >= monitoring_bindings.updated_at)",
        ]
        params: list[object] = []
        if ticker is not None:
            clauses.append("monitoring_standard_messages.ticker = ?")
            params.append(ticker.strip().upper())
        where = f"where {' and '.join(clauses)}"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select monitoring_standard_messages.*
                from monitoring_standard_messages
                left join monitoring_bindings
                    on monitoring_standard_messages.binding_id = monitoring_bindings.binding_id
                {where}
                order by normalized_at desc
                limit ?
                """,
                params,
            ).fetchall()
        return [_standard_from_row(row) for row in rows]

    def recent_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 20,
    ) -> list[EventStreamItem]:
        clauses = [
            "monitoring_bindings.binding_id is not null",
            "(monitoring_standard_messages.published_at is null "
            "or monitoring_standard_messages.published_at >= monitoring_bindings.updated_at)",
        ]
        params: list[object] = []
        if ticker is not None:
            clauses.append("monitoring_event_stream.ticker = ?")
            params.append(ticker.strip().upper())
        where = f"where {' and '.join(clauses)}"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select monitoring_event_stream.*
                from monitoring_event_stream
                left join monitoring_standard_messages
                    on monitoring_event_stream.standard_message_id =
                        monitoring_standard_messages.standard_message_id
                left join monitoring_bindings
                    on monitoring_standard_messages.binding_id = monitoring_bindings.binding_id
                {where}
                order by stream_offset desc
                limit ?
                """,
                params,
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def pending_events(
        self,
        *,
        ticker: str | None = None,
        limit: int = 100,
    ) -> list[EventStreamItem]:
        clauses = [
            "monitoring_event_stream.consumed = 0",
            "monitoring_bindings.binding_id is not null",
            "(monitoring_standard_messages.published_at is null "
            "or monitoring_standard_messages.published_at >= monitoring_bindings.updated_at)",
        ]
        params: list[object] = []
        if ticker is not None:
            clauses.append("monitoring_event_stream.ticker = ?")
            params.append(ticker.strip().upper())
        where = f"where {' and '.join(clauses)}"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select monitoring_event_stream.*
                from monitoring_event_stream
                left join monitoring_standard_messages
                    on monitoring_event_stream.standard_message_id =
                        monitoring_standard_messages.standard_message_id
                left join monitoring_bindings
                    on monitoring_standard_messages.binding_id = monitoring_bindings.binding_id
                {where}
                order by stream_offset asc
                limit ?
                """,
                params,
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def list_media_enrichment_records(
        self,
        *,
        ticker: str | None = None,
        limit: int = 50,
        incomplete_only: bool = True,
    ) -> list[MediaEnrichmentRecord]:
        resolved_limit = max(0, limit)
        if resolved_limit == 0:
            return []
        clauses = ["s.source_type = ?"]
        params: list[object] = [SourceType.MEDIA.value]
        if ticker is not None:
            clauses.append("s.ticker = ?")
            params.append(ticker.strip().upper())
        where = f"where {' and '.join(clauses)}"
        fetch_limit = resolved_limit if not incomplete_only else max(resolved_limit * 5, 50)
        params.append(fetch_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select
                    s.*,
                    r.raw_payload_json as raw_payload_json,
                    r.source_url as raw_source_url
                from monitoring_standard_messages s
                left join monitoring_raw_messages r
                    on s.raw_message_id = r.raw_message_id
                {where}
                order by coalesce(s.published_at, s.normalized_at, s.collected_at) desc
                limit ?
                """,
                params,
            ).fetchall()
        records: list[MediaEnrichmentRecord] = []
        for row in rows:
            message = _standard_from_row(row)
            if incomplete_only and assess_media_body(message.body, message.title).complete_like:
                continue
            raw_payload = dict(_load_json(row["raw_payload_json"]) or {})
            raw_url = row["raw_source_url"]
            records.append(
                MediaEnrichmentRecord(
                    standard_message_id=message.standard_message_id,
                    raw_message_id=message.raw_message_id,
                    source_id=message.source_id,
                    ticker=message.ticker,
                    title=message.title,
                    body=message.body,
                    url=choose_media_fetch_url(
                        standard_url=message.url,
                        raw_url=raw_url,
                        raw_payload=raw_payload,
                    ),
                    raw_url=raw_url,
                    source_name=_media_source_name(message, raw_payload),
                )
            )
            if len(records) >= resolved_limit:
                break
        return records

    def apply_media_enrichment_results(
        self,
        results: Iterable[MediaExtractionResult],
    ) -> int:
        written = 0
        with self._connect() as conn:
            for result in results:
                row = conn.execute(
                    "select * from monitoring_standard_messages where standard_message_id = ?",
                    (result.record.standard_message_id,),
                ).fetchone()
                if row is None:
                    continue
                message = _standard_from_row(row)
                metadata = media_enrichment_metadata(message.metadata, result)
                update: dict[str, Any] = {"metadata": metadata}
                if result.succeeded and result.content:
                    update.update(
                        {
                            "body": result.content,
                            "url": result.final_url or message.url,
                            "author": result.source_name or message.author,
                        }
                    )
                    written += 1
                updated = message.model_copy(update=update, deep=True)
                conn.execute(
                    """
                    update monitoring_standard_messages
                    set body = ?, url = ?, author = ?, metadata_json = ?
                    where standard_message_id = ?
                    """,
                    (
                        updated.body,
                        updated.url,
                        updated.author,
                        _json(updated.metadata),
                        updated.standard_message_id,
                    ),
                )
                conn.execute(
                    """
                    update monitoring_event_stream
                    set payload_json = ?
                    where standard_message_id = ?
                    """,
                    (
                        canonical_json(updated.model_dump(mode="json")),
                        updated.standard_message_id,
                    ),
                )
        return written

    def snapshot(self, *, ticker: str | None = None, limit: int = 20) -> MonitoringSnapshot:
        return MonitoringSnapshot(
            sources=self.list_sources(),
            bindings=self.list_bindings(ticker=ticker),
            poll_states=self.list_poll_states(ticker=ticker),
            recent_raw_messages=self.recent_raw_messages(ticker=ticker, limit=limit),
            recent_standard_messages=self.recent_standard_messages(ticker=ticker, limit=limit),
            recent_events=self.recent_events(ticker=ticker, limit=limit),
        )

    def _get_poll_state(self, binding_id: str) -> PollState | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from monitoring_poll_states where binding_id = ?",
                (binding_id,),
            ).fetchone()
        return _poll_state_from_row(row) if row is not None else None

    def _upsert_poll_state(self, state: PollState, *, merge_counts: bool) -> PollState:
        if merge_counts:
            existing = self._get_poll_state(state.binding_id)
            if existing is not None:
                state = existing.model_copy(
                    update={
                        "last_attempt_at": state.last_attempt_at,
                        "updated_at": datetime.now(UTC),
                    },
                    deep=True,
                )
        with self._connect() as conn:
            conn.execute(
                """
                insert into monitoring_poll_states
                    (binding_id, source_id, ticker, status, last_attempt_at,
                     last_success_at, last_error_at, last_error_message,
                     collected_count, historical_skipped_count, raw_inserted_count,
                     duplicate_count, standardized_count, event_count,
                     last_collected_count, last_historical_skipped_count,
                     last_raw_inserted_count, last_duplicate_count,
                     last_standardized_count, last_event_count, last_latency_ms,
                     metadata_json, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(binding_id) do update set
                    status = excluded.status,
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = excluded.last_success_at,
                    last_error_at = excluded.last_error_at,
                    last_error_message = excluded.last_error_message,
                    collected_count = excluded.collected_count,
                    historical_skipped_count = excluded.historical_skipped_count,
                    raw_inserted_count = excluded.raw_inserted_count,
                    duplicate_count = excluded.duplicate_count,
                    standardized_count = excluded.standardized_count,
                    event_count = excluded.event_count,
                    last_collected_count = excluded.last_collected_count,
                    last_historical_skipped_count = excluded.last_historical_skipped_count,
                    last_raw_inserted_count = excluded.last_raw_inserted_count,
                    last_duplicate_count = excluded.last_duplicate_count,
                    last_standardized_count = excluded.last_standardized_count,
                    last_event_count = excluded.last_event_count,
                    last_latency_ms = excluded.last_latency_ms,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                _poll_state_row(state),
            )
        resolved = self._get_poll_state(state.binding_id)
        if resolved is None:
            raise RuntimeError(f"poll state was not persisted: {state.binding_id}")
        return resolved

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists monitoring_sources (
                    source_id text primary key,
                    provider text not null,
                    display_name text not null,
                    source_type text not null,
                    interface_type text not null,
                    endpoint_kind text not null,
                    enabled integer not null,
                    poll_interval_seconds integer not null,
                    required_api_key_env text,
                    config_json text not null,
                    created_at text not null,
                    updated_at text not null
                );

                create table if not exists monitoring_bindings (
                    binding_id text primary key,
                    ticker text not null,
                    source_id text not null references monitoring_sources(source_id),
                    enabled integer not null,
                    parameters_json text not null,
                    created_at text not null,
                    updated_at text not null,
                    updated_by text not null,
                    updated_reason text,
                    unique(ticker, source_id)
                );

                create table if not exists monitoring_raw_messages (
                    raw_message_id text primary key,
                    dedupe_key text not null unique,
                    source_id text not null,
                    binding_id text not null,
                    ticker text not null,
                    source_type text not null,
                    interface_type text not null,
                    provider_message_id text,
                    payload_hash text not null,
                    source_url text,
                    source_published_at text,
                    collected_at text not null,
                    raw_payload_json text not null,
                    metadata_json text not null,
                    duplicate_seen_count integer not null default 0,
                    last_seen_at text
                );

                create table if not exists monitoring_standard_messages (
                    standard_message_id text primary key,
                    raw_message_id text not null unique
                        references monitoring_raw_messages(raw_message_id),
                    source_id text not null,
                    binding_id text not null,
                    ticker text not null,
                    source_type text not null,
                    interface_type text not null,
                    title text,
                    body text,
                    url text,
                    author text,
                    symbols_json text not null,
                    keywords_json text not null,
                    username text,
                    published_at text,
                    collected_at text not null,
                    normalized_at text not null,
                    provider_message_id text,
                    metadata_json text not null
                );

                create table if not exists monitoring_event_stream (
                    stream_offset integer primary key autoincrement,
                    event_id text not null unique,
                    standard_message_id text not null unique
                        references monitoring_standard_messages(standard_message_id),
                    event_type text not null,
                    event_time text not null,
                    ticker text not null,
                    source_id text not null,
                    payload_json text not null,
                    consumed integer not null default 0
                );

                create table if not exists monitoring_poll_states (
                    binding_id text primary key,
                    source_id text not null,
                    ticker text not null,
                    status text not null,
                    last_attempt_at text,
                    last_success_at text,
                    last_error_at text,
                    last_error_message text,
                    collected_count integer not null,
                    historical_skipped_count integer not null default 0,
                    raw_inserted_count integer not null,
                    duplicate_count integer not null,
                    standardized_count integer not null,
                    event_count integer not null,
                    last_collected_count integer not null default 0,
                    last_historical_skipped_count integer not null default 0,
                    last_raw_inserted_count integer not null default 0,
                    last_duplicate_count integer not null default 0,
                    last_standardized_count integer not null default 0,
                    last_event_count integer not null default 0,
                    last_latency_ms integer,
                    metadata_json text not null default '{}',
                    updated_at text not null
                );
                """
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "historical_skipped_count",
                "historical_skipped_count integer not null default 0",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "last_collected_count",
                "last_collected_count integer not null default 0",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "last_historical_skipped_count",
                "last_historical_skipped_count integer not null default 0",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "last_raw_inserted_count",
                "last_raw_inserted_count integer not null default 0",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "last_duplicate_count",
                "last_duplicate_count integer not null default 0",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "last_standardized_count",
                "last_standardized_count integer not null default 0",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "last_event_count",
                "last_event_count integer not null default 0",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "last_latency_ms",
                "last_latency_ms integer",
            )
            _ensure_column(
                conn,
                "monitoring_poll_states",
                "metadata_json",
                "metadata_json text not null default '{}'",
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _require_source(
    source: MonitoringSourceConfig | None,
    source_id: str,
) -> MonitoringSourceConfig:
    if source is None:
        raise KeyError(f"Unknown monitoring source: {source_id}")
    return source


def _require_binding(
    binding: TickerSourceBinding | None,
    binding_id: str,
) -> TickerSourceBinding:
    if binding is None:
        raise KeyError(f"Unknown monitoring binding: {binding_id}")
    return binding


def _merge_default_source(
    existing: MonitoringSourceConfig,
    default: MonitoringSourceConfig,
) -> MonitoringSourceConfig:
    config = {**default.config, **existing.config}
    if existing.source_id == "stocktwits_messages":
        if existing.config.get("mode") in {None, "rapidapi_or_public", "public"}:
            config["mode"] = default.config.get("mode", "durable_polling")
        if existing.config.get("limit") == 199:
            config["limit"] = default.config.get("limit")
        config["force_refresh"] = default.config.get("force_refresh", False)
    return default.model_copy(
        update={
            "enabled": existing.enabled,
            "poll_interval_seconds": existing.poll_interval_seconds,
            "config": config,
            "created_at": existing.created_at,
            "updated_at": existing.updated_at,
        },
        deep=True,
    )


def _media_source_name(message: StandardMessage, raw_payload: Mapping[str, Any]) -> str | None:
    for value in (raw_payload.get("source"), raw_payload.get("author"), message.author):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"pragma table_info({table})")}
    if column not in columns:
        conn.execute(f"alter table {table} add column {declaration}")


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)


def _row_int(row: sqlite3.Row, key: str, default: int = 0) -> int:
    if key not in row.keys() or row[key] is None:
        return default
    return int(row[key])


def _row_optional_int(row: sqlite3.Row, key: str) -> int | None:
    if key not in row.keys() or row[key] is None:
        return None
    return int(row[key])


def _json(value: object) -> str:
    return canonical_json(value)


def _load_json(value: object) -> Any:
    if value is None:
        return None
    return __import__("json").loads(str(value))


def _source_row(source: MonitoringSourceConfig) -> tuple[object, ...]:
    return (
        source.source_id,
        source.provider.value,
        source.display_name,
        source.source_type.value,
        source.interface_type.value,
        source.endpoint_kind.value,
        int(source.enabled),
        source.poll_interval_seconds,
        source.required_api_key_env,
        _json(source.config),
        _dt(source.created_at),
        _dt(source.updated_at),
    )


def _source_from_row(row: sqlite3.Row) -> MonitoringSourceConfig:
    return MonitoringSourceConfig(
        source_id=str(row["source_id"]),
        provider=MonitoringProvider(str(row["provider"])),
        display_name=str(row["display_name"]),
        source_type=SourceType(str(row["source_type"])),
        interface_type=InterfaceType(str(row["interface_type"])),
        endpoint_kind=EndpointKind(str(row["endpoint_kind"])),
        enabled=bool(row["enabled"]),
        poll_interval_seconds=int(row["poll_interval_seconds"]),
        required_api_key_env=row["required_api_key_env"],
        config=dict(_load_json(row["config_json"]) or {}),
        created_at=_parse_dt(row["created_at"]) or datetime.now(UTC),
        updated_at=_parse_dt(row["updated_at"]) or datetime.now(UTC),
    )


def _binding_row(binding: TickerSourceBinding) -> tuple[object, ...]:
    return (
        binding.binding_id,
        binding.ticker,
        binding.source_id,
        int(binding.enabled),
        _json(binding.parameters.model_dump(mode="json")),
        _dt(binding.created_at),
        _dt(binding.updated_at),
        binding.updated_by.value,
        binding.updated_reason,
    )


def _binding_from_row(row: sqlite3.Row) -> TickerSourceBinding:
    return TickerSourceBinding(
        binding_id=str(row["binding_id"]),
        ticker=str(row["ticker"]),
        source_id=str(row["source_id"]),
        enabled=bool(row["enabled"]),
        parameters=MonitoringParameters.model_validate(_load_json(row["parameters_json"])),
        created_at=_parse_dt(row["created_at"]) or datetime.now(UTC),
        updated_at=_parse_dt(row["updated_at"]) or datetime.now(UTC),
        updated_by=UpdateActor(str(row["updated_by"])),
        updated_reason=row["updated_reason"],
    )


def _raw_row(message: RawExternalMessage) -> tuple[object, ...]:
    return (
        message.raw_message_id,
        message.dedupe_key,
        message.source_id,
        message.binding_id,
        message.ticker,
        message.source_type.value,
        message.interface_type.value,
        message.provider_message_id,
        message.payload_hash,
        message.source_url,
        _dt(message.source_published_at),
        _dt(message.collected_at),
        _json(message.raw_payload),
        _json(message.metadata),
        message.duplicate_seen_count,
        _dt(message.last_seen_at),
    )


def _raw_from_row(row: sqlite3.Row) -> RawExternalMessage:
    return RawExternalMessage(
        raw_message_id=str(row["raw_message_id"]),
        dedupe_key=str(row["dedupe_key"]),
        source_id=str(row["source_id"]),
        binding_id=str(row["binding_id"]),
        ticker=str(row["ticker"]),
        source_type=SourceType(str(row["source_type"])),
        interface_type=InterfaceType(str(row["interface_type"])),
        provider_message_id=row["provider_message_id"],
        payload_hash=str(row["payload_hash"]),
        source_url=row["source_url"],
        source_published_at=_parse_dt(row["source_published_at"]),
        collected_at=_parse_dt(row["collected_at"]) or datetime.now(UTC),
        raw_payload=dict(_load_json(row["raw_payload_json"]) or {}),
        metadata=dict(_load_json(row["metadata_json"]) or {}),
        duplicate_seen_count=int(row["duplicate_seen_count"]),
        last_seen_at=_parse_dt(row["last_seen_at"]),
    )


def _standard_row(message: StandardMessage) -> tuple[object, ...]:
    return (
        message.standard_message_id,
        message.raw_message_id,
        message.source_id,
        message.binding_id,
        message.ticker,
        message.source_type.value,
        message.interface_type.value,
        message.title,
        message.body,
        message.url,
        message.author,
        _json(message.symbols),
        _json(message.keywords),
        message.username,
        _dt(message.published_at),
        _dt(message.collected_at),
        _dt(message.normalized_at),
        message.provider_message_id,
        _json(message.metadata),
    )


def _standard_from_row(row: sqlite3.Row) -> StandardMessage:
    return StandardMessage(
        standard_message_id=str(row["standard_message_id"]),
        raw_message_id=str(row["raw_message_id"]),
        source_id=str(row["source_id"]),
        binding_id=str(row["binding_id"]),
        ticker=str(row["ticker"]),
        source_type=SourceType(str(row["source_type"])),
        interface_type=InterfaceType(str(row["interface_type"])),
        title=row["title"],
        body=row["body"],
        url=row["url"],
        author=row["author"],
        symbols=list(_load_json(row["symbols_json"]) or []),
        keywords=list(_load_json(row["keywords_json"]) or []),
        username=row["username"],
        published_at=_parse_dt(row["published_at"]),
        collected_at=_parse_dt(row["collected_at"]) or datetime.now(UTC),
        normalized_at=_parse_dt(row["normalized_at"]) or datetime.now(UTC),
        provider_message_id=row["provider_message_id"],
        metadata=dict(_load_json(row["metadata_json"]) or {}),
    )


def _event_from_row(row: sqlite3.Row) -> EventStreamItem:
    return EventStreamItem(
        event_id=str(row["event_id"]),
        stream_offset=int(row["stream_offset"]),
        standard_message_id=str(row["standard_message_id"]),
        event_type=str(row["event_type"]),
        event_time=_parse_dt(row["event_time"]) or datetime.now(UTC),
        ticker=str(row["ticker"]),
        source_id=str(row["source_id"]),
        payload=dict(_load_json(row["payload_json"]) or {}),
        consumed=bool(row["consumed"]),
    )


def _poll_state_row(state: PollState) -> tuple[object, ...]:
    return (
        state.binding_id,
        state.source_id,
        state.ticker,
        state.status.value,
        _dt(state.last_attempt_at),
        _dt(state.last_success_at),
        _dt(state.last_error_at),
        state.last_error_message,
        state.collected_count,
        state.historical_skipped_count,
        state.raw_inserted_count,
        state.duplicate_count,
        state.standardized_count,
        state.event_count,
        state.last_collected_count,
        state.last_historical_skipped_count,
        state.last_raw_inserted_count,
        state.last_duplicate_count,
        state.last_standardized_count,
        state.last_event_count,
        state.last_latency_ms,
        _json(state.metadata),
        _dt(state.updated_at),
    )


def _poll_state_from_row(row: sqlite3.Row) -> PollState:
    return PollState(
        binding_id=str(row["binding_id"]),
        source_id=str(row["source_id"]),
        ticker=str(row["ticker"]),
        status=PollStatus(str(row["status"])),
        last_attempt_at=_parse_dt(row["last_attempt_at"]),
        last_success_at=_parse_dt(row["last_success_at"]),
        last_error_at=_parse_dt(row["last_error_at"]),
        last_error_message=row["last_error_message"],
        collected_count=int(row["collected_count"]),
        historical_skipped_count=(
            int(row["historical_skipped_count"])
            if "historical_skipped_count" in row.keys()
            else 0
        ),
        raw_inserted_count=int(row["raw_inserted_count"]),
        duplicate_count=int(row["duplicate_count"]),
        standardized_count=int(row["standardized_count"]),
        event_count=int(row["event_count"]),
        last_collected_count=_row_int(row, "last_collected_count"),
        last_historical_skipped_count=_row_int(row, "last_historical_skipped_count"),
        last_raw_inserted_count=_row_int(row, "last_raw_inserted_count"),
        last_duplicate_count=_row_int(row, "last_duplicate_count"),
        last_standardized_count=_row_int(row, "last_standardized_count"),
        last_event_count=_row_int(row, "last_event_count"),
        last_latency_ms=_row_optional_int(row, "last_latency_ms"),
        metadata=(
            dict(_load_json(row["metadata_json"]) or {})
            if "metadata_json" in row.keys()
            else {}
        ),
        updated_at=_parse_dt(row["updated_at"]) or datetime.now(UTC),
    )
