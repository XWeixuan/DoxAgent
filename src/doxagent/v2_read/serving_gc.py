"""Bounded protocol retention. Source repair owns receipts, not all Read history."""
import json,time
from datetime import UTC,datetime,timedelta


def collect(store, *, now=None, limit=500, budget=.1, time_floor=None):
    from .repository import instant
    from .settings import Limits
    if not 1 <= limit <= 5000:
        raise ValueError('GC limit must be 1..5000')
    now=now or datetime.now(UTC)
    with store.connect() as reader:
        row=reader.execute('SELECT min(seq) FROM commit_retention WHERE projected_at>=?',
                           (instant(now-timedelta(minutes=Limits.load().stream_minutes)),)).fetchone()
        floor=row[0] if row[0] is not None else store.highwater(reader)
    counts={};started=time.monotonic()
    with store.connect(write=True,timeout=.05) as db:
        db.set_progress_handler(lambda:time.monotonic()-started>=budget,1000)
        for sql in ('SELECT min(seq) FROM views WHERE expires_at>?',
                    "SELECT min(CAST(json_extract(payload,'$.seq') AS INTEGER)-1) FROM cursors WHERE expires_at>?"):
            pin=db.execute(sql,(instant(now),)).fetchone()[0]
            if pin is not None:floor=min(floor,max(0,pin))
        for table in ('views','cursors'):
            counts[table]=db.execute('DELETE FROM '+table+' WHERE rowid IN (SELECT rowid FROM '+table+' WHERE expires_at<=? LIMIT ?)',(instant(now),limit)).rowcount
        for table in ('object_history','contributions_history','metric_buckets_history'):
            counts[table]=db.execute('DELETE FROM '+table+' WHERE rowid IN (SELECT rowid FROM '+table+' WHERE valid_to<=? ORDER BY valid_to LIMIT ?)',(floor,limit)).rowcount
        counts['changes']=db.execute('DELETE FROM changes WHERE rowid IN (SELECT rowid FROM changes WHERE seq<? ORDER BY seq LIMIT ?)',(floor,limit)).rowcount
        cursor=db.execute("SELECT value FROM read_meta WHERE key='search_gc_cursor'").fetchone()
        search=[r[0] for r in db.execute('SELECT digest FROM content_search WHERE digest>? ORDER BY digest LIMIT ?',
                                         (cursor[0] if cursor else '',limit))]
        counts['content_search']=db.execute('DELETE FROM content_search WHERE digest IN '
            '(SELECT value FROM json_each(?)) AND NOT EXISTS '
            '(SELECT 1 FROM contents c WHERE c.sha256=content_search.digest)',(json.dumps(search),)).rowcount
        db.execute("INSERT INTO read_meta VALUES('search_gc_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (search[-1] if search else '',))
        exiting=[r[0] for r in db.execute('SELECT sha256 FROM definition_exit LIMIT ?',(limit,))]
        counts['definitions']=0
        for digest in exiting:
            counts['definitions']+=db.execute('DELETE FROM definitions WHERE sha256=? AND NOT EXISTS(SELECT 1 FROM definition_ref r WHERE r.sha256=?)',(digest,digest)).rowcount
            db.execute('DELETE FROM definition_exit WHERE sha256=?',(digest,))
        # Entity/contribution watermarks retain replay idempotency after old
        # numeric receipt identities leave the protocol ledger.
        counts['commits']=db.execute("DELETE FROM commits WHERE seq IN (SELECT c.seq FROM commits c WHERE c.seq<? AND NOT EXISTS(SELECT 1 FROM gaps g WHERE g.source=c.source AND g.event=c.source_event) LIMIT ?)",(floor,limit)).rowcount
        counts['commit_retention']=db.execute('DELETE FROM commit_retention WHERE seq IN (SELECT r.seq FROM commit_retention r WHERE r.seq<? AND NOT EXISTS(SELECT 1 FROM commits c WHERE c.seq=r.seq) LIMIT ?)',(floor,limit)).rowcount
        db.execute("UPDATE read_meta SET value=CAST(max(CAST(value AS INTEGER),?) AS TEXT) WHERE key='retained_floor'",(floor,))
        gaps=[tuple(r) for r in db.execute('SELECT source,event,reason FROM gaps')]
    return {'deleted':counts,'retention_floor':floor,'time_floor':floor,'protected':False,
            'policy':'SCOPED_PROTOCOL','protected_sources':[],
            'repair_scope':gaps,'scanned':sum(counts.values()),'physical_space_reclaimed':False}
