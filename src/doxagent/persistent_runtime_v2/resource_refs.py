"""Exact live Case pins; existing Case transactions register and release owners."""
import json
from contextlib import closing
import sqlite3

ACTIVE = ('CREATED','RUNNING','ADJUDICATED','PENDING_W3','PENDING_RETRY')


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS runtime_case_resource_refs('
               'case_id TEXT PRIMARY KEY,ticker TEXT NOT NULL,event_root TEXT,event_version INTEGER,policy_version INTEGER)')
    db.execute('CREATE INDEX IF NOT EXISTS runtime_resource_event_ref ON runtime_case_resource_refs(event_root,ticker,event_version)')
    db.execute('CREATE INDEX IF NOT EXISTS runtime_resource_policy_ref ON runtime_case_resource_refs(ticker,policy_version)')
    db.execute('CREATE TABLE IF NOT EXISTS runtime_resource_ref_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    statuses=','.join("'"+s+"'" for s in ACTIVE)
    insert=("INSERT OR REPLACE INTO runtime_case_resource_refs SELECT NEW.case_id,NEW.ticker,"
            "json_extract(NEW.payload_json,'$.version_pin.event_library_root'),"
            "json_extract(NEW.payload_json,'$.version_pin.event_library_version'),"
            "json_extract(NEW.payload_json,'$.version_pin.policy_set_version') "
            "WHERE NEW.status IN ("+statuses+");")
    db.execute('CREATE TRIGGER IF NOT EXISTS runtime_case_resource_insert AFTER INSERT ON runtime_v2_cases BEGIN '+insert+' END')
    # VersionPin is immutable for a persisted Case. Ordinary Context/phase saves
    # do not run JSON queries or write another owner row.
    db.execute('CREATE TRIGGER IF NOT EXISTS runtime_case_resource_status AFTER UPDATE OF status ON runtime_v2_cases '
               'WHEN OLD.status<>NEW.status BEGIN DELETE FROM runtime_case_resource_refs WHERE case_id=NEW.case_id;'+insert+' END')
    db.execute('CREATE TRIGGER IF NOT EXISTS runtime_case_resource_delete AFTER DELETE ON runtime_v2_cases '
               'BEGIN DELETE FROM runtime_case_resource_refs WHERE case_id=OLD.case_id; END')


def backfill(journal, *, limit=32):
    """One indexed active-status page, only during migration; never expand a Case."""
    with journal.transaction(write=False,expand=False) as db:
        meta=dict(db.execute('SELECT key,value FROM runtime_resource_ref_meta'))
        if meta.get('complete')=='1':return True
        phase=int(meta.get('phase',0));after=json.loads(meta.get('after','["",""]'))
        rows=db.execute('SELECT case_id,updated_at FROM runtime_v2_cases INDEXED BY idx_runtime_v2_cases_status '
            'WHERE status=? AND (updated_at,case_id)>(?,?) ORDER BY updated_at,case_id LIMIT ?',
            (ACTIVE[phase],*after,limit)).fetchall()
    with journal.transaction(timeout=.01,expand=False,operation='resource.refs.backfill') as db:
        # Re-read only these exact identities under the existing short fence.
        for row in rows:
            db.execute('INSERT OR REPLACE INTO runtime_case_resource_refs SELECT case_id,ticker,'
                "json_extract(payload_json,'$.version_pin.event_library_root'),"
                "json_extract(payload_json,'$.version_pin.event_library_version'),"
                "json_extract(payload_json,'$.version_pin.policy_set_version') "
                'FROM runtime_v2_cases WHERE case_id=? AND status IN ('+','.join('?' for _ in ACTIVE)+')',
                (row[0],*ACTIVE))
        if len(rows)==limit:after=[rows[-1][1],rows[-1][0]]
        else:phase+=1;after=['','']
        complete=phase==len(ACTIVE)
        for key,value in [('phase',str(phase)),('after',json.dumps(after)),('complete','1' if complete else '0')]:
            db.execute('INSERT INTO runtime_resource_ref_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
    return complete


def pins(journal, ticker, *, event_root=None):
    with journal.transaction(write=False,expand=False) as db:
        if not db.execute("SELECT 1 FROM runtime_resource_ref_meta WHERE key='complete' AND value='1'").fetchone():
            raise ValueError('RESOURCE_REFERENCE_BACKFILL_PENDING')
        if event_root is None:
            return {r[0] for r in db.execute('SELECT DISTINCT policy_version FROM runtime_case_resource_refs WHERE ticker=?',(ticker,)) if r[0] is not None}
        return {r[0] for r in db.execute('SELECT DISTINCT event_version FROM runtime_case_resource_refs WHERE event_root=? AND ticker=?',(event_root,ticker)) if r[0] is not None}
