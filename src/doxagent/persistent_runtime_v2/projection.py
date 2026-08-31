"""Transactional local outbox and compact Postgres/Supabase projections."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from doxagent.postgres import connect_postgres

from .schema import DailyCloseRun, RuntimeCase, RuntimeV2Model


class RuntimeTerminalProjection(RuntimeV2Model):
    projection_version: Literal["persistent-runtime-terminal.v1"] = (
        "persistent-runtime-terminal.v1"
    )
    case_id: str
    ticker: str
    trading_date: date
    case_status: str
    technical_status: str
    primary_route: str | None
    event_library_version: int = Field(ge=1)
    policy_set_version: int = Field(ge=1)
    hot_path_latency_ms: int | None = Field(default=None, ge=0)
    model_turn_count: int = Field(ge=0)
    updated_at: datetime

    @classmethod
    def from_case(cls, case: RuntimeCase, *, model_turn_count: int) -> RuntimeTerminalProjection:
        return cls(
            case_id=case.case_id,
            ticker=case.ticker,
            trading_date=case.trading_date,
            case_status=case.status.value,
            technical_status=case.technical_status.value,
            primary_route=(case.route.primary_route.value if case.route else None),
            event_library_version=case.version_pin.event_library_version,
            policy_set_version=case.version_pin.policy_set_version,
            hot_path_latency_ms=case.hot_path_latency_ms,
            model_turn_count=model_turn_count,
            updated_at=case.updated_at,
        )


class RuntimeDailyProjection(RuntimeV2Model):
    projection_version: Literal["persistent-runtime-daily.v1"] = (
        "persistent-runtime-daily.v1"
    )
    run_id: str
    ticker: str
    trading_date: date
    stage: str
    base_library_version: int = Field(ge=0)
    published_library_version: int | None = Field(default=None, ge=0)
    candidate_count: int = Field(ge=0)
    trade_record_count: int = Field(ge=0)
    badcase_count: int = Field(ge=0)
    updated_at: datetime

    @classmethod
    def from_run(cls, run: DailyCloseRun) -> RuntimeDailyProjection:
        return cls(
            run_id=run.run_id,
            ticker=run.ticker,
            trading_date=run.trading_date,
            stage=run.stage.value,
            base_library_version=run.base_library_version,
            published_library_version=run.published_library_version,
            candidate_count=len(run.candidate_keys),
            trade_record_count=len(run.trade_record_ids),
            badcase_count=len(run.badcase_ids),
            updated_at=run.updated_at,
        )


class PostgresRuntimeV2ProjectionSink:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def upsert(self, kind: str, payload: dict[str, Any]) -> None:
        import psycopg

        with connect_postgres(psycopg, self.database_url) as connection:
            with connection.cursor() as cursor:
                if kind == "terminal_case":
                    terminal = RuntimeTerminalProjection.model_validate(payload)
                    cursor.execute(
                        """
                        insert into doxagent.persistent_runtime_v2_terminal_cases(
                          case_id,ticker,trading_date,case_status,technical_status,
                          primary_route,event_library_version,policy_set_version,
                          hot_path_latency_ms,model_turn_count,projection_json,updated_at
                        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                        on conflict(case_id) do update set
                          case_status=excluded.case_status,
                          technical_status=excluded.technical_status,
                          primary_route=excluded.primary_route,
                          hot_path_latency_ms=excluded.hot_path_latency_ms,
                          model_turn_count=excluded.model_turn_count,
                          projection_json=excluded.projection_json,
                          updated_at=excluded.updated_at
                        """,
                        (
                            terminal.case_id,
                            terminal.ticker,
                            terminal.trading_date,
                            terminal.case_status,
                            terminal.technical_status,
                            terminal.primary_route,
                            terminal.event_library_version,
                            terminal.policy_set_version,
                            terminal.hot_path_latency_ms,
                            terminal.model_turn_count,
                            terminal.model_dump_json(),
                            terminal.updated_at,
                        ),
                    )
                elif kind == "daily_close":
                    daily = RuntimeDailyProjection.model_validate(payload)
                    cursor.execute(
                        """
                        insert into doxagent.persistent_runtime_v2_daily_closes(
                          run_id,ticker,trading_date,stage,base_library_version,
                          published_library_version,candidate_count,trade_record_count,
                          badcase_count,projection_json,updated_at
                        ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                        on conflict(run_id) do update set
                          stage=excluded.stage,
                          published_library_version=excluded.published_library_version,
                          candidate_count=excluded.candidate_count,
                          trade_record_count=excluded.trade_record_count,
                          badcase_count=excluded.badcase_count,
                          projection_json=excluded.projection_json,
                          updated_at=excluded.updated_at
                        """,
                        (
                            daily.run_id,
                            daily.ticker,
                            daily.trading_date,
                            daily.stage,
                            daily.base_library_version,
                            daily.published_library_version,
                            daily.candidate_count,
                            daily.trade_record_count,
                            daily.badcase_count,
                            daily.model_dump_json(),
                            daily.updated_at,
                        ),
                    )
                else:
                    raise ValueError(f"Unsupported Runtime V2 projection kind: {kind}")
            connection.commit()


class RuntimeV2ProjectionOutbox:
    def __init__(self, sqlite_path: str | Path, *, database_url: str) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.sink = PostgresRuntimeV2ProjectionSink(database_url)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists runtime_v2_projection_outbox (
                  kind text not null,
                  projection_id text not null,
                  payload_json text not null,
                  created_at text not null default current_timestamp,
                  primary key(kind, projection_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.sqlite_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def enqueue_case(self, case: RuntimeCase, *, model_turn_count: int) -> None:
        value = RuntimeTerminalProjection.from_case(
            case, model_turn_count=model_turn_count
        )
        self._enqueue("terminal_case", value.case_id, value.model_dump(mode="json"))

    def enqueue_daily(self, run: DailyCloseRun) -> None:
        value = RuntimeDailyProjection.from_run(run)
        self._enqueue("daily_close", value.run_id, value.model_dump(mode="json"))

    def _enqueue(self, kind: str, projection_id: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                insert into runtime_v2_projection_outbox(kind,projection_id,payload_json)
                values (?,?,?)
                on conflict(kind,projection_id) do update set payload_json=excluded.payload_json
                """,
                (
                    kind,
                    projection_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def flush(self, *, limit: int = 50) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "select kind,projection_id,payload_json from runtime_v2_projection_outbox "
                "order by created_at limit ?",
                (limit,),
            ).fetchall()
        completed = 0
        for row in rows:
            self.sink.upsert(str(row["kind"]), json.loads(row["payload_json"]))
            with self._lock, self._connect() as connection:
                connection.execute(
                    "delete from runtime_v2_projection_outbox where kind=? and projection_id=?",
                    (row["kind"], row["projection_id"]),
                )
            completed += 1
        return completed
