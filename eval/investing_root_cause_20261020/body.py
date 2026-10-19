"""Fresh ordinary-public articles through the real body pipeline, never published."""
import subprocess

CODE = r'''
import asyncio,json,uuid
from datetime import UTC,datetime
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessRequest,AccessMode,SitePurpose
from doxagent.message_bus_v2.market_sources import parse_investing
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.managed import SiteAccessTransport,SiteAccessBrowserReader
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
async def main():
 nonce=uuid.uuid4().hex
 class FreshClient(SiteAccessClient):
  async def execute(self,request):
   return await super().execute(request.model_copy(update={'operation_id':request.operation_id+':acceptance:'+nonce}))
 s=DoxAgentSettings();client=FreshClient(s.site_access_url,token=read_token(s.site_access_worker_token,s.site_access_worker_token_file))
 try:
  value=await client.execute(AccessRequest(operation_id='investing-body-list',url='https://www.investing.com/equities/intel-corp-news',purpose=SitePurpose.CRAWLER,mode=AccessMode.BROWSER,remaining_budget_ms=45000,recipe_parameters={'ticker':'INTC'},origin_scope_id='source:investing_ticker_news'))
  print(json.dumps({'at':datetime.now(UTC).isoformat(),'listing_disposition':value.disposition.value,'reason':value.reason_code}),flush=True)
  if value.disposition.value!='SUCCESS':return
  rows=parse_investing(value.body,ticker='INTC',now=datetime.now(UTC))
  rows=[r for r in rows if '/news/pro/' not in r.url and not r.title.lower().startswith('form ')]
  for index,row in enumerate(rows[:2]):
   transport=SiteAccessTransport(client)
   pipeline=ArticlePipeline(transport,browser=SiteAccessBrowserReader(client,transport),site_client=client,pipeline_version='body_v2.2')
   result=await pipeline.extract(MediaEnrichmentRecord(nonce+str(index),nonce+str(index),'investing','INTC',row.title,None,row.url))
   print(json.dumps({'at':datetime.now(UTC).isoformat(),'url':row.url,'title':row.title,'published_at':row.published_at.isoformat(),'succeeded':result.succeeded,'reason':result.reason,'outcome':result.diagnostics.get('outcome'),'content_length':result.to_payload().get('content_length'),'trace':transport.access_trace}),flush=True)
 finally:await client.close()
asyncio.run(main())
'''
p = subprocess.run(['docker', 'exec', '-i', 'doxagent-v2-v2-message-bus-1', 'python', '-'],
                   input=CODE, text=True, capture_output=True, timeout=240)
print(p.stdout)
if p.returncode:
    print(p.stderr[-700:])
assert p.returncode == 0
