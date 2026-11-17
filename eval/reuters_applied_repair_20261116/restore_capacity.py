import asyncio,json
from pathlib import Path
import httpx
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings()
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+Path(s.site_access_admin_token_file).read_text().strip()},timeout=40,trust_env=False) as h:
  r=await h.get('/v1/identities/reuters-backup2');r.raise_for_status();spec=r.json()['spec'];before=spec['access'];spec['access']={'max_concurrency':2,'min_interval_ms':1500}
  r=await h.post('/v1/identities:apply',json={'spec':spec,'expected_revision':spec['revision'],'actor':'ops:reuters-restore-production-capacity-20261116'});r.raise_for_status()
  r=await h.post('/v1/maintenance/operations',json={'action':'ACTIVATE_PRIMARY','scope_id':'source:reuters_site_search','idempotency_key':'reuters-profile-capacity-20261116'});r.raise_for_status();v=r.json();print(json.dumps({'before':before,'after':spec['access'],'operation_id':v['operation_id'],'state':v['state']}))
asyncio.run(main())
