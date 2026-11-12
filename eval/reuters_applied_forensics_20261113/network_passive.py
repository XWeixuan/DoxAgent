"""Bounded local observation of existing natural traffic. Never navigates or purchases."""
import asyncio,json,sqlite3,time,importlib.util
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
from websockets.asyncio.client import connect
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
spec=importlib.util.spec_from_file_location('doxagent.site_strategy.solver_forensics','/tmp/reuters-solver-forensics.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
async def one(identity,duration):
 s=DoxAgentSettings();sup=ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='reuters-network-passive-audit');status=await sup.status(identity);traces={};pending={};tasks=set();observed=asyncio.Event();n=0
 async with connect(status['cdp_websocket_url'],max_size=4000000,close_timeout=1) as ws:
  async def call(method,params=None,session=None):
   nonlocal n;n+=1;num=n;f=asyncio.get_running_loop().create_future();pending[num]=f;v={'id':num,'method':method,'params':params or {}}
   if session:v['sessionId']=session
   await ws.send(json.dumps(v))
   try:return await asyncio.wait_for(f,5)
   finally:pending.pop(num,None)
  async def attach(info):
   if info.get('type')!='page' or info['targetId'] in [v['target'] for v in traces.values()]:return
   r=await call('Target.attachToTarget',{'targetId':info['targetId'],'flatten':True});session=r['sessionId'];trace=m.WireEvidence('recovery_4519eaad023a490bb5549f123a64c88b');traces[session]={'target':info['targetId'],'trace':trace}
   await call('Network.enable',{'maxTotalBufferSize':131072,'maxResourceBufferSize':65536},session)
  async def reader():
   async for raw in ws:
    v=json.loads(raw)
    if v.get('id') in pending:
     f=pending[v['id']]
     if not f.done():
      if 'error'in v:f.set_exception(RuntimeError('cdp_error'))
      else:f.set_result(v.get('result',{}))
    elif v.get('method')=='Target.targetCreated':
     t=asyncio.create_task(attach(v['params']['targetInfo']));tasks.add(t);t.add_done_callback(tasks.discard)
    elif v.get('sessionId') in traces:
     entry=traces[v['sessionId']];entry['trace'].event(v)
     rows=entry['trace'].result()
     if any(row.get('finished') and row.get('response') for row in rows):observed.set()
  worker=asyncio.create_task(reader())
  try:
   targets=await call('Target.getTargets')
   for t in targets['targetInfos']:await attach(t)
   await call('Target.setDiscoverTargets',{'discover':True})
   start=time.monotonic()
   while time.monotonic()-start<duration:
    # Main: one naturally rejected search, Backup: one natural API success, then stop.
    rows=[row for x in traces.values() for row in x['trace'].result()]
    complete=any(r.get('finished') and r.get('response',{}).get('status')==401 for r in rows) if identity=='digitimes-de-1' else any(r.get('finished') and r.get('response',{}).get('status')==200 and '/pf/api/' in r.get('url','') for r in rows)
    if complete:break
    observed.clear()
    try:await asyncio.wait_for(observed.wait(),min(10,max(.01,duration-(time.monotonic()-start))))
    except TimeoutError:pass
   for session,x in traces.items():
    async def c(method,params=None):return await call(method,params,session)
    await x['trace'].bodies(c)
   return {'identity':identity,'instance':status['instance_id'],'finished_at':datetime.now(timezone.utc).isoformat(),'targets':[{'target':x['target'],'requests':x['trace'].result()} for x in traces.values()]}
  finally:
   for session in traces:
    try:await call('Target.detachFromTarget',{'sessionId':session})
    except Exception:pass
   for t in tasks:t.cancel()
   if tasks:await asyncio.gather(*tasks,return_exceptions=True)
   worker.cancel();await asyncio.gather(worker,return_exceptions=True)
async def main():
 start=datetime.now(timezone.utc).isoformat();values=await asyncio.gather(one('digitimes-de-1',600),one('reuters-backup',90),return_exceptions=True)
 print(json.dumps({'started_at':start,'ended_at':datetime.now(timezone.utc).isoformat(),'traffic':[type(v).__name__ if isinstance(v,Exception) else v for v in values]},indent=2))
asyncio.run(main())
