"""Read-only production/test inventory. Run on SG host over SSH stdin."""
import collections
import datetime
import hashlib
import json
import pathlib
import sqlite3
import subprocess

ROOT = pathlib.Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
TICKERS = ('MU', 'BE', 'INTC')

def ro(path):
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=4)
    db.row_factory = sqlite3.Row
    db.execute('pragma query_only=on')
    return db

def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]

def sha(data):
    return hashlib.sha256(data).hexdigest()

out = {'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
control = ro(ROOT/'initialization/control.sqlite3')
out['active_revisions'] = [json.loads(r['payload']) for r in control.execute(
    'select r.payload from activation_revisions r join ticker_active_revision a on a.revision_id=r.revision_id')]
out['control_tables'] = rows(control, "select name from sqlite_master where type='table'")
out['databases'] = {}
for label, path in [('production', ROOT/'research/research.sqlite3')] + [
        (t, ROOT/(t.lower()+'-v21-test/research.sqlite3')) for t in TICKERS]:
    db = ro(path)
    tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    result = {'bytes': path.stat().st_size, 'tables': sorted(tables)}
    result['bundles'] = []
    for r in db.execute("select run_id,payload_json from codex_runtime_records where record_type='bundles'"):
        v = json.loads(r['payload_json'])
        if v.get('ticker') not in TICKERS: continue
        if label != 'production' and not r['run_id'].startswith(label.lower()+'-v21-'): continue
        result['bundles'].append({k: v.get(k) for k in (
            'run_id','ticker','status','handoff','published_at','updated_at','c4_product_status')})
    result['policy_versions'] = rows(db,
        'select ticker,policy_set_version,is_current,publication_state,policy_count,published_at from codex_document3_policy_sets where ticker in (?,?,?)', TICKERS)
    if 'codex_document3_version_reservations' in tables:
        result['reservations'] = rows(db, 'select * from codex_document3_version_reservations where ticker in (?,?,?)', TICKERS)
    if 'codex_document3_v21_staged_sets' in tables:
        result['staged'] = []
        for r in db.execute('select * from codex_document3_v21_staged_sets where ticker in (?,?,?)', TICKERS):
            v=json.loads(r['payload']); receipt=json.loads(r['receipt'])
            result['staged'].append({'ticker':r['ticker'],'version':r['version'],'run_id':r['run_id'],
                'schema':v.get('schema_version'),'policies':len(v.get('policies',[])),
                'source':v.get('document2_ref'), 'event':v.get('event_library_ref'),
                'receipt':receipt})
    result['published_bodies'] = []
    for r in db.execute("select run_id,payload_json from codex_runtime_records where record_type='published_documents'"):
        v=json.loads(r['payload_json'])
        if label=='production' or not r['run_id'].startswith(label.lower()+'-v21-'): continue
        content=v.get('content_text'); storage=v.get('storage_path'); expected=v.get('sha256')
        cache=path.parent/'published-contents'/str(expected)
        external=path.parent/'published'/str(storage) if storage else None
        result['published_bodies'].append({'run_id':r['run_id'],'artifact_id':v.get('artifact_id'),
            'size':v.get('size_bytes'),'sha256':expected,'inline_ok':sha(content.encode())==expected if content is not None else None,
            'storage_path':storage,'cache_ok':sha(cache.read_bytes())==expected if cache.is_file() else False,
            'external_ok':sha(external.read_bytes())==expected if external and external.is_file() else None})
    out['databases'][label]=result
    db.close()

runtime=ro(ROOT/'runtime/runtime.sqlite3')
out['runtime_tables']=rows(runtime,"select name from sqlite_master where type='table'")
out['runtime_namespaces']=rows(runtime,'select namespace,count(*) n from runtime_values group by namespace')
out['execution_active']=rows(runtime,"select * from runtime_values where namespace='execution'")
out['runtime_meta']=rows(runtime,'select * from orchestration_meta')
out['runtime_tasks']=rows(runtime,'select kind,status,count(*) n from runtime_tasks group by kind,status')
for table in ('runtime_v2_cases','runtime_v2_w3_cases'):
    out[table]=rows(runtime, 'select ticker,status,count(*) n from '+table+' group by ticker,status')
out['snapshot_schema']=rows(runtime,'pragma table_info(runtime_snapshots)')
out['scheduler_schema']=rows(ro(ROOT/'scheduler/scheduler.sqlite3'),'pragma table_info(runtime_scheduler_states)')
bus=ro(ROOT/'bus/bus.sqlite3')
out['monitoring_states']=rows(bus,'select * from ticker_monitoring_states')
out['bindings_digest']={t:sha(json.dumps(rows(bus,'select * from ticker_source_bindings where ticker=?',(t,)),sort_keys=True).encode()) for t in TICKERS}
out['bus_tables']=rows(bus,"select name from sqlite_master where type='table'")

inspect=json.loads(subprocess.check_output(['docker','inspect']+subprocess.check_output(['docker','ps','-aq']).decode().split()))
out['containers']=[]
safe_keys={'DOXAGENT_PERSISTENT_RUNTIME_V2_EXECUTION_MANIFEST','DOXAGENT_PERSISTENT_RUNTIME_V2_PROMPT_ROOT',
    'DOXAGENT_PERSISTENT_RUNTIME_V2_W3_EXECUTION_MODEL_OVERRIDE','DOXAGENT_CODEX_MODEL',
    'DOXAGENT_PERSISTENT_RUNTIME_V2_W3_MODEL','DOXAGENT_CODEX_WORKER_BASE_URL',
    'DOXAGENT_CODEX_RUNTIME_SQLITE_PATH','DOXAGENT_EVENT_LIBRARY_ROOT','PYTHONPATH'}
for c in inspect:
    if not c['Name'].startswith('/doxagent-'):continue
    state=c['State']; labels=c['Config'].get('Labels') or {}
    out['containers'].append({'name':c['Name'],'id':c['Id'],'image':c['Config']['Image'],'image_id':c['Image'],
        'status':state['Status'],'exit':state['ExitCode'],'oom':state['OOMKilled'],'restart_count':c['RestartCount'],
        'started_at':state['StartedAt'],'finished_at':state['FinishedAt'],
        'compose_files':labels.get('com.docker.compose.project.config_files'),
        'revision_label':labels.get('org.opencontainers.image.revision'),
        'env':{k:v for item in c['Config']['Env'] for k,_,v in [item.partition('=')] if k in safe_keys}})
print(json.dumps(out,ensure_ascii=False))
