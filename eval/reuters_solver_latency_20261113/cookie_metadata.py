import asyncio,json
from websockets.asyncio.client import connect
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
async def main():
 s=DoxAgentSettings();c=ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='cookie-scope-metadata-audit');out=[]
 for identity in ('digitimes-de-1','reuters-backup'):
  v=await c.status(identity)
  async with connect(v['cdp_websocket_url'],close_timeout=1) as ws:
   n=0
   async def call(method,params={}):
    nonlocal n;n+=1;await ws.send(json.dumps({'id':n,'method':method,'params':params}))
    async with asyncio.timeout(5):
     while True:
      r=json.loads(await ws.recv())
      if r.get('id')==n:return r.get('result',{})
   # Browser storage metadata only; no cookie values leave the process.
   cookies=(await call('Storage.getCookies')).get('cookies',[])
   out.append({'identity':identity,'datadome':[ {k:x.get(k) for k in ('domain','path','secure','httpOnly')} for x in cookies if x.get('name')=='datadome' and 'reuters.com' in x.get('domain','')]})
 print(json.dumps(out))
asyncio.run(main())
