"""Event-owned bounded exits. Business clocks and current mappings outlive process bodies."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import hashlib
import json


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS event_batch_exit(batch_id TEXT PRIMARY KEY,due TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS event_batch_exit_due ON event_batch_exit(due,batch_id)')
    db.execute('CREATE TABLE IF NOT EXISTS event_batch_retirements(batch_id TEXT PRIMARY KEY,input_sha256 TEXT NOT NULL,retired_at TEXT NOT NULL)')


def enqueue(db, batch_ids, *, now):
    due=(now+timedelta(hours=24)).isoformat()
    db.executemany('INSERT OR IGNORE INTO event_batch_exit VALUES(?,?)',[(i,due) for i in batch_ids])


def collect_batches(repository, *, now=None, is_live=lambda run:True, limit=1):
    now=now or datetime.now(UTC)
    with repository._read() as db:
        rows=db.execute('SELECT b.batch_id,b.bundle_run_id,b.payload_json FROM event_batch_exit q '
            'JOIN delta_batches b USING(batch_id) WHERE q.due<=? ORDER BY q.due,q.batch_id LIMIT ?',
            (now.isoformat(),limit)).fetchall()
    processed=[]
    for row in rows:
        if is_live(row['bundle_run_id']):
            with closing(repository._connect()) as db,db:
                db.execute('PRAGMA busy_timeout=10')
                db.execute('UPDATE event_batch_exit SET due=? WHERE batch_id=?',((now+timedelta(minutes=15)).isoformat(),row['batch_id']))
            continue
        digest=hashlib.sha256(row['payload_json'].encode()).hexdigest()
        with closing(repository._connect()) as db,db:
            db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM delta_items WHERE batch_id=? AND status='PENDING' LIMIT 1",(row['batch_id'],)).fetchone():continue
            if not db.execute("SELECT 1 FROM delta_batches WHERE batch_id=? AND status='PUBLISHED'",(row['batch_id'],)).fetchone():continue
            first=db.execute('INSERT OR IGNORE INTO event_batch_retirements VALUES(?,?,?)',(row['batch_id'],digest,now.isoformat())).rowcount
            if first:
                db.execute('UPDATE delta_batches SET payload_json=? WHERE batch_id=?',
                           (json.dumps({'process_retired':True,'input_sha256':digest}),row['batch_id']))
            # Keep stable mapping/disposition/counters, discard repeated fact bodies.
            changed=db.execute("UPDATE delta_items SET payload_json='{}' WHERE rowid IN "
                "(SELECT rowid FROM delta_items WHERE batch_id=? AND payload_json<>'{}' LIMIT 32)",(row['batch_id'],)).rowcount
            if not db.execute("SELECT 1 FROM delta_items WHERE batch_id=? AND payload_json<>'{}' LIMIT 1",(row['batch_id'],)).fetchone():
                db.execute('DELETE FROM event_batch_exit WHERE batch_id=?',(row['batch_id'],))
            processed.append({'batch':row['batch_id'],'items':changed})
    return processed


EVENT_TABLES=(('canonical_event_revisions','event_no'),('canonical_event_states','event_no'),
              ('event_fact_memberships',None),('event_relations',None))
FACT_TABLES=(('canonical_fact_revisions','fact_no'),('canonical_fact_states','fact_no'))


def migrate_history(db):
    db.execute('CREATE TABLE IF NOT EXISTS event_reference_exit(ticker TEXT,version INTEGER,PRIMARY KEY(ticker,version))')
    db.execute('CREATE INDEX IF NOT EXISTS event_reference_delta_to ON reference_view_deltas(ticker,to_library_version,from_library_version)')
    db.execute('CREATE TABLE IF NOT EXISTS event_history_exit('
        'ticker TEXT,kind TEXT,object_no INTEGER,generation INTEGER NOT NULL DEFAULT 1,'
        "stage INTEGER NOT NULL DEFAULT 0,after_key TEXT NOT NULL DEFAULT '[]',due TEXT NOT NULL,"
        'PRIMARY KEY(ticker,kind,object_no))')
    db.execute('CREATE INDEX IF NOT EXISTS event_history_exit_due ON event_history_exit(due,ticker,kind,object_no)')
    for table,kind,column in (
        ('canonical_event_revisions','event','event_no'),('canonical_event_states','event','event_no'),
        ('canonical_fact_revisions','fact','fact_no'),('canonical_fact_states','fact','fact_no'),
        ('event_fact_memberships','event','event_no'),('event_relations','event','source_event_no'),
    ):
        for operation in (('INSERT','UPDATE') if column is None or table in {'event_fact_memberships','event_relations'} else ('INSERT',)):
            name='event_history_'+table+'_'+operation.lower()
            db.execute('CREATE TRIGGER IF NOT EXISTS '+name+' AFTER '+operation+' ON '+table+' BEGIN '
                f"INSERT INTO event_history_exit(ticker,kind,object_no,due) VALUES(NEW.ticker,'{kind}',NEW.{column},"+
                "strftime('%Y-%m-%dT%H:%M:%f+00:00','now','+7 days')) "
                'ON CONFLICT(ticker,kind,object_no) DO UPDATE SET generation=generation+1,stage=0,'
                "after_key='[]',due=min(due,excluded.due); END")


def register_legacy(repository,ticker,*,limit=32):
    """One-time native identity pages; not a repeated history or Context scan."""
    key='history-owner-backfill:'+ticker
    with repository._read() as db:
        saved=db.execute('SELECT value FROM event_storage_progress WHERE key=?',(key,)).fetchone()
        state=json.loads(saved[0]) if saved else {'kind':'event','after':0}
        if state.get('complete'):return True
        column='event_no' if state['kind']=='event' else 'fact_no'
        table='canonical_events' if state['kind']=='event' else 'canonical_facts'
        rows=db.execute('SELECT '+column+' FROM '+table+' WHERE ticker=? AND '+column+'>? ORDER BY '+column+' LIMIT ?', (ticker,state['after'],limit)).fetchall()
    with closing(repository._connect()) as db,db:
        db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
        db.executemany("INSERT INTO event_history_exit(ticker,kind,object_no,due) VALUES(?,?,?,?) "
                       'ON CONFLICT(ticker,kind,object_no) DO UPDATE SET due=min(due,excluded.due)',
                       [(ticker,state['kind'],r[0],'1970-01-01T00:00:00+00:00') for r in rows])
        if len(rows)==limit:state['after']=rows[-1][0]
        elif state['kind']=='event':state={'kind':'fact','after':0}
        else:state={'complete':True}
        db.execute('INSERT INTO event_storage_progress VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(state)))
    return bool(state.get('complete'))


def collect_history(repository,ticker,*,keep_versions=(),now=None,limit=32):
    """Indexed version retirement and one changed-object page; no whole-table sweep."""
    from .admission_evidence import ready
    now=now or datetime.now(UTC);cutoff=(now-timedelta(days=7)).isoformat()
    with repository._read() as db:
        if not ready(db,ticker):return {'pending_admissions':True}
        head=db.execute('SELECT published_version FROM library_heads WHERE ticker=?',(ticker,)).fetchone()[0]
        keep=set(keep_versions)|{head}
        keep.update(r[0] for r in db.execute("SELECT version FROM library_versions WHERE ticker=? AND status='PUBLISHED' ORDER BY version DESC LIMIT 3",(ticker,)))
        versions=db.execute("SELECT version,coalesce(published_at,created_at) FROM library_versions WHERE ticker=? AND status='PUBLISHED' AND version<? AND version NOT IN (SELECT value FROM json_each(?)) ORDER BY version LIMIT ?",(ticker,head,json.dumps(sorted(keep)),limit)).fetchall()
        victims=[r[0] for r in versions if r[1]<cutoff]
        owner=db.execute('SELECT * FROM event_history_exit WHERE ticker=? AND due<=? ORDER BY due,ticker,kind,object_no LIMIT 1',(ticker,now.isoformat())).fetchone()
    if victims:
        with closing(repository._connect()) as db,db:
            db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
            db.executemany("UPDATE library_versions SET status='HISTORY_RETIRED' WHERE ticker=? AND version=? AND version<>(SELECT published_version FROM library_heads WHERE ticker=?)",[(ticker,v,ticker) for v in victims])
            db.executemany('INSERT OR IGNORE INTO event_reference_exit VALUES(?,?)',[(ticker,v) for v in victims])
        return {'retired_versions':victims,'pending':True}
    with repository._read() as db:
        exiting=db.execute('SELECT version FROM event_reference_exit WHERE ticker=? LIMIT 1',(ticker,)).fetchone()
        pairs=db.execute('SELECT from_library_version,to_library_version FROM reference_view_deltas WHERE ticker=? AND (from_library_version=? OR to_library_version=?) LIMIT 4',(ticker,exiting[0],exiting[0])).fetchall() if exiting else []
    if exiting:
        with closing(repository._connect()) as db,db:
            db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
            db.executemany('DELETE FROM reference_view_deltas WHERE ticker=? AND from_library_version=? AND to_library_version=?',[(ticker,*r) for r in pairs])
            if len(pairs)<4:db.execute('DELETE FROM event_reference_exit WHERE ticker=? AND version=?',(ticker,exiting[0]))
        return {'retired_reference_views':len(pairs),'pending':True}
    if owner is None:return {'idle':True}
    tables=EVENT_TABLES if owner['kind']=='event' else FACT_TABLES
    table,column=tables[owner['stage']];after=json.loads(owner['after_key']);number=owner['object_no']
    with repository._read() as db:
        if column:
            page=db.execute('SELECT rowid,'+column+',library_version FROM '+table+' WHERE ticker=? AND '+column+'=? AND library_version>? ORDER BY library_version LIMIT ?', (ticker,number,after[0] if after else -1,limit)).fetchall()
            cursor=[page[-1][2]] if page else []
            delete=[]
            for row in page:
                following=db.execute('SELECT min(library_version) FROM '+table+' WHERE ticker=? AND '+column+'=? AND library_version>?',(ticker,number,row[2])).fetchone()[0]
                if following is not None and not db.execute("SELECT 1 FROM library_versions WHERE ticker=? AND status='PUBLISHED' AND version>=? AND version<? LIMIT 1",(ticker,row[2],following)).fetchone():delete.append(tuple(row))
        else:
            relation=table=='event_relations'
            fields=('relation_type','target_event_no','valid_from_version') if relation else ('fact_no','valid_from_version')
            match='source_event_no' if relation else 'event_no'
            ordering=','.join(fields);marks=','.join('?' for _ in fields)
            page=db.execute('SELECT rowid,valid_from_version,valid_to_version,'+ordering+' FROM '+table+
                ' WHERE ticker=? AND '+match+'=? AND ('+ordering+')>('+marks+') ORDER BY '+ordering+' LIMIT ?',
                (ticker,number,*(after or (['',-1,-1] if relation else [-1,-1])),limit)).fetchall()
            cursor=list(page[-1][3:]) if page else []
            delete=[tuple(r[:3]) for r in page if r[2] is not None and not db.execute("SELECT 1 FROM library_versions WHERE ticker=? AND status='PUBLISHED' AND version>=? AND version<=? LIMIT 1",(ticker,r[1],r[2])).fetchone()]
    with closing(repository._connect()) as db,db:
        db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
        for row in delete:
            if column:db.execute('DELETE FROM '+table+' WHERE rowid=? AND '+column+'=? AND library_version=?',row)
            else:db.execute('DELETE FROM '+table+' WHERE rowid=? AND valid_from_version=? AND valid_to_version IS ?',row)
        stage=owner['stage'] if len(page)==limit else owner['stage']+1
        due=now.isoformat()
        if stage==len(tables):
            # An indexed local retry allows old pinned state to exit later;
            # unchanged owners without history leave this queue completely.
            history=False
            for candidate,identity in tables:
                if identity:
                    history=bool(db.execute('SELECT 1 FROM '+candidate+' WHERE ticker=? AND '+identity+'=? ORDER BY library_version LIMIT 1 OFFSET 1',(ticker,number)).fetchone())
                else:
                    identity='source_event_no' if candidate=='event_relations' else 'event_no'
                    history=bool(db.execute('SELECT 1 FROM '+candidate+' WHERE ticker=? AND '+identity+'=? AND valid_to_version IS NOT NULL LIMIT 1',(ticker,number)).fetchone())
                if history:break
            if not history:
                db.execute('DELETE FROM event_history_exit WHERE ticker=? AND kind=? AND object_no=? AND generation=?',(ticker,owner['kind'],number,owner['generation']))
                return {'table':table,'scanned':len(page),'deleted':len(delete),'owner_done':True}
            stage=0;cursor=[];due=(now+timedelta(days=1)).isoformat()
        elif len(page)<limit:cursor=[]
        db.execute('UPDATE event_history_exit SET stage=?,after_key=?,due=? WHERE ticker=? AND kind=? AND object_no=? AND generation=?',(stage,json.dumps(cursor),due,ticker,owner['kind'],number,owner['generation']))
    return {'table':table,'scanned':len(page),'deleted':len(delete),'pending':due==now.isoformat()}
