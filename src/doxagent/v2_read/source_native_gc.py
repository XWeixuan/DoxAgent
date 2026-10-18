"""Bounded source-native reachability reconciliation, owned by transport maintenance."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import re
import shutil
import time

from .outbox import quoted
from .source_lifecycle import native_hashes,replace_packet_refs


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS v2_native_ref_scan(owner TEXT PRIMARY KEY,cursor INTEGER,done INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS v2_native_exit(id TEXT PRIMARY KEY,not_before REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS v2_native_inventory(id INTEGER PRIMARY KEY CHECK(id=1),after_digest TEXT)')


def reconcile(source, *, limit=64, seconds=.3):
    """Repair old append-only owner refs before considering any source CAS file."""
    started=time.monotonic();report=dict(rows=0,owners=0,ready=False)
    with closing(source.connection(write=True)) as db,db:
        db.execute('BEGIN IMMEDIATE');migrate(db)
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables=[]
        for table in sorted(names):
            if table.startswith('sqlite_'):continue
            info=list(db.execute('PRAGMA table_info('+quoted(table)+')'))
            columns=[r[1] for r in info if r[1] in {'payload','payload_json','inputs','receipt','data_json'}]
            if columns:
                keys=[r[1] for r in sorted((r for r in info if r[5]),key=lambda r:r[5])]
                tables.append((table,columns,keys))
        packets=[(table,owner) for table,owner in (('v2_receipt_packs','transport_pack'),('v2_receipt_archive','transport_archive'),
                 ('v2_source_exception_receipts','transport_exception')) if table in names]
        # Register the complete work set before a time budget can yield. A
        # completed prefix is not proof that every owner has been reconciled.
        db.executemany('INSERT OR IGNORE INTO v2_native_ref_scan VALUES(?,0,0)',
                       [(t,) for t,_,_ in tables]+[(owner,) for _,owner in packets])
        for table,columns,keys in tables:
            db.execute('INSERT OR IGNORE INTO v2_native_ref_scan VALUES(?,0,0)',(table,))
            after,done=db.execute('SELECT cursor,done FROM v2_native_ref_scan WHERE owner=?',(table,)).fetchone()
            if done:continue
            rows=db.execute('SELECT rowid AS _native_rowid,* FROM '+quoted(table)+' WHERE rowid>? ORDER BY rowid LIMIT ?',(after,limit)).fetchall()
            for row in rows:
                identity=json.dumps([row[k] for k in keys],separators=(',',':'),ensure_ascii=False) if keys else str(row['_native_rowid'])
                replace_packet_refs(db,table,identity,{k:row[k] for k in columns})
                after=row['_native_rowid'];report['rows']+=1
                if time.monotonic()-started>=seconds:break
            if not rows:
                # Only known table owners are reconciled. Unknown/manual holds
                # remain individual exceptions, never a global GC freeze.
                identity=('json_array('+','.join('t.'+quoted(k) for k in keys)+')') if keys else 'CAST(t.rowid AS TEXT)'
                db.execute('DELETE FROM artifact_ref WHERE owner_type=? AND NOT EXISTS(SELECT 1 FROM '+quoted(table)+' t WHERE '+identity+'=artifact_ref.owner_id)',(table,))
                done=1;report['owners']+=1
            db.execute('UPDATE v2_native_ref_scan SET cursor=?,done=? WHERE owner=?',(after,done,table))
            if time.monotonic()-started>=seconds:break
        # Imported archive mappings need explicit refs; their payloads do not
        # reside in SQL JSON columns. Usually expiry has already removed them.
        for table,owner in packets:
            db.execute('INSERT OR IGNORE INTO v2_native_ref_scan VALUES(?,0,0)',(owner,))
            after,done=db.execute('SELECT cursor,done FROM v2_native_ref_scan WHERE owner=?',(owner,)).fetchone()
            if done:continue
            rows=db.execute('SELECT seq FROM '+table+' WHERE seq>? ORDER BY seq LIMIT ?',(after,limit)).fetchall()
            for seq, in rows:
                raw=source.event(seq,decode=False)
                if raw is None:raise ValueError('native ref packet unavailable')
                replace_packet_refs(db,owner,seq,raw);after=seq;report['rows']+=1
                if time.monotonic()-started>=seconds:break
            if not rows:
                db.execute('DELETE FROM artifact_ref WHERE owner_type=? AND NOT EXISTS(SELECT 1 FROM '+table+' t WHERE CAST(t.seq AS TEXT)=artifact_ref.owner_id)',(owner,));done=1
            db.execute('UPDATE v2_native_ref_scan SET cursor=?,done=? WHERE owner=?',(after,done,owner))
            if time.monotonic()-started>=seconds:break
        report['ready']=not db.execute('SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1').fetchone()
    return report


def inventory(source, *, limit=64):
    """Register only verified-format native CAS paths, including abandoned puts."""
    root=source.path.parent/'native-files';count=0;last=''
    with closing(source.connection(write=True)) as db,db:
        db.execute('BEGIN IMMEDIATE');migrate(db)
        if db.execute('SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1').fetchone():return 0
        row=db.execute('SELECT after_digest FROM v2_native_inventory WHERE id=1').fetchone();after=row[0] if row else ''
        if root.is_symlink():raise ValueError('native root is linked')
        if root.exists():
            for prefix in sorted(root.iterdir()):
                if not prefix.is_dir() or prefix.is_symlink() or not re.fullmatch('[0-9a-f]{2}',prefix.name):continue
                if after and prefix.name<after[:2]:continue
                for p in sorted(prefix.iterdir()):
                    if p.name<=after or p.is_symlink() or not re.fullmatch('[0-9a-f]{64}',p.name):continue
                    if p.parent.name!=p.name[:2]:raise ValueError('native CAS prefix mismatch')
                    manifest=p/'manifest.json'
                    value=json.loads(manifest.read_text(encoding='utf-8'))
                    if value.get('sha256')!=p.name or value.get('version')!=1:raise ValueError('native inventory manifest mismatch')
                    created=datetime.fromtimestamp(manifest.stat().st_mtime,UTC).isoformat()
                    db.execute("INSERT OR IGNORE INTO artifact(id,sha256,size,codec,location,class,created_at) VALUES(?,?,?,'json-chunks-v1',?,'BUSINESS',?)",
                               (p.name,p.name,value['size'],'native-files/'+p.name[:2]+'/'+p.name,created))
                    count+=1;last=p.name
                    if count>=limit:break
                if count>=limit:break
        db.execute('INSERT INTO v2_native_inventory VALUES(1,?) ON CONFLICT(id) DO UPDATE SET after_digest=excluded.after_digest',(last if count>=limit else '',))
    return count


def collect(source, *, now=None, limit=32):
    """Current/immutable/transport/hold refs protect files; unowned puts get 7d."""
    now=now or datetime.now(UTC);report=dict(quarantined=0,deleted=0,allocated=0)
    protected="EXISTS(SELECT 1 FROM artifact_ref r WHERE r.artifact_id=a.id) OR EXISTS(SELECT 1 FROM retention_hold h WHERE h.released_at IS NULL AND (h.scope='ALL' OR h.id=a.id))"
    root=source.path.parent.resolve();quarantine=root/'native-quarantine'
    with closing(source.connection(write=True)) as db,db:
        db.execute('BEGIN IMMEDIATE');migrate(db)
        if not db.execute('SELECT 1 FROM v2_native_ref_scan LIMIT 1').fetchone() or db.execute('SELECT 1 FROM v2_native_ref_scan WHERE done=0 LIMIT 1').fetchone():return report
        candidates=db.execute("SELECT a.id FROM artifact a WHERE a.state='LIVE' AND a.codec='json-chunks-v1' AND a.location='native-files/'||substr(a.id,1,2)||'/'||a.id AND a.created_at<? AND NOT ("+protected+") LIMIT ?",((now-timedelta(days=7)).isoformat(),limit)).fetchall()
        for digest, in candidates:
            if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('invalid native identity')
            p=root/'native-files'/digest[:2]/digest;q=quarantine/digest
            if p.is_symlink() or q.is_symlink():raise ValueError('linked native CAS')
            quarantine.mkdir(exist_ok=True)
            if p.exists():
                if q.exists():shutil.rmtree(q)
                p.rename(q)
            if q.exists():
                db.execute("UPDATE artifact SET state='QUARANTINED' WHERE id=?",(digest,))
                db.execute('INSERT OR REPLACE INTO v2_native_exit VALUES(?,?)',(digest,time.time()+300))
                report['quarantined']+=1
            else:
                db.execute("UPDATE artifact SET state='DELETED' WHERE id=?",(digest,))
        for digest,not_before in db.execute('SELECT id,not_before FROM v2_native_exit LIMIT ?',(limit,)).fetchall():
            p=root/'native-files'/digest[:2]/digest;q=quarantine/digest
            if p.is_symlink() or q.is_symlink():raise ValueError('linked native quarantine')
            if db.execute('SELECT 1 FROM artifact a WHERE a.id=? AND ('+protected+')',(digest,)).fetchone():
                if not p.exists() and q.exists():p.parent.mkdir(parents=True,exist_ok=True);q.rename(p)
                elif q.exists():shutil.rmtree(q)
                db.execute("UPDATE artifact SET state='LIVE' WHERE id=?",(digest,))
                db.execute('DELETE FROM v2_native_exit WHERE id=?',(digest,));continue
            if not_before>time.time():continue
            if q.exists():
                for f in q.iterdir():
                    if f.is_file():
                        stat=f.stat();report['allocated']+=getattr(stat,'st_blocks',(stat.st_size+511)//512)*512
                shutil.rmtree(q)
            db.execute("UPDATE artifact SET state='DELETED' WHERE id=?",(digest,))
            db.execute('DELETE FROM v2_native_exit WHERE id=?',(digest,));report['deleted']+=1
    return report
