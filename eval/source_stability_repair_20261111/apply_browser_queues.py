"""Only two existing queue-policy fields, through the running Site owner."""
import copy,json,sys
from datetime import datetime,timezone
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
s=DoxAgentSettings();token=read_token(s.site_access_admin_token,s.site_access_admin_token_file)
if not token:raise SystemExit('admin token unavailable')
rows=[]
with httpx.Client(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+token},trust_env=False,timeout=30) as c:
 for site in ['investing','investorshub']:
  r=c.get('/v1/sites/'+site);r.raise_for_status();old=r.json();new=copy.deepcopy(old)
  new['access']['crawler_queue_timeout_ms']=max(8000,old['access']['crawler_queue_timeout_ms'])
  row={'site_id':site,'old_revision':old['revision'],'old_queue_ms':old['access']['crawler_queue_timeout_ms'],'queue_ms':new['access']['crawler_queue_timeout_ms'],'applied':False}
  if '--apply' in sys.argv and new!=old:
   r=c.post('/v1/sites:apply',json={'spec':new,'expected_revision':old['revision'],'actor':'source-stability-queue-20261112'});r.raise_for_status();row['new_revision']=r.json()['revision']
   check=c.get('/v1/sites/'+site);check.raise_for_status();after=check.json()
   expected=copy.deepcopy(new);expected['revision']=after['revision']
   assert after==expected,'unexpected field changed during queue update'
   row['applied']=True
  rows.append(row)
print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'changes':rows,'only_queue_policy':True}))
