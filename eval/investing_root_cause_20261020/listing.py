"""Two failing bindings through the normal owner API; no publication or credentials."""
import subprocess

CODE = r'''
import asyncio,json,uuid
from datetime import UTC,datetime
from lxml import html as H
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessRequest,AccessMode,SitePurpose
from doxagent.message_bus_v2.market_sources import parse_investing
async def main():
 s=DoxAgentSettings();client=SiteAccessClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 try:
  for ticker,slug in [('COHR','coherent'),('ONDS','ondas')]:
   value=await client.execute(AccessRequest(operation_id='investing-list-diagnostic-'+uuid.uuid4().hex,url='https://www.investing.com/equities/'+slug+'-news',purpose=SitePurpose.CRAWLER,mode=AccessMode.BROWSER,remaining_budget_ms=180000,recipe_parameters={'ticker':ticker},origin_scope_id='source:investing_ticker_news'))
   out={'at':datetime.now(UTC).isoformat(),'ticker':ticker,'disposition':value.disposition.value,'reason':value.reason_code,'final_url':value.final_url,'bytes':len(value.body or '')}
   if value.body:
    root=H.fromstring(value.body)
    out.update(title=root.xpath('//title/text()'),headings=[' '.join(n.itertext())[:180] for n in root.xpath('//h1|//h2')][:8],links=root.xpath('//*[@data-test="article-title-link"]/@href')[:12],dates=root.xpath('//article//time/@datetime')[:12],cards=[H.tostring(n,encoding='unicode')[:3500] for n in root.xpath('//*[@data-test="article-item"]')[:2]])
    try:out['parsed']=len(parse_investing(value.body,ticker=ticker,now=datetime.now(UTC)))
    except Exception as exc:out['parse_error']=str(exc)[:250]
   print(json.dumps(out),flush=True)
 finally:await client.close()
asyncio.run(main())
'''
p = subprocess.Popen(['docker','exec','-i','doxagent-v2-v2-message-bus-1','python','-'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
p.stdin.write(CODE);p.stdin.close()
for line in p.stdout:
    print(line,end='',flush=True)
assert p.wait(timeout=240)==0,p.stderr.read()[-600:]
