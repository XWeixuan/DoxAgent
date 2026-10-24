import asyncio,json
from datetime import datetime,UTC,timedelta
from types import SimpleNamespace
from contextlib import asynccontextmanager
from pathlib import Path
from doxagent.message_bus_v2.sec_edgar import initial_source
from doxagent.message_bus_v2.sec_edgar.adapter import SecEdgarAdapter
from doxagent.message_bus_v2.sec_edgar.body import SecBodyReader
from doxagent.message_bus_v2.schema import PollContext,TickerSourceBinding
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
@asynccontextmanager
async def permit():yield
async def main():
 adapter=SecEdgarAdapter();reader=SecBodyReader();results=[]
 try:
  for ticker,key,form in [('RKLB','rklb','8-K'),('NBIS','nbis','6-K'),('RKLB','rklb','424B5')]:
   data=json.load(open('eval/sec_edgar_20261024/'+key+'.json'));recent=data['filings']['recent'];i=next(i for i,f in enumerate(recent['form']) if f==form);stamp=datetime.fromisoformat(recent['acceptanceDateTime'][i].replace('Z','+00:00'));s=initial_source();b=TickerSourceBinding(binding_id=ticker+':sec_edgar',ticker=ticker,source_id='sec_edgar',source_parameters={'cik':data['cik']});ctx=PollContext(ticker=ticker,source=s,binding=b,requested_at=stamp+timedelta(seconds=15),request_permit=permit)
   poll=await adapter.poll(ctx);msg=next(m for m in poll.messages if m.external_id==recent['accessionNumber'][i]);job=SimpleNamespace(source=s,binding=b,message=msg);record=MediaEnrichmentRecord(standard_message_id='isolated',raw_message_id='isolated',source_id=s.source_id,ticker=ticker,title=msg.title,body=msg.fallback_body,url=msg.url,raw_url=msg.url,source_name='SEC EDGAR');r=await reader.extract(job,record)
   results.append({'ticker':ticker,'form':form,'filing':msg.raw_payload['sec_filing'],'succeeded':r.succeeded,'reason':r.reason,'chars':len(r.content or ''),'diagnostics':r.diagnostics});Path('eval/sec_edgar_20261024/'+key+'-'+form+'-assembled.txt').write_text(r.content or '',encoding='utf-8');print(json.dumps({k:v for k,v in results[-1].items() if k not in ['diagnostics','filing']}),flush=True)
 finally:await adapter.close();await reader.close()
 Path('eval/sec_edgar_20261024/real-filing-canary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
asyncio.run(main())
