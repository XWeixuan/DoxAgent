"""Production Ready HTTP canary: two independent slots, no model turn or business Case."""
import asyncio,json
from pathlib import Path
from datetime import datetime,UTC
from doxagent.settings import DoxAgentSettings
from doxagent.codex_runtime.client import HttpCodexWorkerClient
async def main():
 s=DoxAgentSettings(); c=HttpCodexWorkerClient(s.codex_worker_base_url,s.codex_worker_bearer_token,capability_secret=s.codex_capability_secret)
 config=json.loads(Path('/tmp/w3-ready-canary-config.json').read_text());config['active_public']=False
 cid='diagnostic-ready-'+datetime.now(UTC).strftime('%Y%m%d%H%M%S')
 requests=[dict(case_id=cid,record_id=cid+'-'+b,parent_thread_id=config['parent_thread_id'],profile=config['profile'],w3_entry_version=config['w3_entry_version']) for b in ('A','B')]
 values=[]
 try:
  await c.configure_w3_ready(config)
  async def claim(r):
   for _ in range(45):
    try:return await c.claim_w3_ready(r)
    except Exception as e:
     if getattr(e,'code',None)!='w3_prepare_pending':raise
     await asyncio.sleep(.5)
   raise TimeoutError('canary original binding did not become ready')
  values=await asyncio.gather(*(claim(r) for r in requests))
  assert len({v['thread_id'] for v in values})==2
  repeated=await asyncio.gather(*(c.claim_w3_ready(r) for r in requests))
  assert [v['slot_id'] for v in repeated]==[v['slot_id'] for v in values]
  print(json.dumps(dict(at=datetime.now(UTC).isoformat(),entry=config['w3_entry_version'],branches=values,idempotent=True,model_turns=0)))
 finally:
  for r in requests:
   await c.release_unused_case(r['case_id'],r['record_id'])
  for v in values:
   await c.delete_thread(v['thread_id'])
  await c.aclose()
asyncio.run(main())
