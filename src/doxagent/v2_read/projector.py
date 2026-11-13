"""Bounded fact ingestion; one bad source receipt cannot stall healthy sources."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import ExitStack, nullcontext
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .recovery import retryable_storage
from .outbox import SourceOutbox
from .repository import ReadStore, instant

Mapping = Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[dict[str, Any]]]]


class ProjectionWorker:
    def __init__(self, store: ReadStore, sources: list[SourceOutbox], mapper: Mapping, *, consumer_id: str | None = None) -> None:
        self.store, self.sources, self.mapper = store, sources, mapper
        self.consumer_id=consumer_id
        self._coverage_keys = {}
        self._acknowledgements = {}
        self._next_repair = 0.0
        self.timings = {}
        self.store.projection_timings = self.timings
        self._slow_window = (0.0, 0)

    def tick(self, *, limit: int = 100) -> int:
        session = self.store.projection_session() if hasattr(self.store, "projection_session") else nullcontext()
        started, cpu = time.monotonic(), time.process_time()
        with session, ExitStack() as readers:
            for source in self.sources:
                if isinstance(source, SourceOutbox):
                    readers.enter_context(source.projection_session())
            count = self._tick(limit=limit)
        self.timings['tick_seconds'] = self.timings.get('tick_seconds', 0) + time.monotonic() - started
        self.timings['cpu_seconds'] = self.timings.get('cpu_seconds', 0) + time.process_time() - cpu
        return count

    def _tick(self, *, limit: int) -> int:
        if not 1 <= limit <= 500:
            raise ValueError("invalid projection batch size")
        count = 0
        for source in self.sources:
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
                if retryable_storage(exc) or (isinstance(exc, sqlite3.OperationalError) and (getattr(exc, "sqlite_errorcode", 0) & 255) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_IOERR)):
                    raise
                reason = str(exc) if str(exc) in {'SOURCE_EPOCH_MISMATCH','SOURCE_SNAPSHOT_REQUIRED'} else 'SOURCE_UNAVAILABLE'
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
            reader = (source.event_reader(serving=True, projection=True) if isinstance(source, SourceOutbox)
                      else source.event_reader() if hasattr(source, "event_reader")
                      else nullcontext(getattr(source, "event", None)))
            with reader as load_event:
                # Capture/health SQL can already consume the entire old one-
                # second allowance. Budget actual event work, not setup time.
                # A full batch gets a bounded catch-up burst; other sources
                # still receive a turn and each event retains its own commit.
                count += self._events(source, events, load_event, budget=5 if len(events) == limit else 1)
            checkpoint = self.store.position(source.source)
            clock = {}
            if hasattr(source, 'projection_clock'):
                try:
                    clock = source.projection_clock(checkpoint, head)
                except (sqlite3.OperationalError, OSError):
                    pass  # Health remains unknown; telemetry cannot skip a receipt.
            with self.store.connect(write=True) as db:
                db.execute(
                    "INSERT OR REPLACE INTO source_health VALUES(?,?,?,?,?)",
                    (source.source, head, checkpoint, instant(datetime.now(UTC)), None),
                )
                db.execute("INSERT INTO read_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           ('projection_clock:'+source.source,json.dumps({'checkpoint':checkpoint,'head':head,**clock})))
                gap = db.execute(
                    "SELECT 1 FROM gaps WHERE source=? LIMIT 1", (source.source,)
                ).fetchone()
            from hashlib import sha256
            consumer = self.consumer_id or "read:" + sha256(str(self.store.path).encode()).hexdigest()[:24]
            with self.store.connect() as db:
                repair = db.execute("SELECT min(CASE WHEN event NOT GLOB '*[^0-9]*' THEN CAST(event AS INTEGER) ELSE 0 END) FROM gaps WHERE source=?", (source.source,)).fetchone()[0]
                repairs = [int(r[0]) for r in db.execute("SELECT event FROM gaps WHERE source=? AND event<>'' AND event NOT GLOB '*[^0-9]*'",(source.source,))]
            if hasattr(source, "acknowledge"):
                try:
                    if hasattr(source,'acknowledge_exceptions'):
                        from .source_lifecycle import sync_business_facts
                        facts_complete = sync_business_facts(source,self.store,budget=.25)
                    else:
                        facts_complete = True
                    signature = (checkpoint, tuple(sorted(repairs)), repair)
                    acknowledged = self._acknowledgements.get(source.source)
                    if facts_complete is not False and (acknowledged is None or signature != acknowledged[0] or time.monotonic() - acknowledged[1] >= 60):
                        if hasattr(source,'acknowledge_exceptions'):
                            source.acknowledge_exceptions(consumer,checkpoint,repairs)
                        else:
                            source.acknowledge(consumer, checkpoint, repair)
                        self._acknowledgements[source.source] = (signature, time.monotonic())
                except sqlite3.OperationalError as exc:
                    if getattr(exc, "sqlite_errorcode", None) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                        raise
                    # Retaining the old acknowledgement is conservative: archive
                    # cannot pass it. Retry next tick without restarting ingestion.
                    pass
            capture = source.capture()
            if capture:
                observed = instant(datetime.now(UTC))
                self._coverage(
                    source.source,
                    {**capture, "end_at": observed, "complete": not gap and checkpoint >= head},
                    f"{head}:{checkpoint}:{observed[:16]}:{bool(gap)}",
                )
        if time.monotonic() >= self._next_repair:
            self.repair(limit=min(20, limit), budget=.25)
            self._next_repair = time.monotonic() + 5
        return count

    def _events(self, source, events, load_event, *, budget):
        count = 0
        started = time.monotonic()
        measured = time.perf_counter()
        skipped = None
        for candidate in events:
            coordinate = candidate if isinstance(candidate,int) else candidate["seq"]
            event = {"seq":coordinate,"recorded_at":None}
            try:
                load_started = time.perf_counter()
                loaded = load_event(candidate) if isinstance(candidate,int) else candidate
                self.timings["load_seconds"] = self.timings.get("load_seconds", 0) + time.perf_counter() - load_started
                if loaded is None:
                    raise ValueError("SOURCE_RECEIPT_MISSING")
                event = {**loaded,"source":source.source}
                if event.get("serving_skipped"):
                    self.timings['verified_skips'] = self.timings.get('verified_skips', 0) + 1
                    skipped = event
                    count += 1
                    if time.monotonic() - started >= budget:
                        break
                    continue
                stage = time.perf_counter()
                records, contributions = self.mapper(event)
                map_seconds = time.perf_counter() - stage
                self.timings["map_seconds"] = self.timings.get("map_seconds", 0) + map_seconds
                stage = time.perf_counter()
                self.store.ingest(
                    source.source,
                    str(event["seq"]),
                    records,
                    contributions=contributions,
                    position=event["seq"],
                    at=event["recorded_at"],
                )
                ingest_seconds = time.perf_counter() - stage
                self.timings["ingest_seconds"] = self.timings.get("ingest_seconds", 0) + ingest_seconds
                dimensions=self.timings.setdefault('sources',{})
                totals=dimensions.setdefault(source.source,{'events':0,'records':0,'seconds':0})
                elapsed=time.perf_counter()-load_started
                totals['events']+=1;totals['records']+=len(records);totals['seconds']+=elapsed
                workloads=self.timings.setdefault('workloads',{})
                workload=':'.join((source.source,event.get('table_name',''),event.get('row',{}).get('namespace','')))
                totals=workloads.setdefault(workload,{'events':0,'records':0,'map_seconds':0,'ingest_seconds':0,'max_seconds':0,'latency_buckets':[0]*7})
                totals['events']+=1;totals['records']+=len(records)
                totals['map_seconds']+=map_seconds;totals['ingest_seconds']+=ingest_seconds
                totals['max_seconds']=max(totals['max_seconds'],elapsed)
                bucket=next((i for i,bound in enumerate((.05,.1,.25,.5,1,5)) if elapsed<=bound),6)
                totals['latency_buckets'][bucket]+=1
                if elapsed>1:
                    window,n=self._slow_window
                    if time.monotonic()-window>=60:window,n=time.monotonic(),0
                    if n<5:
                        print(json.dumps({'projection_slow':{'source':source.source,'table':event.get('table_name'),'namespace':event.get('row',{}).get('namespace'),'seq':coordinate,'records':len(records),'seconds':elapsed,'map_seconds':map_seconds,'ingest_seconds':ingest_seconds}}),flush=True)
                        n+=1
                    self._slow_window=(window,n)
                skipped = None
            except sqlite3.IntegrityError as exc:
                if str(exc) != 'ARTIFACT_RECLAIM_IN_PROGRESS':
                    raise
                # The business owner/receipt still owns the bytes. Retry this
                # coordinate and rematerialize after local GC finishes; never BODY.
                break
            except (OSError, sqlite3.OperationalError):
                raise  # Infrastructure failure is never a successfully skipped receipt.
            except (ValueError, KeyError, TypeError) as exc:
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
            skipped = None
            count += 1
            if time.monotonic() - started >= budget:
                break
        if skipped is not None:
            self.store.ingest(source.source, str(skipped["seq"]), [],
                              position=skipped["seq"], at=skipped["recorded_at"])
        self.timings["events"] = self.timings.get("events", 0) + count
        self.timings["event_seconds"] = self.timings.get("event_seconds", 0) + time.perf_counter() - measured
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

    def repair(self, *, limit: int = 20, budget: float = 1) -> int:
        now = datetime.now(UTC)
        with self.store.connect() as db:
            gaps = db.execute(
                "SELECT source,event,attempts FROM gaps WHERE next_attempt_at<=? "
                "ORDER BY attempts,source,CAST(event AS INTEGER) ASC LIMIT ?",
                (instant(now), limit),
            ).fetchall()
        repaired = 0
        started = time.monotonic()
        sources = {source.source: source for source in self.sources}
        with ExitStack() as stack:
            readers = {}
            for gap in gaps:
                if time.monotonic() - started >= budget:
                    break
                source = sources.get(gap[0])
                if source is None or not gap[1].isdigit():
                    continue
                # Only attempted items accrue attempts; preserve every gap/pin.
                delay = min(21600, 2 ** min(gap[2] + 1, 15))
                try:
                    if gap[0] not in readers:
                        reader = (source.event_reader(serving=True, projection=True) if isinstance(source, SourceOutbox)
                                  else source.event_reader() if hasattr(source, "event_reader")
                                  else nullcontext(source.event))
                        readers[gap[0]] = stack.enter_context(reader)
                    event = readers[gap[0]](int(gap[1]))
                    if event is None:
                        raise ValueError("SOURCE_RECEIPT_MISSING")
                    event["source"] = source.source
                    records, contributions = self.mapper(event)
                    self.store.ingest(source.source, gap[1] + ":repair:1", records,
                                      contributions=contributions, position=event["seq"],
                                      at=event["recorded_at"], resolved_gap=gap[1])
                    repaired += 1
                except sqlite3.IntegrityError as exc:
                    if str(exc) != 'ARTIFACT_RECLAIM_IN_PROGRESS':
                        raise
                except (OSError, sqlite3.OperationalError):
                    raise
                except (ValueError, KeyError, TypeError):
                    with self.store.connect(write=True) as db:
                        db.execute("UPDATE gaps SET attempts=attempts+1,next_attempt_at=? WHERE source=? AND event=?",
                                   (instant(now + timedelta(seconds=delay)),gap[0],gap[1]))
        self.timings["repair_seconds"] = self.timings.get("repair_seconds", 0) + time.monotonic() - started
        self.timings["repaired"] = self.timings.get("repaired", 0) + repaired
        return repaired


def native(event: dict[str, Any]) -> dict[str, Any]:
    if "native_value" in event:
        return event["native_value"]
    row = event["row"]
    for name in ("payload_json", "data_json", "payload"):
        if name in row and isinstance(row[name], str):
            value = json.loads(row[name])
            if isinstance(value, dict):
                if row.get('namespace')=='reference_deliveries':event['native_value']=value
                return value
    return row
