"""Bounded candidates and independently committed cold-hour/resource retirement."""

import json
from datetime import datetime, timedelta
import sqlite3
import time

from ..schema import utc_now, canonical_json
from ..cadence import cadence


def _retire_hour(store, cutoff):
    deadline = time.monotonic() + .5
    archive = None
    cycles = []
    with store.connect() as db:
        db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        first = db.execute(
            "SELECT binding_id,unit,dispatch_at FROM cycles WHERE dispatch_at<? "
            "AND status NOT IN ('SCHEDULE','DISPATCHED') ORDER BY dispatch_at LIMIT 1",
            (cutoff,),
        ).fetchone()
        if first:
            binding, unit, at = first
            start = datetime.fromisoformat(at).replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1)
            known = db.execute(
                "SELECT 1 FROM unit_hours WHERE binding_id=? AND unit=? AND hour=?",
                (binding, unit, start.isoformat()),
            ).fetchone()
            samples = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM cycles WHERE binding_id=? AND unit=? "
                    "AND dispatch_at>=? AND dispatch_at<? AND status NOT IN ('SCHEDULE','DISPATCHED') ORDER BY dispatch_at",
                    (binding, unit, start.isoformat(), end.isoformat()),
                )
            ]
            if not known:
                schedules = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM cycles WHERE status='SCHEDULE' AND binding_id=? "
                        "AND unit=? AND dispatch_at=(SELECT max(dispatch_at) FROM cycles "
                        "WHERE status='SCHEDULE' AND binding_id=? AND unit=? AND dispatch_at<=?) "
                        "UNION ALL SELECT * FROM cycles WHERE status='SCHEDULE' AND binding_id=? "
                        "AND unit=? AND dispatch_at>? AND dispatch_at<? ORDER BY dispatch_at",
                        (binding, unit, binding, unit, start.isoformat(),
                         binding, unit, start.isoformat(), end.isoformat()),
                    )
                ]
                physical = {
                    r[0]: json.loads(r[1])
                    for r in db.execute(
                        "SELECT run_id,requests_json FROM physical_cycles "
                        "WHERE run_id IN (SELECT value FROM json_each(?))",
                        (
                            canonical_json(
                                [json.loads(r["data_json"]).get("physical_run_id") for r in samples]
                            ),
                        ),
                    )
                }
                receipts = []
                for row in samples:
                    row.update(json.loads(row.pop("data_json")))
                    row["requests"] = physical.get(
                        row.get("physical_run_id"), row.get("requests", [])
                    )
                    if row.get("list_validated"):
                        winner = next(
                            (
                                r
                                for r in row["requests"]
                                if r["physical_request_id"] == row.get("winning_request_id")
                            ),
                            None,
                        )
                        if winner:
                            receipts.append(
                                {
                                    **row,
                                    "observed_at": row["listing_received_at"],
                                    "request_started_at": winner["request_started_at"],
                                    "committed_at": row["finished_at"],
                                }
                            )
                stats = cadence(schedules + samples, receipts, start, end)
                archive = (binding, unit, start.isoformat(), canonical_json(stats))
            cycles = [r["run_id"] for r in samples[:512]]
    if not first:
        return None
    with store.connect(write=True) as db:
        if archive:
            db.execute("INSERT OR IGNORE INTO unit_hours VALUES(?,?,?,?)", archive)
        db.executemany("DELETE FROM cycles WHERE run_id=? AND status!='DISPATCHED'", [(v,) for v in cycles])
    return {'binding': binding, 'unit': unit, 'hour': start.isoformat(), 'cycles': len(cycles)}


def _physical(store, cutoff):
    cursor = store.get_meta('physical_exit_cursor', ['', ''])
    with store.connect() as db:
        rows = db.execute(
            'SELECT finished_at,run_id FROM physical_cycles WHERE finished_at<? '
            'AND (finished_at,run_id)>(?,?) ORDER BY finished_at,run_id LIMIT 512',
            (cutoff, *cursor)).fetchall()
    deleted = 0
    with store.connect(write=True) as db:
        for at, run in rows:
            # Recheck both live references inside the same short writer transaction.
            used = db.execute('SELECT 1 FROM cycles WHERE run_id>=? AND run_id<? LIMIT 1',
                              (run+':', run+';')).fetchone()
            pending = db.execute("SELECT 1 FROM captures WHERE run_id=? AND status!='ACCEPTED' LIMIT 1",
                                 (run,)).fetchone()
            if not used and not pending:
                deleted += db.execute('DELETE FROM physical_cycles WHERE run_id=? AND finished_at<?',
                                      (run, cutoff)).rowcount
        db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('physical_exit_cursor', canonical_json(list(rows[-1]) if rows else ['', ''])))
    return {'scanned': len(rows), 'deleted': deleted}


def _small(store, now, cutoff):
    """Independent of hourly archive and physical orphan scans."""
    with store.connect(write=True) as db:
        counts = {}
        for name, sql, args in (
            ('captures', "DELETE FROM captures WHERE capture_id IN (SELECT capture_id FROM captures "
             "WHERE observed_at<? AND status='ACCEPTED' ORDER BY observed_at LIMIT 128)", (cutoff,)),
            ('inputs', 'DELETE FROM inputs WHERE input_key IN (SELECT input_key FROM inputs '
             'WHERE observed_at<? ORDER BY observed_at LIMIT 512)', (cutoff,)),
            ('unit_hours', 'DELETE FROM unit_hours WHERE rowid IN (SELECT rowid FROM unit_hours '
             'WHERE hour<? ORDER BY hour LIMIT 64)', ((now-timedelta(days=7)).isoformat(),)),
        ):
            counts[name] = db.execute(sql, args).rowcount
    return counts


def _schedules(store, now):
    cursor = store.get_meta('schedule_exit_cursor', ['', '', ''])
    cutoff = (now-timedelta(days=7)).isoformat()
    with store.connect() as db:
        rows = db.execute("SELECT run_id,binding_id,unit,dispatch_at FROM cycles INDEXED BY cycles_schedule "
                          "WHERE status='SCHEDULE' AND (binding_id,unit,dispatch_at)>(?,?,?) "
                          "ORDER BY binding_id,unit,dispatch_at LIMIT 128", cursor).fetchall()
    deleted = 0
    with store.connect(write=True) as db:
        for run, binding, unit, at in rows:
            if at >= cutoff:
                continue
            successor = db.execute("SELECT 1 FROM cycles WHERE status='SCHEDULE' AND binding_id=? AND unit=? "
                                   "AND dispatch_at>? AND dispatch_at<? LIMIT 1",
                                   (binding, unit, at, cutoff)).fetchone()
            if successor:
                deleted += db.execute("DELETE FROM cycles WHERE run_id=? AND status='SCHEDULE'", (run,)).rowcount
        db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('schedule_exit_cursor', canonical_json(list(rows[-1])[1:] if rows else ['', '', ''])))
    return {'scanned': len(rows), 'deleted': deleted}


def _checkpoints(store):
    cursor = store.get_meta('checkpoint_exit_cursor', '')
    with store.connect() as db:
        rows = [r[0] for r in db.execute('SELECT identity FROM physical_checkpoints '
                                        'WHERE identity>? ORDER BY identity LIMIT 16', (cursor,))]
    deleted = 0
    with store.connect(write=True) as db:
        for identity in rows:
            used = db.execute("SELECT 1 FROM units WHERE json_extract(state,'$.checkpoint_ref')=? LIMIT 1",
                              (identity,)).fetchone()
            if not used:
                deleted += db.execute('DELETE FROM physical_checkpoints WHERE identity=?', (identity,)).rowcount
        db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('checkpoint_exit_cursor', canonical_json(rows[-1] if rows else '')))
    return {'scanned': len(rows), 'deleted': deleted}


def maintain(store, *, budget=2, hours=128):
    now = utc_now()
    cutoff = (now-timedelta(hours=48)).replace(minute=0, second=0, microsecond=0).isoformat()
    deadline = time.monotonic()+budget
    report = {'at': now.isoformat(), 'hours': [], 'errors': [], 'stages': {}}
    try:
        for _ in range(hours):
            if time.monotonic() >= deadline:
                break
            result = _retire_hour(store, cutoff)
            if result is None:
                break
            report['hours'].append(result)
    except sqlite3.Error as exc:
        report['errors'].append({'stage': 'hours', 'error': str(exc)})
    # One expensive/blocked stage never rolls back the already committed exits.
    for name, function in (
        ('small', lambda: _small(store, now, cutoff)),
        ('physical', lambda: _physical(store, cutoff)),
        ('schedules', lambda: _schedules(store, now)),
        ('checkpoints', lambda: _checkpoints(store)),
    ):
        try:
            report['stages'][name] = function()
        except sqlite3.Error as exc:
            report['errors'].append({'stage': name, 'error': str(exc)})
    report['elapsed_seconds'] = time.monotonic()-(deadline-budget)
    store.meta('retention', report)
    return report
