"""Bounded protocol retention, with independently committed, resumable stages."""
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

FLOOR_SQL = ('SELECT min(seq) FROM commit_retention INDEXED BY commit_retention_at '
             'WHERE projected_at>=?')
STAGES = ('tokens', 'poll_current', 'object_history', 'contributions_history', 'metric_buckets_history',
          'changes', 'search', 'definitions', 'commits', 'commit_retention')


def _meta(db, key, value):
    db.execute('INSERT INTO read_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (key, str(value)))


def collect(store, *, now=None, limit=500, budget=.1, time_floor=None):
    from .repository import instant
    from .settings import Limits
    if not 1 <= limit <= 5000:
        raise ValueError('GC limit must be 1..5000')
    now = now or datetime.now(UTC)
    deadline = time.monotonic() + budget
    with store.connect() as reader:
        reader.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        if time_floor is None:
            row = reader.execute(FLOOR_SQL, (instant(now-timedelta(
                minutes=Limits.load().stream_minutes)),)).fetchone()
            time_floor = row[0] if row[0] is not None else store.highwater(reader)
        row = reader.execute("SELECT value FROM read_meta WHERE key='gc_stage'").fetchone()
        stage = int(row[0]) if row else 0
        gaps = [tuple(r) for r in reader.execute('SELECT source,event,reason FROM gaps LIMIT 64')]
    counts, errors = {}, []
    floor, scanned, completed = time_floor, 0, 0
    for _ in STAGES:
        if time.monotonic() >= deadline:
            break
        name = STAGES[stage]
        delta, seen = {}, 0
        try:
            if name == 'poll_current':
                delta, seen = _poll_current(store, now, limit, deadline)
            with store.connect(write=True, timeout=.05) as db:
                db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
                # Pins are rechecked under the writer fence for every independent commit.
                floor = time_floor
                for sql in ('SELECT min(seq) FROM views WHERE expires_at>?',
                            "SELECT min(CAST(json_extract(payload,'$.seq') AS INTEGER)-1) "
                            'FROM cursors WHERE expires_at>?'):
                    pin = db.execute(sql, (instant(now),)).fetchone()[0]
                    if pin is not None:
                        floor = min(floor, max(0, pin))
                if name == 'poll_current':
                    pass
                elif name == 'tokens':
                    for table in ('views', 'cursors'):
                        delta[table] = db.execute('DELETE FROM '+table+' WHERE rowid IN '
                            '(SELECT rowid FROM '+table+' WHERE expires_at<=? LIMIT ?)',
                            (instant(now), limit)).rowcount
                elif name.endswith('_history'):
                    delta[name] = db.execute('DELETE FROM '+name+' WHERE rowid IN '
                        '(SELECT rowid FROM '+name+' WHERE valid_to<=? ORDER BY valid_to LIMIT ?)',
                        (floor, limit)).rowcount
                elif name == 'changes':
                    delta[name] = db.execute('DELETE FROM changes WHERE rowid IN '
                        '(SELECT rowid FROM changes WHERE seq<? ORDER BY seq LIMIT ?)',
                        (floor, limit)).rowcount
                elif name == 'search':
                    row = db.execute("SELECT value FROM read_meta WHERE key='search_gc_cursor'").fetchone()
                    candidates = [r[0] for r in db.execute('SELECT digest FROM content_search '
                        'WHERE digest>? ORDER BY digest LIMIT ?', (row[0] if row else '', limit))]
                    seen = len(candidates)
                    delta['content_search'] = db.execute('DELETE FROM content_search WHERE digest IN '
                        '(SELECT value FROM json_each(?)) AND NOT EXISTS '
                        '(SELECT 1 FROM contents c WHERE c.sha256=content_search.digest)',
                        (json.dumps(candidates),)).rowcount
                    _meta(db, 'search_gc_cursor', candidates[-1] if candidates else '')
                elif name == 'definitions':
                    candidates = [r[0] for r in db.execute('SELECT sha256 FROM definition_exit LIMIT ?', (limit,))]
                    seen = len(candidates)
                    delta['definitions'] = 0
                    for digest in candidates:
                        delta['definitions'] += db.execute('DELETE FROM definitions WHERE sha256=? '
                            'AND NOT EXISTS(SELECT 1 FROM definition_ref WHERE sha256=?)', (digest, digest)).rowcount
                        db.execute('DELETE FROM definition_exit WHERE sha256=?', (digest,))
                else:
                    key = 'gc_scan_'+name
                    row = db.execute('SELECT value FROM read_meta WHERE key=?', (key,)).fetchone()
                    cursor = int(row[0]) if row else 0
                    candidates = [r[0] for r in db.execute('SELECT seq FROM '+name+
                        ' WHERE seq>? AND seq<? ORDER BY seq LIMIT ?', (cursor, floor, limit))]
                    seen = len(candidates)
                    if name == 'commits':
                        sql = ('DELETE FROM commits WHERE seq=? AND NOT EXISTS '
                               '(SELECT 1 FROM gaps WHERE source=commits.source AND event=commits.source_event)')
                    else:
                        sql = ('DELETE FROM commit_retention WHERE seq=? AND NOT EXISTS '
                               '(SELECT 1 FROM commits WHERE seq=commit_retention.seq)')
                    delta[name] = sum(db.execute(sql, (seq,)).rowcount for seq in candidates)
                    _meta(db, key, candidates[-1] if candidates else 0)
                _meta(db, 'gc_stage', (stage+1) % len(STAGES))
                db.execute("UPDATE read_meta SET value=CAST(max(CAST(value AS INTEGER),?) AS TEXT) "
                           "WHERE key='retained_floor'", (floor,))
            for key, value in delta.items():
                counts[key] = counts.get(key, 0) + value
            scanned += seen
            completed += 1
            stage = (stage+1) % len(STAGES)
        except sqlite3.OperationalError as exc:
            errors.append({'stage': name, 'error': str(exc)})
            break
    return {'deleted': counts, 'retention_floor': floor, 'time_floor': time_floor,
            'protected': False, 'policy': 'SCOPED_PROTOCOL', 'protected_sources': [],
            'repair_scope': gaps, 'errors': errors, 'scanned': scanned+completed,
            'stages_completed': completed, 'next_stage': STAGES[stage],
            'cycle_complete': completed == len(STAGES) and not scanned and not any(counts.values()),
            'physical_space_reclaimed': False}


def _poll_current(store, now, limit, deadline):
    """Close legacy technical heads through normal MVCC, preserving pinned preimages."""
    from doxagent.message_bus_v2.collection_plane.observability import store_from_environment
    if store_from_environment() is None:
        return {}, 0
    cutoff=(now-timedelta(hours=48)).isoformat()
    rows=[]
    with store.connect() as db:
        db.set_progress_handler(lambda:time.monotonic()>=deadline,1000)
        for kind in ('native:poll_runs','native:poll_captures'):
            rows.extend(db.execute("SELECT kind,ticker,id,valid_from FROM object_current WHERE kind=? AND "
                "kind IN ('native:poll_runs','native:poll_captures') AND "
                "coalesce(json_extract(payload,'$.dispatch_at'),json_extract(payload,'$.observed_at'))<? "
                "ORDER BY ticker,coalesce(json_extract(payload,'$.dispatch_at'),json_extract(payload,'$.observed_at')),valid_from LIMIT ?",
                (kind,cutoff,min(limit,128))).fetchall())
    if rows:
        import hashlib
        identity=hashlib.sha256(json.dumps([tuple(r) for r in rows]).encode()).hexdigest()
        store.ingest('read-retention',identity,[dict(kind=r[0],ticker=r[1],id=r[2],data=None) for r in rows])
    return {'poll_current':len(rows)},len(rows)
