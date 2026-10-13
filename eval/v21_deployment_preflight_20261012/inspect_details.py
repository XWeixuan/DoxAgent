"""Bounded read-only details; no repository constructors or domain writes."""
import collections
import datetime
import hashlib
import json
import pathlib
import sqlite3
import subprocess

ROOT=pathlib.Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
TICKERS=('MU','BE','INTC')
def ro(p):
    d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=3);d.row_factory=sqlite3.Row;d.execute('pragma query_only=on');return d
def rows(d,sql,args=()):return [dict(r) for r in d.execute(sql,args)]
def unpack(v,base):
    if isinstance(v,dict):
        if '$doxagent_content_v1' in v:
            h=v['$doxagent_content_v1'];folder=base/'native-files'/h[:2]/h
            manifest=json.loads((folder/'manifest.json').read_text())
            return {'external_native_marker':h,'manifest':manifest}
        return {k:unpack(x,base) for k,x in v.items()}
    if isinstance(v,list):return [unpack(x,base) for x in v]
    return v
out={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
r=ro(ROOT/'runtime/runtime.sqlite3');c=ro(ROOT/'initialization/control.sqlite3');p=ro(ROOT/'research/research.sqlite3')
active=[json.loads(v[0]) for v in c.execute('select r.payload from activation_revisions r join ticker_active_revision a on a.revision_id=r.revision_id')]
identity=json.loads(r.execute("select payload from runtime_values where namespace='execution' and key='active'").fetchone()[0])
bundle=json.loads(r.execute('select payload from runtime_snapshots where id=?',(identity,)).fetchone()[0])
out['execution']={'id':identity,'keys':list(bundle),'revision':bundle.get('orchestration_revision'),
    'contracts':bundle.get('contracts'),'assets':list(bundle.get('assets',{})),
    'prompts_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in bundle.get('prompts',{}).items() if isinstance(v,str)}}
out['control_states']=rows(r,'select ticker,revision,payload from v2_ticker_control')
out['worker_ack']=rows(c,'select * from activation_worker_ack')
out['scheduler']=rows(ro(ROOT/'scheduler/scheduler.sqlite3'),'select * from runtime_scheduler_states')
out['old_claims']=rows(r,'select ticker,count(*) n from runtime_v2_policy_activations group by ticker')
out['test_products']=[]
for t in TICKERS:
    path=ROOT/(t.lower()+'-v21-test/research.sqlite3');d=ro(path)
    prefix={'MU':'mu-v21-20261010-02','BE':'be-v21-20261011-01','INTC':'intc-v21-20261011-01'}[t]
    bundles={v['run_id']:json.loads(v['payload_json']) for v in d.execute("select run_id,payload_json from codex_runtime_records where record_type='bundles' and run_id like ?",(prefix+'%',))}
    b=bundles[prefix+'-d1']
    s=d.execute('select * from codex_document3_v21_staged_sets where ticker=? order by version desc limit 1',(t,)).fetchone()
    policy=json.loads(s['payload']);receipt=json.loads(s['receipt'])
    current=next(v for v in active if v['ticker']==t)
    old=json.loads(p.execute('select policy_set_json from codex_document3_policy_sets where ticker=? and policy_set_version=?',(t,current['artifacts']['document3']['version'])).fetchone()[0])
    ids={v['policy_id'] for v in policy['policies']};oldids={v['policy_id'] for v in old['policies']}
    claimids={v[0] for v in r.execute('select policy_id from runtime_v2_policy_activations where ticker=?',(t,))}
    product={'ticker':t,'d1_keys':list(b),'c4_product_status':b.get('c4_product_status'),
        'd1_artifact_nodes':collections.Counter(v.get('node') for v in b.get('artifacts',[])),
        'new_old_policy_id_overlap':len(ids&oldids),'new_claim_id_overlap':sorted(ids&claimids),
        'd1_handoff':b.get('handoff'),'d3_hashes':receipt['hashes'],'source_receipt_version':receipt['version'],
        'source_missing':None,'release_files':{}}
    for name,f in receipt['files'].items():
        matches=[candidate for candidate in (
            path.parent/'workspaces'/s['run_id']/f,
            ROOT/'workspaces'/s['run_id']/f,
        ) if candidate.is_file()]
        found=matches[0] if matches else None
        if found:
            raw=found.read_bytes();product['release_files'][name]={'host_path':str(found),'size':len(raw),'sha256_ok':hashlib.sha256(raw).hexdigest()==receipt['hashes'][name]}
            if name=='coverage_map.json':product['coverage_keys']=list(json.loads(raw))
        else:product['release_files'][name]={'found':False,'relative':f}
    product['run_state']=json.loads(d.execute('select payload from codex_document3_v21_runs where run_id=?',(s['run_id'],)).fetchone()[0])
    product['run_state']={k:v for k,v in product['run_state'].items() if k in ('phase','status','missing','commit','identity','warnings')}
    eventroot=ROOT/str(current['artifacts']['event_library']['root']).removeprefix('/data/')
    ep=eventroot/'US'/t/'event_library.sqlite3'
    e=ro(ep);product['production_event']={'path':str(ep),'tables':[v[0] for v in e.execute("select name from sqlite_master where type='table'")]}
    product['production_event']['versions']=rows(e,'select * from library_versions order by version desc limit 2')
    out['test_products'].append(product)
out['db_sizes']={str(v.relative_to(ROOT)):v.stat().st_size for v in ROOT.glob('*/*.sqlite3')}
out['read_aliases']={str(f):f.read_text() for f in (ROOT/'read').glob('*.json') if f.name in ('active.json','production-schema.json') or 'alias' in f.name}
print(json.dumps(out,ensure_ascii=False))
