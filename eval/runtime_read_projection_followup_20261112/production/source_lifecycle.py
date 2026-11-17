"""Source-owned transport expiry: current domain rows, not CDC packs, rebuild Read."""
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import shutil
import time


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


def retire_consumer(source, consumer):
    """Explicit generation retirement; inactivity alone never retires a consumer."""
    with closing(source.connection(write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE'); migrate(db)
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
    from .artifact_registry import retain
    db.execute('DELETE FROM artifact_ref WHERE owner_type=? AND owner_id=?',(owner,str(identity)))
    for digest in set(native_hashes(packet)):
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
            for row in read.execute('SELECT * FROM '+table+' WHERE '+column+'=? AND valid_from>? AND valid_from<=?',(kind,previous,boundary)):
                row=dict(row)
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
        if records:
            with closing(source.connection(write=True)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                owner=db.execute('SELECT path FROM v2_serving_fact_owner WHERE id=1').fetchone()
                if not owner or owner[0]!=str(store.path):return False
                db.executemany('INSERT INTO v2_serving_business_facts VALUES(?,?,?,?) ON CONFLICT(kind,ticker,id) DO UPDATE SET payload=excluded.payload WHERE payload<>excluded.payload',records)
                db.execute('INSERT INTO v2_transport_gc_state(kind,cursor) VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET cursor=excluded.cursor',(key,boundary))
        cursors[key]=boundary
    source._fact_cursors=cursors
    complete=all(cursors.get(prefix+':'.join(j),0)>=seq for j in jobs)
    if complete:
        with store.connect() as read:
            hits=[r[0] for r in read.execute("SELECT ticker||':'||id FROM object_current WHERE kind='policy_hit_set'")]
        hit_ids=frozenset(hits)
        if hit_ids!=getattr(source,'_business_fact_hits',None):
            with closing(source.connection(write=True)) as db,db:
                db.execute("DELETE FROM v2_serving_business_facts WHERE kind='policy_hit_set' AND ticker||':'||id NOT IN (SELECT value FROM json_each(?))",(json.dumps(hits),))
            source._business_fact_hits=hit_ids
        source._business_fact_seq=seq
    return complete


def expire(source, *, now=None, limit=500):
    """Bounded scan, including old archives; one repair pins one packet only."""
    if not 1 <= limit <= 500:raise ValueError('invalid expiry batch')
    now=now or datetime.now(UTC);before=now-timedelta(days=7)
    report=dict(expired=0,exceptions=0,scanned=0,files=0)
    # The capture contract and the immutable business tables remain in place.
    # Expiry never changes domain rows, capture epoch or source head.
    if not source.capture():raise ValueError('current source capture metadata required')
    with closing(source.connection(write=True)) as db, db:
        db.execute('BEGIN IMMEDIATE');migrate(db)
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
        pins={r[0] for r in db.execute('SELECT seq FROM v2_source_exception_pins')}
        tables=['v2_source_outbox','v2_receipt_archive']
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_receipt_packs'").fetchone():tables.append('v2_receipt_packs')
        pack_cache = {}
        for table in tables:
            db.execute('INSERT OR IGNORE INTO v2_transport_gc_state(kind) VALUES(?)',(table,))
            cursor=db.execute('SELECT cursor FROM v2_transport_gc_state WHERE kind=?',(table,)).fetchone()[0]
            rows=db.execute('SELECT * FROM '+table+' WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?',(cursor,floor,limit)).fetchall()
            for row in rows:
                report['scanned']+=1
                if table=='v2_source_outbox' and datetime.fromisoformat(row['recorded_at']) >= before:
                    continue
                packet=source.event(row['seq'],decode=False,connection=db,pack_cache=pack_cache)
                if packet is None:raise ValueError('transport packet missing')
                if datetime.fromisoformat(packet['recorded_at']) >= before:continue
                if row['seq'] in pins:
                    raw={k:v for k,v in packet.items() if k!='row'}
                    db.execute('INSERT OR REPLACE INTO v2_source_exception_receipts VALUES(?,?)',
                               (row['seq'],json.dumps(raw,ensure_ascii=False,separators=(',',':'))))
                    replace_packet_refs(db,'transport_exception',row['seq'],raw)
                    report['exceptions']+=1
                if table=='v2_source_outbox':
                    db.execute('DELETE FROM v2_source_outbox WHERE seq=?',(row['seq'],))
                    db.execute('DELETE FROM v2_source_receipts WHERE id=?',(row['receipt_id'],))
                else:
                    db.execute('DELETE FROM '+table+' WHERE seq=?',(row['seq'],))
                    db.execute('DELETE FROM artifact_ref WHERE owner_type=? AND owner_id=?',
                               ('transport_pack' if table=='v2_receipt_packs' else 'transport_archive',str(row['seq'])))
                    if not db.execute('SELECT 1 FROM '+table+' WHERE digest=? LIMIT 1',(row['digest'],)).fetchone():
                        folder='receipt-packs' if table=='v2_receipt_packs' else 'receipt-files'
                        db.execute('INSERT OR IGNORE INTO v2_transport_files_exit VALUES(?,?,?)',(folder,row['digest'],time.time()+300))
                db.execute('UPDATE v2_transport_gc_state SET expired_floor=max(expired_floor,?) WHERE kind=?',(row['seq'],table))
                report['expired']+=1
            db.execute('UPDATE v2_transport_gc_state SET cursor=? WHERE kind=?',(rows[-1]['seq'] if rows else 0,table))
        for seq, in db.execute('SELECT seq FROM v2_source_exception_receipts WHERE NOT EXISTS(SELECT 1 FROM v2_source_exception_pins p WHERE p.seq=v2_source_exception_receipts.seq) LIMIT ?',(limit,)).fetchall():
            db.execute('DELETE FROM v2_source_exception_receipts WHERE seq=?',(seq,))
            db.execute("DELETE FROM artifact_ref WHERE owner_type='transport_exception' AND owner_id=?",(str(seq),))
        for folder,digest in db.execute('SELECT folder,digest FROM v2_transport_files_exit WHERE not_before<=? LIMIT ?',(time.time(),limit)).fetchall():
            table='v2_receipt_packs' if folder=='receipt-packs' else 'v2_receipt_archive'
            if not db.execute('SELECT 1 FROM '+table+' WHERE digest=? LIMIT 1',(digest,)).fetchone():
                p=source.path.parent/folder/source.source/digest[:2]/digest
                if p.exists():
                    if p.is_symlink() or p.resolve().parent.parent != (source.path.parent/folder/source.source).resolve():
                        raise ValueError('transport path escaped owner')
                    shutil.rmtree(p);report['files']+=1
                db.execute('DELETE FROM v2_transport_files_exit WHERE folder=? AND digest=?',(folder,digest))
    return report
