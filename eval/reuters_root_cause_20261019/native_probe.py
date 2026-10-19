"""One native foreground search in the already maintained identity, no PW/Fetch gate.

The operator's original target is untouched; only our returned probe ID is closed.
"""
import subprocess
code=r'''
import asyncio,json,httpx,websockets,time
from doxagent.content_enrichment.quality import inspect_html
async def main():
 out={'url':'https://www.reuters.com/site-search/?query=Intel&offset=0','variant':'native_target_foreground_no_fetch_gate'}
 async with httpx.AsyncClient(trust_env=False) as http:
  version=(await http.get('http://127.0.0.1:9225/json/version')).json()
  async with websockets.connect(version['webSocketDebuggerUrl'],max_size=10*1024*1024) as ws:
   seq=0
   async def call(method,params={},session=None):
    nonlocal seq
    seq+=1;i=seq;cmd={'id':i,'method':method,'params':params}
    if session:cmd['sessionId']=session
    await ws.send(json.dumps(cmd))
    async with asyncio.timeout(5):
     while True:
      r=json.loads(await ws.recv())
      if r.get('id')==i:
       if r.get('error'):raise RuntimeError(str(r['error']))
       return r['result']
   target=None
   try:
    target=(await call('Target.createTarget',{'url':out['url'],'background':False}))['targetId'];out['probe_target_id']=target
    session=(await call('Target.attachToTarget',{'targetId':target,'flatten':True}))['sessionId']
    samples=[]
    for i in range(5):
     await asyncio.sleep(3)
     root=(await call('DOM.getDocument',{'depth':0},session))['root']
     html=(await call('DOM.getOuterHTML',{'nodeId':root['nodeId']},session))['outerHTML']
     info=inspect_html(html,root.get('documentURL') or out['url'],'',generic_extraction=False)
     samples.append({'second':(i+1)*3,'bytes':len(html),'kind':info.page_kind,'reason':info.access_reason,'interactive':info.interactive_challenge})
     if info.page_kind in ('listing','challenge'):break
    out['samples']=samples
   except Exception as e:out['error']=type(e).__name__+':'+str(e)[:200]
   finally:
    if target:
     await call('Target.closeTarget',{'targetId':target});out['only_probe_closed']=True
 print(json.dumps(out))
asyncio.run(main())
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-'],input=code,text=True,capture_output=True,timeout=45)
print(p.stdout);print(p.stderr[-1000:]);assert p.returncode==0
