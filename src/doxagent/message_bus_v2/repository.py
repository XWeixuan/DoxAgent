"""SQLite persistence for Message Bus v2.

The repository uses one fresh database and ticker-local stream offsets. Control
plane rows are mutable and versioned; Raw, Standard, Stream and audit history are
append-only. Every connection enables WAL and a busy timeout so the global worker,
Runtime consumer and API can safely share the database.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, TypeVar

from pydantic import BaseModel

from doxagent.message_bus_v2.schema import (
    AcquisitionFailure,
    AuditRecord,
    ConsumerOffset,
    DefaultMonitoringProfile,
    IngestDecision,
    MaterializedStreamItem,
    MaterializedStreamMember,
    OperationalAlert,
    PollState,
    PublicationMode,
    RawMessage,
    RawProcessingStatus,
    SchedulerGroupState,
    SourceDefinition,
    StandardMessage,
    StreamingConfig,
    StreamItem,
    StreamMember,
    TickerMonitoringState,
    TickerSourceBinding,
    UpdateActor,
    new_id,
    utc_now,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()
        return False


class MessageBusV2Repository:
    def __init__(self, sqlite_path: str | Path) -> None:
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("pragma journal_mode = WAL")
        connection.execute("pragma synchronous = NORMAL")
        connection.execute("pragma foreign_keys = ON")
        connection.execute("pragma busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("begin immediate")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        """Connections are operation-scoped; provided for lifecycle symmetry."""

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                create table if not exists source_definitions (
                    source_id text primary key,
                    version integer not null,
                    enabled integer not null,
                    scheduler_group text not null,
                    data_json text not null
                );
                create table if not exists source_definition_revisions (
                    source_id text not null,
                    version integer not null,
                    data_json text not null,
                    primary key (source_id, version)
                );
                create table if not exists default_profiles (
                    profile_id text primary key,
                    version integer not null,
                    data_json text not null
                );
                create table if not exists default_profile_revisions (
                    profile_id text not null,
                    version integer not null,
                    data_json text not null,
                    primary key (profile_id, version)
                );
                create table if not exists ticker_monitoring_states (
                    ticker text primary key,
                    status text not null,
                    data_json text not null
                );
                create table if not exists ticker_source_bindings (
                    binding_id text primary key,
                    ticker text not null,
                    source_id text not null,
                    enabled integer not null,
                    tombstoned_at text,
                    version integer not null,
                    data_json text not null
                );
                create index if not exists idx_mbv2_bindings_ticker
                    on ticker_source_bindings(ticker, tombstoned_at);
                create index if not exists idx_mbv2_bindings_source
                    on ticker_source_bindings(source_id, tombstoned_at);
                create table if not exists ticker_source_binding_revisions (
                    binding_id text not null,
                    version integer not null,
                    data_json text not null,
                    primary key (binding_id, version)
                );
                create table if not exists raw_messages (
                    raw_message_id text primary key,
                    ticker text not null,
                    source_id text not null,
                    binding_id text not null,
                    identity_key text not null,
                    content_hash text not null,
                    raw_hash text not null,
                    revision integer not null,
                    processing_status text not null,
                    published_at text not null,
                    collected_at text not null,
                    data_json text not null,
                    unique(ticker, source_id, identity_key, content_hash)
                );
                create index if not exists idx_mbv2_raw_identity
                    on raw_messages(ticker, source_id, identity_key, revision desc);
                create index if not exists idx_mbv2_raw_processing
                    on raw_messages(processing_status, collected_at);
                create table if not exists source_item_baselines (
                    binding_id text not null,
                    identity_key text not null,
                    content_hash text not null,
                    observed_at text not null,
                    primary key(binding_id, identity_key, content_hash)
                );
                create table if not exists standard_messages (
                    standard_message_id text primary key,
                    raw_message_id text not null unique,
                    ticker text not null,
                    source_id text not null,
                    binding_id text not null,
                    published_at text not null,
                    data_json text not null
                );
                create index if not exists idx_mbv2_standard_ticker
                    on standard_messages(ticker, published_at desc);
                create table if not exists stream_items (
                    stream_item_id text primary key,
                    ticker text not null,
                    stream_offset integer not null,
                    publication_mode text not null,
                    published_at text not null,
                    data_json text not null,
                    unique(ticker, stream_offset)
                );
                create index if not exists idx_mbv2_stream_ticker
                    on stream_items(ticker, stream_offset);
                create table if not exists stream_members (
                    stream_item_id text not null,
                    member_index integer not null,
                    standard_message_id text not null,
                    data_json text not null,
                    primary key(stream_item_id, member_index),
                    foreign key(stream_item_id) references stream_items(stream_item_id)
                );
                create unique index if not exists idx_mbv2_stream_member_standard
                    on stream_members(standard_message_id);
                create table if not exists consumer_offsets (
                    consumer_id text not null,
                    ticker text not null,
                    stream_offset integer not null,
                    committed_at text not null,
                    data_json text not null,
                    primary key(consumer_id, ticker)
                );
                create table if not exists poll_states (
                    binding_id text primary key,
                    source_id text not null,
                    ticker text not null,
                    status text not null,
                    next_dispatch_at text,
                    data_json text not null
                );
                create index if not exists idx_mbv2_poll_dispatch
                    on poll_states(next_dispatch_at);
                create table if not exists buffer_entries (
                    standard_message_id text primary key,
                    binding_id text not null,
                    ticker text not null,
                    source_id text not null,
                    added_at text not null,
                    data_json text not null
                );
                create index if not exists idx_mbv2_buffer_binding
                    on buffer_entries(binding_id, added_at);
                create table if not exists acquisition_failures (
                    failure_id text primary key,
                    source_id text not null,
                    binding_id text not null,
                    ticker text not null,
                    error_code text not null,
                    raw_hash text not null,
                    last_seen_at text not null,
                    data_json text not null,
                    unique(binding_id, error_code, raw_hash)
                );
                create table if not exists operational_alerts (
                    alert_id text primary key,
                    alert_key text not null unique,
                    severity text not null,
                    resolved_at text,
                    last_seen_at text not null,
                    data_json text not null
                );
                create table if not exists scheduler_group_states (
                    scheduler_group text primary key,
                    data_json text not null
                );
                create table if not exists audit_log (
                    audit_id text primary key,
                    entity_type text not null,
                    entity_id text not null,
                    action text not null,
                    created_at text not null,
                    data_json text not null
                );
                create index if not exists idx_mbv2_audit_entity
                    on audit_log(entity_type, entity_id, created_at desc);
                """
            )

    @staticmethod
    def _json(model: BaseModel) -> str:
        return model.model_dump_json()

    @staticmethod
    def _model(model_type: type[ModelT], row: sqlite3.Row | None) -> ModelT | None:
        return None if row is None else model_type.model_validate_json(row["data_json"])

    @staticmethod
    def _models(model_type: type[ModelT], rows: Sequence[sqlite3.Row]) -> list[ModelT]:
        return [model_type.model_validate_json(row["data_json"]) for row in rows]

    # -- Source registry and profiles -------------------------------------------------

    def save_source(self, source: SourceDefinition) -> None:
        with self.transaction() as connection:
            self._save_source_tx(connection, source)

    def save_source_with_bindings(
        self, source: SourceDefinition, bindings: Sequence[TickerSourceBinding]
    ) -> None:
        with self.transaction() as connection:
            self._save_source_tx(connection, source)
            for binding in bindings:
                self._save_binding_tx(connection, binding)

    def _save_source_tx(self, connection: sqlite3.Connection, source: SourceDefinition) -> None:
        value = self._json(source)
        connection.execute(
            """insert into source_definitions(
                     source_id, version, enabled, scheduler_group, data_json)
                   values(?, ?, ?, ?, ?)
                   on conflict(source_id) do update set version=excluded.version,
                     enabled=excluded.enabled, scheduler_group=excluded.scheduler_group,
                     data_json=excluded.data_json""",
            (
                source.source_id,
                source.version,
                int(source.enabled),
                source.scheduler_group,
                value,
            ),
        )
        connection.execute(
            """insert into source_definition_revisions(source_id, version, data_json)
                   values(?, ?, ?) on conflict(source_id, version) do nothing""",
            (source.source_id, source.version, value),
        )

    def get_source(self, source_id: str) -> SourceDefinition | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from source_definitions where source_id=?",
                (source_id.strip().lower(),),
            ).fetchone()
        return self._model(SourceDefinition, row)

    def list_sources(self, *, include_disabled: bool = True) -> list[SourceDefinition]:
        where = "" if include_disabled else "where enabled=1"
        with self._connect() as connection:
            rows = connection.execute(
                f"select data_json from source_definitions {where} order by source_id"
            ).fetchall()
        return self._models(SourceDefinition, rows)

    def source_revision(self, source_id: str, version: int) -> SourceDefinition | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from source_definition_revisions where source_id=? and version=?",
                (source_id.strip().lower(), version),
            ).fetchone()
        return self._model(SourceDefinition, row)

    def list_source_revisions(self, source_id: str) -> list[SourceDefinition]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from source_definition_revisions
                   where source_id=? order by version desc""",
                (source_id.strip().lower(),),
            ).fetchall()
        return self._models(SourceDefinition, rows)

    def delete_source_control_plane(
        self,
        source_id: str,
        *,
        actor: UpdateActor,
        reason: str | None,
    ) -> int:
        normalized = source_id.strip().lower()
        with self.transaction() as connection:
            count = connection.execute(
                """select count(*) from ticker_source_bindings
                   where source_id=? and tombstoned_at is null""",
                (normalized,),
            ).fetchone()[0]
            connection.execute("delete from buffer_entries where source_id=?", (normalized,))
            connection.execute("delete from poll_states where source_id=?", (normalized,))
            connection.execute(
                "delete from ticker_source_bindings where source_id=?", (normalized,)
            )
            connection.execute("delete from source_definitions where source_id=?", (normalized,))
            profiles = connection.execute(
                "select profile_id, data_json from default_profiles"
            ).fetchall()
            for row in profiles:
                profile = DefaultMonitoringProfile.model_validate_json(row["data_json"])
                entries = [entry for entry in profile.entries if entry.source_id != normalized]
                if len(entries) == len(profile.entries):
                    continue
                updated = profile.model_copy(
                    update={
                        "entries": entries,
                        "version": profile.version + 1,
                        "updated_at": utc_now(),
                        "updated_by": actor,
                        "updated_reason": reason,
                    }
                )
                value = self._json(updated)
                connection.execute(
                    "update default_profiles set version=?, data_json=? where profile_id=?",
                    (updated.version, value, updated.profile_id),
                )
                connection.execute(
                    """insert into default_profile_revisions(
                         profile_id, version, data_json) values(?,?,?)""",
                    (updated.profile_id, updated.version, value),
                )
        return int(count)

    def save_default_profile(self, profile: DefaultMonitoringProfile) -> None:
        value = self._json(profile)
        with self.transaction() as connection:
            connection.execute(
                """insert into default_profiles(profile_id, version, data_json) values(?,?,?)
                   on conflict(profile_id) do update set version=excluded.version,
                     data_json=excluded.data_json""",
                (profile.profile_id, profile.version, value),
            )
            connection.execute(
                """insert into default_profile_revisions(profile_id, version, data_json)
                   values(?,?,?) on conflict(profile_id, version) do nothing""",
                (profile.profile_id, profile.version, value),
            )

    def get_default_profile(self, profile_id: str = "default") -> DefaultMonitoringProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from default_profiles where profile_id=?", (profile_id,)
            ).fetchone()
        return self._model(DefaultMonitoringProfile, row)

    def profile_revision(self, profile_id: str, version: int) -> DefaultMonitoringProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from default_profile_revisions where profile_id=? and version=?",
                (profile_id, version),
            ).fetchone()
        return self._model(DefaultMonitoringProfile, row)

    def list_profile_revisions(self, profile_id: str) -> list[DefaultMonitoringProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from default_profile_revisions where profile_id=?
                   order by version desc""",
                (profile_id,),
            ).fetchall()
        return self._models(DefaultMonitoringProfile, rows)

    # -- Ticker lifecycle and bindings ----------------------------------------------

    def save_ticker_state(self, state: TickerMonitoringState) -> None:
        with self.transaction() as connection:
            prior = connection.execute(
                "SELECT data_json FROM ticker_monitoring_states WHERE ticker=?", (state.ticker,)
            ).fetchone()
            previous = TickerMonitoringState.model_validate_json(prior[0]) if prior else None
            began = None
            if state.status.value == "running":
                began = (
                    previous.continuous_run_started_at
                    if previous and previous.status.value == "running"
                    else utc_now()
                )
            state = state.model_copy(update={"continuous_run_started_at": began})
            from doxagent.v2_control.mirror import state_in

            control = state_in(connection, state.ticker)
            if (
                control
                and state.status.value == "running"
                and not (control["analysis_allowed"] or control.get("admission_allowed"))
            ):
                raise ValueError("V2 control has stopped Bus admission")
            connection.execute(
                """insert into ticker_monitoring_states(ticker, status, data_json) values(?,?,?)
                   on conflict(ticker) do update set status=excluded.status,
                     data_json=excluded.data_json""",
                (state.ticker, state.status.value, self._json(state)),
            )

    def get_ticker_state(self, ticker: str) -> TickerMonitoringState | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from ticker_monitoring_states where ticker=?",
                (ticker.strip().upper(),),
            ).fetchone()
        return self._model(TickerMonitoringState, row)

    def list_ticker_states(self) -> list[TickerMonitoringState]:
        with self._connect() as connection:
            rows = connection.execute(
                "select data_json from ticker_monitoring_states order by ticker"
            ).fetchall()
        return self._models(TickerMonitoringState, rows)

    def save_binding(self, binding: TickerSourceBinding) -> None:
        with self.transaction() as connection:
            self._save_binding_tx(connection, binding)

    def _save_binding_tx(
        self, connection: sqlite3.Connection, binding: TickerSourceBinding
    ) -> None:
        value = self._json(binding)
        connection.execute(
            """insert into ticker_source_bindings(
                     binding_id,ticker,source_id,enabled,tombstoned_at,version,data_json)
                   values(?,?,?,?,?,?,?) on conflict(binding_id) do update set
                     ticker=excluded.ticker, source_id=excluded.source_id,
                     enabled=excluded.enabled, tombstoned_at=excluded.tombstoned_at,
                     version=excluded.version, data_json=excluded.data_json""",
            (
                binding.binding_id,
                binding.ticker,
                binding.source_id,
                int(binding.enabled),
                binding.tombstoned_at.isoformat() if binding.tombstoned_at else None,
                binding.version,
                value,
            ),
        )
        connection.execute(
            """insert into ticker_source_binding_revisions(binding_id,version,data_json)
                   values(?,?,?) on conflict(binding_id,version) do nothing""",
            (binding.binding_id, binding.version, value),
        )

    def get_binding(
        self, binding_id: str, *, include_tombstoned: bool = False
    ) -> TickerSourceBinding | None:
        clause = "" if include_tombstoned else " and tombstoned_at is null"
        with self._connect() as connection:
            row = connection.execute(
                f"select data_json from ticker_source_bindings where binding_id=?{clause}",
                (binding_id,),
            ).fetchone()
        return self._model(TickerSourceBinding, row)

    def list_bindings(
        self,
        *,
        ticker: str | None = None,
        source_id: str | None = None,
        active_only: bool = False,
        include_tombstoned: bool = False,
    ) -> list[TickerSourceBinding]:
        clauses: list[str] = []
        params: list[object] = []
        if ticker:
            clauses.append("ticker=?")
            params.append(ticker.strip().upper())
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id.strip().lower())
        if active_only:
            clauses.append("enabled=1")
        if not include_tombstoned:
            clauses.append("tombstoned_at is null")
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"select data_json from ticker_source_bindings {where} order by ticker,source_id",
                params,
            ).fetchall()
        return self._models(TickerSourceBinding, rows)

    def binding_revision(self, binding_id: str, version: int) -> TickerSourceBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                """select data_json from ticker_source_binding_revisions
                   where binding_id=? and version=?""",
                (binding_id, version),
            ).fetchone()
        return self._model(TickerSourceBinding, row)

    # -- Raw, Standard and buffering -------------------------------------------------

    def record_raw(self, candidate: RawMessage) -> tuple[IngestDecision, RawMessage]:
        with self.transaction() as connection:
            latest_row = connection.execute(
                """select data_json from raw_messages
                   where ticker=? and source_id=? and identity_key=?
                   order by revision desc limit 1""",
                (candidate.ticker, candidate.source_id, candidate.identity_key),
            ).fetchone()
            if latest_row is not None:
                latest = RawMessage.model_validate_json(latest_row["data_json"])
                if latest.content_hash == candidate.content_hash:
                    self._record_body_attempt(connection, candidate, latest)
                    updated = latest.model_copy(
                        update={
                            "last_seen_at": candidate.collected_at,
                            "duplicate_seen_count": latest.duplicate_seen_count + 1,
                        }
                    )
                    connection.execute(
                        "update raw_messages set data_json=? where raw_message_id=?",
                        (self._json(updated), updated.raw_message_id),
                    )
                    return IngestDecision.DUPLICATE, updated
                candidate = candidate.model_copy(update={"revision": latest.revision + 1})
                decision = IngestDecision.REVISION
            else:
                decision = IngestDecision.INSERTED
            # Capture origin at first ingestion, never on a delayed normalization/replay.
            # Caller metadata is untrusted and cannot manufacture business provenance.
            from doxagent.v2_control.mirror import state_in

            origin = state_in(connection, candidate.ticker)
            metadata = dict(candidate.metadata)
            metadata.pop("v2_control_origin", None)
            if origin is not None:
                metadata["v2_control_origin"] = {
                    "epoch": origin["epoch"],
                    "mode": origin["mode"],
                    "recorded_at": candidate.collected_at.isoformat(),
                }
            candidate = candidate.model_copy(update={"metadata": metadata})
            self._record_body_attempt(connection, candidate, candidate)
            connection.execute(
                """insert into raw_messages(
                     raw_message_id,ticker,source_id,binding_id,identity_key,content_hash,
                     raw_hash,revision,processing_status,published_at,collected_at,data_json)
                   values(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate.raw_message_id,
                    candidate.ticker,
                    candidate.source_id,
                    candidate.binding_id,
                    candidate.identity_key,
                    candidate.content_hash,
                    candidate.raw_hash,
                    candidate.revision,
                    candidate.processing_status.value,
                    candidate.published_at.isoformat(),
                    candidate.collected_at.isoformat(),
                    self._json(candidate),
                ),
            )
            return decision, candidate

    def _record_body_attempt(self, connection, candidate, persisted):
        attempt = candidate.metadata.get("v2_body_completion")
        origin = persisted.metadata.get("v2_control_origin")
        if not attempt or not origin:
            return
        audit = AuditRecord(
            audit_id=attempt["attempt_id"],
            entity_type="body_completion",
            entity_id=persisted.raw_message_id,
            action="completed",
            actor=UpdateActor.SYSTEM,
            payload={
                **attempt,
                "ticker": persisted.ticker,
                "source_id": persisted.source_id,
                "binding_id": persisted.binding_id,
                "raw_message_id": persisted.raw_message_id,
                "v2_control_origin": origin,
            },
        )
        connection.execute(
            "INSERT OR IGNORE INTO audit_log "
            "(audit_id,entity_type,entity_id,action,created_at,data_json) "
            "VALUES(?,?,?,?,?,?)",
            (
                audit.audit_id,
                audit.entity_type,
                audit.entity_id,
                audit.action,
                audit.created_at.isoformat(),
                self._json(audit),
            ),
        )

    def save_raw(self, raw: RawMessage) -> None:
        with self.transaction() as connection:
            connection.execute(
                """update raw_messages set processing_status=?, data_json=?
                   where raw_message_id=?""",
                (raw.processing_status.value, self._json(raw), raw.raw_message_id),
            )

    def get_raw(self, raw_message_id: str) -> RawMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from raw_messages where raw_message_id=?", (raw_message_id,)
            ).fetchone()
        return self._model(RawMessage, row)

    def list_raw(
        self,
        *,
        ticker: str | None = None,
        status: RawProcessingStatus | None = None,
        limit: int = 100,
    ) -> list[RawMessage]:
        clauses: list[str] = []
        params: list[object] = []
        if ticker:
            clauses.append("ticker=?")
            params.append(ticker.strip().upper())
        if status:
            clauses.append("processing_status=?")
            params.append(status.value)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"select data_json from raw_messages {where} order by collected_at desc limit ?",
                params,
            ).fetchall()
        return self._models(RawMessage, rows)

    def add_baseline(self, raw: RawMessage) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into source_item_baselines(
                     binding_id,identity_key,content_hash,observed_at)
                   values(?,?,?,?) on conflict do nothing""",
                (raw.binding_id, raw.identity_key, raw.content_hash, raw.collected_at.isoformat()),
            )

    def reset_binding_baseline(self, binding_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "delete from source_item_baselines where binding_id=?", (binding_id,)
            )
            connection.execute("delete from poll_states where binding_id=?", (binding_id,))

    def save_standard(self, message: StandardMessage) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into standard_messages(
                     standard_message_id,raw_message_id,ticker,source_id,binding_id,published_at,data_json)
                   values(?,?,?,?,?,?,?) on conflict(raw_message_id) do nothing""",
                (
                    message.standard_message_id,
                    message.raw_message_id,
                    message.ticker,
                    message.source_id,
                    message.binding_id,
                    message.published_at.isoformat(),
                    self._json(message),
                ),
            )

    def finalize_standard(
        self,
        *,
        raw: RawMessage,
        message: StandardMessage,
        streaming: StreamingConfig,
    ) -> StreamItem | None:
        """Atomically persist Standard + publication target + completed Raw state."""

        completed = raw.model_copy(update={"processing_status": RawProcessingStatus.COMPLETED})
        with self.transaction() as connection:
            connection.execute(
                """insert into standard_messages(
                     standard_message_id,raw_message_id,ticker,source_id,binding_id,published_at,data_json)
                   values(?,?,?,?,?,?,?) on conflict(raw_message_id) do nothing""",
                (
                    message.standard_message_id,
                    message.raw_message_id,
                    message.ticker,
                    message.source_id,
                    message.binding_id,
                    message.published_at.isoformat(),
                    self._json(message),
                ),
            )
            stored_row = connection.execute(
                "select data_json from standard_messages where raw_message_id=?",
                (raw.raw_message_id,),
            ).fetchone()
            stored = StandardMessage.model_validate_json(stored_row["data_json"])
            item: StreamItem | None = None
            if streaming.publication_mode is PublicationMode.IMMEDIATE:
                existing_row = connection.execute(
                    """select si.data_json from stream_items si
                       join stream_members sm on sm.stream_item_id=si.stream_item_id
                       where sm.standard_message_id=?""",
                    (stored.standard_message_id,),
                ).fetchone()
                if existing_row is not None:
                    item = StreamItem.model_validate_json(existing_row["data_json"])
                else:
                    item = self._publish_in_transaction(
                        connection, stored.ticker, [stored], PublicationMode.IMMEDIATE
                    )
            else:
                added_at = utc_now()
                connection.execute(
                    """insert into buffer_entries(
                         standard_message_id,binding_id,ticker,source_id,added_at,data_json)
                       values(?,?,?,?,?,?) on conflict(standard_message_id) do nothing""",
                    (
                        stored.standard_message_id,
                        stored.binding_id,
                        stored.ticker,
                        stored.source_id,
                        added_at.isoformat(),
                        self._json(stored),
                    ),
                )
            connection.execute(
                """update raw_messages set processing_status=?,data_json=?
                   where raw_message_id=?""",
                (
                    completed.processing_status.value,
                    self._json(completed),
                    completed.raw_message_id,
                ),
            )
        return item

    def get_standard(self, standard_message_id: str) -> StandardMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from standard_messages where standard_message_id=?",
                (standard_message_id,),
            ).fetchone()
        return self._model(StandardMessage, row)

    def get_standard_for_raw(self, raw_message_id: str) -> StandardMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from standard_messages where raw_message_id=?",
                (raw_message_id,),
            ).fetchone()
        return self._model(StandardMessage, row)

    def list_standard(
        self, *, ticker: str | None = None, limit: int = 100
    ) -> list[StandardMessage]:
        where = "where ticker=?" if ticker else ""
        params: list[object] = [ticker.strip().upper()] if ticker else []
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""select data_json from standard_messages {where}
                    order by published_at desc limit ?""",
                params,
            ).fetchall()
        return self._models(StandardMessage, rows)

    def add_buffer_entry(
        self, message: StandardMessage, *, added_at: datetime | None = None
    ) -> None:
        when = added_at or utc_now()
        with self.transaction() as connection:
            connection.execute(
                """insert into buffer_entries(
                     standard_message_id,binding_id,ticker,source_id,added_at,data_json)
                   values(?,?,?,?,?,?) on conflict(standard_message_id) do nothing""",
                (
                    message.standard_message_id,
                    message.binding_id,
                    message.ticker,
                    message.source_id,
                    when.isoformat(),
                    self._json(message),
                ),
            )

    def list_buffer(self, binding_id: str) -> list[tuple[StandardMessage, datetime]]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json,added_at from buffer_entries where binding_id=?
                   order by added_at,standard_message_id""",
                (binding_id,),
            ).fetchall()
        return [
            (
                StandardMessage.model_validate_json(row["data_json"]),
                datetime.fromisoformat(row["added_at"]),
            )
            for row in rows
        ]

    def delete_buffer_members(self, standard_message_ids: Sequence[str]) -> None:
        if not standard_message_ids:
            return
        placeholders = ",".join("?" for _ in standard_message_ids)
        with self.transaction() as connection:
            connection.execute(
                f"delete from buffer_entries where standard_message_id in ({placeholders})",
                list(standard_message_ids),
            )

    def list_buffer_binding_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "select distinct binding_id from buffer_entries order by binding_id"
            ).fetchall()
        return [str(row[0]) for row in rows]

    # -- Durable ticker stream and consumer offsets ---------------------------------

    def publish(
        self, ticker: str, messages: Sequence[StandardMessage], mode: PublicationMode
    ) -> StreamItem:
        if not messages:
            raise ValueError("cannot publish an empty stream item")
        normalized_ticker = ticker.strip().upper()
        if any(message.ticker != normalized_ticker for message in messages):
            raise ValueError("all stream members must belong to the stream ticker")
        with self.transaction() as connection:
            return self._publish_in_transaction(connection, normalized_ticker, messages, mode)

    def publish_buffered(self, ticker: str, messages: Sequence[StandardMessage]) -> StreamItem:
        """Atomically publish a buffered batch and remove exactly its pending members."""

        if not messages:
            raise ValueError("cannot publish an empty buffered stream item")
        normalized_ticker = ticker.strip().upper()
        if any(message.ticker != normalized_ticker for message in messages):
            raise ValueError("all buffered members must belong to the stream ticker")
        identifiers = [message.standard_message_id for message in messages]
        placeholders = ",".join("?" for _ in identifiers)
        with self.transaction() as connection:
            present = connection.execute(
                f"""select standard_message_id from buffer_entries
                    where standard_message_id in ({placeholders})""",
                identifiers,
            ).fetchall()
            if {str(row[0]) for row in present} != set(identifiers):
                raise ValueError("buffer membership changed before publication")
            item = self._publish_in_transaction(
                connection, normalized_ticker, messages, PublicationMode.BUFFERED
            )
            connection.execute(
                f"delete from buffer_entries where standard_message_id in ({placeholders})",
                identifiers,
            )
            return item

    def _publish_in_transaction(
        self,
        connection: sqlite3.Connection,
        ticker: str,
        messages: Sequence[StandardMessage],
        mode: PublicationMode,
    ) -> StreamItem:
        item_id = new_id("stream")
        now = utc_now()
        row = connection.execute(
            "select coalesce(max(stream_offset),0) from stream_items where ticker=?",
            (ticker,),
        ).fetchone()
        offset = int(row[0]) + 1
        item = StreamItem(
            stream_item_id=item_id,
            ticker=ticker,
            stream_offset=offset,
            publication_mode=mode,
            published_at=now,
            member_count=len(messages),
        )
        connection.execute(
            """insert into stream_items(
                 stream_item_id,ticker,stream_offset,publication_mode,published_at,data_json)
               values(?,?,?,?,?,?)""",
            (item_id, ticker, offset, mode.value, now.isoformat(), self._json(item)),
        )
        for index, message in enumerate(messages):
            member = StreamMember(
                stream_item_id=item_id,
                member_index=index,
                standard_message_id=message.standard_message_id,
            )
            connection.execute(
                """insert into stream_members(
                     stream_item_id,member_index,standard_message_id,data_json)
                   values(?,?,?,?)""",
                (item_id, index, message.standard_message_id, self._json(member)),
            )
        return item

    def get_stream_item(self, stream_item_id: str) -> MaterializedStreamItem | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from stream_items where stream_item_id=?", (stream_item_id,)
            ).fetchone()
            if row is None:
                return None
            members = self._materialized_members(connection, stream_item_id)
        item = StreamItem.model_validate_json(row["data_json"])
        return MaterializedStreamItem(item=item, members=members)

    def read_stream(
        self, ticker: str, *, after_offset: int = 0, limit: int = 100
    ) -> list[MaterializedStreamItem]:
        normalized = ticker.strip().upper()
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from stream_items where ticker=? and stream_offset>?
                   order by stream_offset limit ?""",
                (normalized, after_offset, limit),
            ).fetchall()
            result: list[MaterializedStreamItem] = []
            for row in rows:
                item = StreamItem.model_validate_json(row["data_json"])
                result.append(
                    MaterializedStreamItem(
                        item=item,
                        members=self._materialized_members(connection, item.stream_item_id),
                    )
                )
        return result

    def _materialized_members(
        self, connection: sqlite3.Connection, stream_item_id: str
    ) -> list[MaterializedStreamMember]:
        rows = connection.execute(
            """select sm.data_json as relation_json, std.data_json as standard_json
               from stream_members sm
               join standard_messages std
                 on std.standard_message_id=sm.standard_message_id
               where sm.stream_item_id=?
               order by sm.member_index""",
            (stream_item_id,),
        ).fetchall()
        result: list[MaterializedStreamMember] = []
        for row in rows:
            relation = StreamMember.model_validate_json(row["relation_json"])
            standard = StandardMessage.model_validate_json(row["standard_json"])
            result.append(
                MaterializedStreamMember(
                    stream_item_id=relation.stream_item_id,
                    member_index=relation.member_index,
                    standard_message_id=relation.standard_message_id,
                    source_id=standard.source_id,
                    binding_id=standard.binding_id,
                    title=standard.title,
                    body=standard.body,
                    source=standard.source,
                    url=standard.url,
                    published_at=standard.published_at,
                    normalized_at=standard.normalized_at,
                )
            )
        return result

    def latest_stream_offset(self, ticker: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "select coalesce(max(stream_offset),0) from stream_items where ticker=?",
                (ticker.strip().upper(),),
            ).fetchone()
        return int(row[0])

    def get_consumer_offset(self, consumer_id: str, ticker: str) -> ConsumerOffset:
        normalized = ticker.strip().upper()
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from consumer_offsets where consumer_id=? and ticker=?",
                (consumer_id, normalized),
            ).fetchone()
        return self._model(ConsumerOffset, row) or ConsumerOffset(
            consumer_id=consumer_id, ticker=normalized
        )

    def initialize_runtime_cursor(self, consumer_id: str, ticker: str) -> int:
        """Commit the initial tail and initialization marker in one transaction.

        An existing consumer row is also a durable receipt from older releases;
        never seek it forward after a crash between their two separate writes.
        """
        ticker = ticker.strip().upper()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT data_json FROM ticker_monitoring_states WHERE ticker=?", (ticker,)
            ).fetchone()
            if row is None:
                raise KeyError(f"ticker monitoring state not found: {ticker}")
            state = TickerMonitoringState.model_validate_json(row[0])
            existing = connection.execute(
                "SELECT stream_offset FROM consumer_offsets WHERE consumer_id=? AND ticker=?",
                (consumer_id, ticker),
            ).fetchone()
            if existing:
                offset = int(existing[0])
            elif state.runtime_cursor_initialized:
                offset = 0
            else:
                offset = int(
                    connection.execute(
                        "SELECT coalesce(max(stream_offset),0) FROM stream_items WHERE ticker=?",
                        (ticker,),
                    ).fetchone()[0]
                )
            if existing is None:
                cursor = ConsumerOffset(
                    consumer_id=consumer_id, ticker=ticker, stream_offset=offset
                )
                connection.execute(
                    """INSERT INTO consumer_offsets
                    (consumer_id,ticker,stream_offset,committed_at,data_json) VALUES(?,?,?,?,?)""",
                    (
                        consumer_id,
                        ticker,
                        offset,
                        cursor.committed_at.isoformat(),
                        self._json(cursor),
                    ),
                )
            if not state.runtime_cursor_initialized:
                state = state.model_copy(
                    update={
                        "runtime_cursor_initialized": True,
                        "updated_at": utc_now(),
                    }
                )
                connection.execute(
                    "UPDATE ticker_monitoring_states SET data_json=? WHERE ticker=?",
                    (self._json(state), ticker),
                )
            return offset

    def commit_consumer_offset(
        self, consumer_id: str, ticker: str, stream_offset: int
    ) -> ConsumerOffset:
        current = self.get_consumer_offset(consumer_id, ticker)
        if stream_offset < current.stream_offset:
            raise ValueError("consumer offsets cannot move backwards")
        updated = ConsumerOffset(
            consumer_id=consumer_id,
            ticker=ticker.strip().upper(),
            stream_offset=stream_offset,
            committed_at=utc_now(),
        )
        with self.transaction() as connection:
            connection.execute(
                """insert into consumer_offsets(
                     consumer_id,ticker,stream_offset,committed_at,data_json) values(?,?,?,?,?)
                   on conflict(consumer_id,ticker) do update set
                     stream_offset=excluded.stream_offset, committed_at=excluded.committed_at,
                     data_json=excluded.data_json""",
                (
                    updated.consumer_id,
                    updated.ticker,
                    updated.stream_offset,
                    updated.committed_at.isoformat(),
                    self._json(updated),
                ),
            )
        return updated

    # -- Poll state, failures, alerts and audit --------------------------------------

    def save_poll_state(self, state: PollState) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into poll_states(
                     binding_id,source_id,ticker,status,next_dispatch_at,data_json)
                   values(?,?,?,?,?,?) on conflict(binding_id) do update set
                     source_id=excluded.source_id,ticker=excluded.ticker,status=excluded.status,
                     next_dispatch_at=excluded.next_dispatch_at,data_json=excluded.data_json""",
                (
                    state.binding_id,
                    state.source_id,
                    state.ticker,
                    state.status.value,
                    state.next_dispatch_at.isoformat() if state.next_dispatch_at else None,
                    self._json(state),
                ),
            )

    def get_poll_state(self, binding: TickerSourceBinding) -> PollState:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from poll_states where binding_id=?", (binding.binding_id,)
            ).fetchone()
        return self._model(PollState, row) or PollState(
            binding_id=binding.binding_id, source_id=binding.source_id, ticker=binding.ticker
        )

    def list_poll_states(self, *, ticker: str | None = None) -> list[PollState]:
        where = "where ticker=?" if ticker else ""
        params = (ticker.strip().upper(),) if ticker else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"select data_json from poll_states {where} order by ticker,source_id", params
            ).fetchall()
        return self._models(PollState, rows)

    def save_failure(self, failure: AcquisitionFailure) -> AcquisitionFailure:
        with self.transaction() as connection:
            row = connection.execute(
                """select data_json from acquisition_failures
                   where binding_id=? and error_code=? and raw_hash=?""",
                (failure.binding_id, failure.error_code, failure.raw_hash),
            ).fetchone()
            if row is not None:
                current = AcquisitionFailure.model_validate_json(row["data_json"])
                failure = current.model_copy(
                    update={
                        "last_seen_at": failure.last_seen_at,
                        "repeat_count": current.repeat_count + 1,
                        "error_message": failure.error_message,
                    }
                )
                connection.execute(
                    """update acquisition_failures set last_seen_at=?,data_json=?
                       where failure_id=?""",
                    (failure.last_seen_at.isoformat(), self._json(failure), failure.failure_id),
                )
            else:
                connection.execute(
                    """insert into acquisition_failures(
                         failure_id,source_id,binding_id,ticker,error_code,raw_hash,last_seen_at,data_json)
                       values(?,?,?,?,?,?,?,?)""",
                    (
                        failure.failure_id,
                        failure.source_id,
                        failure.binding_id,
                        failure.ticker,
                        failure.error_code,
                        failure.raw_hash,
                        failure.last_seen_at.isoformat(),
                        self._json(failure),
                    ),
                )
        return failure

    def list_failures(
        self, *, ticker: str | None = None, limit: int = 100
    ) -> list[AcquisitionFailure]:
        where = "where ticker=?" if ticker else ""
        params: list[object] = [ticker.strip().upper()] if ticker else []
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""select data_json from acquisition_failures {where}
                    order by last_seen_at desc limit ?""",
                params,
            ).fetchall()
        return self._models(AcquisitionFailure, rows)

    def upsert_alert(self, alert: OperationalAlert) -> OperationalAlert:
        with self.transaction() as connection:
            row = connection.execute(
                "select data_json from operational_alerts where alert_key=?", (alert.alert_key,)
            ).fetchone()
            if row is not None:
                current = OperationalAlert.model_validate_json(row["data_json"])
                alert = current.model_copy(
                    update={
                        "severity": alert.severity,
                        "message": alert.message,
                        "last_seen_at": alert.last_seen_at,
                        "repeat_count": current.repeat_count + 1,
                        "resolved_at": None,
                        "metadata": alert.metadata,
                    }
                )
                connection.execute(
                    """update operational_alerts set
                         severity=?,resolved_at=null,last_seen_at=?,data_json=?
                       where alert_key=?""",
                    (
                        alert.severity.value,
                        alert.last_seen_at.isoformat(),
                        self._json(alert),
                        alert.alert_key,
                    ),
                )
            else:
                connection.execute(
                    """insert into operational_alerts(
                         alert_id,alert_key,severity,resolved_at,last_seen_at,data_json)
                       values(?,?,?,?,?,?)""",
                    (
                        alert.alert_id,
                        alert.alert_key,
                        alert.severity.value,
                        None,
                        alert.last_seen_at.isoformat(),
                        self._json(alert),
                    ),
                )
        return alert

    def resolve_alert(self, alert_key: str) -> OperationalAlert | None:
        with self.transaction() as connection:
            row = connection.execute(
                "select data_json from operational_alerts where alert_key=?", (alert_key,)
            ).fetchone()
            if row is None:
                return None
            current = OperationalAlert.model_validate_json(row["data_json"])
            resolved_at = utc_now()
            resolved = current.model_copy(update={"resolved_at": resolved_at})
            connection.execute(
                "update operational_alerts set resolved_at=?,data_json=? where alert_key=?",
                (resolved_at.isoformat(), self._json(resolved), alert_key),
            )
        return resolved

    def list_alerts(self, *, active_only: bool = False) -> list[OperationalAlert]:
        where = "where resolved_at is null" if active_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"select data_json from operational_alerts {where} order by last_seen_at desc"
            ).fetchall()
        return self._models(OperationalAlert, rows)

    def save_scheduler_group_state(self, state: SchedulerGroupState) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into scheduler_group_states(scheduler_group,data_json) values(?,?)
                   on conflict(scheduler_group) do update set data_json=excluded.data_json""",
                (state.scheduler_group, self._json(state)),
            )

    def get_scheduler_group_state(self, group: str) -> SchedulerGroupState:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from scheduler_group_states where scheduler_group=?", (group,)
            ).fetchone()
        return self._model(SchedulerGroupState, row) or SchedulerGroupState(scheduler_group=group)

    def save_audit(self, audit: AuditRecord) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into audit_log(
                     audit_id,entity_type,entity_id,action,created_at,data_json)
                   values(?,?,?,?,?,?)""",
                (
                    audit.audit_id,
                    audit.entity_type,
                    audit.entity_id,
                    audit.action,
                    audit.created_at.isoformat(),
                    self._json(audit),
                ),
            )

    def list_audit(self, entity_type: str, entity_id: str) -> list[AuditRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from audit_log where entity_type=? and entity_id=?
                   order by created_at desc""",
                (entity_type, entity_id),
            ).fetchall()
        return self._models(AuditRecord, rows)

    def snapshot_counts(self) -> dict[str, int]:
        tables = (
            "source_definitions",
            "ticker_source_bindings",
            "raw_messages",
            "standard_messages",
            "stream_items",
            "buffer_entries",
            "acquisition_failures",
            "operational_alerts",
        )
        with self._connect() as connection:
            return {
                table: int(connection.execute(f"select count(*) from {table}").fetchone()[0])
                for table in tables
            }


__all__ = ["MessageBusV2Repository"]
