"""Read browser DOM without executing publisher JavaScript; bound every CDP request."""
import subprocess, sys
code=r'''
import asyncio,json,httpx,websockets,time,os,sys
from doxagent.content_enrichment.quality import inspect_html
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
async def main():
 out=[]
 status=await ChromeSupervisorClient(os.environ['DOXAGENT_SITE_ACCESS_CHROME_SUPERVISOR_SOCKET'],owner_id='readonly-diagnostic').status(sys.argv[1])
 if not status.get('running'):
  print(json.dumps({'running':False}));return
 async with httpx.AsyncClient(trust_env=False) as http:
  tabs=(await http.get(status['cdp_http_url']+'/json/list')).json()
 for tab in tabs:
  if tab.get('type')!='page' or 'reuters.com' not in tab.get('url',''):continue
  row={'target':tab['id'],'url':tab['url'],'title':tab.get('title')};out.append(row)
  async with websockets.connect(tab['webSocketDebuggerUrl'],max_size=10*1024*1024) as ws:
   async def call(i,method,params={}):
    await ws.send(json.dumps({'id':i,'method':method,'params':params}))
    async with asyncio.timeout(4):
     while True:
      r=json.loads(await ws.recv())
      if r.get('id')==i:return r
   try:
    root=await call(1,'DOM.getDocument',{'depth':0})
    row['root']=root['result']['root'].get('documentURL')
    result=await call(2,'DOM.getOuterHTML',{'nodeId':root['result']['root']['nodeId']})
    html=result['result']['outerHTML'];info=inspect_html(html,row['root'] or tab['url'],'',generic_extraction=False)
    row.update(bytes=len(html),kind=info.page_kind,reason=info.access_reason,interactive=info.interactive_challenge,captcha_frame='iframe' in html and 'captcha-delivery.com' in html,enable_js='Please enable JS' in html)
    from lxml import html as parser
    tree=parser.fromstring(html)
    row['visible']=' '.join(tree.xpath('//text()[not(ancestor::script or ancestor::style)]'))[:700]
   except Exception as e:row['error']=type(e).__name__
 print(json.dumps(out))
asyncio.run(main())
'''
identity=sys.argv[1] if len(sys.argv)>1 else 'digitimes-de-1'
assert identity in ('digitimes-de-1','reuters-backup')
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-',identity],input=code,text=True,capture_output=True,timeout=40)
print(p.stdout);print(p.stderr[-1000:]);assert p.returncode==0
