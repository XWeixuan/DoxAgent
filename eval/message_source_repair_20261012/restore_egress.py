"""Coordinated fixed-node maintenance. Run as root on the production host."""
import json, os, shutil, subprocess, time
from datetime import UTC, datetime

SITE='doxagent-v2-v2-site-access-1'
BUS='doxagent-v2-v2-message-bus-1'
CONFIG='/opt/doxagent-egress-clash/config.yaml'
BACKUP='/opt/doxagent-egress-clash/config.pre-20261012.yaml'
MAPPING={'us-standard-5':'🇩🇪 德国标准 IEPL 专线 1', 'nl-standard-2':'🇩🇪 德国标准 IEPL 专线 2'}
def exec_py(container, code, data=''):
    p=subprocess.run(['docker','exec','-i',container,'python','-c',code],input=data,capture_output=True,text=True,timeout=35)
    if p.returncode: raise RuntimeError('container_operation_failed:'+container)
    return p.stdout
check="import sqlite3,json; d=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True); rows=[json.loads(r[0]) for r in d.execute('select data_json from browser_profiles')]; assert not any(p.get('maintenance_session_id') for p in rows), 'active_manual_maintenance'; print('no_manual_session')"
print(exec_py(SITE,check).strip(),flush=True)
if os.path.exists(BACKUP): raise RuntimeError('backup_already_exists_check_previous_receipt')
with open(CONFIG) as f: prior=f.read()
parse="import yaml,json,sys; c=yaml.safe_load(sys.stdin.read()); print(json.dumps({'controller':c.get('external-controller'),'secret':c.get('secret')}))"
control=json.loads(exec_py(BUS,parse,prior))
render="import yaml,json,sys; x=json.load(sys.stdin); c=yaml.safe_load(x['config']); nodes={p['name'] for p in c['proxies']}; assert set(x['mapping'].values())<=nodes; [(c['sub-rules'].__setitem__('doxagent-egress-'+k,[*c['sub-rules']['doxagent-egress-'+k][:-1],'MATCH,'+v])) for k,v in x['mapping'].items()]; print(yaml.safe_dump(c,allow_unicode=True,sort_keys=False))"
candidate=exec_py(BUS,render,json.dumps({'config':prior,'mapping':MAPPING}))
validation=subprocess.run(['docker','exec','-i','doxagent-egress-clash','/mihomo','-t','-f','/dev/stdin'],input=candidate,capture_output=True,text=True,timeout=30)
if validation.returncode:
    print((validation.stdout+validation.stderr)[-1400:])
    raise RuntimeError('mihomo_config_validation_failed')
shutil.copy2(CONFIG,BACKUP);os.chmod(BACKUP,0o600)
subprocess.run(['docker','stop','-t','100',SITE],capture_output=True,check=True,timeout=110)
try:
    # In-place update is intentional: the container binds this exact config inode.
    with open(CONFIG,'w') as f: f.write(candidate)
    pid=subprocess.check_output(['docker','inspect','-f','{{.State.Pid}}','doxagent-egress-clash'],text=True).strip()
    reload_code="""import sys,json,urllib.request
c=json.load(sys.stdin); root='http://127.0.0.1:'+c['controller'].rsplit(':',1)[1]
headers={'Authorization':'Bearer '+(c['secret'] or ''),'Content-Type':'application/json'}
def call(path,method='GET',body=None):
 r=urllib.request.urlopen(urllib.request.Request(root+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method),timeout=15)
 raw=r.read(); return json.loads(raw) if raw else {}
call('/configs?force=true','PUT',{'path':'/root/.config/mihomo/config.yaml'})
connections=call('/connections').get('connections',[]); closed=0
for item in connections:
 m=item.get('metadata',{}); port=str(m.get('inboundPort') or m.get('sourcePort') or '')
 if port in ('18081','18082') or any('荷兰标准' in n for n in item.get('chains',[])):
  call('/connections/'+item['id'],'DELETE'); closed+=1
print(json.dumps({'reload':'ok','closed_affected_connections':closed}))
"""
    p=subprocess.run(['nsenter','-t',pid,'-n','python3','-c',reload_code],input=json.dumps(control),capture_output=True,text=True,timeout=45,check=True)
    print(p.stdout.strip(),flush=True)
except Exception:
    with open(CONFIG,'w') as f: f.write(prior)
    raise
finally:
    subprocess.run(['docker','start',SITE],capture_output=True,check=True,timeout=25)
time.sleep(4)
update="""import json,sys,time,hashlib,httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
s=DoxAgentSettings(); mapping=json.load(sys.stdin)
with httpx.Client(base_url='http://127.0.0.1:8011',timeout=45,headers={'Authorization':'Bearer '+read_token(s.site_access_admin_token,s.site_access_admin_token_file)}) as c:
 for key,node in mapping.items():
  prior=next(v for v in c.get('/v1/egresses').json() if v['egress_id']==key)
  changed={**prior,'node_ref':node,'node_fingerprint':hashlib.sha256(node.encode()).hexdigest()}
  r=c.put('/v1/egresses/'+key,json=changed);r.raise_for_status()
  r=c.post('/v1/egresses/'+key+':probe');r.raise_for_status();v=r.json()
  print(json.dumps({k:v.get(k) for k in ('egress_id','node_ref','observed_ip','status','generation')},ensure_ascii=False),flush=True)
"""
print(exec_py(SITE,update,json.dumps(MAPPING)).strip(),flush=True)
print(json.dumps({'completed_at':datetime.now(UTC).isoformat(),'backup':BACKUP,'country_change':'NL to DE','chrome_restart':False}))
