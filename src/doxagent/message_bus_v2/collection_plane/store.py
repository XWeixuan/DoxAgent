"""Small acquisition owner. No business tables, native codec or CDC triggers."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from functools import wraps
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path

from ..schema import canonical_json, new_id, utc_now


def path_for(bus_path):
    return Path(bus_path).parent / "acquisition.sqlite3"


def required_unit(source_id, unit):
    return (not unit.startswith('TAIL:') or unit=='TAIL:YAHOO_TIME' or unit.startswith('TAIL:IR:')
            or source_id in {'reuters_site_search','globenewswire_search','calcalist_search'})


def durable_write(function):
    @wraps(function)
    def run(*args, **kwargs):
        for attempt in range(4):
            try:
                return function(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                busy = (getattr(exc, "sqlite_errorcode", 0) & 255) in {5, 6} or "locked" in str(
                    exc
                ).lower()
                if not busy or attempt == 3:
                    raise
                time.sleep(0.025 * 2**attempt)

    return run


class AcquisitionStore:
    def __init__(self, path, *, initialize=False):
        self.path = Path(path)
        self._writer = threading.RLock()
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS units(
                        binding_id TEXT,unit TEXT,source_id TEXT,ticker TEXT,required INTEGER,
                        generation TEXT,state TEXT NOT NULL,input_key TEXT,head_json TEXT NOT NULL DEFAULT '{}',
                        PRIMARY KEY(binding_id,unit));
                    CREATE TABLE IF NOT EXISTS cycles(
                        run_id TEXT PRIMARY KEY,binding_id TEXT,source_id TEXT,ticker TEXT,unit TEXT,
                        due_at TEXT,dispatch_at TEXT,interval_seconds INTEGER,source_version INTEGER,
                        binding_version INTEGER,status TEXT,reason TEXT,finished_at TEXT,
                        data_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS cycles_time ON cycles(dispatch_at,run_id);
                    CREATE INDEX IF NOT EXISTS cycles_ticker_time ON cycles(ticker,dispatch_at);
                    CREATE INDEX IF NOT EXISTS cycles_schedule ON cycles(status,binding_id,unit,dispatch_at);
                    CREATE INDEX IF NOT EXISTS cycles_schedule_ticker ON cycles(ticker,binding_id,unit,dispatch_at) WHERE status='SCHEDULE';
                    CREATE TABLE IF NOT EXISTS physical_cycles(run_id TEXT PRIMARY KEY,finished_at TEXT,requests_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS physical_cycles_time ON physical_cycles(finished_at,run_id);
                    CREATE INDEX IF NOT EXISTS cycles_unit_time ON cycles(binding_id,unit,dispatch_at);
                    CREATE TABLE IF NOT EXISTS unit_hours(binding_id TEXT,unit TEXT,hour TEXT,data_json TEXT NOT NULL,
                        PRIMARY KEY(binding_id,unit,hour));
                    CREATE INDEX IF NOT EXISTS unit_hours_time ON unit_hours(hour);
                    CREATE TABLE IF NOT EXISTS physical_checkpoints(identity TEXT PRIMARY KEY,checkpoint TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS captures(
                        capture_id TEXT PRIMARY KEY,run_id TEXT,binding_id TEXT,source_id TEXT,
                        ticker TEXT,unit TEXT,observed_at TEXT,committed_at TEXT,
                        request_started_at TEXT,source_version INTEGER,binding_version INTEGER,
                        targets_json TEXT,status TEXT,payload TEXT NOT NULL,accepted_at TEXT,
                        not_before TEXT,last_error TEXT);
                    CREATE INDEX IF NOT EXISTS captures_pending ON captures(status,not_before,observed_at);
                    CREATE INDEX IF NOT EXISTS captures_source_ready ON captures(status,source_id,not_before,observed_at);
                    CREATE INDEX IF NOT EXISTS captures_time ON captures(observed_at,capture_id);
                    CREATE TABLE IF NOT EXISTS inputs(input_key TEXT PRIMARY KEY,observed_at TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS inputs_time ON inputs(observed_at);
                    CREATE TABLE IF NOT EXISTS first_seen(identity TEXT PRIMARY KEY,observed_at TEXT NOT NULL);
                """)
                if "head_json" not in {r[1] for r in db.execute("PRAGMA table_info(units)")}:
                    db.execute("ALTER TABLE units ADD COLUMN head_json TEXT NOT NULL DEFAULT '{}'")
                db.execute("CREATE INDEX IF NOT EXISTS units_checkpoint ON units(json_extract(state,'$.checkpoint_ref'))")

    @contextmanager
    def connect(self, *, write=False):
        # SQLite has one physical writer. Queue only these short durable
        # commits, never a request permit, browser visit or business admission.
        with self._writer if write else nullcontext():
            db = sqlite3.connect(
                self.path if write else self.path.resolve().as_uri() + "?mode=ro",
                uri=not write,
                timeout=1.0 if write else 0.2,
                isolation_level=None,
            )
            try:
                db.row_factory = sqlite3.Row
                if write:
                    db.execute("PRAGMA synchronous=NORMAL")
                    db.execute("BEGIN IMMEDIATE")
                yield db
                if write:
                    db.commit()
            except BaseException:
                if write:
                    db.rollback()
                raise
            finally:
                db.close()

    def get_meta(self, key, default=None):
        with self.connect() as db:
            r = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    @durable_write
    def meta(self, key, value):
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                "WHERE meta.value<>excluded.value",
                (key, canonical_json(value)),
            )

    def states(self):
        with self.connect() as db:
            result={
                (r["binding_id"], r["unit"]): dict(r) | {"state": json.loads(r["state"])}
                for r in db.execute("SELECT * FROM units")
            }
            checkpoints={r[0]:json.loads(r[1]) for r in db.execute("SELECT identity,checkpoint FROM physical_checkpoints "
                "WHERE identity IN (SELECT json_extract(state,'$.checkpoint_ref') FROM units)")}
        for row in result.values():
            ref=row['state'].pop('checkpoint_ref',None)
            if ref:row['state']['checkpoint']=checkpoints.get(ref,{})
        return result

    @durable_write
    def disable_unit(self, binding_id, unit, *, at=None):
        at = at or utc_now()
        with self.connect(write=True) as db:
            row = db.execute('SELECT source_id,ticker,state FROM units WHERE binding_id=? AND unit=? '
                             'AND required>=0', (binding_id, unit)).fetchone()
            if row is None:
                return
            state = json.loads(row['state'])
            epoch = state.get('target_due_at') or at.isoformat()
            db.execute('INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('schedule:'+new_id('epoch'),binding_id,row['source_id'],row['ticker'],unit,
                 epoch,at.isoformat(),state.get('schedule_interval_seconds',60),None,None,
                 'SCHEDULE','DISABLED',None,canonical_json({'epoch_at':epoch})))
            db.execute(
                "UPDATE units SET required=-1 WHERE binding_id=? AND unit=?", (binding_id, unit)
            )

    @durable_write
    def save_unit(
        self, source, binding, unit, state, generation, *, required=True, input_key=None, db=None
    ):
        head = {
            k: state.get(k)
            for k in (
                "status",
                "last_capture_at",
                "last_success_at",
                "last_attempt_at",
                "last_latency_ms",
                "last_error_code",
                "next_dispatch_at",
            )
        }
        if unit.startswith("IR:"):
            spec = next(
                (
                    s
                    for s in source.default_parameters.get("endpoints", [])
                    if "IR:" + s["endpoint_id"] == unit
                ),
                None,
            )
            if spec:
                ir = state.get("checkpoint", {}).get("ir_state", {})
                head["ir_endpoint"] = {
                    **spec,
                    **ir,
                    "status": "failed"
                    if state.get("status") == "failed"
                    else ir.get("status", "never_polled"),
                    "last_attempt_at": state.get("last_attempt_at"),
                    "next_due_at": state.get("next_dispatch_at"),
                    "latency_ms": state.get("last_latency_ms") or 0,
                    "last_error_message": state.get("last_error_code"),
                }
                head["ir_endpoint"].pop("checkpoint", None)
        checkpoint=None
        if source.acquisition_mode.value=='by_distribution' and 'checkpoint' in state:
            checkpoint=(source.source_id+':'+unit+':'+generation,canonical_json(state['checkpoint']))
            state={k:v for k,v in state.items() if k!='checkpoint'}|{'checkpoint_ref':checkpoint[0]}
        args = (
            binding.binding_id,
            unit,
            source.source_id,
            binding.ticker,
            int(required),
            generation,
            canonical_json(state),
            input_key,
            canonical_json(head),
        )
        sql = """INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(binding_id,unit) DO UPDATE SET
               required=excluded.required,generation=excluded.generation,state=excluded.state,
               input_key=coalesce(excluded.input_key,units.input_key),head_json=excluded.head_json"""
        if db is not None:
            if checkpoint:db.execute('INSERT INTO physical_checkpoints VALUES(?,?) ON CONFLICT(identity) DO UPDATE SET '
                'checkpoint=excluded.checkpoint WHERE checkpoint!=excluded.checkpoint',checkpoint)
            db.execute(sql, args)
        else:
            with self.connect(write=True) as conn:
                if checkpoint:conn.execute('INSERT INTO physical_checkpoints VALUES(?,?) ON CONFLICT(identity) DO UPDATE SET '
                    'checkpoint=excluded.checkpoint WHERE checkpoint!=excluded.checkpoint',checkpoint)
                conn.execute(sql, args)

    @durable_write
    def schedule(self, source, binding, unit, epoch, enabled, at):
        identity = "schedule:" + new_id("epoch")
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    binding.binding_id,
                    source.source_id,
                    binding.ticker,
                    unit,
                    epoch.isoformat(),
                    at.isoformat(),
                    binding.polling.target_interval_seconds,
                    source.version,
                    binding.version,
                    "SCHEDULE",
                    "ENABLED" if enabled else "DISABLED",
                    None,
                    canonical_json({"epoch_at": epoch.isoformat(), 'required':required_unit(source.source_id,unit)}),
                ),
            )

    @durable_write
    def finish(
        self,
        source,
        targets,
        unit,
        state,
        generation,
        *,
        run_id,
        due,
        dispatched,
        requests,
        valid,
        reason=None,
        capture_payloads=(),
    ):
        now = utc_now().isoformat()
        listing = [
            r
            for r in requests
            if r.get("role", "listing") == "listing"
            and r.get('list_validated') and r.get('proof_basis')=='TRANSPORT_SEND'
            and r.get("response_received_at")
            and not r.get("error_type")
            and (r.get("status_code") is None or 200 <= r["status_code"] < 400)
        ]
        winning = listing[-1] if valid and listing else None
        proof = {
            "physical_run_id": run_id,
            "required": required_unit(source.source_id,unit),
            "list_validated": bool(valid and winning),
            "winning_request_id": winning.get("physical_request_id") if winning else None,
            "listing_received_at": winning.get("response_received_at") if winning else None,
        }
        with self.connect(write=True) as db:
            db.execute('INSERT INTO physical_cycles VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET '
                'finished_at=excluded.finished_at,requests_json=excluded.requests_json',(run_id,now,canonical_json(requests)))
            for target in targets:
                previous = db.execute(
                    "SELECT data_json FROM cycles WHERE run_id=?",
                    (run_id + ":" + target.binding_id,),
                ).fetchone()
                prior_proof = json.loads(previous[0]) if previous else {}
                target_proof = {**proof, "terminal_error": reason, "attempt_finished": True}
                if not target_proof["list_validated"] and prior_proof.get("list_validated"):
                    target_proof.update(
                        {
                            k: prior_proof[k]
                            for k in ("list_validated", "winning_request_id", "listing_received_at")
                        }
                    )
                values = (
                    run_id + ":" + target.binding_id,
                    target.binding_id,
                    source.source_id,
                    target.ticker,
                    unit,
                    due.isoformat(),
                    dispatched.isoformat(),
                    target.polling.target_interval_seconds,
                    source.version,
                    target.version,
                    "CAPTURED"
                    if target_proof["list_validated"]
                    else "DEFERRED"
                    if reason and ("deferred" in reason.lower() or "recovery" in reason.lower())
                    else "FAILED"
                    if reason
                    else "UNPROVEN",
                    reason,
                    now,
                    canonical_json(target_proof),
                )
                db.execute(
                    "INSERT OR REPLACE INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
                )
                prior = db.execute(
                    "SELECT state,generation,required FROM units WHERE binding_id=? AND unit=?",
                    (target.binding_id, unit),
                ).fetchone()
                if prior and (prior["generation"] != generation or prior["required"] < 0):
                    continue
                current = json.loads(prior[0]) if prior else {}
                if current.get('checkpoint_ref'):
                    cp=db.execute('SELECT checkpoint FROM physical_checkpoints WHERE identity=?',(current.pop('checkpoint_ref'),)).fetchone()
                    current['checkpoint']=json.loads(cp[0]) if cp else {}
                current.update(state)
                if target_proof["list_validated"]:
                    current.update(
                        last_capture_at=target_proof["listing_received_at"], last_success_at=now
                    )
                self.save_unit(
                    source, target, unit, current, generation, required=proof["required"], db=db
                )
            for payload, input_key, observed in capture_payloads:
                self._capture(
                    db, source, targets, unit, payload, input_key, observed, run_id, winning, now
                )

    @durable_write
    def start(self, source, targets, unit, run_id, due, dispatched, interval):
        with self.connect(write=True) as db:
            db.executemany(
                "INSERT OR IGNORE INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run_id + ":" + b.binding_id,
                        b.binding_id,
                        source.source_id,
                        b.ticker,
                        unit,
                        due.isoformat(),
                        dispatched.isoformat(),
                        interval,
                        source.version,
                        b.version,
                        "DISPATCHED",
                        None,
                        None,
                        canonical_json(
                            {"required": required_unit(source.source_id,unit), "list_validated": False}
                        ),
                    )
                    for b in targets
                ],
            )

    def _capture(
        self, db, source, targets, unit, payload, input_key, observed, run_id, winning, now
    ):
        if not payload["result"]["messages"]:
            return
        # One full business input is emitted once. A physical refresh is always
        # evidenced separately, including an unchanged/empty validated list.
        current = db.execute(
            "SELECT input_key FROM units WHERE binding_id=? AND unit=?",
            (targets[0].binding_id, unit),
        ).fetchone()
        if input_key and current and current[0] == input_key:
            return
        keys = payload.get("input_keys") or []
        if keys:
            known = set()
            for offset in range(0, len(keys), 300):
                batch = keys[offset : offset + 300]
                known.update(
                    r[0]
                    for r in db.execute(
                        "SELECT input_key FROM inputs WHERE input_key IN ("
                        + ",".join("?" for _ in batch)
                        + ")",
                        batch,
                    )
                )
            payload = {
                **payload,
                "result": {
                    **payload["result"],
                    "messages": [
                        m
                        for m, k in zip(payload["result"]["messages"], keys, strict=True)
                        if k not in known
                    ],
                },
            }
            if not payload["result"]["messages"]:
                return
            db.executemany(
                "INSERT OR IGNORE INTO inputs VALUES(?,?)", [(k, observed) for k in keys]
            )
        identity = new_id("capture")
        roster = [
            {"binding_id": b.binding_id, "ticker": b.ticker, "binding_version": b.version}
            for b in targets
        ]
        db.execute(
            "INSERT INTO captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,NULL)",
            (
                identity,
                run_id,
                targets[0].binding_id,
                source.source_id,
                targets[0].ticker,
                unit,
                observed,
                now,
                winning.get("request_started_at") if winning else None,
                source.version,
                targets[0].version,
                canonical_json(roster),
                "PENDING",
                canonical_json(payload),
                now,
            ),
        )
        db.executemany(
            "UPDATE units SET input_key=? WHERE binding_id=? AND unit=? AND generation=?",
            [(input_key, b.binding_id, unit, payload.get("generation", "")) for b in targets],
        )

    @durable_write
    def normalize_unknown(self, source_id, messages, observed):
        from ..schema import identity_key_for
        from datetime import datetime

        if not any(m.publication_time_basis == "UNKNOWN_FIRST_SEEN" for m in messages):
            return messages
        keys={source_id+':'+identity_key_for(source_id,m) for m in messages
              if m.publication_time_basis=='UNKNOWN_FIRST_SEEN'}
        with self.connect() as db:
            existing=dict(db.execute('SELECT identity,observed_at FROM first_seen WHERE identity IN '
                '(SELECT value FROM json_each(?))',(canonical_json(list(keys)),)))
        missing=keys-existing.keys()
        if missing:
            with self.connect(write=True) as db:
                db.executemany('INSERT OR IGNORE INTO first_seen VALUES(?,?)',[(k,observed) for k in missing])
                existing.update(db.execute('SELECT identity,observed_at FROM first_seen WHERE identity IN '
                    '(SELECT value FROM json_each(?))',(canonical_json(list(missing)),)))
        return [m.model_copy(update={'published_at':datetime.fromisoformat(existing[source_id+':'+identity_key_for(source_id,m)])})
            if m.publication_time_basis=='UNKNOWN_FIRST_SEEN' else m for m in messages]

    def health(self, *, now=None):
        from datetime import datetime
        instant=now or utc_now();stalled=[];unknown=[];required=0
        with self.connect() as db:
            rows=db.execute('SELECT source_id,binding_id,unit,state,head_json FROM units WHERE required=1').fetchall()
        for row in rows:
            required+=1;head=json.loads(row['head_json']);state=json.loads(row['state'])
            interval=state.get('schedule_interval_seconds',30)
            started=state.get('target_due_at')
            if started and instant<datetime.fromisoformat(started):continue
            captured=head.get('last_capture_at')
            if not captured:unknown.append(dict(source_id=row['source_id'],binding_id=row['binding_id'],unit=row['unit']))
            basis=captured or started
            if basis and (instant-datetime.fromisoformat(basis)).total_seconds()>max(90,3*interval):
                stalled.append(dict(source_id=row['source_id'],binding_id=row['binding_id'],unit=row['unit'],
                    last_fresh=captured,error=head.get('last_error_code')))
        return {'status':'degraded' if stalled else 'ok','required_units':required,'unproven_units':len(unknown),
            'stalled_units':stalled,'basis':'VALIDATED_TRANSPORT_LIST_PROGRESS'}

    def maintain(self):
        from .maintenance import maintain
        maintain(self)

    @durable_write
    def capture_page(self, source, targets, unit, payload, input_key, observed, run_id, requests):
        listing = [
            r
            for r in requests
            if r.get("role", "listing") == "listing" and r.get("response_received_at")
            and r.get('list_validated') and r.get('proof_basis')=='TRANSPORT_SEND'
        ]
        with self.connect(write=True) as db:
            winning = listing[-1] if listing else None
            if (
                winning
                and (winning.get("status_code") is None or 200 <= winning["status_code"] < 400)
                and not winning.get("error_type")
            ):
                proof = {
                    "physical_run_id": run_id,
                    "required": required_unit(source.source_id,unit),
                    "list_validated": True,
                    "winning_request_id": winning.get("physical_request_id"),
                    "listing_received_at": winning["response_received_at"],
                    "attempt_finished": False,
                }
                db.executemany(
                    "UPDATE cycles SET status='CAPTURED',data_json=? WHERE run_id=?",
                    [(canonical_json(proof), run_id + ":" + b.binding_id) for b in targets],
                )
            db.execute('INSERT INTO physical_cycles VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET '
                'finished_at=excluded.finished_at,requests_json=excluded.requests_json',
                (run_id,utc_now().isoformat(),canonical_json(requests)))
            self._capture(
                db,
                source,
                targets,
                unit,
                payload,
                input_key,
                observed,
                run_id,
                listing[-1] if listing else None,
                utc_now().isoformat(),
            )

    @durable_write
    def recover_previous_owner(self):
        with self.connect(write=True) as db:
            return db.execute(
                "UPDATE cycles SET status='ABANDONED_GENERATION',reason='PREVIOUS_COLLECTOR_EXIT',finished_at=? WHERE status='DISPATCHED'",
                (utc_now().isoformat(),),
            ).rowcount

    def pending(self, *, limit=8):
        # Round-robin publishers. A large older delta backlog cannot keep a new
        # source's head outside the global eight-row handoff window.
        with self.connect() as db:
            sources=[r[0] for r in db.execute("SELECT DISTINCT source_id FROM captures WHERE status='PENDING' ORDER BY source_id")]
            cursor=getattr(self,"_pending_source","")
            sources=[s for s in sources if s>cursor]+[s for s in sources if s<=cursor]
            rows=[]
            self._pending_round=getattr(self,"_pending_round",0)+1
            order="ASC" if self._pending_round%8==0 else "DESC"
            for source in sources:
                row=db.execute("SELECT * FROM captures WHERE status='PENDING' AND source_id=? "
                    "AND not_before<=? ORDER BY observed_at "+order+" LIMIT 1",(source,utc_now().isoformat())).fetchone()
                if row:
                    rows.append(dict(row));self._pending_source=source
                if len(rows)>=limit:break
            return rows

    @durable_write
    def acknowledge(self, capture_id):
        with self.connect(write=True) as db:
            db.execute(
                "UPDATE captures SET status='ACCEPTED',accepted_at=?,payload='{}' WHERE capture_id=?",
                (utc_now().isoformat(), capture_id),
            )

    @durable_write
    def retry(self, capture_id, exc):
        from datetime import timedelta

        with self.connect(write=True) as db:
            db.execute(
                "UPDATE captures SET not_before=?,last_error=? WHERE capture_id=?",
                ((utc_now() + timedelta(seconds=2)).isoformat(), type(exc).__name__, capture_id),
            )
