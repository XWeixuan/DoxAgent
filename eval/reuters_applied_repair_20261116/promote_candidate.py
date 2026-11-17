import asyncio,json,os,importlib.util
from pathlib import Path
from datetime import datetime,timezone
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
s=DoxAgentSettings();repo=SiteStrategyRepository(s.site_access_sqlite_path)
m=importlib.util.spec_from_file_location('promotion','/tmp/promote_reuters_identity.py');ops=importlib.util.module_from_spec(m);m.loader.exec_module(ops)
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+Path(s.site_access_admin_token_file).read_text().strip()},timeout=30,trust_env=False) as h:
  async def call(path,data=None,method='POST'):
   r=await h.request(method,path,json=data);r.raise_for_status();return r.json()
  scopes=await call('/v2/scopes',method='GET');source=next(v for v in scopes if v['scope_id']==ops.SOURCE);trial=next(v for v in scopes if v['scope_id']==ops.TRIAL)
  assert source['primary_identity_id']=='digitimes-de-1' and source['fallback_identity_id']=='reuters-backup'
  backup=Path(s.site_access_sqlite_path).parent/'reuters-promotion-config-20261116.json'
  if not backup.exists():
   snap={'scopes':[source,trial],'identities':[repo.get_identity(i).model_dump(mode='json') for i in ['digitimes-de-1','reuters-backup2','reuters-backup']]}
   backup.write_text(json.dumps(snap));backup.chmod(0o600)
  client=ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='promotion-observer')
  before={}
  for i in ['digitimes-de-1','reuters-backup','reuters-backup2']:
   v=await client.status(i);before[i]={k:v.get(k) for k in ['instance_id','pid','running']}
  for i in ['digitimes-de-1','reuters-backup2']:
   spec=repo.get_identity(i)
   if spec.owner_scope_id is not None:
    await call('/v1/identities:apply',{'spec':spec.model_copy(update={'owner_scope_id':None}).model_dump(mode='json'),'expected_revision':spec.revision,'actor':'ops:reuters-retain-scene-promotion-20261116'})
  result=ops.promote(repo,source_revision=source['revision'],trial_revision=trial['revision'],apply=True)
  after={}
  for i in before:
   v=await client.status(i);after[i]={k:v.get(k) for k in ['instance_id','pid','running']}
  operation=await call('/v1/maintenance/operations',{'action':'ACTIVATE_PRIMARY','scope_id':ops.SOURCE,'idempotency_key':'reuters-profile-promotion-20261116'})
  print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'result':result,'before':before,'after':after,'all_chrome_preserved':before==after,'operation_id':operation['operation_id'],'operation_state':operation['state']},indent=2))
asyncio.run(main());repo.close()
