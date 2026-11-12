"""Read ONE existing paid result; no createTask, application, navigation or new purchase."""
import asyncio,json,sqlite3
from pathlib import Path
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.datadome_browser import solution_cookie
import importlib.util
spec=importlib.util.spec_from_file_location('doxagent.site_strategy.solver_forensics','/tmp/reuters-solver-forensics.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
async def main():
 s=DoxAgentSettings();db=sqlite3.connect(Path(s.site_access_sqlite_path).as_uri()+'?mode=ro',uri=True);job=json.loads(db.execute("select data_json from datadome_solver_jobs where json_extract(data_json,'$.task_id')=84403569365").fetchone()[0]);config=json.loads((Path(s.site_access_credential_root)/'datadome-solver.json').read_text())
 async with httpx.AsyncClient(trust_env=False,timeout=12) as client:r=await client.post('https://api.2captcha.com/getTaskResult',json={'clientKey':config['api_key'],'taskId':job['task_id']})
 v=r.json();out={'method':'getTaskResult','task_id':job['task_id'],'http_status':r.status_code,**{k:v.get(k) for k in ('errorId','errorCode','status','cost','createTime','endTime','solveCount')}}
 if v.get('status')=='ready' and v.get('solution',{}).get('cookie'):
  c=solution_cookie(v['solution']['cookie'],'https://www.reuters.com/site-search/');out['provider_cookie']={k:c[k] for k in ('domain','path','secure','httpOnly','sameSite')};out['solution_fingerprint']=m.fingerprint(c['value'],job['recovery_id'])
 print(json.dumps(out,indent=2))
asyncio.run(main())
