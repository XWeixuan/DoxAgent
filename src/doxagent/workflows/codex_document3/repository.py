"""Versioned Policy Set repositories with atomic current-head publication."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from doxagent.postgres import connect_postgres, record_postgres_failure

from .schema import (
    PolicyDetailSnapshot,
    PolicySet,
    PolicySetVersionMetadata,
    RuntimePolicyProjection,
)


class StalePolicySetBaseError(RuntimeError):
    pass


class Document3PolicyRepository(Protocol):
    def get_current(self, ticker: str) -> PolicySet | None: ...

    def get_current_version(self, ticker: str) -> int | None: ...

    def get_version(self, ticker: str, version: int) -> PolicySet | None: ...

    def get_current_projection(self, ticker: str) -> RuntimePolicyProjection | None: ...

    def get_projection(
        self, ticker: str, version: int
    ) -> RuntimePolicyProjection | None: ...

    def get_policy_details(
        self, ticker: str, version: int, policy_ids: list[str]
    ) -> PolicyDetailSnapshot: ...

    def list_version_metadata(
        self, ticker: str, *, limit: int = 20
    ) -> list[PolicySetVersionMetadata]: ...

    def publish(self, policy_set: PolicySet, *, expected_base_version: int | None) -> None: ...


def _assert_expected_base(
    *, ticker: str, current_version: int | None, expected_base_version: int | None
) -> None:
    if current_version != expected_base_version:
        raise StalePolicySetBaseError(
            f"stale D3 head for {ticker}: expected={expected_base_version}, "
            f"current={current_version}"
        )


def _validate_history_limit(limit: int) -> int:
    if not 1 <= limit <= 100:
        raise ValueError("D3 version metadata limit must be between 1 and 100")
    return limit


def _project(policy_set: PolicySet) -> RuntimePolicyProjection:
    # Kept lazy to avoid a repository/runtime-consumer import cycle.
    from .runtime_projection import project_policy_set

    return project_policy_set(policy_set)


def _metadata(policy_set: PolicySet, *, is_current: bool) -> PolicySetVersionMetadata:
    return PolicySetVersionMetadata(
        ticker=policy_set.ticker,
        policy_set_version=policy_set.policy_set_version,
        is_current=is_current,
        publication_state=policy_set.publication_state,
        policy_count=len(policy_set.policies),
        published_at=policy_set.published_at,
    )


def _policy_details(
    policy_set: PolicySet | None,
    *,
    ticker: str,
    version: int,
    policy_ids: list[str],
) -> PolicyDetailSnapshot:
    requested = list(dict.fromkeys(item.strip() for item in policy_ids if item.strip()))
    by_id = {policy.policy_id: policy for policy in policy_set.policies} if policy_set else {}
    return PolicyDetailSnapshot(
        ticker=ticker.upper(),
        policy_set_version=version,
        requested_policy_ids=requested,
        policies=[by_id[item] for item in requested if item in by_id],
        missing_policy_ids=[item for item in requested if item not in by_id],
    )


def _decode_projection(policy_set_payload: Any, projection_payload: Any) -> RuntimePolicyProjection:
    raw_projection = (
        projection_payload
        if isinstance(projection_payload, dict)
        else __import__("json").loads(str(projection_payload))
    )
    if raw_projection.get("schema_version") == "document3.runtime_projection.v2":
        return RuntimePolicyProjection.model_validate(raw_projection)
    policy_set = (
        PolicySet.model_validate(policy_set_payload)
        if isinstance(policy_set_payload, dict)
        else PolicySet.model_validate_json(str(policy_set_payload))
    )
    return _project(policy_set)


class InMemoryDocument3PolicyRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[int, PolicySet]] = {}
        self._current: dict[str, int] = {}
        self._lock = threading.RLock()

    def get_current(self, ticker: str) -> PolicySet | None:
        with self._lock:
            key = ticker.upper()
            version = self._current.get(key)
            return self._items.get(key, {}).get(version) if version is not None else None

    def get_current_version(self, ticker: str) -> int | None:
        with self._lock:
            return self._current.get(ticker.upper())

    def get_version(self, ticker: str, version: int) -> PolicySet | None:
        with self._lock:
            return self._items.get(ticker.upper(), {}).get(version)

    def get_current_projection(self, ticker: str) -> RuntimePolicyProjection | None:
        policy_set = self.get_current(ticker)
        return _project(policy_set) if policy_set else None

    def get_projection(
        self, ticker: str, version: int
    ) -> RuntimePolicyProjection | None:
        policy_set = self.get_version(ticker, version)
        return _project(policy_set) if policy_set else None

    def get_policy_details(
        self, ticker: str, version: int, policy_ids: list[str]
    ) -> PolicyDetailSnapshot:
        return _policy_details(
            self.get_version(ticker, version),
            ticker=ticker,
            version=version,
            policy_ids=policy_ids,
        )

    def list_version_metadata(
        self, ticker: str, *, limit: int = 20
    ) -> list[PolicySetVersionMetadata]:
        bounded_limit = _validate_history_limit(limit)
        with self._lock:
            key = ticker.upper()
            current = self._current.get(key)
            items = sorted(self._items.get(key, {}).items(), reverse=True)[:bounded_limit]
            return [_metadata(item, is_current=version == current) for version, item in items]

    def publish(self, policy_set: PolicySet, *, expected_base_version: int | None) -> None:
        key = policy_set.ticker.upper()
        with self._lock:
            current = self._current.get(key)
            _assert_expected_base(
                ticker=key,
                current_version=current,
                expected_base_version=expected_base_version,
            )
            expected_version = 1 if current is None else current + 1
            if policy_set.policy_set_version != expected_version:
                raise ValueError(
                    f"D3 version must be monotonic: expected={expected_version}, "
                    f"received={policy_set.policy_set_version}"
                )
            self._items.setdefault(key, {})[policy_set.policy_set_version] = policy_set
            self._current[key] = policy_set.policy_set_version


class SQLiteDocument3PolicyRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS codex_document3_policy_sets (
                    ticker TEXT NOT NULL,
                    policy_set_version INTEGER NOT NULL,
                    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                    publication_state TEXT NOT NULL,
                    policy_count INTEGER NOT NULL CHECK (policy_count >= 0),
                    policy_set_json TEXT NOT NULL,
                    runtime_projection_json TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    PRIMARY KEY (ticker, policy_set_version)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS codex_document3_one_current
                    ON codex_document3_policy_sets (ticker) WHERE is_current = 1;
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(codex_document3_policy_sets)"
                ).fetchall()
            }
            if "policy_count" not in columns:
                connection.execute(
                    "ALTER TABLE codex_document3_policy_sets "
                    "ADD COLUMN policy_count INTEGER"
                )
            if "runtime_projection_json" not in columns:
                connection.execute(
                    "ALTER TABLE codex_document3_policy_sets "
                    "ADD COLUMN runtime_projection_json TEXT"
                )
            rows = connection.execute(
                "SELECT ticker, policy_set_version, policy_set_json "
                "FROM codex_document3_policy_sets "
                "WHERE policy_count IS NULL OR runtime_projection_json IS NULL"
            ).fetchall()
            for row in rows:
                policy_set = PolicySet.model_validate_json(row["policy_set_json"])
                connection.execute(
                    "UPDATE codex_document3_policy_sets "
                    "SET policy_count = ?, runtime_projection_json = ? "
                    "WHERE ticker = ? AND policy_set_version = ?",
                    (
                        len(policy_set.policies),
                        _project(policy_set).model_dump_json(),
                        row["ticker"],
                        row["policy_set_version"],
                    ),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _read_one(self, query: str, args: tuple[Any, ...]) -> PolicySet | None:
        with self._connect() as connection:
            row = connection.execute(query, args).fetchone()
        return PolicySet.model_validate_json(row["policy_set_json"]) if row else None

    def get_current(self, ticker: str) -> PolicySet | None:
        return self._read_one(
            "SELECT policy_set_json FROM codex_document3_policy_sets "
            "WHERE ticker = ? AND is_current = 1",
            (ticker.upper(),),
        )

    def get_current_version(self, ticker: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT policy_set_version FROM codex_document3_policy_sets "
                "WHERE ticker = ? AND is_current = 1",
                (ticker.upper(),),
            ).fetchone()
        return int(row[0]) if row else None

    def get_version(self, ticker: str, version: int) -> PolicySet | None:
        return self._read_one(
            "SELECT policy_set_json FROM codex_document3_policy_sets "
            "WHERE ticker = ? AND policy_set_version = ?",
            (ticker.upper(), version),
        )

    def _read_projection(
        self, query: str, args: tuple[Any, ...]
    ) -> RuntimePolicyProjection | None:
        with self._connect() as connection:
            row = connection.execute(query, args).fetchone()
        return _decode_projection(row[0], row[1]) if row else None

    def get_current_projection(self, ticker: str) -> RuntimePolicyProjection | None:
        return self._read_projection(
            "SELECT policy_set_json, runtime_projection_json FROM codex_document3_policy_sets "
            "WHERE ticker = ? AND is_current = 1",
            (ticker.upper(),),
        )

    def get_projection(
        self, ticker: str, version: int
    ) -> RuntimePolicyProjection | None:
        return self._read_projection(
            "SELECT policy_set_json, runtime_projection_json FROM codex_document3_policy_sets "
            "WHERE ticker = ? AND policy_set_version = ?",
            (ticker.upper(), version),
        )

    def get_policy_details(
        self, ticker: str, version: int, policy_ids: list[str]
    ) -> PolicyDetailSnapshot:
        return _policy_details(
            self.get_version(ticker, version),
            ticker=ticker,
            version=version,
            policy_ids=policy_ids,
        )

    def list_version_metadata(
        self, ticker: str, *, limit: int = 20
    ) -> list[PolicySetVersionMetadata]:
        bounded_limit = _validate_history_limit(limit)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT ticker, policy_set_version, is_current, publication_state, "
                "policy_count, published_at FROM codex_document3_policy_sets "
                "WHERE ticker = ? ORDER BY policy_set_version DESC LIMIT ?",
                (ticker.upper(), bounded_limit),
            ).fetchall()
        return [PolicySetVersionMetadata.model_validate(dict(row)) for row in rows]

    def publish(self, policy_set: PolicySet, *, expected_base_version: int | None) -> None:
        key = policy_set.ticker.upper()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT policy_set_version FROM codex_document3_policy_sets "
                "WHERE ticker = ? AND is_current = 1",
                (key,),
            ).fetchone()
            current = int(row[0]) if row else None
            _assert_expected_base(
                ticker=key,
                current_version=current,
                expected_base_version=expected_base_version,
            )
            expected_version = 1 if current is None else current + 1
            if policy_set.policy_set_version != expected_version:
                raise ValueError(
                    f"D3 version must be {expected_version}, got {policy_set.policy_set_version}"
                )
            connection.execute(
                "UPDATE codex_document3_policy_sets SET is_current = 0 "
                "WHERE ticker = ? AND is_current = 1",
                (key,),
            )
            connection.execute(
                "INSERT INTO codex_document3_policy_sets "
                "(ticker, policy_set_version, is_current, publication_state, "
                "policy_count, policy_set_json, runtime_projection_json, published_at) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
                (
                    key,
                    policy_set.policy_set_version,
                    policy_set.publication_state.value,
                    len(policy_set.policies),
                    policy_set.model_dump_json(),
                    _project(policy_set).model_dump_json(),
                    policy_set.published_at.isoformat(),
                ),
            )
            connection.commit()


class PostgresDocument3PolicyRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        import psycopg

        try:
            with connect_postgres(psycopg, self._database_url) as connection:
                yield connection
        except Exception as exc:
            record_postgres_failure(
                exc,
                database_url=self._database_url,
                operation="document3.policy_set",
                table="codex_document3_policy_sets",
            )
            raise

    @staticmethod
    def _decode(row: Any | None) -> PolicySet | None:
        if row is None:
            return None
        payload = row[0]
        return PolicySet.model_validate(payload)

    def get_current(self, ticker: str) -> PolicySet | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_set_json FROM codex_document3_policy_sets "
                "WHERE ticker = %s AND is_current = true",
                (ticker.upper(),),
            )
            return self._decode(cursor.fetchone())

    def get_current_version(self, ticker: str) -> int | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_set_version FROM codex_document3_policy_sets "
                "WHERE ticker = %s AND is_current = true",
                (ticker.upper(),),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else None

    def get_version(self, ticker: str, version: int) -> PolicySet | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_set_json FROM codex_document3_policy_sets "
                "WHERE ticker = %s AND policy_set_version = %s",
                (ticker.upper(), version),
            )
            return self._decode(cursor.fetchone())

    @staticmethod
    def _decode_projection(row: Any | None) -> RuntimePolicyProjection | None:
        if row is None:
            return None
        return _decode_projection(row[0], row[1])

    def get_current_projection(self, ticker: str) -> RuntimePolicyProjection | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_set_json, runtime_projection_json "
                "FROM codex_document3_policy_sets "
                "WHERE ticker = %s AND is_current = true",
                (ticker.upper(),),
            )
            return self._decode_projection(cursor.fetchone())

    def get_projection(
        self, ticker: str, version: int
    ) -> RuntimePolicyProjection | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT policy_set_json, runtime_projection_json "
                "FROM codex_document3_policy_sets "
                "WHERE ticker = %s AND policy_set_version = %s",
                (ticker.upper(), version),
            )
            return self._decode_projection(cursor.fetchone())

    def get_policy_details(
        self, ticker: str, version: int, policy_ids: list[str]
    ) -> PolicyDetailSnapshot:
        return _policy_details(
            self.get_version(ticker, version),
            ticker=ticker,
            version=version,
            policy_ids=policy_ids,
        )

    def list_version_metadata(
        self, ticker: str, *, limit: int = 20
    ) -> list[PolicySetVersionMetadata]:
        bounded_limit = _validate_history_limit(limit)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT ticker, policy_set_version, is_current, publication_state, "
                "policy_count, published_at FROM codex_document3_policy_sets "
                "WHERE ticker = %s ORDER BY policy_set_version DESC LIMIT %s",
                (ticker.upper(), bounded_limit),
            )
            columns = [description.name for description in cursor.description]
            return [
                PolicySetVersionMetadata.model_validate(dict(zip(columns, row, strict=True)))
                for row in cursor.fetchall()
            ]

    def publish(self, policy_set: PolicySet, *, expected_base_version: int | None) -> None:
        key = policy_set.ticker.upper()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"d3:{key}",))
            cursor.execute(
                "SELECT policy_set_version FROM codex_document3_policy_sets "
                "WHERE ticker = %s AND is_current = true FOR UPDATE",
                (key,),
            )
            row = cursor.fetchone()
            current = int(row[0]) if row else None
            _assert_expected_base(
                ticker=key,
                current_version=current,
                expected_base_version=expected_base_version,
            )
            expected_version = 1 if current is None else current + 1
            if policy_set.policy_set_version != expected_version:
                raise ValueError(
                    f"D3 version must be {expected_version}, got {policy_set.policy_set_version}"
                )
            cursor.execute(
                "UPDATE codex_document3_policy_sets SET is_current = false "
                "WHERE ticker = %s AND is_current = true",
                (key,),
            )
            cursor.execute(
                "INSERT INTO codex_document3_policy_sets "
                "(ticker, policy_set_version, is_current, publication_state, "
                "document2_run_id, event_library_version, policy_count, policy_set_json, "
                "runtime_projection_json, published_at) "
                "VALUES (%s, %s, true, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)",
                (
                    key,
                    policy_set.policy_set_version,
                    policy_set.publication_state.value,
                    policy_set.document2_ref.run_id,
                    (
                        policy_set.event_library_ref.version
                        if policy_set.event_library_ref
                        else None
                    ),
                    len(policy_set.policies),
                    policy_set.model_dump_json(),
                    _project(policy_set).model_dump_json(),
                    policy_set.published_at,
                ),
            )
            connection.commit()


class HybridDocument3PolicyRepository:
    """Remote authority with a local read fallback and best-effort mirror."""

    def __init__(
        self,
        primary: Document3PolicyRepository,
        local: Document3PolicyRepository,
    ) -> None:
        self.primary = primary
        self.local = local

    def get_current(self, ticker: str) -> PolicySet | None:
        try:
            primary_version = self.primary.get_current_version(ticker)
            local_current = self.local.get_current(ticker)
            if primary_version is None:
                return local_current
            if (
                local_current is not None
                and local_current.policy_set_version == primary_version
            ):
                return local_current
            return self.primary.get_version(ticker, primary_version)
        except Exception:
            return self.local.get_current(ticker)

    def get_current_version(self, ticker: str) -> int | None:
        try:
            return self.primary.get_current_version(ticker)
        except Exception:
            return self.local.get_current_version(ticker)

    def get_version(self, ticker: str, version: int) -> PolicySet | None:
        local_value = self.local.get_version(ticker, version)
        if local_value is not None:
            return local_value
        try:
            return self.primary.get_version(ticker, version)
        except Exception:
            return None

    def get_current_projection(self, ticker: str) -> RuntimePolicyProjection | None:
        try:
            primary_version = self.primary.get_current_version(ticker)
            if primary_version is None:
                return self.local.get_current_projection(ticker)
            local_value = self.local.get_projection(ticker, primary_version)
            return local_value or self.primary.get_projection(ticker, primary_version)
        except Exception:
            return self.local.get_current_projection(ticker)

    def get_projection(
        self, ticker: str, version: int
    ) -> RuntimePolicyProjection | None:
        local_value = self.local.get_projection(ticker, version)
        if local_value is not None:
            return local_value
        try:
            return self.primary.get_projection(ticker, version)
        except Exception:
            return None

    def get_policy_details(
        self, ticker: str, version: int, policy_ids: list[str]
    ) -> PolicyDetailSnapshot:
        policy_set = self.get_version(ticker, version)
        return _policy_details(
            policy_set,
            ticker=ticker,
            version=version,
            policy_ids=policy_ids,
        )

    def list_version_metadata(
        self, ticker: str, *, limit: int = 20
    ) -> list[PolicySetVersionMetadata]:
        _validate_history_limit(limit)
        try:
            return self.primary.list_version_metadata(ticker, limit=limit)
        except Exception:
            return self.local.list_version_metadata(ticker, limit=limit)

    def publish(self, policy_set: PolicySet, *, expected_base_version: int | None) -> None:
        self.primary.publish(policy_set, expected_base_version=expected_base_version)
        try:
            local_base = self.local.get_current_version(policy_set.ticker)
            if local_base == expected_base_version:
                self.local.publish(policy_set, expected_base_version=local_base)
        except Exception:
            # The remote head is authoritative; a failed local mirror must not make a
            # committed CAS look failed and invite a duplicate publish retry.
            return
