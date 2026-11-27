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
    rule = db.execute("SELECT value FROM runtime_resource_ref_meta WHERE key='rule'").fetchone()
    changed = not rule or rule[0] != 'reader-v3'
    if changed:
        db.execute('DELETE FROM runtime_resource_ref_meta')
        db.execute("INSERT INTO runtime_resource_ref_meta VALUES('rule','reader-v3')")
    statuses=','.join("'"+s+"'" for s in ACTIVE)
    insert=("INSERT OR IGNORE INTO runtime_case_resource_refs SELECT NEW.case_id,NEW.ticker,"
            "json_extract(NEW.payload_json,'$.version_pin.event_library_root'),"
            "json_extract(NEW.payload_json,'$.version_pin.event_library_version'),"
            "json_extract(NEW.payload_json,'$.version_pin.policy_set_version') "
            "WHERE NEW.status IN ("+statuses+") AND NOT EXISTS (SELECT 1 FROM runtime_case_resource_refs WHERE case_id=NEW.case_id);")
    if changed:db.execute('DROP TRIGGER IF EXISTS runtime_case_resource_insert')
    db.execute('CREATE TRIGGER IF NOT EXISTS runtime_case_resource_insert AFTER INSERT ON runtime_v2_cases BEGIN '+insert+' END')
    # VersionPin is immutable for a persisted Case. Ordinary Context/phase saves
    # do not run JSON queries or write another owner row.
    if changed:db.execute('DROP TRIGGER IF EXISTS runtime_case_resource_status')
    db.execute('CREATE TRIGGER IF NOT EXISTS runtime_case_resource_status AFTER UPDATE OF status ON runtime_v2_cases '
               'WHEN OLD.status<>NEW.status BEGIN DELETE FROM runtime_case_resource_refs WHERE case_id=NEW.case_id AND NEW.status NOT IN ('+statuses+');'+insert+' END')
    db.execute('CREATE TRIGGER IF NOT EXISTS runtime_case_resource_delete AFTER DELETE ON runtime_v2_cases '
               'BEGIN DELETE FROM runtime_case_resource_refs WHERE case_id=OLD.case_id; END')


def backfill(journal, *, limit=32):
    """One indexed active-status page, only during migration; never expand a Case."""
    with journal.transaction(write=False,expand=False) as db:
        sweeps=db.execute("SELECT v.key,v.payload FROM runtime_tasks t JOIN runtime_values v "
            "ON v.namespace='sweep_bundle' AND v.key=t.id WHERE t.kind='SWEEP' "
            "AND t.status IN ('PENDING','RUNNING','HELD') AND json_type(v.payload,'$.event_body_ref') IS NULL LIMIT 4").fetchall()
    if sweeps:
        headers=[]
        for key,raw in sweeps:
            inputs=json.loads(journal.content.text(raw))['inputs']
            headers.append((json.dumps(dict(root=inputs.get('event_library_root'),version=inputs['index']['version'])),key,raw))
        with journal.transaction(timeout=.01,expand=False,operation='sweep.refs.backfill') as db:
            for ref,key,raw in headers:
                db.execute("UPDATE runtime_values SET payload=json_set(payload,'$.event_body_ref',json(?)) "
                           "WHERE namespace='sweep_bundle' AND key=? AND payload=?",(ref,key,raw))
        return False
    from doxagent.semantic_clock import semantic_day
    with journal.transaction(write=False,expand=False) as db:
        daily=db.execute("SELECT ticker,semantic_day,payload_json FROM runtime_v2_w1_daily_indexes "
            "WHERE semantic_day>=? AND json_extract(payload_json,'$.event_library_version') IS NULL LIMIT ?",
            (str(semantic_day(journal.clock())),limit)).fetchall()
    headers=[(json.loads(journal.content.text(r[2]))['index']['version'],r) for r in daily]
    if headers:
        with journal.transaction(timeout=.01,expand=False,operation='daily.refs.backfill') as db:
            for version,row in headers:
                db.execute("UPDATE runtime_v2_w1_daily_indexes SET payload_json=json_set(payload_json,"
                    "'$.event_library_version',?) WHERE ticker=? AND semantic_day=? AND payload_json=?",
                    (version,*row))
        return False
    with journal.transaction(write=False,expand=False) as db:
        meta=dict(db.execute('SELECT key,value FROM runtime_resource_ref_meta'))
        if meta.get('complete')=='1':return True
        phase=int(meta.get('phase',0));after=json.loads(meta.get('after','["",""]'))
        rows=db.execute('SELECT case_id,updated_at,payload_json FROM runtime_v2_cases INDEXED BY idx_runtime_v2_cases_status '
            'WHERE status=? AND (updated_at,case_id)>(?,?) ORDER BY updated_at,case_id LIMIT ?',
            (ACTIVE[phase],*after,limit)).fetchall()
    from .schema import RuntimeCase
    prepared=[]
    for row in rows:
        case=RuntimeCase.model_validate(journal.content.decode(json.loads(row[2])))
        with journal.transaction(write=False,expand=False) as db:
            task=db.execute('SELECT status FROM runtime_tasks WHERE id=?',
                ('inbox:'+case.ticker+':'+case.source.source_message_id,)).fetchone()
            ref=list(reader_ref(case,legacy_w3=legacy_reader(db,case),
                                hot_terminal=bool(task and task[0] in ('FAILED','SUCCEEDED'))))
            for lane, index in [('W1',3)]:
                if case.w3_contract_version == 'persistent-runtime.w3.v6' and db.execute("SELECT 1 FROM runtime_values WHERE namespace='round_inputs' AND key=?",
                              (case.case_id+':'+lane+':R2',)).fetchone():ref[index]=None
        prepared.append((row,ref))
    with journal.transaction(timeout=.01,expand=False,operation='resource.refs.backfill') as db:
        for row,ref in prepared:
            db.execute('INSERT OR REPLACE INTO runtime_case_resource_refs SELECT ?,?,?,?,? '
                       'WHERE EXISTS(SELECT 1 FROM runtime_v2_cases WHERE case_id=? AND updated_at=?)',
                       (*ref,row[0],row[1]))
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
            return {r[0] for r in db.execute('SELECT DISTINCT policy_version FROM runtime_case_resource_refs WHERE ticker=?',(ticker,)) if r[0] is not None} | {r[3] for r in legacy_effect_refs(db,ticker) if r[3] is not None}
        return {r[0] for r in db.execute('SELECT DISTINCT event_version FROM runtime_case_resource_refs WHERE event_root=? AND ticker=?',(event_root,ticker)) if r[0] is not None}


def reader_ref(case, *, legacy_w3=False, hot_terminal=False):
    pin = case.frozen_inputs.get('w1_daily_index') or {}
    frozen = case.frozen_inputs.get('round_input_refs', {}) | case.frozen_inputs.get('round_inputs', {})
    event = None if hot_terminal or case.w1_final is not None or 'W1:R2' in frozen else pin.get(
        'event_library_version', case.version_pin.event_library_version)
    policy = None if hot_terminal or case.w2_final is not None else case.version_pin.policy_set_version
    if legacy_w3:
        return (case.case_id,case.ticker,case.version_pin.event_library_root,
                case.version_pin.event_library_version,case.version_pin.policy_set_version)
    return (case.case_id, case.ticker, pin.get('event_library_root', case.version_pin.event_library_root), event, policy)


def save_reader(db, case):
    """Already loaded Case, original transaction; no Native expansion/extra read."""
    if case.status.value not in ACTIVE:
        db.execute('DELETE FROM runtime_case_resource_refs WHERE case_id=?', (case.case_id,))
        return
    values = reader_ref(case,legacy_w3=legacy_reader(db,case))
    db.execute('INSERT INTO runtime_case_resource_refs VALUES(?,?,?,?,?) '
               'ON CONFLICT(case_id) DO UPDATE SET event_root=excluded.event_root,'
               'event_version=excluded.event_version,policy_version=excluded.policy_version '
               'WHERE event_root IS NOT excluded.event_root OR event_version IS NOT excluded.event_version '
               'OR policy_version IS NOT excluded.policy_version', values)


def daily_pins(journal, ticker, root):
    from doxagent.semantic_clock import semantic_day
    with journal.transaction(write=False, expand=False) as db:
        # Future-day baselines are legitimate (scheduled closed-cycle processing).
        return {row[0] for row in db.execute(
            "SELECT coalesce(json_extract(payload_json,'$.event_library_version'),"
            "json_extract(payload_json,'$.index.version')) FROM runtime_v2_w1_daily_indexes "
            "WHERE ticker=? AND semantic_day>=? AND json_extract(payload_json,'$.event_library_root') IS ?",
            (ticker, str(semantic_day(journal.clock())), root)) if row[0] is not None}


def legacy_reader(db, case):
    if case.w3_contract_version == 'persistent-runtime.w3.v6':return False
    return bool(db.execute("SELECT 1 FROM runtime_v2_effects WHERE case_id=? "
        "AND effect_type IN ('route_to_w3','w3_post_decision') "
        "AND status IN ('PENDING','PENDING_RETRY','RUNNING') LIMIT 1",(case.case_id,)).fetchone())


def legacy_effect_refs(db, ticker=None):
    """Active legacy W3 consumers only, including Operations after Trade completion.

    Start at the two existing live-Effect indexes, never scan historical Cases.
    Current v6 frozen-input Operations do not acquire body dependencies here.
    """
    refs=[]
    for index,where in [('idx_runtime_v2_effects_pending',"e.status IN ('PENDING','PENDING_RETRY')"),
                        ('idx_runtime_v2_effects_running',"e.status='RUNNING'")]:
        refs.extend(tuple(r) for r in db.execute(
            "SELECT c.ticker,json_extract(c.payload_json,'$.version_pin.event_library_root'),"
            "json_extract(c.payload_json,'$.version_pin.event_library_version'),"
            "json_extract(c.payload_json,'$.version_pin.policy_set_version') "
            "FROM runtime_v2_effects e INDEXED BY "+index+" JOIN runtime_v2_cases c USING(case_id) "
            "WHERE "+where+" AND e.effect_type IN ('route_to_w3','w3_post_decision') "
            "AND coalesce(json_extract(c.payload_json,'$.w3_contract_version'),'')!='persistent-runtime.w3.v6' "
            +("AND c.ticker=?" if ticker else ''),(ticker,) if ticker else ()))
    return refs
