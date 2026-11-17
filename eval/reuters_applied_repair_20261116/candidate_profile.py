import asyncio,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
import httpx
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings();token=Path(s.site_access_admin_token_file).read_text().strip()
ID='reuters-backup2';SCOPE='publisher:reuters-backup2'
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+token},timeout=65,trust_env=False) as h:
  async def call(path,data=None,method='POST'):
   headers={'Authorization':'Bearer '+Path(s.site_access_worker_token_file).read_text().strip()} if path=='/v1/access/execute' else {}
   r=await h.request(method,path,json=data,headers=headers)
   if r.status_code>=400:raise RuntimeError(f'{path}:HTTP{r.status_code}:'+str(r.json().get('detail',''))[:250])
   return r.json()
  identities=await call('/v1/identities',method='GET');profiles=await call('/v1/profiles',method='GET')
  env={'locale':'en-US','timezone_id':'Europe/Berlin','window_width':1440,'window_height':1000,'revision':1}
  if not any(p['profile_id']==ID for p in profiles):await call('/v1/profiles/'+ID,{'profile_id':ID,'site_id':'reuters','bound_egress_id':'de-standard-1','directory_key':ID,'environment':env},'PUT')
  if not any(i['spec']['identity_id']==ID for i in identities):await call('/v1/identities:apply',{'spec':{'identity_id':ID,'owner_scope_id':SCOPE,'runtime_kind':'external_chrome','profile_id':ID,'egress_id':'de-standard-1','browser_release':'google-chrome-153.0.8010.52','environment':env,'lifecycle':{'residency':'on_demand','idle_seconds':43200},'access':{'max_concurrency':1,'min_interval_ms':3000}},'actor':'ops:reuters-cookie-acceptance-20261116'})
  scopes=await call('/v2/scopes',method='GET');current=next(p for p in scopes if p['scope_id']=='source:reuters_site_search')
  if not any(p['scope_id']==SCOPE for p in scopes):
   plan=dict(current['verification_plan']);plan['default_publisher']=False;plan.pop('crawler_routing',None);plan.pop('_routing_capability_revision',None)
   await call('/v2/scopes:apply',{'spec':{'scope_id':SCOPE,'owner_site_id':'reuters','primary_identity_id':ID,'verification_plan':plan}})
  req={'operation_id':'reuters-profile-control-20261116','origin_scope_id':SCOPE,'purpose':'CRAWLER','url':'https://www.reuters.com/site-search/?query=Micron&offset=0','mode':'BROWSER','recipe_ref':'builtin:reuters_search@1','recipe_parameters':{'query':'Micron','offset':0},'remaining_budget_ms':45000}
  t=datetime.now(timezone.utc);v=await call('/v1/access/execute',req)
  out={k:v.get(k) for k in ['disposition','reason_code','status_code','effective_scope_id','identity_id','runtime_instance_id','stage_timings','attempts','recovery_id','challenge_task_id']};p=v.get('recipe_result') or {};out['rows']=len(p.get('rows',[]));out['sample_articles']=[{k:r.get(k) for k in ['url','title']} for r in p.get('rows',[])[:3]];out['started_at']=t.isoformat();out['at']=datetime.now(timezone.utc).isoformat();print(json.dumps(out,indent=2))
asyncio.run(main())


