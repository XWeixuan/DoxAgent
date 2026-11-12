"""One-shot passive local CDP and SQLite audit. No navigation, reload, input or provider API."""
import asyncio,json,sqlite3,hashlib,time,re
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from datetime import datetime,timezone
import httpx
from websockets.asyncio.client import connect
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
from doxagent.site_strategy.challenge_evidence import classify,safe_url,safe_text,EvidenceStore
s=DoxAgentSettings();db=sqlite3.connect(Path(s.site_access_sqlite_path).as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
pick=lambda v,keys:{k:v.get(k) for k in keys.split()}
def data(table,key,val):
 row=db.execute('select data_json from '+table+' where '+key+'=?',(val,)).fetchone();return json.loads(row[0]) if row else {}
async def channel(url,fn):
 async with connect(url,close_timeout=1,max_size=6000000) as ws:
  n=0
  async def call(method,params=None,**extra):
   nonlocal n;n+=1;await ws.send(json.dumps({'id':n,'method':method,'params':params or {},**extra}))
   async with asyncio.timeout(4):
    while True:
     r=json.loads(await ws.recv())
     if r.get('id')==n:
      if 'error' in r: raise RuntimeError('cdp_'+method+'_failed')
      return r.get('result',{})
  return await fn(call)
async def main():
 out={'at':datetime.now(timezone.utc).isoformat(),'identities':[]};client=ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='reuters-passive-forensics')
 for id in ('digitimes-de-1','reuters-backup'):
  row=db.execute('select r.spec_json from browser_identity_revisions r join browser_identity_heads h on r.identity_id=h.identity_id and r.revision=h.active_revision where h.identity_id=?',(id,)).fetchone();identity=json.loads(row[0]);p=data('browser_profiles','profile_id',identity['profile_id']);eg=data('proxy_egresses','egress_id',identity['egress_id']);recovery=data('browser_recoveries','identity_id',id)
  v={'identity':pick(identity,'identity_id revision profile_id runtime_kind browser_release environment access'),'profile':pick(p,'operational_state maintenance_session_id auth_state'),'egress':pick(eg,'egress_id generation observed_ip observed_at status transport proxy_url node_ref'),'runtime':pick(data('identity_runtime','identity_id',id),'instance_id session_revision operational_state'),'recovery':pick(recovery,'recovery_id phase opened_at resolved_at original_instance_id original_target_id last_error')};v['egress'].pop('proxy_url',None)
  status=await client.status(id);v['supervisor']=pick(status,'instance_id generation pid running');v['open_sessions']=db.execute("select count(*) from maintenance_sessions where identity_id=? and state='OPEN'",(id,)).fetchone()[0]
  async def storage(call):return (await call('Storage.getCookies')).get('cookies',[])
  cookies=await channel(status['cdp_websocket_url'],storage);cs=[c for c in cookies if c.get('domain','').lstrip('.') in {'reuters.com','www.reuters.com'}];dd=[c for c in cs if c['name']=='datadome'];v['cookies']=[{**pick(c,'name domain path secure httpOnly sameSite session sourceScheme sourcePort partitionKey'), 'ttl_seconds':round(c.get('expires',-1)-time.time()) if c.get('expires',-1)>0 else None} for c in cs];v['datadome_cookie_count']=len(dd)
  async with httpx.AsyncClient(trust_env=False,timeout=4) as http:
   tabs=(await http.get(status['cdp_http_url']+'/json/list')).json();version=(await http.get(status['cdp_http_url']+'/json/version')).json();v['browser']=pick(version,'Browser User-Agent Protocol-Version')
  pages=[t for t in tabs if t.get('type')=='page' and urlsplit(t.get('url','')).hostname=='www.reuters.com'][:4];v['pages']=[]
  for tab in pages:
   page={'target_id':tab['id'],'url':safe_url(tab['url']),'original':tab['id']==recovery.get('original_target_id')}
   async def observe(call):
    root=(await call('DOM.getDocument',{'depth':0}))['root'];html=(await call('DOM.getOuterHTML',{'nodeId':root['nodeId']}))['outerHTML'];facts=classify(html);page.update(classification=facts,html_bytes=len(html),session_by_header_marker=bool(re.search('sessionByHeader',html)),dd_script_marker=bool(re.search(r'(?:var|let|const)\s+dd\s*=',html)))
    frame=(await call('Page.getFrameTree')).get('frameTree',{});children=frame.get('childFrames',[]);page['frames']=[]
    for child in children[:8]:
     f=child.get('frame',{});u=f.get('url','');q=parse_qs(urlsplit(u).query);page['frames'].append({'frame_id':f.get('id'),'url':safe_url(u),'t':q.get('t',[None])[0],'cid_matches_cookie':any(q.get('cid',[None])[0]==c.get('value') for c in dd) if 'cid' in q else None})
    try:
     stored=(await call('DOMStorage.getDOMStorageItems',{'storageId':{'securityOrigin':'https://www.reuters.com','isLocalStorage':True}})).get('entries',[]);session=next((v for k,v in stored if k=='ddSession'),None);page['ddSession']={'present':session is not None,'matches_cookie':any(session==c.get('value') for c in dd) if session else None}
    except Exception:page['ddSession']={'readable':False}
    try:
     nav=(await call('Runtime.evaluate',{'expression':"JSON.stringify({ua:navigator.userAgent,sessionByHeader:window.ddoptions?.sessionByHeader,nav:performance.getEntriesByType('navigation').map(n=>({responseStatus:n.responseStatus,redirectCount:n.redirectCount,type:n.type}))})",'returnByValue':True})).get('result',{}).get('value');page['navigation']=json.loads(nav) if isinstance(nav,str) else None
    except Exception:page['navigation']=None
   try:await channel(tab['webSocketDebuggerUrl'],observe)
   except Exception as e:page['read_error']=type(e).__name__
   v['pages'].append(page)
  out['identities'].append(v)
 jobrow=db.execute("select data_json from datadome_solver_jobs where json_extract(data_json,'$.task_id')=84403569365").fetchone();j=json.loads(jobrow[0]);out['job']=pick(j,'job_id identity_id recovery_id allocation_id scene binding task_id status created_at submitted_at provider_ready_at applied_at verification_state verification_reason cookie_scope_verified confirmation_probe_reserved business_probe_id diagnostic_timeline');out['job_fields']=sorted(j)
 target=data('owned_browser_targets','allocation_id',j.get('allocation_id'));out['job_target']=pick(target,'identity_id browser_instance_id target_id profile_id state owner_kind owner_ref');key=EvidenceStore.key({'identity_id':j['identity_id'],'browser_instance_id':target.get('browser_instance_id'),'target_id':target.get('target_id')});ep=Path(s.site_access_profile_root).parent/'challenge-evidence'/(key+'.json');out['evidence']=json.loads(ep.read_text()) if ep.exists() else None
 for identity in out['identities']:
  identity['other_cookie_count']=sum(c['name'] not in {'datadome','ArcP','ArcId.USER_PROFILE'} for c in identity['cookies'])
  identity['cookies']=[c for c in identity['cookies'] if c['name'] in {'datadome','ArcP','ArcId.USER_PROFILE'}]
  for page in identity['pages']:
   facts=page.get('classification',{});facts['visible_excerpt']=facts.get('visible_excerpt','')[:100]
   for k in ('iframe_urls','script_urls'):facts[k]=[u for u in facts.get(k,[]) if any(h in u for h in ('captcha-delivery.com','dd.reuters.com'))]
 print(json.dumps(out,indent=2))
asyncio.run(main())
