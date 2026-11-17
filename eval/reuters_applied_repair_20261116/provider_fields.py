import asyncio,json,sqlite3
from pathlib import Path
from http.cookies import SimpleCookie
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.datadome_solver import TwoCaptcha
from doxagent.site_strategy.solver_forensics import fingerprint
s=DoxAgentSettings();cfg=json.loads((Path(s.site_access_sqlite_path).parent.parent/'credentials/datadome-solver.json').read_text());db=sqlite3.connect(Path(s.site_access_sqlite_path).as_uri()+'?mode=ro',uri=True);j=json.loads(db.execute("select data_json from datadome_solver_jobs where json_extract(data_json,'$.task_id')=84434823900").fetchone()[0])
async def main():
 try:v=await TwoCaptcha().request(cfg['api_key'],'getTaskResult',taskId=j['task_id'])
 except Exception as e:print(json.dumps({'read_failed':type(e).__name__,'reason':str(e)[:60]}));return
 solution=v.get('solution') or {};out={'task_id':j['task_id'],'response_fields':list(v),'solution_fields':list(solution),'receipt':{k:v.get(k) for k in ['status','cost','createTime','endTime','solveCount']},'extra_user_agent':solution.get('userAgent') if isinstance(solution.get('userAgent'),str) else None}
 c=SimpleCookie();c.load(solution.get('cookie',''));f=fingerprint(c['datadome'].value,j['recovery_id']) if 'datadome' in c else None;out['cookie_matches_actual_applied']=f==j['application_evidence']['solution_fingerprint'];print(json.dumps(out,indent=2))
asyncio.run(main())
