"""Read public frontend assets to identify current acquisition endpoint names."""
import asyncio,json,re,httpx
from lxml import html
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
s=DoxAgentSettings()
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',timeout=60,headers={'Authorization':'Bearer '+read_token(s.site_access_worker_token,s.site_access_worker_token_file)}) as c:
  v=(await c.post('/v1/access/execute',json={'operation_id':'yahoo-assets-20261012','purpose':'CRAWLER','mode':'BROWSER','url':'https://finance.yahoo.com/quote/MU/latest-news/','remaining_budget_ms':45000})).json()
 body=v.get('body','');tree=html.fromstring(body)
 urls=tree.xpath('//link[@rel="modulepreload"]/@href')
 urls=[u for u in urls if any(k in u.lower() for k in ('news','stream','ncp','fetch','story','content'))]
 print(json.dumps({'selected':urls}),flush=True)
 async with httpx.AsyncClient(timeout=15,proxy='http://doxagent-egress-clash:18083') as c:
  for url in urls[:20]:
   try:
    r=await c.get(url)
    print(json.dumps({'asset':url.rsplit('/',1)[-1],'status':r.status_code}),flush=True)
    if r.status_code!=200:continue
    if '/loadLatestNewsOrPressReleasesStream.' in url or '/loadTickerNews.' in url:
     print(r.text[:18000],flush=True)
    matches=[]
    for pattern in ('latestNews','queryRef','/xhr/','listAlias'):
     for m in list(re.finditer(re.escape(pattern),r.text))[:3]:matches.append(r.text[max(0,m.start()-120):m.end()+220])
    if matches:print(json.dumps({'asset':url,'matches':matches}),flush=True)
   except Exception as e:print(type(e).__name__)
asyncio.run(main())
