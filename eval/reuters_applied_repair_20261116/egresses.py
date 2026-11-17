import json,sqlite3,subprocess
from pathlib import Path
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3');d=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);out=[]
for (raw,) in d.execute('select data_json from proxy_egresses'):
 v=json.loads(raw)
 if '德国' in v.get('node_ref','') or v.get('egress_id','').startswith('de-'):
  row=d.execute('select country_code from egress_regions where egress_id=?',(v['egress_id'],)).fetchone();out.append({**{k:v.get(k) for k in ('egress_id','node_ref','listener_port','endpoint','observed_ip','status','enabled')},'country':row[0] if row else None})
print(json.dumps(out,ensure_ascii=False,indent=2));i=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0];print(json.dumps({'clash_mounts':[(x['Source'],x['Destination']) for x in i['Mounts']]}))
