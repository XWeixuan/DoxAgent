"""Read already-open native DOM; no navigation, JavaScript execution or storage reads."""
import subprocess

CODE = r'''
import asyncio,json,os,httpx,websockets
from lxml import html as H
from doxagent.content_enrichment.quality import inspect_html
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient

async def main():
 out=[]
 client=ChromeSupervisorClient(os.environ['DOXAGENT_SITE_ACCESS_CHROME_SUPERVISOR_SOCKET'],owner_id='readonly-diagnostic')
 for identity in ('public-markets-nl','investing-backup'):
  status=await client.status(identity)
  entry={'identity':identity,'running':status.get('running'),'instance_id':status.get('instance_id'),'pages':[]};out.append(entry)
  if not status.get('running'):continue
  async with httpx.AsyncClient(trust_env=False) as http:
   tabs=(await http.get(status['cdp_http_url']+'/json/list')).json()
  for tab in tabs:
   if tab.get('type')!='page' or 'investing.com' not in tab.get('url',''):continue
   row={'target':tab['id'],'url':tab['url'],'title':tab.get('title')};entry['pages'].append(row)
   try:
    async with websockets.connect(tab['webSocketDebuggerUrl'],max_size=12*1024*1024) as ws:
     async def call(i,method,params):
      await ws.send(json.dumps({'id':i,'method':method,'params':params}))
      async with asyncio.timeout(5):
       while True:
        r=json.loads(await ws.recv())
        if r.get('id')==i:return r['result']
     root=await call(1,'DOM.getDocument',{'depth':0})
     raw=await call(2,'DOM.getOuterHTML',{'nodeId':root['root']['nodeId']})
     html=raw['outerHTML'];tree=H.fromstring(html)
     info=inspect_html(html,tab['url'],'',generic_extraction=False)
     text=' '.join(tree.xpath('//text()[not(ancestor::script or ancestor::style)]'))
     row.update(bytes=len(html),kind=info.page_kind,reason=info.access_reason,
       headings=[' '.join(n.itertext())[:160] for n in tree.xpath('//h1|//h2[not(ancestor::article)]')][:12],
       article_items=len(tree.xpath('//*[@data-test="article-item"]')),
       titles=tree.xpath('//*[@data-test="article-title-link"]/@href')[:12],
       datetimes=tree.xpath('//article//time/@datetime')[:12],
       dialogs=[' '.join(n.itertext())[:250] for n in tree.xpath('//*[@role="dialog"]')][:3],
       signals={x:x.lower() in html.lower() for x in ['_cf_chl_opt','/cdn-cgi/challenge-platform/','Verify you are human','captcha-delivery.com','Access is temporarily restricted','Sign in']})
     row['articles']=[H.tostring(n,encoding='unicode')[:12000] for n in tree.xpath('//*[@data-test="article-item"]')[:2]]
   except Exception as exc:row['error']=type(exc).__name__
 print(json.dumps(out))
asyncio.run(main())
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-'],input=CODE,text=True,capture_output=True,timeout=55)
print(p.stdout)
assert p.returncode==0,p.stderr[-600:]
