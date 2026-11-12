"""Read already-loaded document/resource timings and exact child frames. No website I/O."""
import asyncio,json,sqlite3
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from datetime import datetime,timezone
import httpx
from websockets.asyncio.client import connect
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
from doxagent.site_strategy.verification_frames import observe_verification_document,frame_context
from doxagent.site_strategy.datadome_browser import challenge_state
from doxagent.site_strategy.challenge_evidence import safe_url
EXPR="""JSON.stringify({timeOrigin:performance.timeOrigin, navigation:performance.getEntriesByType('navigation').map(n=>({name:n.name,start:n.startTime,duration:n.duration,status:n.responseStatus,redirects:n.redirectCount})), resources:performance.getEntriesByType('resource').filter(n=>/reuters.com\\/(?:pf\\/api|api|arc)|captcha-delivery.com|datadome.co/.test(n.name)).slice(-45).map(n=>({name:n.name,start:n.startTime,duration:n.duration,status:n.responseStatus,initiator:n.initiatorType}))})"""
def clean(raw):
 v=json.loads(raw);origin=v['timeOrigin']
 for k in ('navigation','resources'):
  for r in v[k]:
   u=r.pop('name');r['url']=safe_url(u);r['observed_at']=datetime.fromtimestamp((origin+r['start'])/1000,timezone.utc).isoformat();q=parse_qs(urlsplit(u).query);r['t']=q.get('t',[None])[0]
 return v
async def main():
 s=DoxAgentSettings();d=sqlite3.connect(Path(s.site_access_sqlite_path).as_uri()+'?mode=ro',uri=True);out=[];sup=ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='reuters-resource-timings')
 for identity in ('digitimes-de-1','reuters-backup'):
  v=await sup.status(identity);recovery=json.loads(d.execute('select data_json from browser_recoveries where identity_id=?',(identity,)).fetchone()[0])
  async with httpx.AsyncClient(trust_env=False,timeout=3) as http:tabs=(await http.get(v['cdp_http_url']+'/json/list')).json()
  tab=next((t for t in tabs if t.get('id')==recovery.get('original_target_id')),None) if identity=='digitimes-de-1' else next((t for t in tabs if t.get('type')=='page' and urlsplit(t.get('url','')).hostname=='www.reuters.com'),None)
  if not tab:out.append({'identity':identity,'missing':True});continue
  async with connect(tab['webSocketDebuggerUrl'],close_timeout=1,max_size=6000000) as ws:
   n=0
   async def call(method,params=None,*,session_id=None):
    nonlocal n;n+=1;msg={'id':n,'method':method,'params':params or {}}
    if session_id:msg['sessionId']=session_id
    await ws.send(json.dumps(msg))
    async with asyncio.timeout(4):
     while True:
      r=json.loads(await ws.recv())
      if r.get('id')==n:
       if 'error'in r:raise RuntimeError('cdp_'+method)
       return r.get('result',{})
   row={'identity':identity,'target_id':tab['id']}
   try:
    r=await call('Runtime.evaluate',{'expression':EXPR,'returnByValue':True});row['performance']=clean(r['result']['value'])
   except Exception as e:row['performance_error']=type(e).__name__
   if identity=='digitimes-de-1':
    doc=await observe_verification_document(call);row['assessment']=doc['assessment'].model_dump(mode='json');row['frames']=[]
    cookies=(await call('Network.getCookies',{'urls':['https://www.reuters.com/']})).get('cookies',[]);dd=[c['value'] for c in cookies if c['name']=='datadome']
    for f in doc['frames']:
     q=parse_qs(urlsplit(f.get('url','')).query);frame={'frame_id':f.get('frame_id'),'url':safe_url(f.get('url')),'t':q.get('t',[None])[0],'visible':f.get('visible'),'loaded':f.get('loaded'),'state':challenge_state(f.get('url'),f.get('html')),'cid_matches_current_cookie':q.get('cid',[None])[0] in dd}
     try:
      async with frame_context(call,f['frame_id']) as (session,ctx):
       params={'expression':EXPR,'returnByValue':True}
       if ctx is not None:params['contextId']=ctx
       r=await call('Runtime.evaluate',params,session_id=session);frame['performance']=clean(r['result']['value'])
     except Exception as e:frame['performance_error']=type(e).__name__
     row['frames'].append(frame)
   out.append(row)
 print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'documents':out},indent=2))
asyncio.run(main())
