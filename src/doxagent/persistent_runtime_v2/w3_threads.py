"""Durable W3 phase records, public templates and Case-owned threads."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

from doxagent.w3_timing import observed, span
from doxagent.control_io import short_write

from .fencing import assert_write_lease
from .journal import encode


class W3ThreadRegistry:
    def __init__(self, journal):
        self.journal = journal
        with journal.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runtime_v2_w3_threads (
                    record_id TEXT PRIMARY KEY, thread_id TEXT UNIQUE, ticker TEXT NOT NULL,
                    kind TEXT NOT NULL, template_key TEXT, compatibility_key TEXT NOT NULL,
                    parent_id TEXT, workspace TEXT NOT NULL, status TEXT NOT NULL,
                    last_received_at TEXT, expires_at TEXT, owner_case_id TEXT,
                    lease_token INTEGER NOT NULL DEFAULT 0, lease_until TEXT, error TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS w3_template_key
                    ON runtime_v2_w3_threads(template_key) WHERE kind='TEMPLATE';
                CREATE INDEX IF NOT EXISTS w3_thread_expiry
                    ON runtime_v2_w3_threads(ticker,status,expires_at);
                CREATE INDEX IF NOT EXISTS w3_legacy_invocation_thread
                    ON runtime_values(json_extract(payload,'$.job.thread_id'))
                    WHERE namespace='worker_invocations'
                      AND json_extract(payload,'$.node')='persistent_runtime_w3';
                CREATE TABLE IF NOT EXISTS runtime_v2_w3_thread_links (
                    record_id TEXT NOT NULL, ticker TEXT NOT NULL, link_kind TEXT NOT NULL,
                    subject_key TEXT NOT NULL, source_case_id TEXT NOT NULL,
                    PRIMARY KEY(record_id,link_kind,subject_key));
                CREATE INDEX IF NOT EXISTS w3_thread_subject
                    ON runtime_v2_w3_thread_links(ticker,link_kind,subject_key);
                CREATE TABLE IF NOT EXISTS runtime_v2_w3_stages (
                    case_id TEXT NOT NULL, phase TEXT NOT NULL, thread_id TEXT,
                    status TEXT NOT NULL, result_json TEXT, input_refs TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0, worker_job_id TEXT,
                    accepted_at TEXT NOT NULL, committed_at TEXT, disposition TEXT, error TEXT,
                    PRIMARY KEY(case_id,phase));
            """)

    def stage(self, case_id, phase):
        with self.journal.transaction(write=False) as db:
            row = db.execute(
                "SELECT * FROM runtime_v2_w3_stages WHERE case_id=? AND phase=?", (case_id, phase)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        for key in ("result_json", "input_refs"):
            value[key] = json.loads(value[key]) if value[key] else None
        return value

    @observed("stage.begin")
    def begin_stage(self, case_id, phase, *, thread_id=None, input_refs=None):
        existing = self.stage(case_id, phase)
        if existing is not None:
            return existing
        with self.journal.transaction() as db:
            assert_write_lease(db)
            db.execute(
                "INSERT OR IGNORE INTO runtime_v2_w3_stages"
                "(case_id,phase,thread_id,status,input_refs,accepted_at) "
                "VALUES(?,?,?,'PENDING',?,?)",
                (
                    case_id,
                    phase,
                    thread_id,
                    encode(input_refs) if input_refs is not None else None,
                    self.journal.clock().isoformat(),
                ),
            )
        result = self.stage(case_id, phase)
        with span("stage.accepted", accepted_at=result["accepted_at"], status=result["status"]):
            pass
        return result

    @short_write()
    def save_result(self, case_id, phase, result, *, thread_id, worker_job_id=None):
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        prepared = encode(payload)
        with self.journal.transaction() as db:
            self.save_result_in_transaction(db,case_id,phase,payload,prepared,
                                            thread_id=thread_id,worker_job_id=worker_job_id)

    def save_result_in_transaction(self,db,case_id,phase,payload,prepared,*,thread_id,worker_job_id=None):
        assert_write_lease(db)
        old = db.execute(
            "SELECT result_json FROM runtime_v2_w3_stages WHERE case_id=? AND phase=?",
            (case_id, phase),
        ).fetchone()
        if not old:
            raise ValueError("phase must be registered before saving its result")
        if old[0] and json.loads(old[0]) != payload:
            raise ValueError("a saved W3 phase result is immutable")
        db.execute(
            "UPDATE runtime_v2_w3_stages SET result_json=?,thread_id=?,"
            "worker_job_id=?,status='RESULT_SAVED' WHERE case_id=? AND phase=? "
            "AND status!='COMMITTED'",
            (prepared, thread_id, worker_job_id, case_id, phase),
        )

    def commit_stage(self, db, case_id, phase, disposition=None):
        assert_write_lease(db)
        row = db.execute("SELECT worker_job_id FROM runtime_v2_w3_stages WHERE case_id=? AND phase=?",
                         (case_id, phase)).fetchone()
        if row and row[0]:
            # Publish cleanup eligibility in the same transaction as COMMITTED.
            db.execute("INSERT OR IGNORE INTO runtime_values VALUES('w3_ack_pending',?,?)",
                       (row[0], encode({'case_id': case_id})))
        db.execute(
            "UPDATE runtime_v2_w3_stages SET status='COMMITTED',committed_at=?,"
            "disposition=?,error=NULL WHERE case_id=? AND phase=?",
            (self.journal.clock().isoformat(), disposition, case_id, phase),
        )

    def template(self, ticker, key, compatibility):
        # A published compatibility key is immutable. Normal periodic preparation
        # must not join the business writer just to rediscover a READY template.
        with self.journal.transaction(write=False) as db:
            ready = db.execute(
                "SELECT * FROM runtime_v2_w3_threads WHERE kind='TEMPLATE' AND template_key=? AND status='READY'",
                (key,),
            ).fetchone()
        if ready is not None:
            return dict(ready), False
        now = self.journal.clock()
        with self.journal.transaction() as db:
            assert_write_lease(db)
            db.execute(
                "INSERT OR IGNORE INTO runtime_v2_w3_threads"
                "(record_id,ticker,kind,template_key,compatibility_key,workspace,status) "
                "VALUES(?,?,'TEMPLATE',?,?,?,'CREATING')",
                (uuid4().hex, ticker, key, compatibility, "w3-template-" + key[:24]),
            )
            row = dict(
                db.execute(
                    "SELECT * FROM runtime_v2_w3_threads WHERE kind='TEMPLATE' AND template_key=?", (key,)
                ).fetchone()
            )
            acquired = row["status"] != "READY" and (
                not row["lease_until"] or row["lease_until"] <= now.isoformat()
            )
            if acquired:
                db.execute(
                    "UPDATE runtime_v2_w3_threads SET lease_until=?,lease_token=lease_token+1 "
                    "WHERE record_id=?",
                    ((now + timedelta(minutes=15)).isoformat(), row["record_id"]),
                )
                row = dict(
                    db.execute(
                        "SELECT * FROM runtime_v2_w3_threads WHERE record_id=?", (row["record_id"],)
                    ).fetchone()
                )
        return row, acquired

    def ready_template(self, record_id, thread_id, token):
        with self.journal.transaction() as db:
            assert_write_lease(db)
            changed = db.execute(
                "UPDATE runtime_v2_w3_threads SET thread_id=?,status='READY',lease_until=NULL "
                "WHERE record_id=? AND lease_token=? AND kind='TEMPLATE'",
                (thread_id, record_id, token),
            ).rowcount
            if not changed:
                raise ValueError("template initialization lease was lost")

    def create_case(self, *, ticker, case_id, compatibility, parent_id, accepted_at=None, stage_key="trade_decision"):
        now = self.journal.clock()
        received = accepted_at or now
        record_id = uuid4().hex
        with self.journal.transaction() as db:
            assert_write_lease(db)
            stage = db.execute("SELECT input_refs FROM runtime_v2_w3_stages WHERE case_id=? AND phase=?", (case_id,stage_key)).fetchone()
            if stage is None:
                raise ValueError('Trade stage must exist before binding a Case thread')
            refs = json.loads(stage[0]) if stage and stage[0] else {}
            if refs.get('record_id'):
                bound = db.execute('SELECT * FROM runtime_v2_w3_threads WHERE record_id=?', (refs['record_id'],)).fetchone()
                if bound is None or bound['owner_case_id'] not in {None, case_id}:
                    raise ValueError('original Case thread identity is unavailable')
                return dict(bound)
            db.execute(
                "INSERT INTO runtime_v2_w3_threads(record_id,ticker,kind,compatibility_key,"
                "parent_id,workspace,status,last_received_at,expires_at,owner_case_id,"
                "lease_token,lease_until) VALUES(?,?,'CASE',?,?,?,'ACTIVE',?,?,?,1,?)",
                (
                    record_id,
                    ticker,
                    compatibility,
                    parent_id,
                    "w3-case-" + record_id,
                    received.isoformat(),
                    (received + timedelta(hours=48)).isoformat(),
                    case_id,
                    (now + timedelta(minutes=20)).isoformat(),
                ),
            )
            refs.update(record_id=record_id, operation_key='w3-fork:' + record_id)
            db.execute("UPDATE runtime_v2_w3_stages SET input_refs=? WHERE case_id=? AND phase=?", (encode(refs),case_id,stage_key))
            return dict(
                db.execute(
                    "SELECT * FROM runtime_v2_w3_threads WHERE record_id=?", (record_id,)
                ).fetchone()
            )

    def set_thread(self, record_id, thread_id):
        with self.journal.transaction() as db:
            assert_write_lease(db)
            db.execute(
                "UPDATE runtime_v2_w3_threads SET thread_id=? WHERE record_id=?",
                (thread_id, record_id),
            )

    def release(self, case_id):
        with self.journal.transaction() as db:
            assert_write_lease(db)
            db.execute(
                "UPDATE runtime_v2_w3_threads SET owner_case_id=NULL,lease_until=NULL "
                "WHERE owner_case_id=?",
                (case_id,),
            )

    def bound(self, case_id, stage_key="trade_decision"):
        stage = self.stage(case_id, stage_key) or {}
        rid = (stage.get('input_refs') or {}).get('record_id')
        with self.journal.transaction(write=False) as db:
            row = db.execute('SELECT * FROM runtime_v2_w3_threads WHERE record_id=?', (rid,)).fetchone()
        return dict(row) if row else None

    def owned(self, case_id):
        with self.journal.transaction(write=False) as db:
            row = db.execute(
                "SELECT * FROM runtime_v2_w3_threads WHERE owner_case_id=?", (case_id,)
            ).fetchone()
        return dict(row) if row else None

    def expired(self):
        with self.journal.transaction(write=False) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM runtime_v2_w3_threads WHERE kind IN ('CASE','LEGACY_CASE') "
                    "AND status IN ('ACTIVE','DELETING') "
                    "AND expires_at<=? AND owner_case_id IS NULL",
                    (self.journal.clock().isoformat(),),
                )
            ]
