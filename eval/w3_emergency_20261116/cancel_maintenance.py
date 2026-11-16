"""Cancel only explicitly identified maintenance Jobs for emergency cutover."""
import asyncio,json,sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.codex_runtime.client import HttpCodexWorkerClient
async def main():
 s=DoxAgentSettings();p=Path(s.codex_workspace_root)/'worker-jobs.sqlite3'
 d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
 rows=d.execute("select id,payload,request from jobs where status in ('running','queued')").fetchall();d.close()
 assert all(json.loads(p)['run_id'].startswith('runtime-maintain-') and json.loads(r or '{}').get('task_phase') not in ('trade_decision','post_decision_operations') for _,p,r in rows), 'Non-maintenance work exists; preserve it'
 c=HttpCodexWorkerClient('http://127.0.0.1:8791',s.codex_worker_bearer_token)
 try:
  out=[]
  for identity,p,_ in rows:
   v=await c.cancel(identity);assert v is not None and v.status in ('cancelled','succeeded','failed')
   out.append(dict(job_id=identity,run_id=json.loads(p)['run_id'],status=v.status))
  print(json.dumps(out))
 finally:await c.aclose()
asyncio.run(main())
