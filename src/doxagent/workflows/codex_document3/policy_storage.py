"""Object sharing in the existing Native CAS; no independent history chain."""
import json
from pathlib import Path

from doxagent.v2_read.native_content import MARKER, NativeContent


def shared_ready(codec):
    # Roll readers before enabling a format which old Policy repositories
    # cannot decode. Checked only on publication, never during W3 admission.
    return (Path(codec.database).parent/'.d3-shared-policy-ready').is_file()


def pack_policy_set(codec: NativeContent, data):
    """Publish immutable objects before taking a SQLite writer.

    Each version keeps its order/identity/metadata; unchanged policy objects
    share content across staged/formal/history. Small objects stay inline.
    """
    if not shared_ready(codec):
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    value = dict(data)
    policies = []
    for policy in value.get('policies', []):
        policies.append(pack_object(codec,policy))
    value['policies'] = policies
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def pack_object(codec, value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return {MARKER:codec.files.put(raw),'bytes':len(raw)} if len(raw)>=2048 else value


def install_refs(db, table, columns, identity):
    from doxagent.v2_read.artifact_registry import migrate, native_triggers

    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", ('v2_artifact_'+table+'_INSERT',)).fetchone():
        migrate(db)
        native_triggers(db, table, columns, identity)


def convert_legacy(state):
    """One old row per explicit maintenance step, packing outside the writer.

    The durable PK cursor finishes once. New inserts already use this codec;
    ordinary current reads never run a conversion or walk historical versions.
    """
    if not shared_ready(state.codec):return {'waiting_for_reader_rollout':True}
    with state.connect() as db:
        saved=db.execute("SELECT value FROM codex_document3_storage_meta WHERE key='policy_object_backfill'").fetchone()
        phase,after=json.loads(saved[0]) if saved else [0,['',0]]
        if phase==2:return {'complete':True}
        table,key,column=(('codex_document3_policy_sets','policy_set_version','policy_set_json'),('codex_document3_v21_staged_sets','version','payload'))[phase]
        present=db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone()
        row=db.execute('SELECT ticker,'+key+','+column+(' ,is_current,runtime_projection_json' if phase==0 else ',receipt')+' FROM '+table+' WHERE (ticker,'+key+')>(?,?) ORDER BY ticker,'+key+' LIMIT 1',after).fetchone() if present else None
    if row:
        if len(row[2].encode())>8*1024**2:
            # Explicitly record a large migration owner without making it the
            # head-of-line blocker for every other version or terminal exit.
            with state.connect() as db:
                db.execute('PRAGMA busy_timeout=10')
                db.execute('INSERT OR REPLACE INTO codex_document3_storage_meta VALUES(?,?)',('policy_conversion_large:'+table+':'+row[0]+':'+str(row[1]),str(len(row[2].encode()))))
                db.execute("INSERT OR REPLACE INTO codex_document3_storage_meta VALUES('policy_object_backfill',?)",(json.dumps([phase,list(row[:2])]),))
            return {'deferred_large_version':row[1],'ticker':row[0]}
        value=state.codec.decode(json.loads(row[2]));packed=pack_policy_set(state.codec,value)
        derived=(row[4] if row[3] else '{}') if phase==0 else json.dumps({k:v for k,v in json.loads(row[3]).items() if k!='projection'},ensure_ascii=False,separators=(',',':'),sort_keys=True)
    with state.connect() as db:
        db.execute('PRAGMA busy_timeout=10');db.execute('BEGIN IMMEDIATE')
        if row:
            field='runtime_projection_json' if phase==0 else 'receipt'
            # Check the derivation fields too; a concurrent publication keeps its hot cache.
            guard=' AND is_current=? AND runtime_projection_json IS ?' if phase==0 else ' AND receipt=?'
            args=(row[3],row[4]) if phase==0 else (row[3],)
            changed=db.execute('UPDATE '+table+' SET '+column+'=?,'+field+'=? WHERE ticker=? AND '+key+'=? AND '+column+'=?'+guard,(packed,derived,row[0],row[1],row[2],*args)).rowcount
            if not changed:return {'retry':'owner_changed'}
            after=list(row[:2])
        else:phase+=1;after=['',0]
        db.execute("INSERT INTO codex_document3_storage_meta VALUES('policy_object_backfill',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps([phase,after]),))
    return {'complete':phase==2,'table':table,'version':row[1] if row else None}
