"""Complete Registry telemetry after coordinated listener maintenance."""
import hashlib,json,time,httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
s=DoxAgentSettings()
mapping={'us-standard-5':'🇩🇪 德国标准 IEPL 专线 1','nl-standard-2':'🇩🇪 德国标准 IEPL 专线 2'}
with httpx.Client(base_url='http://127.0.0.1:8011',timeout=45,headers={'Authorization':'Bearer '+read_token(s.site_access_admin_token,s.site_access_admin_token_file)}) as c:
    for key,node in mapping.items():
        prior=next(v for v in c.get('/v1/egresses').json() if v['egress_id']==key)
        changed={**prior,'node_ref':node,'node_fingerprint':hashlib.sha256(node.encode()).hexdigest()}
        r=c.put('/v1/egresses/'+key,json=changed);r.raise_for_status()
        r=c.post('/v1/egresses/'+key+':probe');r.raise_for_status();v=r.json()
        print(json.dumps({k:v.get(k) for k in ('egress_id','node_ref','observed_ip','status','generation')},ensure_ascii=False),flush=True)
