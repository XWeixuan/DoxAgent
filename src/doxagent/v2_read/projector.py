"""Bounded fact ingestion; one bad source receipt cannot stall healthy sources."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .outbox import SourceOutbox
from .repository import ReadStore, instant

Mapping = Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[dict[str, Any]]]]


class ProjectionWorker:
    def __init__(self, store: ReadStore, sources: list[SourceOutbox], mapper: Mapping) -> None:
        self.store, self.sources, self.mapper = store, sources, mapper
        self._coverage_keys = {}

    def tick(self, *, limit: int = 100) -> int:
        if not 1 <= limit <= 500:
            raise ValueError("invalid projection batch size")
        count = 0
        for source in self.sources:
            started = time.monotonic()
            position = self.store.position(source.source)
            try:
                if source.source == "initialization":
                    from doxagent.ticker_initialization.usage_capture import flush

                    flush(source.path, limit=limit)
                capture_identity = source.capture()
                if capture_identity and capture_identity.get("source_epoch"):
                    with self.store.connect(write=True) as db:
                        prior_epoch = db.execute("SELECT epoch FROM source_epochs WHERE source=?", (source.source,)).fetchone()
                        if prior_epoch and prior_epoch[0] != capture_identity["source_epoch"]:
                            raise sqlite3.OperationalError("SOURCE_EPOCH_MISMATCH")
                        db.execute("INSERT OR IGNORE INTO source_epochs VALUES(?,?)", (source.source, capture_identity["source_epoch"]))
                head = source.head()
                events = source.coordinates(position, limit) if hasattr(source,"coordinates") else source.read(position, limit)
            except (sqlite3.OperationalError, OSError, ValueError) as exc:
                reason = "SOURCE_EPOCH_MISMATCH" if str(exc) == "SOURCE_EPOCH_MISMATCH" else "SOURCE_UNAVAILABLE"
                with self.store.connect(write=True) as db:
                    db.execute(
                        "INSERT OR REPLACE INTO source_health VALUES(?,?,?,?,?)",
                        (
                            source.source,
                            0,
                            position,
                            instant(datetime.now(UTC)),
                            reason,
                        ),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO gaps(source,event,reason) VALUES(?,?,?)",
                        (source.source, "source-unavailable", reason),
                    )
                capture = self.store.get("capture_coverage", "", source.source)
                if capture:
                    self._coverage(
                        source.source,
                        {**capture, "complete": False},
                        f"unavailable:{position}:{instant(datetime.now(UTC))[:16]}",
                    )
                continue
            with self.store.connect(write=True) as db:
                db.execute(
                    "DELETE FROM gaps WHERE source=? AND event='source-unavailable'",
                    (source.source,),
                )
            for candidate in events:
                coordinate = candidate if isinstance(candidate,int) else candidate["seq"]
                event = {"seq":coordinate,"recorded_at":None}
                try:
                    loaded = source.event(candidate) if isinstance(candidate,int) else candidate
                    if loaded is None:
                        raise ValueError("SOURCE_RECEIPT_MISSING")
                    event = {**loaded,"source":source.source}
                    records, contributions = self.mapper(event)
                    self.store.ingest(
                        source.source,
                        str(event["seq"]),
                        records,
                        contributions=contributions,
                        position=event["seq"],
                        at=event["recorded_at"],
                    )
                except (ValueError, KeyError, TypeError, OSError, sqlite3.OperationalError) as exc:
                    self.store.ingest(
                        source.source,
                        str(event["seq"]),
                        [],
                        position=event["seq"],
                        gap=str(exc)
                        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", str(exc))
                        else type(exc).__name__,
                        at=event["recorded_at"],
                    )
                count += 1
                if time.monotonic() - started >= 1:
                    break
            checkpoint = self.store.position(source.source)
            with self.store.connect(write=True) as db:
                db.execute(
                    "INSERT OR REPLACE INTO source_health VALUES(?,?,?,?,?)",
                    (source.source, head, checkpoint, instant(datetime.now(UTC)), None),
                )
                gap = db.execute(
                    "SELECT 1 FROM gaps WHERE source=? LIMIT 1", (source.source,)
                ).fetchone()
            from hashlib import sha256
            consumer = "read:" + sha256(str(self.store.path).encode()).hexdigest()[:24]
            with self.store.connect() as db:
                repair = db.execute("SELECT min(CASE WHEN event NOT GLOB '*[^0-9]*' THEN CAST(event AS INTEGER) ELSE 0 END) FROM gaps WHERE source=?", (source.source,)).fetchone()[0]
            if hasattr(source, "acknowledge"):
                source.acknowledge(consumer, checkpoint, repair)
            capture = source.capture()
            if capture:
                observed = instant(datetime.now(UTC))
                self._coverage(
                    source.source,
                    {**capture, "end_at": observed, "complete": not gap and checkpoint >= head},
                    f"{head}:{checkpoint}:{observed[:16]}:{bool(gap)}",
                )
        self.repair(limit=min(20, limit))
        return count

    def _coverage(self, source, capture, key):
        key = (key, capture.get("started_at"), capture.get("tables_json"))
        if self._coverage_keys.get(source) == key:
            return
        records = [{"kind": "capture_coverage", "ticker": "", "id": source, "data": capture}]
        reconcile = getattr(self.mapper, "coverage", None)
        if reconcile:
            records.extend(reconcile(source, capture))
        # Recovery may revisit the same head/checkpoint within one minute. It is
        # a new observation, even if an earlier complete observation had that key.
        self.store.ingest("capture_coverage", f"policy-proof-v1:{source}:{uuid4().hex}", records)
        self._coverage_keys[source] = key

    def repair(self, *, limit: int = 20) -> int:
        now = datetime.now(UTC)
        with self.store.connect(write=True) as db:
            gaps = db.execute(
                "SELECT source,event,attempts FROM gaps WHERE next_attempt_at<=? "
                "ORDER BY attempts,source,CAST(event AS INTEGER) LIMIT ?",
                (instant(now), limit),
            ).fetchall()
            for gap in gaps:
                db.execute(
                    "UPDATE gaps SET attempts=attempts+1,next_attempt_at=? "
                    "WHERE source=? AND event=?",
                    (
                        instant(now + timedelta(seconds=min(300, 2 ** min(gap[2] + 1, 8)))),
                        gap[0],
                        gap[1],
                    ),
                )
        repaired = 0
        sources = {source.source: source for source in self.sources}
        for gap in gaps:
            source = sources.get(gap[0])
            if source is None or not gap[1].isdigit():
                continue
            try:
                event = source.event(int(gap[1]))
            except sqlite3.OperationalError:
                continue
            if event is None:
                continue
            event["source"] = source.source
            try:
                records, contributions = self.mapper(event)
                self.store.ingest(
                    source.source,
                    gap[1] + ":repair:1",
                    records,
                    contributions=contributions,
                    position=event["seq"],
                    at=event["recorded_at"],
                    resolved_gap=gap[1],
                )
                repaired += 1
            except (ValueError, KeyError, TypeError, sqlite3.OperationalError):
                continue
        return repaired


def native(event: dict[str, Any]) -> dict[str, Any]:
    row = event["row"]
    for name in ("payload_json", "data_json", "payload"):
        if name in row and isinstance(row[name], str):
            value = json.loads(row[name])
            if isinstance(value, dict):
                return value
    return row
