"""Source-owned transport expiry: current domain rows, not CDC packs, rebuild Read."""
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import time


HOT_EXPIRY_INDEX = 'v2_source_outbox_expiry'


def migrate_expiry_index(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_source_outbox'").fetchone():
        db.execute(f'CREATE INDEX IF NOT EXISTS {HOT_EXPIRY_INDEX} ON v2_source_outbox(julianday(recorded_at),seq)')


def migrate_archive_times(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_archive_times'").fetchone():
        db.execute('CREATE TABLE v2_archive_times('
                   'kind TEXT NOT NULL,seq INTEGER NOT NULL,digest TEXT NOT NULL,recorded_at TEXT NOT NULL,'
                   'PRIMARY KEY(kind,seq)) WITHOUT ROWID')


def migrate(db):
    for sql in (
        'CREATE TABLE IF NOT EXISTS v2_source_exception_pins(consumer TEXT,seq INTEGER,PRIMARY KEY(consumer,seq))',
        'CREATE INDEX IF NOT EXISTS v2_source_exception_sequence ON v2_source_exception_pins(seq)',
        'CREATE TABLE IF NOT EXISTS v2_source_exception_receipts(seq INTEGER PRIMARY KEY,packet TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS v2_transport_gc_state(kind TEXT PRIMARY KEY,cursor INTEGER NOT NULL DEFAULT 0,expired_floor INTEGER NOT NULL DEFAULT 0)',
        'CREATE TABLE IF NOT EXISTS v2_transport_files_exit(folder TEXT,digest TEXT,not_before REAL,PRIMARY KEY(folder,digest))',
        'CREATE TABLE IF NOT EXISTS v2_serving_business_facts(kind TEXT,ticker TEXT,id TEXT,payload TEXT NOT NULL,PRIMARY KEY(kind,ticker,id))',
        'CREATE TABLE IF NOT EXISTS v2_serving_fact_owner(id INTEGER PRIMARY KEY CHECK(id=1),path TEXT NOT NULL)',
    ):
        db.execute(sql)


def check_replay(db, after):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_transport_gc_state'").fetchone():
        floor = db.execute('SELECT coalesce(max(expired_floor),0) FROM v2_transport_gc_state').fetchone()[0]
        if after < floor:
            raise ValueError('SOURCE_SNAPSHOT_REQUIRED')


def acknowledge(source, consumer, checkpoint, repairs):
    repairs = {int(x) for x in repairs if int(x) > 0}
    with closing(source.connection(write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE'); migrate(db)
        existing = {r[0] for r in db.execute('SELECT seq FROM v2_source_exception_pins WHERE consumer=?',(consumer,))}
        db.executemany('DELETE FROM v2_source_exception_pins WHERE consumer=? AND seq=?',
                       [(consumer,x) for x in existing-repairs])
        db.executemany('INSERT INTO v2_source_exception_pins VALUES(?,?)',
                       [(consumer,x) for x in repairs-existing])
        db.execute('DELETE FROM v2_source_pins WHERE id=?',(consumer+':repair',))
        db.execute("INSERT INTO v2_source_consumers VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                   "ON CONFLICT(id) DO UPDATE SET checkpoint=max(checkpoint,excluded.checkpoint),updated_at=excluded.updated_at "
                   "WHERE checkpoint<excluded.checkpoint OR updated_at<strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 minute')",
                   (consumer,checkpoint))


def acknowledge_prepared(source, consumer, checkpoint, repairs, previous=None):
    """Projector-owned consumer: compile pin deltas without the shared writer.

    The Projector's existing exclusive file lock owns this consumer. Metadata and
    source epoch are still compared at commit, including on a process restart.
    Snapshot importers keep the independent full-set acknowledgement API.
    """
    repairs = frozenset(int(x) for x in repairs if int(x) > 0)
    for attempt in range(2):
        if previous is None:
            # projection_session lends a connection whose close() is a no-op.
            # End our read snapshot here, before acquiring the separate writer.
            with closing(source.connection()) as db, db:
                db.execute('BEGIN')
                epoch = db.execute('SELECT epoch FROM v2_source_state WHERE source=?', (source.source,)).fetchone()[0]
                row = db.execute('SELECT checkpoint,updated_at FROM v2_source_consumers WHERE id=?', (consumer,)).fetchone()
                metadata = tuple(row) if row else None
                existing = frozenset(r[0] for r in db.execute('SELECT seq FROM v2_source_exception_pins WHERE consumer=?', (consumer,)))
        else:
            epoch, metadata, existing = previous
        removed, added = existing - repairs, repairs - existing
        with closing(source.connection(write=True)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            current_epoch = db.execute('SELECT epoch FROM v2_source_state WHERE source=?', (source.source,)).fetchone()[0]
            if current_epoch != epoch:
                raise ValueError('SOURCE_EPOCH_MISMATCH')
            row = db.execute('SELECT checkpoint,updated_at FROM v2_source_consumers WHERE id=?', (consumer,)).fetchone()
            if (tuple(row) if row else None) != metadata:
                previous = None
                continue
            db.executemany('DELETE FROM v2_source_exception_pins WHERE consumer=? AND seq=?', ((consumer, x) for x in removed))
            db.executemany('INSERT INTO v2_source_exception_pins VALUES(?,?)', ((consumer, x) for x in added))
            db.execute('DELETE FROM v2_source_pins WHERE id=?', (consumer+':repair',))
            db.execute("INSERT INTO v2_source_consumers VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                       "ON CONFLICT(id) DO UPDATE SET checkpoint=max(checkpoint,excluded.checkpoint),updated_at=excluded.updated_at",
                       (consumer, checkpoint))
            row = db.execute('SELECT checkpoint,updated_at FROM v2_source_consumers WHERE id=?', (consumer,)).fetchone()
            result = (epoch, tuple(row), repairs)
        return result
    error = sqlite3.OperationalError('consumer acknowledgement changed during preparation')
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    raise error


def retire_consumer(source, consumer):
    """Explicit generation retirement; inactivity alone never retires a consumer."""
    with closing(source.connection(write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM v2_source_consumers WHERE id=?',(consumer,))
        db.execute('DELETE FROM v2_source_exception_pins WHERE consumer=?',(consumer,))
        db.execute('DELETE FROM v2_source_pins WHERE id=? OR id=?',(consumer,consumer+':repair'))


def native_hashes(value):
    """Receipt envelopes contain native JSON serialized inside JSON columns."""
    if isinstance(value,dict):
        digest=value.get('$doxagent_content_v1')
        if isinstance(digest,str):yield digest
        for item in value.values():yield from native_hashes(item)
    elif isinstance(value,list):
        for item in value:yield from native_hashes(item)
    elif isinstance(value,str) and value[:1] in ('{','['):
        try:decoded=json.loads(value)
        except ValueError:return
        yield from native_hashes(decoded)


def replace_packet_refs(db, owner, identity, packet):
    replace_packet_hashes(db, owner, identity, set(native_hashes(packet)))


def replace_packet_hashes(db, owner, identity, hashes):
    from .artifact_registry import retain
    db.execute('DELETE FROM artifact_ref WHERE owner_type=? AND owner_id=?',(owner,str(identity)))
    for digest in hashes:
        retain(db,digest,digest,0,codec='json-chunks-v1',
               location='native-files/'+digest[:2]+'/'+digest,owner_type=owner,owner_id=str(identity))


BUSINESS_FACT_KINDS=('policy_admission','policy_hit_set','policy_change','event_change','fact_change',
                     'policy_catalog','event_catalog','fact_catalog','usage','attempt')
BUSINESS_FACT_METRICS=tuple(a+'_'+b for a in ('added','modified','retired') for b in ('events','facts'))+('new_fact_candidates',)


def sync_business_facts(source, store, *, budget=None):
    """Canonical small settlements/transition facts survive expiry of their CDC.

    This deliberately excludes native payloads, negative Policy observations,
    generic history and Context. Ordinary immutable definitions remain owned by
    their existing business repositories.
    """
    if source.source!='runtime':return
    with closing(source.connection()) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_serving_fact_owner'").fetchone():return
        owner=db.execute('SELECT path FROM v2_serving_fact_owner WHERE id=1').fetchone()
        if not owner or owner[0]!=str(store.path):return
    from .repository import encode
    from hashlib import sha256
    prefix='serving:'+sha256(str(store.path).encode()).hexdigest()[:16]+':'
    jobs=[('object',k) for k in BUSINESS_FACT_KINDS]+[('metric',k) for k in BUSINESS_FACT_METRICS]+[('field',k) for k in ('activation','activation_revision')]
    with closing(source.connection()) as db:
        cursors={r[0]:r[1] for r in db.execute('SELECT kind,cursor FROM v2_transport_gc_state WHERE kind LIKE ?', (prefix+'%',))}
    # The previous owner ACK was committed only after its fact copy succeeded.
    # Its indexed Read receipt is a conservative restart floor (coverage and
    # other sources after that receipt are replayed), never the current head.
    if not cursors and not getattr(source,'_fact_cursors',None):
        consumer='read:'+sha256(str(store.path).encode()).hexdigest()[:24]
        with closing(source.connection()) as db:
            ack=db.execute('SELECT checkpoint FROM v2_source_consumers WHERE id=?',(consumer,)).fetchone()
            existing=db.execute('SELECT 1 FROM v2_serving_business_facts LIMIT 1').fetchone()
        if ack and existing:
            candidates=[str(n) for n in range(max(0,ack[0]-1000),ack[0]+1)]
            with store.connect() as read:
                floor=read.execute("SELECT max(seq) FROM commits WHERE source='runtime' AND source_event IN (SELECT value FROM json_each(?))",(json.dumps(candidates),)).fetchone()[0]
            if floor:
                cursors.update({prefix+':'.join(j):floor for j in jobs})
    cursors.update(getattr(source,'_fact_cursors',{}))
    started=time.monotonic()
    with store.connect() as read:
        seq=store.highwater(read)
    if getattr(source, '_business_fact_seq', None) == seq:
        return True
    positions = getattr(source, '_fact_positions', {})
    for group,kind in sorted(jobs,key=lambda j:cursors.get(prefix+':'.join(j),0)):
        if budget is not None and time.monotonic()-started>budget:break
        key=prefix+group+':'+kind;previous=cursors.get(key,0)
        if previous>=seq:continue
        records=[]
        with store.connect() as read:
            table='contributions_current' if group=='metric' else 'object_current'
            column='metric' if group=='metric' else 'kind'
            # Index order and a revision boundary keep every batch complete,
            # including multiple objects emitted by the same source event.
            revisions=[r[0] for r in read.execute('SELECT DISTINCT valid_from FROM '+table+' WHERE '+column+'=? AND valid_from>? AND valid_from<=? ORDER BY valid_from LIMIT 16',(kind,previous,seq))]
            boundary=revisions[-1] if len(revisions)==16 else seq
            position = positions.get(key, (previous, 0))
            page = read.execute('SELECT rowid AS _rowid,* FROM '+table+' WHERE '+column+
                '=? AND valid_from>? AND valid_from<=? AND (valid_from,rowid)>(?,?) '
                'ORDER BY valid_from,rowid LIMIT 128', (kind,previous,boundary,*position)).fetchall()
            last = None
            used_bytes = 0
            for row in page:
                size = len(row['payload'].encode()) if group != 'metric' else len(row['dimensions'].encode())
                if last is not None and used_bytes + size > 1024*1024:
                    break
                used_bytes += size
                last = (row['valid_from'], row['_rowid'])
                row=dict(row)
                row.pop('_rowid')
                if group=='object':
                    value={k:row[k] for k in ('kind','ticker','id','parent','sort_key','day','source_id','route','search_text')}
                    value['data']=json.loads(row['payload'])
                    records.append((kind,row['ticker'],row['id'],encode(value)))
                elif group=='metric':
                    value=row;value.pop('valid_from');value.pop('valid_to');value['dimensions']=json.loads(value['dimensions'])
                    identity=encode([value[k] for k in ('entity','day','dimensions')])
                    records.append(('metric:'+kind,value['ticker'],identity,encode(value)))
                else:
                    value=json.loads(row['payload'])
                    if 'activated_at' in value:
                        records.append(('field:'+kind,row['ticker'],row['id'],encode({k:value[k] for k in ('activated_at','runtime_activation_id') if k in value})))
            if last is not None and (len(page) == 128 or last[1] != page[-1]['_rowid']):
                positions[key] = last
                boundary = last[0] - 1
            else:
                positions.pop(key, None)
        if records:
            with closing(source.connection(write=True)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                owner=db.execute('SELECT path FROM v2_serving_fact_owner WHERE id=1').fetchone()
                if not owner or owner[0]!=str(store.path):return False
                db.executemany('INSERT INTO v2_serving_business_facts VALUES(?,?,?,?) ON CONFLICT(kind,ticker,id) DO UPDATE SET payload=excluded.payload WHERE payload<>excluded.payload',records)
                db.execute('INSERT INTO v2_transport_gc_state(kind,cursor) VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET cursor=excluded.cursor',(key,boundary))
        cursors[key]=boundary
    source._fact_cursors=cursors
    source._fact_positions=positions
    complete=all(cursors.get(prefix+':'.join(j),0)>=seq for j in jobs)
    if complete:
        # Durable exact tombstones, produced by the existing Read mutation.
        # No full policy-hit identity list or NOT IN(all rows) on every pass.
        with store.connect() as read:
            exits = read.execute('SELECT kind,ticker,id,seq FROM serving_fact_exits ORDER BY seq LIMIT 128').fetchall()
            absent = [tuple(row) for row in exits if not read.execute(
                'SELECT 1 FROM object_current WHERE kind=? AND ticker=? AND id=?', tuple(row[:3]),
            ).fetchone()]
        if exits:
            with closing(source.connection(write=True)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                owner=db.execute('SELECT path FROM v2_serving_fact_owner WHERE id=1').fetchone()
                if not owner or owner[0]!=str(store.path):return False
                db.executemany('DELETE FROM v2_serving_business_facts WHERE kind=? AND ticker=? AND id=?', [r[:3] for r in absent])
            with store.connect(write=True,timeout=.01) as read:
                read.executemany('DELETE FROM serving_fact_exits WHERE kind=? AND ticker=? AND id=? AND seq=?', [tuple(r) for r in exits])
        if len(exits)==128:
            return False
        source._business_fact_seq=seq
    return complete


def expire(source, *, now=None, limit=500, hot_only=False):
    """Bounded scan, including old archives; one repair pins one packet only."""
    if not 1 <= limit <= 500:raise ValueError('invalid expiry batch')
    now = now or datetime.now(UTC)
    before = now - timedelta(days=2 if source.source in {'research', 'bus'} else 7)
    report=dict(expired=0,exceptions=0,scanned=0,files=0)
    # The capture contract and the immutable business tables remain in place.
    # Expiry never changes domain rows, capture epoch or source head.
    capture = source.capture()
    if not capture:
        raise ValueError('current source capture metadata required')
    from .detached_files import detach, drain

    discard = source.path.parent / 'transport-quarantine' / source.source
    if not hot_only:
        report['files'] += drain(discard)
    # Prepare archived packets and pinned exception payloads without the writer.
    with closing(source.connection()) as db:
        tables = ["v2_source_outbox"]
        if not hot_only:
            tables.append("v2_receipt_archive")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_receipt_packs'").fetchone():
                tables.append("v2_receipt_packs")
        has_archive_times = not hot_only and db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='v2_archive_times'"
        ).fetchone() is not None
        has_gc_state = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='v2_transport_gc_state'"
        ).fetchone()
        cursors = dict(db.execute("SELECT kind,cursor FROM v2_transport_gc_state")) if has_gc_state else {}
        indexed_hot = not hot_only and bool(db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (HOT_EXPIRY_INDEX,)
        ).fetchone())
        ack = db.execute("SELECT min(checkpoint) FROM v2_source_consumers").fetchone()[0]
        if ack is None:
            return report
        pin = db.execute("SELECT min(seq) FROM v2_source_pins").fetchone()[0]
        floor = min(ack, pin - 1) if pin is not None else ack
        pack_cache = {}
        pages = {}
        waiting = getattr(source, '_expiry_waiting', {})
        source._expiry_waiting = waiting
        for table in tables:
            due = waiting.get(table)
            if due and due[0] == floor and now < due[1]:
                pages[table] = []
                continue
            if table == 'v2_source_outbox' and indexed_hot:
                cutoff_deadline = time.monotonic() + .05
                db.set_progress_handler(lambda: time.monotonic() >= cutoff_deadline, 1000)
                try:
                    rows = db.execute(
                        "SELECT * FROM v2_source_outbox WHERE julianday(recorded_at)<julianday(?) "
                        "AND seq<=? ORDER BY julianday(recorded_at),seq LIMIT ?",
                        (before.isoformat(), floor, limit),
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    if exc.sqlite_errorcode != sqlite3.SQLITE_INTERRUPT:
                        raise
                    indexed_hot = False  # A lagging ACK can make the time index unselective.
                finally:
                    db.set_progress_handler(None, 0)
            if table != 'v2_source_outbox' or not indexed_hot:
                rows = db.execute(
                    "SELECT * FROM " + table + " WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?",
                    (cursors.get(table, 0), floor, limit),
                ).fetchall()
                if not rows and cursors.get(table, 0):
                    # Seq is not an expiry clock: rows passed while young must
                    # be revisited once they age. Wrap only a bounded page.
                    rows = db.execute(
                        "SELECT * FROM " + table + " WHERE seq<=? ORDER BY seq LIMIT ?",
                        (floor, limit),
                    ).fetchall()
            candidates = json.dumps([row['seq'] for row in rows])
            pins = {r[0] for r in db.execute('SELECT DISTINCT seq FROM v2_source_exception_pins '
                    'WHERE seq IN (SELECT value FROM json_each(?))', (candidates,))} if has_gc_state else set()
            prepared = []
            for row in rows:
                seq = row["seq"]
                archived = table != 'v2_source_outbox'
                hint = db.execute(
                    'SELECT recorded_at FROM v2_archive_times WHERE kind=? AND seq=? AND digest=?',
                    (table, seq, row['digest']),
                ).fetchone() if archived and has_archive_times else None
                stamp = (hint[0] if hint else None) if archived else row['recorded_at']
                recent = stamp is not None and datetime.fromisoformat(stamp) >= before
                packet, raw_json, hashes = None, None, ()
                if stamp is None or (seq in pins and not recent):
                    packet = source.event(seq, decode=False, connection=db, pack_cache=pack_cache)
                    if packet is None:
                        raise ValueError("transport packet missing")
                    stamp = packet['recorded_at']
                    recent = datetime.fromisoformat(stamp) >= before
                    if seq in pins and not recent:
                        raw = {key: value for key, value in packet.items() if key != "row"}
                        raw_json = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                        hashes = set(native_hashes(raw))
                prepared.append((row, recent, raw_json, hashes, stamp if archived and hint is None else None))
            pages[table] = prepared
            if prepared and len(prepared) < limit and all(item[1] for item in prepared):
                waiting[table] = (floor, min(datetime.fromisoformat(item[0]['recorded_at'])
                    for item in prepared) + (now-before)) if table == 'v2_source_outbox' else (floor, now+timedelta(minutes=15))
            else:
                waiting.pop(table, None)
        cleanup_due = not hot_only and has_gc_state and any((
            db.execute("SELECT 1 FROM v2_source_consumers WHERE id LIKE 'snapshot:%' AND julianday(updated_at)<julianday(?) LIMIT 1", ((now-timedelta(hours=24)).isoformat(),)).fetchone(),
            db.execute('SELECT 1 FROM v2_source_exception_receipts e WHERE NOT EXISTS(SELECT 1 FROM v2_source_exception_pins p WHERE p.seq=e.seq) LIMIT 1').fetchone(),
            db.execute('SELECT 1 FROM v2_transport_files_exit WHERE not_before<=? LIMIT 1', (time.time(),)).fetchone(),
        ))

    if not any(pages.values()) and not cleanup_due:
        return report
    page_ids = {table: json.dumps([item[0]['seq'] for item in prepared]) for table, prepared in pages.items()}
    # Explicit source/archive migrations own schema creation. Read-only planning
    # above never installs schema while contending with live business commits.
    if not has_gc_state:
        raise ValueError('source lifecycle migration required')
    if not hot_only and not has_archive_times and any(pages.get(t) for t in tables[1:]):
        raise ValueError('archive-time migration required')
    with closing(source.connection(write=True)) as db, db:
        if hot_only:
            db.execute('PRAGMA busy_timeout=0')
        db.execute('BEGIN IMMEDIATE')
        epoch = db.execute('SELECT epoch FROM v2_source_state WHERE source=?', (source.source,)).fetchone()
        if not epoch or epoch[0] != capture['source_epoch']:
            return report
        # Only the temporary importer prefix has a bounded lease. A killed
        # importer must not pin a suffix forever; ordinary Read consumers are
        # retired explicitly by their generation owner.
        stale=[r[0] for r in db.execute("SELECT id FROM v2_source_consumers WHERE id LIKE 'snapshot:%' AND julianday(updated_at)<julianday(?) LIMIT 32",((now-timedelta(hours=24)).isoformat(),))]
        for consumer in stale:
            db.execute('DELETE FROM v2_source_consumers WHERE id=?',(consumer,))
            db.execute('DELETE FROM v2_source_exception_pins WHERE consumer=?',(consumer,))
            db.execute('DELETE FROM v2_source_pins WHERE id=? OR id=?',(consumer,consumer+':repair'))
        if source.source=='runtime' and not db.execute('SELECT 1 FROM v2_serving_fact_owner WHERE id=1').fetchone():
            raise ValueError('active serving business-fact owner not registered')
        ack=db.execute('SELECT min(checkpoint) FROM v2_source_consumers').fetchone()[0]
        if ack is None:return report
        pin=db.execute('SELECT min(seq) FROM v2_source_pins').fetchone()[0]
        floor=min(ack,pin-1) if pin is not None else ack
        for table, prepared in pages.items():
            if not prepared:
                continue
            pins = {r[0] for r in db.execute('SELECT DISTINCT seq FROM v2_source_exception_pins '
                    'WHERE seq IN (SELECT value FROM json_each(?))', (page_ids[table],))}
            cursor = cursors.get(table, 0)
            use_cursor = table != 'v2_source_outbox' or not indexed_hot
            db.execute("INSERT OR IGNORE INTO v2_transport_gc_state(kind) VALUES(?)", (table,))
            if use_cursor and db.execute("SELECT cursor FROM v2_transport_gc_state WHERE kind=?", (table,)).fetchone()[0] != cursor:
                continue
            last = 0 if not prepared else cursor
            for row, recent, raw_json, hashes, learned_time in prepared:
                seq = row["seq"]
                if seq > floor or (not recent and seq in pins and raw_json is None):
                    break
                current = db.execute("SELECT * FROM " + table + " WHERE seq=?", (seq,)).fetchone()
                if current is not None and tuple(current) != tuple(row):
                    break
                last = seq
                report["scanned"] += 1
                if current is not None and recent and learned_time is not None:
                    db.execute('INSERT INTO v2_archive_times VALUES(?,?,?,?) ON CONFLICT(kind,seq) '
                               'DO UPDATE SET digest=excluded.digest,recorded_at=excluded.recorded_at',
                               (table, seq, row['digest'], learned_time))
                if current is None or recent:
                    continue
                if seq in pins:
                    db.execute("INSERT OR REPLACE INTO v2_source_exception_receipts VALUES(?,?)",
                               (seq, raw_json))
                    replace_packet_hashes(db, "transport_exception", seq, hashes)
                    report["exceptions"] += 1
                if table=='v2_source_outbox':
                    db.execute('DELETE FROM v2_source_outbox WHERE seq=?',(row['seq'],))
                    db.execute('DELETE FROM v2_source_receipts WHERE id=?',(row['receipt_id'],))
                else:
                    db.execute('DELETE FROM '+table+' WHERE seq=?',(row['seq'],))
                    if has_archive_times:
                        db.execute('DELETE FROM v2_archive_times WHERE kind=? AND seq=?', (table, row['seq']))
                    db.execute('DELETE FROM artifact_ref WHERE owner_type=? AND owner_id=?',
                               ('transport_pack' if table=='v2_receipt_packs' else 'transport_archive',str(row['seq'])))
                    if not db.execute('SELECT 1 FROM '+table+' WHERE digest=? LIMIT 1',(row['digest'],)).fetchone():
                        folder='receipt-packs' if table=='v2_receipt_packs' else 'receipt-files'
                        db.execute('INSERT OR IGNORE INTO v2_transport_files_exit VALUES(?,?,?)',(folder,row['digest'],time.time()+300))
                db.execute('UPDATE v2_transport_gc_state SET expired_floor=max(expired_floor,?) WHERE kind=?',(row['seq'],table))
                report['expired']+=1
            if use_cursor:
                db.execute('UPDATE v2_transport_gc_state SET cursor=? WHERE kind=? AND cursor IS NOT ?', (last, table, last))
        if hot_only:
            return report
        for seq, in db.execute('SELECT seq FROM v2_source_exception_receipts WHERE NOT EXISTS(SELECT 1 FROM v2_source_exception_pins p WHERE p.seq=v2_source_exception_receipts.seq) LIMIT ?',(limit,)).fetchall():
            db.execute('DELETE FROM v2_source_exception_receipts WHERE seq=?',(seq,))
            db.execute("DELETE FROM artifact_ref WHERE owner_type='transport_exception' AND owner_id=?",(str(seq),))
        for folder,digest in db.execute('SELECT folder,digest FROM v2_transport_files_exit WHERE not_before<=? LIMIT 1',(time.time(),)).fetchall():
            table='v2_receipt_packs' if folder=='receipt-packs' else 'v2_receipt_archive'
            if not db.execute('SELECT 1 FROM '+table+' WHERE digest=? LIMIT 1',(digest,)).fetchone():
                p=source.path.parent/folder/source.source/digest[:2]/digest
                if p.exists():
                    if p.is_symlink() or p.resolve().parent.parent != (source.path.parent/folder/source.source).resolve():
                        raise ValueError('transport path escaped owner')
                    detach(p, discard)
                db.execute('DELETE FROM v2_transport_files_exit WHERE folder=? AND digest=?',(folder,digest))
            else:
                db.execute('DELETE FROM v2_transport_files_exit WHERE folder=? AND digest=?',(folder,digest))
    report['files'] += drain(discard)
    return report
