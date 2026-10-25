"""Durable captures and a bounded, replayable intake cursor in the existing Bus DB."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .admission import AdmissionContext
from .schema import (
    PollExecutionResult,
    PollResult,
    SourceDefinition,
    TickerSourceBinding,
    new_id,
    utc_now,
)

COUNTERS = (
    "collected_count",
    "filtered_count",
    "queued_count",
    "inserted_count",
    "duplicate_count",
    "revision_count",
    "invalid_count",
    "published_count",
    "bootstrap_suppressed_count",
)


def add_receipt(target, receipt):
    target.enrichment_job_ids.extend(receipt.enrichment_job_ids)
    for field in COUNTERS:
        setattr(target, field, getattr(target, field) + getattr(receipt, field))


@dataclass(frozen=True)
class PreparedCapture:
    capture_id: str
    run_id: str
    binding: TickerSourceBinding
    unit: str
    observed_at: datetime
    checkpoint: dict | None
    payload: str
    count: int
    distribution: dict | None = None
    request_started_at: str | None = None
    source_version: int | None = None


class PollJournal:
    def __init__(self, repository):
        self.repo = repository
        self._schedule_cache = None
        with repository.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS poll_runs (
                    run_id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    ticker TEXT NOT NULL, unit TEXT NOT NULL, due_at TEXT NOT NULL,
                    dispatch_at TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
                    source_version INTEGER NOT NULL, binding_version INTEGER NOT NULL,
                    status TEXT NOT NULL, reason TEXT, finished_at TEXT, data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS poll_runs_binding_time
                    ON poll_runs(binding_id,dispatch_at);
                CREATE INDEX IF NOT EXISTS poll_runs_time ON poll_runs(dispatch_at);
                CREATE INDEX IF NOT EXISTS poll_runs_schedule
                    ON poll_runs(status,binding_id,unit,dispatch_at);
                CREATE TABLE IF NOT EXISTS poll_captures (
                    capture_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, binding_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, ticker TEXT NOT NULL, unit TEXT NOT NULL,
                    observed_at TEXT NOT NULL, committed_at TEXT NOT NULL,
                    status TEXT NOT NULL, intake_position INTEGER NOT NULL DEFAULT 0,
                    message_count INTEGER NOT NULL, queue_at TEXT NOT NULL,
                    not_before TEXT NOT NULL,
                    claim_token TEXT, lease_until TEXT, last_error TEXT,
                    payload_json TEXT NOT NULL, receipt_json TEXT NOT NULL,
                    first_intake_at TEXT, completed_at TEXT,
                    targets_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS poll_captures_queue
                    ON poll_captures(status,not_before,queue_at);
                CREATE INDEX IF NOT EXISTS poll_captures_run ON poll_captures(run_id,observed_at);
                CREATE INDEX IF NOT EXISTS poll_captures_time ON poll_captures(observed_at);
                CREATE INDEX IF NOT EXISTS poll_captures_completed
                    ON poll_captures(status,completed_at);
                CREATE INDEX IF NOT EXISTS poll_captures_binding_time
                    ON poll_captures(binding_id,unit,observed_at);
            """)
            if "targets_json" not in {r[1] for r in db.execute("PRAGMA table_info(poll_captures)")}:
                db.execute(
                    "ALTER TABLE poll_captures ADD COLUMN targets_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "request_started_at" not in {
                r[1] for r in db.execute("PRAGMA table_info(poll_captures)")
            }:
                db.execute("ALTER TABLE poll_captures ADD COLUMN request_started_at TEXT")
            for name in ("source_version", "binding_version"):
                if name not in {r[1] for r in db.execute("PRAGMA table_info(poll_captures)")}:
                    db.execute(f"ALTER TABLE poll_captures ADD COLUMN {name} INTEGER")

    def schedules(self, entries, now, *, owner="bus"):
        """Config/calendar transitions, not one write per overdue clock tick."""
        if self._schedule_cache is None:
            with self.repo._connect() as db:
                rows = db.execute(
                    "SELECT binding_id,unit,data_json FROM poll_runs WHERE status='SCHEDULE' "
                    "ORDER BY dispatch_at"
                ).fetchall()
            self._schedule_cache = {
                (r["binding_id"], r["unit"]): json.loads(r["data_json"])["signature"] for r in rows
            }
        cache = dict(self._schedule_cache)
        present = set()
        with self.repo.atomic_intake():
            for source, binding, unit, epoch, enabled in entries:
                key = binding.binding_id, unit
                present.add(key)
                signature = [
                    source.version,
                    binding.version,
                    binding.polling.target_interval_seconds,
                    enabled,
                ]
                if cache.get(key) == signature:
                    continue
                run_id = new_id("schedule")
                self.start(run_id, source, binding, epoch or now, now, unit)
                self.update_run(
                    run_id,
                    status="SCHEDULE",
                    reason="ELIGIBILITY_UNKNOWN"
                    if enabled == "UNKNOWN"
                    else "ENABLED"
                    if enabled
                    else "PAUSED",
                    signature=signature,
                    epoch_at=(epoch or now).isoformat(),
                )
                cache[key] = signature
            for key, signature in list(cache.items()):
                if key[1].startswith("IR:") != (owner == "ir"):
                    continue
                if key not in present and signature[-1]:
                    with self.repo._connect() as db:
                        previous = db.execute(
                            "SELECT * FROM poll_runs WHERE binding_id=? AND unit=? AND "
                            "status='SCHEDULE' ORDER BY dispatch_at DESC LIMIT 1",
                            key,
                        ).fetchone()
                    run_id = new_id("schedule")
                    db_signature = [*signature[:-1], False]
                    with self.repo.transaction() as db:
                        db.execute(
                            "INSERT INTO poll_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                run_id,
                                *key[:1],
                                previous["source_id"],
                                previous["ticker"],
                                key[1],
                                previous["due_at"],
                                now.isoformat(),
                                previous["interval_seconds"],
                                previous["source_version"],
                                previous["binding_version"],
                                "SCHEDULE",
                                "PAUSED",
                                now.isoformat(),
                                json.dumps(
                                    {"signature": db_signature, "epoch_at": previous["due_at"]}
                                ),
                            ),
                        )
                    cache[key] = db_signature
        self._schedule_cache = cache

    def start(self, run_id, source, binding, due_at, dispatch_at, unit="HEAD"):
        data = {"requests": [], "captures": [], "stages_ms": {}}
        with self.repo.transaction() as db:
            db.execute(
                "INSERT INTO poll_runs VALUES(?,?,?,?,?,?,?,?,?,?,? ,NULL,NULL,?)",
                (
                    run_id,
                    binding.binding_id,
                    source.source_id,
                    binding.ticker,
                    unit,
                    due_at.isoformat(),
                    dispatch_at.isoformat(),
                    binding.polling.target_interval_seconds,
                    source.version,
                    binding.version,
                    "DISPATCHED",
                    json.dumps(data),
                ),
            )

    def update_run(self, run_id, *, status=None, reason=None, **fields):
        with self.repo.transaction() as db:
            row = db.execute(
                "SELECT data_json,status,reason FROM poll_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                return
            value = json.loads(row["data_json"])
            value.update(fields)
            db.execute(
                "UPDATE poll_runs SET "
                "data_json=?,status=?,reason=?,finished_at=coalesce(?,finished_at) WHERE "
                "run_id=?",
                (
                    json.dumps(value),
                    status or row["status"],
                    reason or row["reason"],
                    utc_now().isoformat() if status else None,
                    run_id,
                ),
            )

    def prepare(
        self,
        *,
        source,
        binding,
        result,
        run_id,
        observed_at,
        unit="HEAD",
        checkpoint=None,
        admission_context=None,
        bootstrap=False,
        distribution=None,
    ):
        context = (admission_context or AdmissionContext()).model_copy(
            update={"observed_at": observed_at}
        )
        payload = "{}"
        if result.messages or distribution:
            value = {
                "source": source.model_dump(mode="json"),
                "binding": binding.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
                "bootstrap": bootstrap,
                "admission": context.model_dump(mode="json"),
                "checkpoint": checkpoint,
                "distribution": distribution,
            }
            # File compression/hash/fsync happen before taking the business writer.
            payload = self.repo.content.encode(value)
        from .poll_trace import REQUESTS

        requests = REQUESTS.get() or []
        request_at = next(
            (
                r.get("request_started_at")
                for r in reversed(requests)
                if r.get("request_started_at")
            ),
            None,
        )
        return PreparedCapture(
            new_id("capture"),
            run_id,
            binding,
            unit,
            observed_at,
            checkpoint,
            payload,
            len(result.messages),
            distribution,
            request_at,
            source.version,
        )

    def commit(self, capture):
        now = utc_now().isoformat()
        receipt = PollExecutionResult(
            binding_id=capture.binding.binding_id, poll_run_id=capture.run_id
        )
        with self.repo.atomic_intake() as db:
            if capture.distribution:
                info = capture.distribution
                db.execute(
                    "INSERT OR REPLACE INTO distribution_acquisitions VALUES(?,?)",
                    (info["run_id"], capture.observed_at.isoformat()),
                )
                changed = db.execute(
                    "UPDATE distribution_runs SET status='CAPTURED',checkpoint_json=?,coverage=?,"
                    "claim_token=NULL,lease_until=NULL WHERE run_id=? AND claim_token=?",
                    (
                        json.dumps(info["checkpoint"]),
                        info["coverage"],
                        info["run_id"],
                        info["fetch_token"],
                    ),
                ).rowcount
                if not changed:
                    raise RuntimeError("distribution_run_lease_lost")
            db.execute(
                "INSERT INTO poll_captures "
                "VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,NULL,NULL,NULL,?,?,NULL,?,?,?,?,?)",
                (
                    capture.capture_id,
                    capture.run_id,
                    capture.binding.binding_id,
                    capture.binding.source_id,
                    capture.binding.ticker,
                    capture.unit,
                    capture.observed_at.isoformat(),
                    now,
                    "COMPLETED" if not capture.count and not capture.distribution else "PENDING",
                    capture.count,
                    now,
                    now,
                    capture.payload,
                    receipt.model_dump_json(),
                    now if not capture.count and not capture.distribution else None,
                    json.dumps(
                        capture.distribution.get("targets", []) if capture.distribution else []
                    ),
                    capture.request_started_at,
                    capture.source_version,
                    capture.binding.version,
                ),
            )
            if not capture.distribution:
                state = self.repo.get_poll_unit_state(capture.binding, capture.unit)
                # A newer physical capture must never be rolled back by a late response.
                if not state.last_capture_at or capture.observed_at >= state.last_capture_at:
                    changed = {
                        "last_capture_at": capture.observed_at,
                        "source_definition_version": capture.source_version,
                        "binding_version": capture.binding.version,
                    }
                    if capture.checkpoint is not None:
                        changed["checkpoint"] = capture.checkpoint
                    if not state.last_capture_at or capture.observed_at > state.last_capture_at:
                        changed.update(
                            last_first_capture_at=capture.observed_at,
                            last_first_intake_at=None,
                            last_final_intake_at=None,
                        )
                    self.repo.save_poll_unit_state(state.model_copy(update=changed), capture.unit)
        return capture.capture_id

    def claim(self):
        now = utc_now()
        with self.repo.transaction() as db:
            row = db.execute(
                "SELECT capture_id FROM poll_captures WHERE "
                "(status='PENDING' AND not_before<=?) OR "
                "(status='RUNNING' AND lease_until<=?) "
                "ORDER BY (unit LIKE 'TAIL:%' AND unit!='TAIL:YAHOO_TIME'),queue_at LIMIT 1",
                (now.isoformat(), now.isoformat()),
            ).fetchone()
            if not row:
                return None
            token = new_id("intake")
            db.execute(
                "UPDATE poll_captures SET status='RUNNING',claim_token=?,lease_until=? "
                "WHERE capture_id=?",
                (token, (now + timedelta(seconds=60)).isoformat(), row[0]),
            )
            return row[0], token

    def load(self, capture_id):
        with self.repo._connect() as db:
            row = db.execute(
                "SELECT * FROM poll_captures WHERE capture_id=?", (capture_id,)
            ).fetchone()
        return dict(row) if row else None

    def prepare_piece(self, row):
        payload = json.loads(row["payload_json"])
        source = SourceDefinition.model_validate(payload["source"])
        binding = TickerSourceBinding.model_validate(payload["binding"])
        position = row["intake_position"]
        result = PollResult.model_validate(
            {
                **payload["result"],
                "messages": payload["result"]["messages"][position : position + 16],
                "failures": [],
                "acquisition_metadata": {"poll_run_id": row["run_id"]},
            }
        )
        context = AdmissionContext.model_validate(payload["admission"])
        result = result.model_copy(
            update={
                "messages": [
                    message.model_copy(update={"admission_context": context})
                    for message in result.messages
                ]
            }
        )
        return (
            row,
            payload["bootstrap"],
            source,
            binding,
            result,
            context,
            payload.get("distribution"),
        )

    def accept_piece(self, bus, prepared, token, distribution=None):
        row, bootstrap, source, binding, result, context, shared = prepared
        now = utc_now().isoformat()
        with self.repo.atomic_intake() as db:
            live = db.execute(
                "SELECT claim_token,status FROM poll_captures WHERE capture_id=?",
                (row["capture_id"],),
            ).fetchone()
            if not live or live["claim_token"] != token or live["status"] != "RUNNING":
                return
            current_source = self.repo.get_source(source.source_id)
            current_binding = self.repo.get_binding(binding.binding_id)
            if (
                not current_source
                or not current_source.enabled
                or (
                    not shared
                    and (
                        not current_binding
                        or (not current_binding.enabled or current_binding.tombstoned_at)
                    )
                )
            ):
                db.execute(
                    "UPDATE poll_captures SET "
                    "status='CANCELLED',last_error='subscription_disabled',completed_at=? "
                    "WHERE capture_id=?",
                    (now, row["capture_id"]),
                )
                return
            if shared:
                claim = new_id("shared-intake")
                live_run = db.execute(
                    "SELECT roster_json FROM distribution_runs WHERE run_id=?", (shared["run_id"],)
                ).fetchone()
                live_roster = json.loads(distribution._expand(db, live_run[0]))
                frozen = {
                    (r["binding"]["binding_id"], r["binding"]["version"]): r
                    for r in shared["roster"]
                }
                roster_json = distribution._snapshot(
                    db,
                    [
                        frozen.get((r["binding"]["binding_id"], r["binding"]["version"]), r)
                        for r in live_roster
                    ],
                )
                db.execute(
                    "UPDATE distribution_runs SET claim_token=?,lease_until=?,roster_json=? "
                    "WHERE run_id=?",
                    (
                        claim,
                        (utc_now() + timedelta(seconds=60)).isoformat(),
                        roster_json,
                        shared["run_id"],
                    ),
                )
                last = row["intake_position"] + len(result.messages) >= row["message_count"]
                jobs = distribution.ingest(
                    shared["run_id"],
                    claim,
                    current_source,
                    result.messages,
                    checkpoint=shared["checkpoint"],
                    coverage=shared["coverage"],
                    done=last and shared["done"],
                    deadline_seconds=bus.enrichment_retry_deadline_seconds,
                    pipeline_version=bus.enrichment_pipeline_version,
                )
                receipt = PollExecutionResult(
                    binding_id=binding.binding_id,
                    poll_run_id=row["run_id"],
                    collected_count=len(result.messages),
                    queued_count=len(jobs),
                    enrichment_job_ids=jobs,
                )
            else:
                receipt = bus._accept_poll_result(
                    source=current_source,
                    binding=current_binding,
                    result=result,
                    attempted_at=context.observed_at,
                    admission_context=context,
                    persist_poll_state=False,
                    bootstrap_override=bootstrap,
                    strict_intake=True,
                )
            total = PollExecutionResult.model_validate_json(row["receipt_json"])
            add_receipt(total, receipt)
            state = self.repo.get_poll_state(binding)
            if not shared:
                self.repo.save_poll_state(
                    state.model_copy(
                        update={
                            "collected_count": state.collected_count + receipt.collected_count,
                            "filtered_count": state.filtered_count + receipt.filtered_count,
                            "published_count": state.published_count + receipt.published_count,
                        }
                    )
                )
                unit_state = self.repo.get_poll_unit_state(binding, row["unit"])
                if unit_state.last_capture_at == context.observed_at:
                    unit_state = unit_state.model_copy(
                        update={
                            "last_first_capture_at": context.observed_at,
                            "last_first_intake_at": unit_state.last_first_intake_at or utc_now(),
                            "last_final_intake_at": utc_now()
                            if row["intake_position"] + len(result.messages) >= row["message_count"]
                            else None,
                        }
                    )
                    self.repo.save_poll_unit_state(unit_state, row["unit"])
            following = row["intake_position"] + len(result.messages)
            done = following >= row["message_count"]
            db.execute(
                "UPDATE poll_captures SET status=?,intake_position=?,queue_at=?,claim_token=NULL,"
                "lease_until=NULL,receipt_json=?,first_intake_at=coalesce(first_intake_at,?),"
                "completed_at=? WHERE capture_id=?",
                (
                    "COMPLETED" if done else "PENDING",
                    following,
                    now,
                    total.model_dump_json(),
                    now,
                    now if done else None,
                    row["capture_id"],
                ),
            )

    def retry(self, capture_id, token, error):
        with self.repo.transaction() as db:
            status = "FAILED" if isinstance(error, (ValueError, TypeError)) else "PENDING"
            db.execute(
                "UPDATE poll_captures SET "
                "status=?,claim_token=NULL,lease_until=NULL,not_before=?,last_error=? "
                "WHERE capture_id=? AND claim_token=?",
                (
                    status,
                    (utc_now() + timedelta(seconds=1)).isoformat(),
                    type(error).__name__,
                    capture_id,
                    token,
                ),
            )

    def run_receipt(self, run_id):
        with self.repo._connect() as db:
            rows = db.execute(
                "SELECT status,receipt_json FROM poll_captures WHERE run_id=?", (run_id,)
            ).fetchall()
        if any(r["status"] == "FAILED" for r in rows):
            raise RuntimeError("capture_intake_failed:" + run_id)
        if any(r["status"] not in {"COMPLETED", "CANCELLED"} for r in rows):
            return None
        target = None
        for row in rows:
            receipt = PollExecutionResult.model_validate_json(row["receipt_json"])
            if target is None:
                target = PollExecutionResult(binding_id=receipt.binding_id, poll_run_id=run_id)
            add_receipt(target, receipt)
        return target

    def pending(self, run_id):
        with self.repo._connect() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM poll_captures WHERE run_id=? AND status IN "
                    "('PENDING','RUNNING') LIMIT 1",
                    (run_id,),
                ).fetchone()
            )

    def backlogged(self, *, now=None):
        """Pause optional history when durable intake has exceeded its 3s tail target."""
        cutoff = ((now or utc_now()) - timedelta(seconds=3)).isoformat()
        with self.repo._connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM poll_captures WHERE status IN ('PENDING','RUNNING') "
                    "AND committed_at<? LIMIT 1",
                    (cutoff,),
                ).fetchone()
                is not None
            )

    def maintain(self, *, now=None, limit=64):
        """Bound completed diagnostic history; unresolved input and boundary proof survive."""
        cutoff = ((now or utc_now()) - timedelta(days=3)).isoformat()
        with self.repo.atomic_intake() as db:
            captures = db.execute(
                "DELETE FROM poll_captures WHERE capture_id IN (SELECT p.capture_id "
                "FROM poll_captures p WHERE p.status='COMPLETED' AND p.completed_at<? AND "
                "(p.request_started_at IS NULL OR EXISTS(SELECT 1 FROM poll_captures q "
                "WHERE q.binding_id=p.binding_id AND q.unit=p.unit AND q.status='COMPLETED' "
                "AND q.request_started_at IS NOT NULL AND q.observed_at<? "
                "AND (q.observed_at>p.observed_at OR "
                "(q.observed_at=p.observed_at AND q.capture_id>p.capture_id)))) "
                "ORDER BY p.completed_at LIMIT ?)",
                (cutoff, cutoff, limit),
            ).rowcount
            runs = db.execute(
                "DELETE FROM poll_runs WHERE run_id IN (SELECT r.run_id FROM poll_runs r "
                "WHERE r.status IN ('CAPTURED','FAILED','DEFERRED','CANCELLED') "
                "AND r.dispatch_at<? "
                "AND NOT EXISTS(SELECT 1 FROM poll_captures c WHERE c.run_id=r.run_id) "
                "ORDER BY r.dispatch_at LIMIT ?)",
                (cutoff, limit),
            ).rowcount
            schedules = db.execute(
                "DELETE FROM poll_runs WHERE run_id IN (SELECT r.run_id FROM poll_runs r "
                "WHERE r.status='SCHEDULE' AND r.dispatch_at<? "
                "AND EXISTS(SELECT 1 FROM poll_runs q "
                "WHERE q.status='SCHEDULE' AND q.binding_id=r.binding_id AND q.unit=r.unit "
                "AND q.dispatch_at>r.dispatch_at AND q.dispatch_at<?) LIMIT ?)",
                (cutoff, cutoff, limit),
            ).rowcount
        return captures + runs + schedules


class CaptureIntake:
    def __init__(self, bus):
        from .distribution_repository import DistributionRepository

        self.bus = bus
        self.journal = bus.repository.polling
        self.distribution = DistributionRepository(bus.repository)
        self.task = None
        self.wake = asyncio.Event()
        self.stopping = False
        self._maintenance_at = 0.0

    def start(self):
        if self.task is None or self.task.done():
            if self.task is not None and not self.task.cancelled():
                self.task.result()
            self.wake = asyncio.Event()
            from .collection_override import REQUEST_GATE

            token = REQUEST_GATE.set(None)
            try:
                # Persisted intake is independent of the acquisition session.
                self.task = asyncio.create_task(self.run())
            finally:
                REQUEST_GATE.reset(token)
        self.wake.set()

    async def run(self):
        while not self.stopping:
            claim = await self.bus.storage.call(self.journal.claim, _lane_priority=0)
            if not claim:
                import time

                if time.monotonic() >= self._maintenance_at:
                    self._maintenance_at = time.monotonic() + 1
                    await self.bus.storage.call(self.journal.maintain)
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 0.25)
                except TimeoutError:
                    pass
                continue
            capture_id, token = claim
            try:
                row = await self.bus.storage.read(self.journal.load, capture_id)
                prepared = await self.bus.storage.prepare(self.journal.prepare_piece, row)
                await self.bus.storage.call(
                    self.journal.accept_piece, self.bus, prepared, token, self.distribution
                )
            except asyncio.CancelledError:
                # Claim expires on process loss; a drained commit remains durable.
                raise
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "capture intake failed capture=%s", capture_id
                )
                await self.bus.storage.call(self.journal.retry, capture_id, token, exc)
            await asyncio.sleep(0)

    async def wait(self, run_id):
        self.start()
        while True:
            receipt = await self.bus.storage.read(self.journal.run_receipt, run_id)
            if receipt is not None:
                return receipt
            await asyncio.sleep(0.01)

    async def close(self):
        self.stopping = True
        self.wake.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
