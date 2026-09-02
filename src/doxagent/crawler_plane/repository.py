"""SQLite metadata store for the Crawler Plane."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Literal, TypeVar

from pydantic import BaseModel

from doxagent.crawler_plane.schema import (
    CertificationResult,
    CrawlerAlert,
    CrawlerAlertPolicy,
    CrawlerAlertStatus,
    CrawlerCheckpoint,
    CrawlerExecutionResult,
    CrawlerPackage,
    CrawlerVersion,
    ExecutionArtifact,
    NetworkCassette,
    RegressionCase,
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


class CrawlerPlaneRepository:
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
        connection.execute("pragma journal_mode=WAL")
        connection.execute("pragma synchronous=NORMAL")
        connection.execute("pragma foreign_keys=ON")
        connection.execute("pragma busy_timeout=30000")
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if candidate.exists():
                os.chmod(candidate, 0o666)
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
        """Connections are operation-scoped."""

    def _initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                create table if not exists crawler_packages (
                    crawler_id text primary key,
                    active_version integer,
                    latest_version integer not null,
                    data_json text not null
                );
                create table if not exists crawler_versions (
                    version_id text primary key,
                    crawler_id text not null,
                    version integer not null,
                    status text not null,
                    content_digest text,
                    data_json text not null,
                    unique(crawler_id, version)
                );
                create index if not exists idx_crawler_versions_status
                    on crawler_versions(crawler_id, status, version desc);
                create table if not exists crawler_checkpoints (
                    crawler_id text not null,
                    binding_id text not null,
                    schema_version integer not null,
                    data_json text not null,
                    primary key(crawler_id, binding_id)
                );
                create table if not exists crawler_executions (
                    execution_id text primary key,
                    poll_run_id text not null,
                    crawler_id text not null,
                    crawler_version integer not null,
                    source_id text not null,
                    binding_id text not null,
                    status text not null,
                    started_at text not null,
                    data_json text not null
                );
                create index if not exists idx_crawler_exec_lookup
                    on crawler_executions(crawler_id, binding_id, started_at desc);
                create index if not exists idx_crawler_exec_poll
                    on crawler_executions(poll_run_id);
                create table if not exists crawler_execution_artifacts (
                    artifact_id text primary key,
                    execution_id text not null,
                    kind text not null,
                    path text not null,
                    data_json text not null
                );
                create index if not exists idx_crawler_artifact_execution
                    on crawler_execution_artifacts(execution_id);
                create table if not exists network_cassettes (
                    cassette_id text primary key,
                    crawler_id text not null,
                    crawler_version integer not null,
                    execution_id text,
                    path text not null,
                    data_json text not null
                );
                create table if not exists certification_runs (
                    certification_run_id text primary key,
                    crawler_id text not null,
                    crawler_version integer not null,
                    overall text not null,
                    content_digest text not null,
                    data_json text not null
                );
                create index if not exists idx_crawler_cert_version
                    on certification_runs(crawler_id, crawler_version);
                create table if not exists crawler_regression_cases (
                    regression_id text primary key,
                    crawler_id text not null,
                    source_execution_id text not null,
                    cassette_ref text not null,
                    data_json text not null
                );
                create table if not exists crawler_alert_policies (
                    policy_key text primary key,
                    crawler_id text not null,
                    source_id text,
                    alert_type text not null,
                    data_json text not null
                );
                create table if not exists crawler_alerts (
                    alert_id text primary key,
                    alert_key text not null unique,
                    crawler_id text not null,
                    alert_type text not null,
                    status text not null,
                    last_seen_at text not null,
                    data_json text not null
                );
                create index if not exists idx_crawler_alert_state
                    on crawler_alerts(crawler_id, status, last_seen_at desc);
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

    def save_package(self, value: CrawlerPackage) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into crawler_packages(crawler_id,active_version,latest_version,data_json)
                   values(?,?,?,?) on conflict(crawler_id) do update set
                   active_version=excluded.active_version,
                   latest_version=excluded.latest_version,data_json=excluded.data_json""",
                (value.crawler_id, value.active_version, value.latest_version, self._json(value)),
            )

    def get_package(self, crawler_id: str) -> CrawlerPackage | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from crawler_packages where crawler_id=?",
                (crawler_id.strip().lower(),),
            ).fetchone()
        return self._model(CrawlerPackage, row)

    def list_packages(self) -> list[CrawlerPackage]:
        with self._connect() as connection:
            rows = connection.execute(
                "select data_json from crawler_packages order by crawler_id"
            ).fetchall()
        return self._models(CrawlerPackage, rows)

    def save_version(self, value: CrawlerVersion) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into crawler_versions(
                     version_id,crawler_id,version,status,content_digest,data_json)
                   values(?,?,?,?,?,?) on conflict(version_id) do update set
                   status=excluded.status,content_digest=excluded.content_digest,
                   data_json=excluded.data_json""",
                (
                    value.version_id,
                    value.spec.crawler_id,
                    value.spec.version,
                    value.status.value,
                    value.content_digest,
                    self._json(value),
                ),
            )

    def get_version(self, crawler_id: str, version: int) -> CrawlerVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from crawler_versions where crawler_id=? and version=?",
                (crawler_id.strip().lower(), version),
            ).fetchone()
        return self._model(CrawlerVersion, row)

    def list_versions(self, crawler_id: str) -> list[CrawlerVersion]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from crawler_versions where crawler_id=?
                   order by version desc""",
                (crawler_id.strip().lower(),),
            ).fetchall()
        return self._models(CrawlerVersion, rows)

    def save_checkpoint(self, value: CrawlerCheckpoint) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into crawler_checkpoints(
                     crawler_id,binding_id,schema_version,data_json) values(?,?,?,?)
                   on conflict(crawler_id,binding_id) do update set
                   schema_version=excluded.schema_version,data_json=excluded.data_json""",
                (
                    value.crawler_id,
                    value.binding_id,
                    value.schema_version,
                    self._json(value),
                ),
            )

    def get_checkpoint(self, crawler_id: str, binding_id: str) -> CrawlerCheckpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                """select data_json from crawler_checkpoints
                   where crawler_id=? and binding_id=?""",
                (crawler_id.strip().lower(), binding_id),
            ).fetchone()
        return self._model(CrawlerCheckpoint, row)

    def reset_checkpoints(self, crawler_id: str) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "delete from crawler_checkpoints where crawler_id=?",
                (crawler_id.strip().lower(),),
            )
            return int(cursor.rowcount)

    def save_execution(self, value: CrawlerExecutionResult) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into crawler_executions(
                     execution_id,poll_run_id,crawler_id,crawler_version,source_id,
                     binding_id,status,started_at,data_json) values(?,?,?,?,?,?,?,?,?)
                   on conflict(execution_id) do update set
                   status=excluded.status,data_json=excluded.data_json""",
                (
                    value.execution_id,
                    value.poll_run_id,
                    value.crawler_id,
                    value.crawler_version,
                    value.source_id,
                    value.binding_id,
                    value.status.value,
                    value.started_at.isoformat(),
                    self._json(value),
                ),
            )

    def get_execution(self, execution_id: str) -> CrawlerExecutionResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from crawler_executions where execution_id=?",
                (execution_id,),
            ).fetchone()
        return self._model(CrawlerExecutionResult, row)

    def list_executions(
        self,
        *,
        crawler_id: str | None = None,
        binding_id: str | None = None,
        limit: int = 100,
    ) -> list[CrawlerExecutionResult]:
        clauses: list[str] = []
        params: list[object] = []
        if crawler_id:
            clauses.append("crawler_id=?")
            params.append(crawler_id.strip().lower())
        if binding_id:
            clauses.append("binding_id=?")
            params.append(binding_id)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""select data_json from crawler_executions {where}
                    order by started_at desc limit ?""",
                params,
            ).fetchall()
        return self._models(CrawlerExecutionResult, rows)

    def save_artifact(self, value: ExecutionArtifact) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert or replace into crawler_execution_artifacts(
                     artifact_id,execution_id,kind,path,data_json) values(?,?,?,?,?)""",
                (value.artifact_id, value.execution_id, value.kind, value.path, self._json(value)),
            )

    def list_artifacts(self, execution_id: str) -> list[ExecutionArtifact]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from crawler_execution_artifacts
                   where execution_id=? order by artifact_id""",
                (execution_id,),
            ).fetchall()
        return self._models(ExecutionArtifact, rows)

    def save_cassette(self, value: NetworkCassette) -> None:
        if value.path is None:
            raise ValueError("cassette path is required before persistence")
        with self.transaction() as connection:
            connection.execute(
                """insert or replace into network_cassettes(
                     cassette_id,crawler_id,crawler_version,execution_id,path,data_json)
                   values(?,?,?,?,?,?)""",
                (
                    value.cassette_id,
                    value.crawler_id,
                    value.crawler_version,
                    value.execution_id,
                    value.path,
                    self._json(value),
                ),
            )

    def get_cassette(self, cassette_id: str) -> NetworkCassette | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from network_cassettes where cassette_id=?", (cassette_id,)
            ).fetchone()
        return self._model(NetworkCassette, row)

    def save_certification(self, value: CertificationResult) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into certification_runs(
                     certification_run_id,crawler_id,crawler_version,overall,
                     content_digest,data_json) values(?,?,?,?,?,?)""",
                (
                    value.certification_run_id,
                    value.crawler_id,
                    value.crawler_version,
                    value.overall.value,
                    value.content_digest,
                    self._json(value),
                ),
            )

    def get_certification(self, run_id: str) -> CertificationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from certification_runs where certification_run_id=?",
                (run_id,),
            ).fetchone()
        return self._model(CertificationResult, row)

    def save_regression(self, value: RegressionCase) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert or replace into crawler_regression_cases(
                     regression_id,crawler_id,source_execution_id,cassette_ref,data_json)
                   values(?,?,?,?,?)""",
                (
                    value.regression_id,
                    value.crawler_id,
                    value.source_execution_id,
                    value.cassette_ref,
                    self._json(value),
                ),
            )

    def list_regressions(self, crawler_id: str) -> list[RegressionCase]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from crawler_regression_cases where crawler_id=?
                   order by regression_id""",
                (crawler_id.strip().lower(),),
            ).fetchall()
        return self._models(RegressionCase, rows)

    def save_alert_policy(self, value: CrawlerAlertPolicy) -> None:
        with self.transaction() as connection:
            connection.execute(
                """insert into crawler_alert_policies(
                     policy_key,crawler_id,source_id,alert_type,data_json) values(?,?,?,?,?)
                   on conflict(policy_key) do update set data_json=excluded.data_json""",
                (
                    value.policy_key,
                    value.crawler_id,
                    value.source_id,
                    value.alert_type.value,
                    self._json(value),
                ),
            )

    def get_alert_policy(self, policy_key: str) -> CrawlerAlertPolicy | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from crawler_alert_policies where policy_key=?", (policy_key,)
            ).fetchone()
        return self._model(CrawlerAlertPolicy, row)

    def list_alert_policies(self, crawler_id: str) -> list[CrawlerAlertPolicy]:
        with self._connect() as connection:
            rows = connection.execute(
                """select data_json from crawler_alert_policies where crawler_id=?
                   order by alert_type,source_id""",
                (crawler_id.strip().lower(),),
            ).fetchall()
        return self._models(CrawlerAlertPolicy, rows)

    def upsert_alert(self, value: CrawlerAlert) -> CrawlerAlert:
        with self.transaction() as connection:
            row = connection.execute(
                "select data_json from crawler_alerts where alert_key=?", (value.alert_key,)
            ).fetchone()
            if row is not None:
                current = CrawlerAlert.model_validate_json(row["data_json"])
                value = current.model_copy(
                    update={
                        "status": CrawlerAlertStatus.OPEN,
                        "message": value.message,
                        "metadata": value.metadata,
                        "execution_id": value.execution_id,
                        "binding_id": value.binding_id,
                        "last_seen_at": value.last_seen_at,
                        "repeat_count": current.repeat_count + 1,
                        "resolved_at": None,
                    }
                )
            connection.execute(
                """insert into crawler_alerts(
                     alert_id,alert_key,crawler_id,alert_type,status,last_seen_at,data_json)
                   values(?,?,?,?,?,?,?) on conflict(alert_key) do update set
                   status=excluded.status,last_seen_at=excluded.last_seen_at,
                   data_json=excluded.data_json""",
                (
                    value.alert_id,
                    value.alert_key,
                    value.crawler_id,
                    value.alert_type.value,
                    value.status.value,
                    value.last_seen_at.isoformat(),
                    self._json(value),
                ),
            )
        return value

    def get_alert(self, alert_id: str) -> CrawlerAlert | None:
        with self._connect() as connection:
            row = connection.execute(
                "select data_json from crawler_alerts where alert_id=?", (alert_id,)
            ).fetchone()
        return self._model(CrawlerAlert, row)

    def list_alerts(
        self, *, crawler_id: str | None = None, open_only: bool = False
    ) -> list[CrawlerAlert]:
        clauses: list[str] = []
        params: list[object] = []
        if crawler_id:
            clauses.append("crawler_id=?")
            params.append(crawler_id.strip().lower())
        if open_only:
            clauses.append("status=?")
            params.append(CrawlerAlertStatus.OPEN.value)
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"select data_json from crawler_alerts {where} order by last_seen_at desc",
                params,
            ).fetchall()
        return self._models(CrawlerAlert, rows)

    def resolve_alert(self, alert_id: str) -> CrawlerAlert | None:
        current = self.get_alert(alert_id)
        if current is None:
            return None
        now = utc_now()
        resolved = current.model_copy(
            update={
                "status": CrawlerAlertStatus.RESOLVED,
                "resolved_at": now,
                "last_seen_at": now,
            }
        )
        with self.transaction() as connection:
            connection.execute(
                """update crawler_alerts set status=?,last_seen_at=?,data_json=?
                   where alert_id=?""",
                (resolved.status.value, now.isoformat(), self._json(resolved), alert_id),
            )
        return resolved

    def snapshot_counts(self) -> dict[str, int]:
        tables = (
            "crawler_packages",
            "crawler_versions",
            "crawler_checkpoints",
            "crawler_executions",
            "crawler_execution_artifacts",
            "network_cassettes",
            "certification_runs",
            "crawler_regression_cases",
            "crawler_alerts",
        )
        with self._connect() as connection:
            return {
                table: int(connection.execute(f"select count(*) from {table}").fetchone()[0])
                for table in tables
            }


__all__ = ["CrawlerPlaneRepository"]
