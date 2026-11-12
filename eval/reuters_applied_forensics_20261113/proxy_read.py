import json,sqlite3,re,subprocess
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime,timezone
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data');db=sqlite3.connect((root/'registry/site_strategy.sqlite3').as_uri()+'?mode=ro',uri=True)
config=json.loads((root/'credentials/datadome-solver.json').read_text());squid=Path('/var/lib/doxagent/captcha-proxy/squid.conf').read_text();out={'at':datetime.now(timezone.utc).isoformat(),'tls_bump_configured':bool(re.search(r'^\s*ssl_bump\s',squid,re.M)),'never_direct':bool(re.search(r'^never_direct allow all$',squid,re.M)),'bindings':[]}
peers={}
for line in squid.splitlines():
 if line.startswith('cache_peer '):
  p=line.split();name=next((x[5:] for x in p if x.startswith('name=')),None);peers[name]=(p[1],int(p[3]))
user_to_id={}
for id,b in config.get('bindings',{}).items():
 if id not in {'digitimes-de-1','reuters-backup'}:continue
 user_to_id[b['proxyLogin']]=id
 spec=json.loads(db.execute('select r.spec_json from browser_identity_revisions r join browser_identity_heads h on h.identity_id=r.identity_id and h.active_revision=r.revision where h.identity_id=?',(id,)).fetchone()[0]);eg=json.loads(db.execute('select data_json from proxy_egresses where egress_id=?',(spec['egress_id'],)).fetchone()[0]);ep=urlsplit(eg['endpoint']);acl=next((l.split()[1] for l in squid.splitlines() if l.startswith('acl ') and l.split()[2:]==['proxy_auth',b['proxyLogin']]),None);peer=next((l.split()[1] for l in squid.splitlines() if l.startswith('cache_peer_access ') and l.split()[2:]==['allow',acl]),None)
 out['bindings'].append({'identity':id,'egress_id':eg['egress_id'],'binding_matches_identity':b['egress_id']==eg['egress_id'],'squid_parent_matches_chrome_listener':peers.get(peer)==(ep.hostname,ep.port),'listener_port':ep.port,'observed_exit':eg['observed_ip'],'egress_generation':eg['generation']})
raw=subprocess.check_output(['docker','logs','--since','2026-11-12T22:09:10Z','--until','2026-11-12T22:11:00Z','doxagent-captcha-proxy'],stderr=subprocess.DEVNULL,text=True);rows=[]
for line in raw.splitlines():
 p=line.split()
 if len(p)>=6 and re.fullmatch(r'\d+\.\d+',p[0]):
  rows.append({'at':datetime.fromtimestamp(float(p[0]),timezone.utc).isoformat(),'status':p[2],'bytes':p[3],'method':p[4],'identity':user_to_id.get(p[5],'UNKNOWN'),'provider_source':p[1]=='138.201.188.166'})
out['proxy_log_window']=rows;print(json.dumps(out,indent=2))
