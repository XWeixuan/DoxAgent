"""Durable captures and a bounded, replayable intake cursor in the existing Bus DB."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .admission import AdmissionContext, evaluate_admission
from .schema import (
    PollExecutionResult,
    PollResult,
    RawMessageInput,
    SourceDefinition,
    TickerSourceBinding,
    canonical_json,
    identity_key_for,
    new_id,
    sha256_text,
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
    input_key: str = ""
    fresh_count: int = 0
    repeated_count: int = 0


def capture_input_key(payload, unit):
    observed = payload.get('admission',{}).get('observed_at')
    if observed and datetime.fromisoformat(str(observed).replace('Z','+00:00')) > utc_now()+timedelta(seconds=1):
        return ''  # An unelapsed observation is not evidence for a completed peer.
    if payload.get('semantic_input_key','').startswith('v2:'):
        return payload['semantic_input_key']
    keys = article_input_keys(payload, unit, payload['result']['messages'])
    return 'v2:' + sha256_text(canonical_json(keys)) if keys else ''


def article_input_keys(payload, unit, values):
    """Business input, not transport traces; never merge distinct admission verdicts."""
    from .deduplication import stable_fingerprint
    if payload["admission"].get("mode", "REALTIME") != "REALTIME":
        return []
    context = AdmissionContext.model_validate(payload["admission"])
    shared = payload.get("distribution")
    contexts = ([AdmissionContext.model_validate(t["admission"]) for t in shared["roster"]]
                if shared else [context])
    messages = [RawMessageInput.model_validate(value) for value in values]
    roster = [(t["binding"], t["terms_revision"],
               {k:v for k,v in t["admission"].items() if k != "observed_at"})
              for t in shared["roster"]] if shared else None
    scope = [
        "semantic-input-v2",
        payload["source"]["source_id"], payload["source"]["version"],
        payload["binding"]["binding_id"], payload["binding"]["version"],
        unit, payload["bootstrap"], payload["admission"].get("policy_version", 1),
        roster]
    def business(value):
        if isinstance(value,dict):
            return {k:business(v) for k,v in value.items() if k not in {
                'pipeline_trace','site_access_trace','request_started_at','response_received_at',
                'poll_run_id','physical_request_id','capture_id','fetch_latency_ms','listing_rank'}}
        if isinstance(value,list):
            return [business(v) for v in value]
        return value
    return [sha256_text(canonical_json([scope,
         (identity_key_for(payload["source"]["source_id"], m), stable_fingerprint(m),
          business(m.raw_payload), business(m.metadata),
          m.publication_time_basis, m.metadata.get("language"), m.metadata.get("attachments"),
          [evaluate_admission(m.published_at,c,context.observed_at or utc_now(),
                              m.publication_time_basis) for c in contexts])])) for m in messages]


class PollJournal:
    def __init__(self, repository):
        self.repo = repository
        self._schedule_cache = None
        self._decoded = OrderedDict()
        self._decoded_bytes = 0
        self._decoded_lock = threading.Lock()
        if not getattr(repository, 'initialize', True):
            return
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
                CREATE TABLE IF NOT EXISTS poll_capture_inputs (
                    capture_id TEXT PRIMARY KEY REFERENCES poll_captures(capture_id) ON DELETE CASCADE,
                    binding_id TEXT NOT NULL,unit TEXT NOT NULL,input_hash TEXT NOT NULL,
                    reused_from TEXT
                );
                CREATE INDEX IF NOT EXISTS poll_capture_inputs_match
                    ON poll_capture_inputs(binding_id,unit,input_hash);
                CREATE INDEX IF NOT EXISTS poll_capture_inputs_parent
                    ON poll_capture_inputs(reused_from) WHERE reused_from IS NOT NULL;
                CREATE TABLE IF NOT EXISTS poll_unit_states (
                    binding_id TEXT NOT NULL REFERENCES ticker_source_bindings(binding_id) ON DELETE CASCADE,
                    unit TEXT NOT NULL, data_json TEXT NOT NULL,
                    PRIMARY KEY(binding_id,unit)
                );
                CREATE TABLE IF NOT EXISTS poll_input_verdicts (
                    input_key TEXT PRIMARY KEY, completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS poll_input_verdicts_age ON poll_input_verdicts(completed_at);
                CREATE TABLE IF NOT EXISTS google_url_resolutions (
                    wrapper TEXT PRIMARY KEY, final_url TEXT NOT NULL, resolved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS poll_intake_reuse (
                    input_hash TEXT PRIMARY KEY, capture_id TEXT NOT NULL, valid_until TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS poll_intake_reuse_expiry ON poll_intake_reuse(valid_until);
                CREATE TABLE IF NOT EXISTS poll_capture_priority (
                    capture_id TEXT PRIMARY KEY, fresh_remaining INTEGER NOT NULL
                );
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

    def google_url(self, wrapper, final_url=None):
        if final_url is not None:
            with self.repo.transaction() as db:
                db.execute(
                    "INSERT OR REPLACE INTO google_url_resolutions VALUES(?,?,?)",
                    (wrapper, final_url, utc_now().isoformat()),
                )
            return final_url
        with self.repo._connect() as db:
            row = db.execute(
                "SELECT final_url FROM google_url_resolutions WHERE wrapper=?", (wrapper,)
            ).fetchone()
        return row[0] if row else None

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
        prepared = []
        for source,binding,unit,epoch,enabled in entries:
            key=binding.binding_id,unit
            signature=[source.version,binding.version,binding.polling.target_interval_seconds,
                round(epoch.timestamp()*1_000_000) % (binding.polling.target_interval_seconds*1_000_000)
                if epoch else None,enabled]
            prepared.append(((source,binding,unit,epoch,enabled),key,signature))
        present = {key for _,key,_ in prepared}
        retired = any(key not in present and signature[-1] and
            (key[1].startswith('IR:') == (owner=='ir')) for key,signature in cache.items())
        if not retired and all(cache.get(key)==signature for _,key,signature in prepared):
            return  # Unchanged schedule evidence requires no SQLite writer visit.
        with self.repo.atomic_intake():
            for (source,binding,unit,epoch,enabled),key,signature in prepared:
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
        data = {"requests": [], "captures": [], "stages_ms": {},
                "required": not unit.startswith('TAIL:'),
                "unit_role": 'PUBLICATION_TIME_VERIFICATION' if unit == 'TAIL:YAHOO_TIME' else
                             'WINDOW_COVERAGE' if unit.startswith('TAIL:') else 'LISTING',
                "transport_plan": source.adapter_ref, "endpoint_key": unit}
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
        input_key = ""
        count, repeated = len(result.messages), 0
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
            input_key = capture_input_key(value, unit)
            with self.repo._connect() as db:
                prior = self.input_leader(db, binding, unit, input_key, utc_now().isoformat())
            if prior:
                payload = prior["encoded_payload"]
                count = prior['message_count']
                repeated = len(result.messages)-count
                value["fresh_count"] = 0
            else:
                keys = article_input_keys(value,unit,value['result']['messages'])
                known = set()
                with self.repo._connect() as db:
                    for offset in range(0,len(keys),400):
                        batch = keys[offset:offset+400]
                        known.update(r[0] for r in db.execute('SELECT input_key FROM poll_input_verdicts '
                            'WHERE input_key IN ('+','.join('?' for _ in batch)+')',batch))
                if known:
                    delta = [message for message,key in zip(result.messages,keys,strict=True) if key not in known]
                    repeated = len(result.messages)-len(delta)
                    result = result.model_copy(update={'messages':delta})
                    value['result'] = result.model_dump(mode='json')
                count = len(result.messages)
                value['semantic_input_key'] = input_key
                value['observed_count'] = count+repeated
                value["intake_order"], value["fresh_count"] = self.candidate_order(
                    binding, result.messages, distribution=bool(distribution), with_count=True,
                    context=context,
                )
                # Reuse is checked before compression and file publication.
                payload = self.repo.content.encode(value, keep=("result",))
        from .poll_trace import REQUESTS

        requests = REQUESTS.get() or []
        request_at = next(
            (
                r.get("request_started_at")
                for r in requests
                if r.get("request_started_at") and r.get("role", "listing") == "listing"
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
            count,
            distribution,
            request_at,
            source.version,
            input_key,
            value.get('fresh_count', 0) if result.messages else 0,
            repeated,
        )

    @staticmethod
    def input_leader(db, binding, unit, key, now):
        if not key:
            return None
        return db.execute(
            "SELECT p.capture_id,p.status,p.message_count,p.payload_json AS encoded_payload FROM poll_capture_inputs k "
            "JOIN poll_captures p ON p.capture_id=k.capture_id "
            "LEFT JOIN poll_intake_reuse r ON r.capture_id=p.capture_id AND r.input_hash=k.input_hash "
            "WHERE k.binding_id=? AND k.unit=? AND k.input_hash=? AND k.reused_from IS NULL "
            "AND (p.status IN ('PENDING','RUNNING') OR (p.status='COMPLETED' AND r.valid_until>?)) "
            "ORDER BY p.observed_at LIMIT 1", (binding.binding_id, unit, key, now)
        ).fetchone()

    def commit(self, capture):
        now = utc_now().isoformat()
        receipt = PollExecutionResult(
            binding_id=capture.binding.binding_id, poll_run_id=capture.run_id,
            collected_count=capture.repeated_count,duplicate_count=capture.repeated_count,
            window_done=capture.distribution.get('done',True) if capture.distribution else True,
            window_coverage=capture.distribution.get('coverage','UNKNOWN') if capture.distribution else 'UNKNOWN',
        )
        with self.repo.atomic_intake() as db:
            reusable = None
            leader = self.input_leader(db, capture.binding, capture.unit, capture.input_key, now)
            if leader:
                if leader['status'] == 'COMPLETED':
                    reusable = leader
                    receipt.collected_count += capture.count
                    receipt.duplicate_count += capture.count
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
            terminal = bool(reusable or (not capture.count and
                (not capture.distribution or capture.distribution.get('done',True))))
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
                    "COMPLETED"
                    if terminal
                    else "PENDING",
                    capture.count,
                    now,
                    now,
                    capture.payload,
                    receipt.model_dump_json(),
                    now if terminal else None,
                    json.dumps(
                        capture.distribution.get("targets", []) if capture.distribution else []
                    ),
                    capture.request_started_at,
                    capture.source_version,
                    capture.binding.version,
                ),
            )
            if capture.fresh_count and not leader:
                db.execute('INSERT INTO poll_capture_priority VALUES(?,?)',
                           (capture.capture_id, capture.fresh_count))
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
                    if terminal:
                        changed.update(last_first_capture_at=capture.observed_at,
                                       last_first_intake_at=utc_now(),last_final_intake_at=utc_now())
                    self.repo.save_poll_unit_state(state.model_copy(update=changed), capture.unit)
            db.execute("INSERT OR IGNORE INTO poll_capture_inputs VALUES(?,?,?,?,NULL)",
                (capture.capture_id,capture.binding.binding_id,capture.unit,capture.input_key))
            if leader:
                db.execute("UPDATE poll_capture_inputs SET reused_from=? WHERE capture_id=?",
                           (leader['capture_id'], capture.capture_id))
            if terminal:
                if capture.input_key and not leader:
                    db.execute('INSERT OR REPLACE INTO poll_intake_reuse VALUES(?,?,?)',
                        (capture.input_key,capture.capture_id,(utc_now()+timedelta(seconds=60)).isoformat()))
                db.execute(
                    "UPDATE poll_captures SET intake_position=message_count,first_intake_at=? WHERE capture_id=?",
                    (now, capture.capture_id),
                )
                db.execute(
                    "UPDATE poll_capture_inputs SET reused_from=? WHERE capture_id=?",
                    (reusable[0] if reusable else None, capture.capture_id),
                )
                if capture.distribution:
                    db.execute("UPDATE distribution_runs SET status=? WHERE run_id=?",
                               ('DONE' if receipt.window_done else 'PENDING',capture.distribution['run_id']))
                else:
                    state = self.repo.get_poll_state(capture.binding)
                    self.repo.save_poll_state(state.model_copy(
                        update={"collected_count": state.collected_count + capture.count+capture.repeated_count}))
        return capture.capture_id

    def ready(self):
        now=utc_now().isoformat()
        with self.repo._connect() as db:
            return db.execute("SELECT 1 FROM poll_captures WHERE "
                "(status='PENDING' AND not_before<=?) OR "
                "(status='RUNNING' AND lease_until<=?) LIMIT 1",(now,now)).fetchone() is not None

    def claim(self, capture_id=None):
        if not self.ready():
            return None
        now = utc_now()
        with self.repo.atomic_intake() as db:
            # Followers are receipts, not independent business workers. Drain
            # bounded groups even when more than 64 arrived before their leader.
            parents = db.execute(
                "SELECT DISTINCT p.capture_id,p.binding_id,p.unit,k.input_hash,p.observed_at "
                "FROM poll_capture_inputs f JOIN poll_captures child ON child.capture_id=f.capture_id "
                "JOIN poll_captures p ON p.capture_id=f.reused_from "
                "JOIN poll_capture_inputs k ON k.capture_id=p.capture_id "
                "WHERE child.status='PENDING' AND p.status='COMPLETED' LIMIT 1"
            ).fetchall()
            for parent in parents:
                binding = self.repo.get_binding(parent['binding_id'])
                if binding:
                    self.reuse_waiting_inputs(db, parent, binding, parent['input_hash'], now.isoformat())
            row = db.execute(
                "SELECT p.capture_id FROM poll_captures p LEFT JOIN poll_capture_priority f "
                "ON f.capture_id=p.capture_id LEFT JOIN poll_capture_inputs k ON k.capture_id=p.capture_id WHERE "
                "k.reused_from IS NULL AND "
                "((status='PENDING' AND not_before<=?) OR "
                "(status='RUNNING' AND lease_until<=?)) "
                + ("AND p.capture_id=? " if capture_id else "") +
                "ORDER BY (p.unit LIKE 'TAIL:%' AND p.unit!='TAIL:YAHOO_TIME'),"
                + ("" if getattr(self, '_fresh_streak', 0) >= 8 else "(coalesce(f.fresh_remaining,0)<=0),")
                + "queue_at LIMIT 1",
                (now.isoformat(), now.isoformat(), *([capture_id] if capture_id else [])),
            ).fetchone()
            if not row:
                return None
            fresh = db.execute('SELECT fresh_remaining FROM poll_capture_priority WHERE capture_id=?',
                               (row[0],)).fetchone()
            self._fresh_streak = getattr(self, '_fresh_streak', 0)+1 if fresh and fresh[0]>0 else 0
            token = new_id("intake")
            db.execute(
                "UPDATE poll_captures SET status='RUNNING',claim_token=?,lease_until=? "
                "WHERE capture_id=?",
                (token, (now + timedelta(seconds=60)).isoformat(), row[0]),
            )
            return row[0], token

    def candidate_order(self, binding, messages, *, distribution=False, with_count=False, context=None):
        """Put absent/changed identities first; keep every original array offset durable."""
        from .deduplication import stable_fingerprint

        keys = [sha256_text(canonical_json([binding.source_id, m.external_id or m.url]))
                if distribution else identity_key_for(binding.source_id, m) for m in messages]
        known = {}
        with self.repo._connect() as db:
            for offset in range(0, len(keys), 400):
                batch = keys[offset : offset + 400]
                if not batch:
                    continue
                rows = db.execute(
                    "SELECT article_key,input_version FROM distribution_articles WHERE article_key IN ("
                    + ",".join("?" for _ in batch) + ")" if distribution else
                    "SELECT identity_key,input_fingerprint FROM message_observations "
                    "WHERE ticker=? AND source_id=? AND identity_key IN ("
                    + ",".join("?" for _ in batch)
                    + ")",
                    tuple(batch) if distribution else (binding.ticker, binding.source_id, *batch),
                ).fetchall()
                known.update((r[0], r[1]) for r in rows)
        def fresh(i):
            message = messages[i]
            if context and evaluate_admission(message.published_at, context, context.observed_at,
                    message.publication_time_basis) in {'EXPIRED_PUBLICATION','OUTSIDE_RECENT_DATES'}:
                return False
            return keys[i] not in known or (not distribution and known[keys[i]] != stable_fingerprint(message))
        flags = [fresh(i) for i in range(len(messages))]
        order = sorted(range(len(messages)), key=lambda i: not flags[i])
        return (order, sum(flags)) if with_count else order

    def load(self, capture_id):
        with self.repo._connect() as db:
            row = db.execute(
                "SELECT p.capture_id,p.run_id,p.binding_id,p.source_id,p.ticker,p.unit,p.observed_at,"
                "p.committed_at,p.status,p.intake_position,p.message_count,p.receipt_json,p.first_intake_at,p.completed_at,"
                "p.payload_json AS encoded_payload,k.input_hash FROM poll_captures p LEFT JOIN poll_capture_inputs k "
                "ON k.capture_id=p.capture_id WHERE p.capture_id=?",
                (capture_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        encoded = result.pop("encoded_payload")
        # Cache by immutable content, not capture ID. A repeated list shares decoding.
        key = sha256_text(encoded)
        with self._decoded_lock:
            cached = self._decoded.get(key)
            if cached is not None:
                self._decoded.move_to_end(key)
        if cached is None:
            payload = self.repo.content.decode(json.loads(encoded))
            size = 4 * len(canonical_json(payload).encode())  # Allow for Python object/string overhead.
            cached = (payload, size)
            with self._decoded_lock:
                if key not in self._decoded and size <= 64 * 1024 * 1024:
                    self._decoded[key] = cached
                    self._decoded_bytes += size
                    while self._decoded_bytes > 64 * 1024 * 1024:
                        _, (_, removed) = self._decoded.popitem(last=False)
                        self._decoded_bytes -= removed
        result["decoded_payload"] = cached[0]
        return result

    def prepare_piece(self, row):
        payload = row.get("decoded_payload") or json.loads(row["payload_json"])
        source = SourceDefinition.model_validate(payload["source"])
        binding = TickerSourceBinding.model_validate(payload["binding"])
        position = row["intake_position"]
        context = AdmissionContext.model_validate(payload["admission"])
        shared = payload.get("distribution")
        input_key = row.get("input_hash") or capture_input_key(payload, row["unit"])
        original = payload["result"]["messages"]
        order = payload.get("intake_order")
        window = (
            [original[i] for i in order[position : position + 256]]
            if order is not None
            else original[position : position + 256]
        )
        messages = window[:16]
        advanced, filtered = len(messages), 0
        repeated, completed_keys = 0, []
        skipped_keys = []
        skipped_times = []
        if context.mode == "REALTIME" and (
            not shared
            or all(t["admission"].get("mode", "REALTIME") == "REALTIME" for t in shared["roster"])
        ):
            # Old large RSS lists must not create per-article/per-ticker delivery
            # work merely to reject it. Keep the original durable payload and
            # count every input; only existing unequivocal exact/date age gates
            # is evaluated here, outside the writer. Real candidates stay <=16.
            contexts = ([AdmissionContext.model_validate(t["admission"]) for t in shared["roster"]]
                        if shared else [context])
            candidates = [RawMessageInput.model_validate(raw) for raw in window]
            verdicts = article_input_keys(payload, row['unit'], window)
            with self.repo._connect() as db:
                known = {r[0] for r in db.execute(
                    'SELECT input_key FROM poll_input_verdicts WHERE input_key IN ('
                    + ','.join('?' for _ in verdicts)+')', verdicts)} if verdicts else set()
            tickers = sorted({t['binding']['ticker'] for t in shared['roster']}) if shared else None
            published = self.repo.published_identity_keys(binding,
                [message for message, verdict in zip(candidates,verdicts,strict=True) if verdict not in known],
                tickers=tickers)
            messages, advanced = [], 0
            for message, verdict in zip(candidates, verdicts, strict=True):
                key = identity_key_for(source.source_id, message)
                if verdict in known:
                    advanced += 1
                    repeated += 1
                    continue
                expired = (key not in published and message.publication_time_basis in {"EXACT", "DATE"}
                    and message.published_at <= context.observed_at and contexts and all(
                    evaluate_admission(message.published_at, target, context.observed_at,
                                       message.publication_time_basis) in {"EXPIRED_PUBLICATION", "OUTSIDE_RECENT_DATES"}
                    for target in contexts
                ))
                if expired:
                    advanced += 1
                    filtered += 1
                    skipped_keys.append(key)
                    skipped_times.append((message, message.published_at.astimezone(UTC).isoformat()))
                    completed_keys.append(verdict)
                elif len(messages) < 16:
                    messages.append(message)
                    advanced += 1
                    completed_keys.append(verdict)
                else:
                    break
        bindings = [t['binding']['binding_id'] for t in shared['roster']] if shared else [binding.binding_id]
        first_seen = [(identity_key_for(binding_id, message), at)
                      for binding_id in bindings for message, at in skipped_times]
        result = PollResult.model_validate(
            {
                **payload["result"],
                "messages": messages,
                "failures": [],
                "acquisition_metadata": {"poll_run_id": row["run_id"],
                    "intake_advanced": advanced, "intake_prefiltered": filtered,
                    "intake_skipped_keys": skipped_keys, "intake_first_seen": first_seen,
                    "intake_repeated": repeated, "intake_verdicts": completed_keys,
                    "intake_input_key": input_key},
            }
        )
        result = result.model_copy(
            update={
                "messages": [
                    message.model_copy(
                        update={
                            "admission_context": context,
                            "metadata": {
                                **message.metadata,
                                "pipeline_trace": {
                                    "capture_id": row["capture_id"],
                                    "poll_run_id": row["run_id"],
                                    "listing_received_at": row["observed_at"],
                                    "capture_committed_at": row["committed_at"],
                                    "candidate_prepared_at": utc_now().isoformat(),
                                },
                            },
                        }
                    )
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
        advanced = result.acquisition_metadata.get("intake_advanced", len(result.messages))
        now = utc_now().isoformat()
        with self.repo.atomic_intake() as db:
            live = db.execute(
                "SELECT claim_token,status FROM poll_captures WHERE capture_id=?",
                (row["capture_id"],),
            ).fetchone()
            if not live or live["claim_token"] != token or live["status"] != "RUNNING":
                return
            db.execute("INSERT OR IGNORE INTO poll_capture_inputs VALUES(?,?,?,?,NULL)",
                (row['capture_id'],binding.binding_id,row['unit'],result.acquisition_metadata['intake_input_key']))
            if result.acquisition_metadata.get("intake_skipped_keys"):
                skipped = set(result.acquisition_metadata["intake_skipped_keys"])
                # A BODY completion may publish while the preparation snapshot
                # waits for the writer. Reprepare instead of losing its update.
                tickers = sorted({t['binding']['ticker'] for t in shared['roster']}) if shared else None
                scope = ("r.ticker IN (" + ",".join("?" for _ in tickers) + ") AND r.source_id=?"
                         if tickers else "r.ticker=? AND r.source_id=? AND r.binding_id=?")
                args = (*tickers, source.source_id) if tickers else (binding.ticker, binding.source_id, binding.binding_id)
                matches = db.execute(
                    "SELECT 1 FROM raw_messages r JOIN standard_messages s "
                    "ON s.raw_message_id=r.raw_message_id JOIN stream_members m "
                    "ON m.standard_message_id=s.standard_message_id "
                    "WHERE " + scope + " "
                    "AND r.identity_key IN (" + ",".join("?" for _ in skipped) + ") LIMIT 1",
                    (*args, *sorted(skipped)),
                ).fetchone()
                if matches:
                    raise RuntimeError("intake_publication_changed_reprepare")
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
                db.execute("UPDATE poll_captures SET status='CANCELLED',last_error='subscription_disabled',completed_at=? "
                           "WHERE capture_id IN (SELECT capture_id FROM poll_capture_inputs WHERE reused_from=?) "
                           "AND status='PENDING'", (now,row['capture_id']))
                return
            if result.acquisition_metadata.get("intake_first_seen"):
                # Bulk rejection must still pin the original publication proof:
                # a later provider fallback to UNKNOWN cannot make old news new.
                db.executemany(
                    "INSERT INTO message_first_seen VALUES(?,?) ON CONFLICT(id) DO UPDATE SET "
                    "first_seen_at=excluded.first_seen_at "
                    "WHERE excluded.first_seen_at < message_first_seen.first_seen_at",
                    result.acquisition_metadata["intake_first_seen"],
                )
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
                last = row["intake_position"] + advanced >= row["message_count"]
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
                    collected_count=advanced,
                    filtered_count=result.acquisition_metadata.get("intake_prefiltered", 0),
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
                prefiltered = result.acquisition_metadata.get("intake_prefiltered", 0)
                receipt.collected_count += prefiltered
                receipt.filtered_count += prefiltered
            total = PollExecutionResult.model_validate_json(row["receipt_json"])
            repeated = result.acquisition_metadata.get('intake_repeated', 0)
            if not shared:
                receipt.collected_count += repeated
            receipt.duplicate_count += repeated
            db.executemany('INSERT OR IGNORE INTO poll_input_verdicts VALUES(?,?)',
                           [(key, now) for key in result.acquisition_metadata.get('intake_verdicts', [])])
            add_receipt(total, receipt)
            completing = row["intake_position"] + advanced >= row["message_count"]
            state = self.repo.get_poll_state(binding)
            if not shared:
                self.repo.save_poll_state(state.model_copy(update={
                    "collected_count": state.collected_count + receipt.collected_count,
                    "filtered_count": state.filtered_count + receipt.filtered_count,
                    "published_count": state.published_count + receipt.published_count}))
                unit_state = self.repo.get_poll_unit_state(binding, row["unit"])
                if unit_state.last_capture_at == context.observed_at and (
                    not unit_state.last_first_intake_at or completing
                ):
                    unit_state = unit_state.model_copy(
                        update={
                            "last_first_capture_at": context.observed_at,
                            "last_first_intake_at": unit_state.last_first_intake_at or utc_now(),
                            "last_final_intake_at": utc_now()
                            if row["intake_position"] + advanced >= row["message_count"]
                            else None,
                        }
                    )
                    self.repo.save_poll_unit_state(unit_state, row["unit"])
            following = row["intake_position"] + advanced
            done = following >= row["message_count"]
            db.execute('UPDATE poll_capture_priority SET fresh_remaining=max(0,fresh_remaining-?) WHERE capture_id=?',
                       (advanced, row['capture_id']))
            if done:
                db.execute('DELETE FROM poll_capture_priority WHERE capture_id=?', (row['capture_id'],))
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
            if done:
                self.reuse_waiting_inputs(db,row,binding,result.acquisition_metadata['intake_input_key'],now)
                key = result.acquisition_metadata["intake_input_key"]
                if key:
                    # BODY recovery is scheduled independently; an overdue recheck
                    # must not disable reuse of every unrelated item in this list.
                    until = (utc_now() + timedelta(seconds=60)).isoformat()
                    db.execute(
                        "INSERT OR REPLACE INTO poll_intake_reuse VALUES(?,?,?)",
                        (key, row["capture_id"], until),
                    )

    def reuse_waiting_inputs(self, db, parent, binding, input_key, now):
        if not input_key:
            return
        peers = db.execute("SELECT p.capture_id,p.run_id,p.message_count,p.intake_position,p.receipt_json,p.observed_at "
            "FROM poll_capture_inputs k JOIN poll_captures p ON p.capture_id=k.capture_id "
            "WHERE k.binding_id=? AND k.unit=? AND k.input_hash=? AND p.status='PENDING' "
            "AND julianday(p.observed_at)>=julianday(?) "
            "AND julianday(p.observed_at)<=julianday(?) "
            "AND p.capture_id!=? ORDER BY p.observed_at LIMIT 64",
            (binding.binding_id,parent['unit'],input_key,parent['observed_at'],now,parent['capture_id'])).fetchall()
        if not peers:
            return
        for peer in peers:
            remaining = peer['message_count']-peer['intake_position']
            receipt = PollExecutionResult.model_validate_json(peer['receipt_json'])
            receipt.collected_count += remaining
            receipt.duplicate_count += remaining
            db.execute("UPDATE poll_captures SET status='COMPLETED',intake_position=message_count,"
                "receipt_json=?,first_intake_at=coalesce(first_intake_at,?),completed_at=? WHERE capture_id=?",
                (receipt.model_dump_json(),now,now,peer['capture_id']))
            db.execute("UPDATE poll_capture_inputs SET reused_from=? WHERE capture_id=?",
                (parent['capture_id'],peer['capture_id']))
            db.execute('DELETE FROM poll_capture_priority WHERE capture_id=?', (peer['capture_id'],))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='distribution_runs'").fetchone():
            db.executemany("UPDATE distribution_runs SET status=? WHERE run_id=? AND status='CAPTURED'",
                           [('DONE' if PollExecutionResult.model_validate_json(peer['receipt_json']).window_done else 'PENDING',peer['run_id']) for peer in peers])
        state = self.repo.get_poll_state(binding)
        self.repo.save_poll_state(state.model_copy(update={
            'collected_count':state.collected_count+sum(p['message_count']-p['intake_position'] for p in peers)}))
        unit = self.repo.get_poll_unit_state(binding,parent['unit'])
        if unit.last_capture_at and unit.last_capture_at.isoformat() in {p['observed_at'] for p in peers}:
            self.repo.save_poll_unit_state(unit.model_copy(update={
                'last_first_capture_at':unit.last_capture_at,'last_first_intake_at':unit.last_first_intake_at or utc_now(),
                'last_final_intake_at':utc_now()}),parent['unit'])

    def backfill_input_keys(self, limit=16):
        """Bounded metadata-only migration; decoding/hashing precedes its short write."""
        with self.repo._connect() as db:
            rows = db.execute("SELECT p.capture_id,p.binding_id,p.unit,p.payload_json AS encoded_payload "
                "FROM poll_captures p LEFT JOIN poll_capture_inputs k ON k.capture_id=p.capture_id "
                "WHERE p.status IN ('PENDING','RUNNING') AND k.capture_id IS NULL "
                "LIMIT ?",(limit,)).fetchall()
        values = [(r['capture_id'],r['binding_id'],r['unit'],capture_input_key(
            self.repo.content.decode(json.loads(r['encoded_payload'])),r['unit'])) for r in rows]
        if values:
            with self.repo.transaction() as db:
                db.executemany('INSERT OR IGNORE INTO poll_capture_inputs VALUES(?,?,?,?,NULL)',values)
        return len(values)

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
            if status == 'FAILED':
                db.execute("UPDATE poll_captures SET status='FAILED',last_error=?,completed_at=? "
                           "WHERE capture_id IN (SELECT capture_id FROM poll_capture_inputs WHERE reused_from=?) "
                           "AND status='PENDING'", (type(error).__name__,utc_now().isoformat(),capture_id))

    def accept_and_claim(self, bus, prepared, token, distribution, stopping, continue_capture=False):
        """Commit a bounded piece and acquire the next lease in one writer visit."""
        with self.repo.connection_scope():
            self.accept_piece(bus, prepared, token, distribution)
            if stopping():
                return None
            if continue_capture and prepared[4].acquisition_metadata.get('intake_input_key'):
                with self.repo._connect() as db:
                    fresh_waiting = db.execute("SELECT 1 FROM poll_capture_priority f JOIN poll_captures p "
                        "USING(capture_id) WHERE f.fresh_remaining>0 AND p.status='PENDING' "
                        "AND p.not_before<=? AND p.capture_id!=? LIMIT 1",
                        (utc_now().isoformat(),prepared[0]['capture_id'])).fetchone()
                following = None if fresh_waiting else self.claim(prepared[0]['capture_id'])
                if following:
                    return following
            return self.claim()

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
            db.execute('DELETE FROM poll_input_verdicts WHERE input_key IN (SELECT input_key '
                       'FROM poll_input_verdicts WHERE completed_at<? ORDER BY completed_at LIMIT ?)',
                       (cutoff, limit))
            db.execute('DELETE FROM poll_capture_priority WHERE capture_id IN ('
                       'SELECT f.capture_id FROM poll_capture_priority f LEFT JOIN poll_captures p '
                       "USING(capture_id) WHERE p.capture_id IS NULL OR p.status IN ('COMPLETED','CANCELLED') LIMIT ?)",
                       (limit,))
            db.execute("DELETE FROM poll_intake_reuse WHERE input_hash IN (SELECT input_hash "
                       "FROM poll_intake_reuse WHERE valid_until<? ORDER BY valid_until LIMIT ?)",
                       ((now or utc_now()).isoformat(), limit))
            captures = db.execute(
                "DELETE FROM poll_captures WHERE capture_id IN (SELECT p.capture_id "
                "FROM poll_captures p WHERE p.status='COMPLETED' AND p.completed_at<? AND "
                "NOT EXISTS(SELECT 1 FROM poll_capture_inputs k JOIN poll_captures child ON child.capture_id=k.capture_id "
                "WHERE k.reused_from=p.capture_id AND child.status IN ('PENDING','RUNNING')) AND "
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
        # Keep the existing storage stages occupied while another capture waits
        # for its writer turn: two reads + two preparations + one commit.
        # These are pipeline tickets, not extra read/browser/DB concurrency.
        self.tasks = [None] * 5
        self.wake = asyncio.Event()
        self.stopping = False
        self._maintenance_at = 0.0

    def start(self):
        if any(task is None or task.done() for task in self.tasks):
            from .collection_override import REQUEST_GATE

            token = REQUEST_GATE.set(None)
            try:
                # Persisted intake is independent of the acquisition session.
                # Read/prepare slots and the single writer still bound actual work.
                for index, task in enumerate(self.tasks):
                    if task is None or task.done():
                        if task is not None and not task.cancelled():
                            task.result()
                        self.tasks[index] = asyncio.create_task(self.run())
                self.task = self.tasks[0]
            finally:
                REQUEST_GATE.reset(token)
        self.wake.set()

    async def run(self):
        claim = None
        previous_capture, burst = None, 0
        while not self.stopping:
            if claim is None:
                if await self.bus.storage.read(self.journal.ready):
                    claim = await self.bus.storage.call(self.journal.claim, _lane_priority=0)
            if not claim:
                import time

                # History retirement is an independent maintenance job. An
                # empty intake queue is not evidence the realtime writer is idle.
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 0.25)
                except TimeoutError:
                    pass
                continue
            capture_id, token = claim
            burst = burst+1 if previous_capture == capture_id else 1
            previous_capture = capture_id
            try:
                row = await self.bus.storage.read(self.journal.load, capture_id)
                prepared = await self.bus.storage.prepare(self.journal.prepare_piece, row)
                claim = await self.bus.storage.call(
                    self.journal.accept_and_claim, self.bus, prepared, token, self.distribution,
                    lambda: self.stopping,
                    continue_capture=burst<4,
                    _lane_priority=0 if prepared[4].messages and row['intake_position'] < row['decoded_payload'].get('fresh_count', 0) else 1,
                )
            except asyncio.CancelledError:
                # Claim expires on process loss; a drained commit remains durable.
                raise
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "capture intake failed capture=%s", capture_id
                )
                await self.bus.storage.call(self.journal.retry, capture_id, token, exc)
                claim = None
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
        tasks = [task for task in self.tasks if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
