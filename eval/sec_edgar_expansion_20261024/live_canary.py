"""Expanded SEC forms, isolated real HTTP BODY checks; no production message writes."""
import asyncio,json
from datetime import datetime,timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from doxagent.message_bus_v2.sec_edgar import initial_source
from doxagent.message_bus_v2.sec_edgar.adapter import SecEdgarAdapter
from doxagent.message_bus_v2.sec_edgar.body import SecBodyReader
from doxagent.message_bus_v2.schema import PollContext,TickerSourceBinding
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
@asynccontextmanager
async def permit():yield
async def main():
 a=SecEdgarAdapter();b=SecBodyReader();out=[]
 try:
  for ticker,cik,form in [('RKLB','1819994','SCHEDULE 13D/A'),('ONDS','1646188','SC 13D'),('ONDS','1646188','S-3'),('NBIS','1513845','F-3ASR'),('ONDS','1646188','ITEM:3.01'),('MU','723125','ITEM:3.03')]:
   _,_,raw=await a.http.get('https://data.sec.gov/submissions/CIK'+cik.zfill(10)+'.json');r=json.loads(raw)['filings']['recent'];i=next(i for i,f in enumerate(r['form']) if (form[5:] in r['items'][i] and f=='8-K' if form.startswith('ITEM:') else f==form));at=datetime.fromisoformat(r['acceptanceDateTime'][i].replace('Z','+00:00'));source=initial_source();binding=TickerSourceBinding(binding_id=ticker+':sec_edgar',ticker=ticker,source_id='sec_edgar',source_parameters={'cik':cik});ctx=PollContext(source=source,binding=binding,ticker=ticker,window_start=at-timedelta(minutes=1),requested_at=at+timedelta(seconds=15),request_permit=permit);polled=await a.poll(ctx);m=next(m for m in polled.messages if m.external_id==r['accessionNumber'][i]);record=MediaEnrichmentRecord(standard_message_id='isolated',raw_message_id='isolated',source_id='sec_edgar',ticker=ticker,title=m.title,body=m.summary,url=m.url);result=await b.extract(SimpleNamespace(message=m),record);entry={'ticker':ticker,'form':r['form'][i],'accession':m.external_id,'items':m.metadata['sec_filing']['selected_items'],'title':result.diagnostics['generated_title'],'success':result.succeeded,'outcome':result.diagnostics['outcome'],'reason':result.reason,'chars':len(result.content or ''),'documents':result.diagnostics['documents'],'errors':result.diagnostics['document_errors']};out.append(entry);print(json.dumps(entry),flush=True)
 finally:await a.close();await b.close()
 Path('eval/sec_edgar_expansion_20261024/live-canary.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
asyncio.run(main())
