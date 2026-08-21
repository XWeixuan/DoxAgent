"""SQLite-backed attempt Observation Store with atomic shared O# allocation."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from doxagent.observations.models import ObservationCallRecord, PersistedObservation
from doxagent.observations.projection import projection_matches, render_observation_projection

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ALIAS = re.compile(r"^O[1-9]\d*$")


class AttemptObservationStore:
    """Private store keyed by canonical node attempt id.

    ``attempt_id`` is the legacy persistence field name and is required to be
    byte-for-byte identical to the Data MCP ``node_attempt_id``.
    """

    def __init__(
        self,
        *,
        control_root: str | Path,
        mirror_root: str | Path,
        run_id: str,
        attempt_id: str,
    ) -> None:
        self._validate_identifier(run_id, "run_id")
        self._validate_identifier(attempt_id, "attempt_id")
        self.run_id = run_id
        self.node_attempt_id = attempt_id
        self.attempt_id = attempt_id
        self.control_root = Path(control_root).resolve()
        self.mirror_root = Path(mirror_root).resolve()
        self.control_root.mkdir(parents=True, exist_ok=True)
        self.mirror_root.mkdir(parents=True, exist_ok=True)
        self._database_path = self.control_root / "observations.sqlite3"
        self._initialize()

    def save_raw_result(self, tool_call_id: str, payload: dict[str, Any]) -> None:
        self._validate_identifier(tool_call_id, "tool_call_id")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO raw_results(tool_call_id,payload_json) VALUES(?,?) "
                "ON CONFLICT(tool_call_id) DO UPDATE SET payload_json=excluded.payload_json",
                (tool_call_id, encoded),
            )

    def save_observation(self, observation: PersistedObservation) -> PersistedObservation:
        if observation.run_id != self.run_id or observation.attempt_id != self.attempt_id:
            raise ValueError("observation scope does not match attempt store")
        self._validate_identifier(observation.tool_call_id, "tool_call_id")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT alias_number,record_json FROM observations WHERE block_id=?",
                (observation.block_id,),
            ).fetchone()
            if existing is None:
                cursor = connection.execute(
                    "INSERT INTO alias_sequence(block_id) VALUES(?)",
                    (observation.block_id,),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not allocate an observation alias")
                alias_number = int(cursor.lastrowid)
                stored = observation.model_copy(update={"alias": f"O{alias_number}"})
                connection.execute(
                    "INSERT INTO observations("
                    "block_id,alias_number,tool_call_id,content_hash,record_json"
                    ") VALUES(?,?,?,?,?)",
                    (
                        stored.block_id,
                        alias_number,
                        stored.tool_call_id,
                        stored.content_hash,
                        stored.model_dump_json(),
                    ),
                )
            else:
                stored = PersistedObservation.model_validate_json(existing[1])
                if stored.content_hash != observation.content_hash:
                    raise ValueError("stable block id was reused with different content")
            connection.commit()
        self._write_mirror(stored)
        return stored

    def save_call(self, call: ObservationCallRecord) -> None:
        if call.run_id != self.run_id or call.attempt_id != self.attempt_id:
            raise ValueError("call scope does not match attempt store")
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO calls(tool_call_id,record_json) VALUES(?,?) "
                "ON CONFLICT(tool_call_id) DO UPDATE SET record_json=excluded.record_json",
                (call.tool_call_id, call.model_dump_json()),
            )

    def read_alias(self, alias: str) -> PersistedObservation | None:
        if not _ALIAS.fullmatch(alias):
            return None
        number = int(alias[1:])
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_json FROM observations WHERE alias_number=?", (number,)
            ).fetchone()
        return PersistedObservation.model_validate_json(row[0]) if row else None

    def list_for_call(self, tool_call_id: str) -> list[PersistedObservation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record_json FROM observations WHERE tool_call_id=? ORDER BY alias_number",
                (tool_call_id,),
            ).fetchall()
        return [PersistedObservation.model_validate_json(row[0]) for row in rows]

    def list_all(self) -> list[PersistedObservation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record_json FROM observations ORDER BY alias_number"
            ).fetchall()
        return [PersistedObservation.model_validate_json(row[0]) for row in rows]

    def cleanup_mirror(self) -> None:
        """Remove stale mirror files not present in the private index."""

        expected = {f"{item.alias}.json" for item in self.list_all()}
        for path in self.mirror_root.glob("O*.json"):
            if path.name not in expected:
                path.unlink(missing_ok=True)

    def _write_mirror(self, observation: PersistedObservation) -> None:
        target = self.mirror_root / f"{observation.alias}.json"
        raw = render_observation_projection(observation).encode("utf-8")
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if not projection_matches(observation, existing):
                raise ValueError(f"immutable observation mirror changed: {observation.alias}")
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{observation.alias}.",
            dir=self.mirror_root,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            try:
                target.chmod(0o444)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS alias_sequence (
                    alias_number INTEGER PRIMARY KEY AUTOINCREMENT,
                    block_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS observations (
                    block_id TEXT PRIMARY KEY,
                    alias_number INTEGER NOT NULL UNIQUE,
                    tool_call_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(alias_number) REFERENCES alias_sequence(alias_number)
                );
                CREATE INDEX IF NOT EXISTS idx_observations_tool_call
                    ON observations(tool_call_id, alias_number);
                CREATE TABLE IF NOT EXISTS raw_results (
                    tool_call_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls (
                    tool_call_id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {name}")
