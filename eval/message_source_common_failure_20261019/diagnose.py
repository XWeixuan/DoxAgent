"""Read-only host/container/owner evidence, with no secrets or Profile contents."""
import json
import os
import sqlite3
import subprocess
from collections import Counter
from datetime import UTC, datetime


def run(args):
    return subprocess.check_output(args, text=True, timeout=50).strip()


names=['doxagent-v2-v2-message-bus-1','doxagent-v2-v2-site-access-1',
       'doxagent-v2-v2-chrome-supervisor-1','doxagent-v2-v2-content-enrichment-1','doxagent-egress-clash']
containers=json.loads(run(['docker','inspect',*names]))
out={'at':datetime.now(UTC).isoformat(),'containers':[]}
for c in containers:
    env=dict(x.split('=',1) for x in c['Config'].get('Env',[]) if '=' in x)
    out['containers'].append({'name':c['Name'],'id':c['Id'],'image':c['Config']['Image'],
       'namespace':os.readlink(f"/proc/{c['State']['Pid']}/ns/net"),
       'network_mode':c['HostConfig']['NetworkMode'],'started':c['State']['StartedAt'],
       'restarts':c['RestartCount'],'oom':c['State']['OOMKilled'],
       'dns':c['HostConfig'].get('Dns'),'memory_limit':c['HostConfig']['Memory'],
       'networks':{k:v['IPAddress'] for k,v in c['NetworkSettings']['Networks'].items()},
       'proxy_env':{k:v for k,v in env.items() if k.lower() in ('http_proxy','https_proxy','all_proxy','no_proxy')},
       'site_url':env.get('DOXAGENT_SITE_ACCESS_URL')})
bus=containers[0]
volume=next(m['Source'] for m in bus['Mounts'] if m['Destination']=='/data')
db=sqlite3.connect('file:'+volume+'/bus/bus.sqlite3?mode=ro',uri=True,timeout=2)
out['bus_tables']={r[0]:[c[1] for c in db.execute('pragma table_info('+r[0]+')')]
                   for r in db.execute("select name from sqlite_master where type='table'")}
out['sources']=[{k:v.get(k) for k in ('source_id','adapter_ref','acquisition_mode','entry_url','enabled','default_parameters')}
                for raw, in db.execute('select data_json from source_definitions') for v in [json.loads(raw)]]
polls=[json.loads(raw) for raw, in db.execute('select data_json from poll_states')]
bindings=[json.loads(raw) for raw, in db.execute('select data_json from ticker_source_bindings')]
tickers=[json.loads(raw) for raw, in db.execute('select data_json from ticker_monitoring_states')]
active_tickers={v['ticker'] for v in tickers if v.get('status')=='running'}
active_sources={v['source_id'] for v in out['sources'] if v.get('enabled')}
active_bindings={v['binding_id'] for v in bindings if v.get('enabled') and v['ticker'] in active_tickers and v['source_id'] in active_sources}
out['active_poll_states']=[p for p in polls if p['binding_id'] in active_bindings]
out['poll_summary']={}
for source in sorted({p['source_id'] for p in polls}):
    rows=[p for p in polls if p['source_id']==source]
    out['poll_summary'][source]={'statuses':dict(Counter(r.get('status') for r in rows)),
                                'samples':rows[:2]}
sitevolume=next(m['Source'] for m in containers[1]['Mounts'] if m['Destination']=='/site-data')
site=sqlite3.connect('file:'+sitevolume+'/registry/site_strategy.sqlite3?mode=ro',uri=True,timeout=2)
out['site_tables']={r[0]:[c[1] for c in site.execute('pragma table_info('+r[0]+')')]
                    for r in site.execute("select name from sqlite_master where type='table'")}
out['scopes']=[json.loads(raw) for raw, in site.execute('select data_json from source_access_scopes')]
out['tasks']=[{k:v.get(k) for k in ('task_id','identity_id','scope_id','state','reason','opened_at')}
              for raw, in site.execute("select data_json from challenge_tasks where state<>'RESOLVED' and superseded_by is null")
              for v in [json.loads(raw)]]
out['identity_states']=[json.loads(raw) for raw, in site.execute('select data_json from identity_runtime')]
out['auth_facts']=[{k:v.get(k) for k in ('site_id','identity_id','auth_state','observed_session_revision','updated_at')}
                   for raw, in site.execute('select data_json from site_identity_auth')
                   for v in [json.loads(raw)] if v.get('site_id') in ('digitimes','reuters','barrons','investing')]
cursor=site.execute('select * from access_events order by rowid desc limit 30')
out['access_events']=[dict(zip([d[0] for d in cursor.description],row)) for row in cursor.fetchall()]
out['stats']=run(['docker','stats','--no-stream','--format','{{.Name}} {{.CPUPerc}} {{.MemUsage}}',*names])
print(json.dumps(out,ensure_ascii=True))
