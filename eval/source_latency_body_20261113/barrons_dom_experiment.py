import asyncio,json,os,pathlib,time
import httpx,websockets
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient

async def main():
 token=pathlib.Path('/run/secrets/site_access_admin_token').read_text().strip()
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+token},timeout=70) as h:
  r=await h.post('/v1/maintenance/operations',json={'action':'OPEN_LOGIN','idempotency_key':'barrons-dom-diagnostic-20261113','scope_id':'source:barrons_ticker_news','identity_id':'dowjones-main'})
  r.raise_for_status();op=r.json()
  for _ in range(25):
   await asyncio.sleep(2);op=(await h.get('/v1/maintenance/operations/'+op['operation_id'])).json()
   if op['state'] not in ['QUEUED','RUNNING']:break
  if op['state']!='DONE':print(json.dumps({'open_state':op['state'],'error':op.get('error')}));return
  session=op['result']['session_id']
  inventory=(await h.get('/v1/maintenance/inventory')).json()
  tid=next(s['target_id'] for s in inventory['sessions'] if s['session_id']==session)
  print(json.dumps({'session_id':session,'target_id':tid,'phase':'opened'}),flush=True)
  status=await ChromeSupervisorClient(os.environ['DOXAGENT_SITE_ACCESS_CHROME_SUPERVISOR_SOCKET'],owner_id='source-latency-audit').status('dowjones-main')
  tabs=(await h.get(status['cdp_http_url']+'/json/list')).json();target=next(t for t in tabs if t['id']==tid)
  async with websockets.connect(target['webSocketDebuggerUrl'],open_timeout=5) as ws:
   seq=0
   async def call(method,params):
    nonlocal seq
    seq+=1;key=seq;await ws.send(json.dumps({'id':key,'method':method,'params':params}))
    async with asyncio.timeout(8):
     while True:
      value=json.loads(await ws.recv())
      if value.get('id')==key:return value
   await call('Page.navigate',{'url':'https://www.barrons.com/articles/micron-memory-chip-stocks-nvidia-shakeup-adf6b711'})
   start=time.monotonic()
   for delay in [2,3,5]:
    await asyncio.sleep(delay)
    expr=r'''JSON.stringify({title:document.title,ready:document.readyState,headline:document.querySelector('h1')?.innerText,
     articles:document.querySelectorAll('article').length,bodyChars:document.body?.innerText.length,
     paragraphs:[...document.querySelectorAll('p')].filter(p=>p.getClientRects().length && (p.innerText||'').length>60).slice(0,16).map(p=>({chars:p.innerText.length,sample:p.innerText.slice(0,90),ancestors:[p,...(function*(n){for(let i=0;n && i<4;i++,n=n.parentElement)yield n})(p.parentElement)].map(n=>({tag:n.tagName,id:n.id,cls:n.className,dt:n.getAttribute('data-type')}))})),
     authLabels:[...document.querySelectorAll('button,a')].filter(n=>n.getClientRects().length && /^(sign in|log in|sign out|log out|subscribe)$/i.test(n.innerText.trim())).map(n=>n.innerText.trim())})'''
    res=await call('Runtime.evaluate',{'expression':expr,'returnByValue':True})
    value=res.get('result',{}).get('result',{}).get('value')
    print(json.dumps({'elapsed':round(time.monotonic()-start,2),'snapshot':json.loads(value) if value else res.get('error')}),flush=True)
  # Release Viewer only; keep this diagnostic original page for quality analysis.
  r=await h.post('/v1/maintenance/operations',json={'action':'RELEASE_VIEWER','idempotency_key':'barrons-dom-release-20261113','session_id':session})
  print(json.dumps({'release_operation':r.json().get('operation_id')}))

asyncio.run(main())
