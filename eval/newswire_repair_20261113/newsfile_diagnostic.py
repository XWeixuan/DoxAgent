import asyncio,json,sqlite3
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
async def main():
 s=DoxAgentSettings();out={}
 async with httpx.AsyncClient(base_url=s.site_access_url,headers={'Authorization':'Bearer '+read_token(s.site_access_admin_token,s.site_access_admin_token_file)},timeout=15) as c:
  for label,url in [('identity','/v1/identities/newsfile-1'),('access','/v1/diagnostics/access')]:
   r=await c.get(url);v=r.json();
   if label=='identity':v={k:x for k,x in v.items() if k!='credential_ref'}
   out[label]={'status':r.status_code,'data':v}
 db=sqlite3.connect('file:'+str(s.site_access_sqlite_path)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
 out['targets']=[{k:r[k] for k in ['identity_id','browser_instance_id','target_id','owner_kind','state','updated_at']} for r in db.execute("select * from owned_browser_targets where identity_id='newsfile-1' order by updated_at desc limit 8")]
 out['runtime']=[json.loads(r[0]) for r in db.execute("select data_json from identity_runtime where identity_id='newsfile-1'")]
 out['events']=[{k:r[k] for k in ['category','occurred_at']}|{'detail':{k:json.loads(r['payload_json']).get(k) for k in ['reason','disposition','stage_timings']}} for r in db.execute("select * from access_events where site_id='newsfile' order by occurred_at desc limit 6")]
 print(json.dumps(out))
asyncio.run(main())
