"""Read only existing local management endpoints; no website/CDP actions."""
import asyncio,json,urllib.request
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
async def main():
    s=DoxAgentSettings()
    token=Path(s.site_access_admin_token_file).read_text().strip()
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8011/v1/diagnostics/access',headers={'Authorization':'Bearer '+token}),timeout=15) as response:
        data=json.load(response)
    ids={'digitimes-de-1','reuters-backup'}
    out={k:data.get(k) for k in ('global_active','global_limit','body_active','body_limit','queue_locked','stalled')}
    out['reuters_waiters']=[w for w in data.get('waiters',[]) if w.get('identity') in ids]
    out['reuters_identity_active']={k:v for k,v in data.get('identity_active',{}).items() if k in ids}
    client=ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='passive-dual-active-audit')
    out['chrome']=[]
    for identity in sorted(ids):
        status=await client.status(identity)
        out['chrome'].append({'identity':identity,**{k:status.get(k) for k in ('running','instance_id','pid')}})
    print(json.dumps(out))
asyncio.run(main())
