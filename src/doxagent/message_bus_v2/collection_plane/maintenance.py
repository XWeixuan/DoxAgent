"""Bounded candidates and independently committed cold-hour/resource retirement."""

import json
from datetime import datetime, timedelta
import sqlite3
import time

from ..schema import utc_now, canonical_json
from ..cadence import cadence


_HOUR_PAGE = 128
_WINDOW = "binding_id=? AND unit=? AND dispatch_at>=? AND dispatch_at<?"
_ACTIVE = ("(status='DISPATCHED' OR (status='CAPTURED' AND finished_at IS NULL "
           "AND coalesce(json_extract(data_json,'$.attempt_finished'),1)=0))")
_TERMINAL = "status!='SCHEDULE' AND NOT " + _ACTIVE
_SCAN_KEY = 'retention_hour_scan'
_SCAN_PAGES = 'retention_hour_scan:'


def _clear_hour_scan(db):
    db.execute('DELETE FROM meta WHERE key=? OR (key>=? AND key<?)',
               (_SCAN_KEY, _SCAN_PAGES, _SCAN_PAGES + '\uffff'))


def _stage_page(store, window, work, page):
    last = page[-1]
    pages = work['pages'] + 1
    progress = {
        'window': window, 'cursor': (last['dispatch_at'], last['run_id']), 'pages': pages,
    }
    with store.connect(write=True) as db:
        if not work['pages']:
            _clear_hour_scan(db)
        db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)', (
            (_SCAN_PAGES + f'{pages:08d}', canonical_json(page)),
            (_SCAN_KEY, canonical_json(progress)),
        ))


def _retire_hour(store, cutoff, archive_floor, deferred, *, failure_cutoff=None):
    failure_cutoff=failure_cutoff or cutoff
    with store.connect() as db:
        deadline = time.monotonic() + .5
        db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        not_deferred=(" AND NOT EXISTS (SELECT 1 FROM json_each(?) d WHERE "
            "binding_id=json_extract(d.value,'$[0]') AND unit=json_extract(d.value,'$[1]') "
            "AND substr(dispatch_at,1,13)=json_extract(d.value,'$[2]')) ")
        firsts=[]
        # Separate indexed seeks avoid repeatedly scanning young failures.
        for status,before in [("status='CAPTURED'",cutoff),("status!='CAPTURED'",failure_cutoff)]:
            first=db.execute('SELECT binding_id,unit,dispatch_at FROM cycles WHERE dispatch_at<? AND '+status+' AND '+_TERMINAL+
                not_deferred+'ORDER BY dispatch_at LIMIT 1',(before,canonical_json(sorted(deferred)))).fetchone()
            if first:firsts.append(first)
        first=min(firsts,key=lambda row:row[2]) if firsts else None
        if not first:
            return None
        binding, unit, at = first
        start = datetime.fromisoformat(at).replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        key = (binding, unit, at[:13])
        window = (binding, unit, start.isoformat(), end.isoformat())
        report = {'binding': binding, 'unit': unit, 'hour': start.isoformat()}

        def defer(reason):
            deferred.add(key)
            return {**report, 'deferred': reason}

        seal = None
        archive = None
        if end.isoformat() <= archive_floor:
            # Past the summary window, raw terminal proof needs no new rollup.
            rows = _exit_rows(db, window, failure_cutoff=failure_cutoff)
        elif known := db.execute(
            'SELECT data_json FROM unit_hours WHERE binding_id=? AND unit=? AND hour=?',
            window[:3],
        ).fetchone():
            seal = json.loads(known[0]).get('_archive_seal')
            if seal is None:
                return defer('legacy_archive')
            rows = _exit_rows(db, window, seal=seal, failure_cutoff=failure_cutoff)
            if not rows:
                return defer('late_finish')
        elif _inflight(db, window):
            return defer('inflight')
        else:
            # Keep one resumable scan. Only slim proof pages are staged, and
            # none are needed after the exact histogram is durably sealed.
            saved = db.execute('SELECT value FROM meta WHERE key=?', (_SCAN_KEY,)).fetchone()
            work = json.loads(saved[0]) if saved else {}
            if work.get('window') != list(window):
                work = {'cursor': ('', ''), 'pages': 0}
            page = [dict(r) for r in db.execute(
                'SELECT rowid AS archive_rowid,* FROM cycles WHERE ' + _WINDOW +
                ' AND ' + _TERMINAL + ' AND (dispatch_at,run_id)>(?,?) '
                'ORDER BY dispatch_at,run_id LIMIT ?', (*window, *work['cursor'], _HOUR_PAGE),
            )]
            _prepare_archive_page(db, page)
            if len(page) == _HOUR_PAGE:
                _stage_page(store, window, work, page)
                return {**report, 'cycles': 0, 'scanned': len(page)}
            samples = []
            if work['pages']:
                for stored in db.execute('SELECT value FROM meta WHERE key>=? AND key<? ORDER BY key',
                                         (_SCAN_PAGES, _SCAN_PAGES + '\uffff')):
                    samples.extend(json.loads(stored[0]))
            samples.extend(page)
            if not samples:
                return defer('source_changed')
            archive, seal = _hour_stats(db, window, start, end, samples)
            rows = [(r['run_id'], r['archive_rowid'], r['finished_at']) for r in samples
                    if r['status']=='CAPTURED' or r['dispatch_at']<failure_cutoff][:512]

    with store.connect(write=True) as db:
        if archive is not None:
            current = db.execute(
                "SELECT count(*),max(rowid),coalesce(max(finished_at),'') FROM cycles WHERE " +
                _WINDOW + ' AND ' + _TERMINAL, window,
            ).fetchone()
            if list(current) != seal or _inflight(db, window):
                _clear_hour_scan(db)
                return defer('source_changed')
            if not db.execute('INSERT OR IGNORE INTO unit_hours VALUES(?,?,?,?)', archive).rowcount:
                _clear_hour_scan(db)
                return defer('already_archived')
            _clear_hour_scan(db)
        deleted = db.executemany(
            'DELETE FROM cycles WHERE run_id=? AND rowid=? AND finished_at IS ? AND ' + _TERMINAL,
            rows,
        ).rowcount
    return {**report, 'cycles': deleted}


def _exit_rows(db, window, *, seal=None, failure_cutoff=None):
    age = " AND rowid<=? AND coalesce(finished_at,'')<=?" if seal is not None else ''
    return db.execute(
        'SELECT run_id,rowid,finished_at FROM cycles WHERE ' + _WINDOW + ' AND ' + _TERMINAL +
        age + (" AND (status='CAPTURED' OR dispatch_at<?)" if failure_cutoff else '') + ' LIMIT 512',
        (*( (*window,*seal[1:]) if seal is not None else window),*([failure_cutoff] if failure_cutoff else [])),
    ).fetchall()


def _inflight(db, window):
    return db.execute('SELECT 1 FROM cycles WHERE ' + _WINDOW + ' AND ' + _ACTIVE + ' LIMIT 1',
                      window).fetchone()


def _prepare_archive_page(db, rows):
    identities = set()
    for row in rows:
        proof = json.loads(row.pop('data_json'))
        row.update({key: proof[key] for key in (
            'physical_run_id', 'required', 'shared_run_id', 'list_validated',
            'listing_received_at', 'winning_request_id', 'requests',
        ) if key in proof})
        if row.get('physical_run_id'):
            identities.add(row['physical_run_id'])
    physical = {r[0]: json.loads(r[1]) for r in db.execute(
        'SELECT run_id,requests_json FROM physical_cycles '
        'WHERE run_id IN (SELECT value FROM json_each(?))', (canonical_json(sorted(identities)),),
    )}
    for row in rows:
        # The rollup uses listing identities and start times. Keep the source
        # physical request and the exact cadence calculation unchanged.
        requests = physical.get(row.get('physical_run_id'), row.get('requests', []))
        row['requests'] = [
            {key: r[key] for key in ('physical_request_id', 'request_started_at') if key in r}
            for r in requests if r.get('role', 'listing') == 'listing'
        ]


def _hour_stats(db, window, start, end, samples):
    binding, unit, first, last = window
    schedules = [dict(r) for r in db.execute(
        "SELECT * FROM cycles WHERE status='SCHEDULE' AND binding_id=? "
        "AND unit=? AND dispatch_at=(SELECT max(dispatch_at) FROM cycles "
        "WHERE status='SCHEDULE' AND binding_id=? AND unit=? AND dispatch_at<=?) "
        "UNION ALL SELECT * FROM cycles WHERE status='SCHEDULE' AND binding_id=? "
        "AND unit=? AND dispatch_at>? AND dispatch_at<? ORDER BY dispatch_at",
        (binding, unit, binding, unit, first, binding, unit, first, last),
    )]
    for row in schedules:
        row.update(json.loads(row.pop('data_json')))
    receipts = []
    for row in samples:
        if not row.get('list_validated'):
            continue
        winner = next((r for r in row['requests']
                       if r.get('physical_request_id') == row.get('winning_request_id')), None)
        if winner and winner.get('request_started_at'):
            receipts.append({**row, 'observed_at': row['listing_received_at'],
                             'request_started_at': winner['request_started_at'],
                             'committed_at': row['finished_at']})
    stats = cadence(schedules + samples, receipts, start, end)
    seal = [len(samples), max(r['archive_rowid'] for r in samples),
            max(r['finished_at'] or '' for r in samples)]
    stats['_archive_seal'] = seal
    return (binding, unit, first, canonical_json(stats)), seal


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
             'WHERE observed_at<? ORDER BY observed_at LIMIT 512)', ((now-timedelta(hours=48)).isoformat(),)),
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
    cutoff = (now-timedelta(hours=2)).replace(minute=0, second=0, microsecond=0).isoformat()
    failure_cutoff=(now-timedelta(hours=72)).replace(minute=0,second=0,microsecond=0).isoformat()
    archive_floor = (now-timedelta(days=7)).isoformat()
    deadline = time.monotonic()+budget
    report = {'at': now.isoformat(), 'hours': [], 'errors': [], 'stages': {}}
    deferred = set()
    try:
        for _ in range(hours):
            if time.monotonic() >= deadline:
                break
            result = _retire_hour(store, cutoff, archive_floor, deferred,failure_cutoff=failure_cutoff)
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
