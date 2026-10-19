"""Read-only Reuters owner evidence. Never collect credentials/Cookies/tokens."""
import json, sqlite3, subprocess
from datetime import datetime, UTC

def run(args):return subprocess.check_output(args,text=True,timeout=60).strip()
def public(value):
    if isinstance(value,dict):
        return {k:public(v) for k,v in value.items() if not any(s in k.casefold() for s in ('token','password','cookie','secret','authorization'))}
    if isinstance(value,list):return [public(v) for v in value]
    return value
names=['doxagent-v2-v2-site-access-1','doxagent-v2-v2-chrome-supervisor-1','doxagent-v2-v2-message-bus-1']
containers=json.loads(run(['docker','inspect',*names]))
site_root=next(m['Source'] for m in containers[0]['Mounts'] if m['Destination']=='/site-data')
bus_root=next(m['Source'] for m in containers[2]['Mounts'] if m['Destination']=='/data')
out={'at':datetime.now(UTC).isoformat(),'containers':[dict(name=c['Name'],id=c['Id'],image=c['Image']) for c in containers]}
site=sqlite3.connect('file:'+site_root+'/registry/site_strategy.sqlite3?mode=ro',uri=True,timeout=2)
site.row_factory=sqlite3.Row
def data(sql,args=()):return [json.loads(r[0]) for r in site.execute(sql,args)]
out['strategy']=data("select r.spec_json from site_strategy_revisions r join site_strategy_heads h on h.site_id=r.site_id and h.active_revision=r.revision where r.site_id='reuters'")
out['scope']=data("select data_json from source_access_scopes where scope_id='source:reuters_site_search'")
ids=[x for s in out['scope'] for x in (s['primary_identity_id'],s.get('fallback_identity_id')) if x]
out['identities']=data("select r.spec_json from browser_identity_revisions r join browser_identity_heads h on h.identity_id=r.identity_id and h.active_revision=r.revision where r.identity_id in (select value from json_each(?))",(json.dumps(ids),))
out['profiles']=[{k:v.get(k) for k in ('profile_id','operational_state','state_reason','auth_state','maintenance_phase','challenge_url','updated_at')} for v in data("select data_json from browser_profiles where profile_id in (select value from json_each(?))",(json.dumps([i['profile_id'] for i in out['identities']]),))]
out['identity_states']=data('select data_json from identity_runtime where identity_id in (select value from json_each(?))',(json.dumps(ids),))
out['scope_runtime']=data("select data_json from site_runtime where runtime_key='scope:source:reuters_site_search'")
out['auth']=data("select data_json from site_identity_auth where site_id='reuters'")
out['egresses']=[{k:v.get(k) for k in ('egress_id','enabled','node_ref','status','observed_ip','observed_at','generation')} for v in data('select data_json from proxy_egresses')]
out['regions']=[dict(r) for r in site.execute('select * from egress_regions')]
out['tasks']=data("select data_json from challenge_tasks where json_extract(data_json,'$.site_id')='reuters' order by updated_at desc limit 20")
out['sessions']=data('select data_json from maintenance_sessions where identity_id in (select value from json_each(?)) order by updated_at desc limit 15',(json.dumps(ids),))
out['maintenance']=data("select data_json from maintenance_operations where json_extract(data_json,'$.site_id')='reuters' or json_extract(data_json,'$.scope_id')='source:reuters_site_search' or json_extract(data_json,'$.identity_id') in (select value from json_each(?)) or json_extract(data_json,'$.session_id') in (select session_id from maintenance_sessions where identity_id in (select value from json_each(?))) order by updated_at desc limit 30",(json.dumps(ids),json.dumps(ids)))
out['events']=[dict(r) for r in site.execute("select operation_id,occurred_at,combination_id,category,payload_json from access_events where site_id='reuters' order by occurred_at desc limit 100")]
bus=sqlite3.connect('file:'+bus_root+'/bus/bus.sqlite3?mode=ro',uri=True,timeout=2)
out['polls']=[json.loads(r[0]) for r in bus.execute("select data_json from poll_states where source_id='reuters_site_search'")]
out['failures']=[json.loads(r[0]) for r in bus.execute("select data_json from acquisition_failures where source_id='reuters_site_search' order by last_seen_at desc limit 30")]
out['bindings']=[json.loads(r[0]) for r in bus.execute("select data_json from ticker_source_bindings where source_id='reuters_site_search'")]
out['bus_tables']=[r[0] for r in bus.execute("select name from sqlite_master where type='table' and (name like '%poll%' or name like '%request%' or name like '%admission%')")]
out['stats']=run(['docker','stats','--no-stream','--format','{{.Name}} {{.CPUPerc}} {{.MemUsage}}',*names])
print(json.dumps(public(out)))
