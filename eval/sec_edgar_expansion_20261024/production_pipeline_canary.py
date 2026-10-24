"""Real SEC bodies through a separate /tmp DB; never write production Raw/Stream."""
import asyncio,json
from datetime import datetime,UTC,timedelta
from contextlib import asynccontextmanager
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.admission import AdmissionContext
from doxagent.message_bus_v2.sec_edgar.adapter import SecEdgarAdapter
from doxagent.message_bus_v2.sec_edgar.body import SecBodyReader
from doxagent.message_bus_v2.schema import PollContext
@asynccontextmanager
async def permit():yield
async def main():
 path='/tmp/sec-expanded-isolated-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S')+'.sqlite3';repo=MessageBusV2Repository(path);bus=MessageBusV2Service(repo,enrichment_queue_enabled=True);bus.bootstrap();adapter=SecEdgarAdapter();hub=ContentEnrichmentHub(repo,bus);hub.sec_body_reader=SecBodyReader();out={'at':datetime.now(UTC).isoformat(),'database':path,'production_raw_written':False,'filings':[]}
 try:
  for ticker,cik,form in [('RKLB','0001819994','SCHEDULE 13D/A'),('ONDS','0001646188','SC 13D'),('ONDS','0001646188','S-3'),('NBIS','0001513845','F-3ASR')]:
   _,_,raw=await adapter.http.get('https://data.sec.gov/submissions/CIK'+cik+'.json');r=json.loads(raw)['filings']['recent'];i=next(i for i,f in enumerate(r['form']) if f==form);at=datetime.fromisoformat(r['acceptanceDateTime'][i].replace('Z','+00:00'));bus.start_ticker(ticker);s=bus.require_source('sec_edgar');b=repo.get_binding(ticker+':sec_edgar');ctx=PollContext(ticker=ticker,source=s,binding=b,requested_at=at+timedelta(seconds=15),window_start=at-timedelta(minutes=1),request_permit=permit);polled=await adapter.poll(ctx);m=next(m for m in polled.messages if m.external_id==r['accessionNumber'][i]);m=m.model_copy(update={'admission_context':AdmissionContext(mode='CLOSED_SWEEP',sweep_id='isolated-production-canary',source_task_id=m.external_id,window_start=at-timedelta(minutes=1),cutoff=at+timedelta(minutes=1))});job,created=bus.enqueue_enrichment(source=s,binding=b,message=m,bootstrap=False,poll_run_id='isolated-production-canary');await hub.run_once();std=next((x for x in repo.list_standard(ticker=ticker) if x.external_id==m.external_id),None);out['filings'].append({'ticker':ticker,'form':form,'accession':m.external_id,'title':std.title if std else None,'chars':len(std.body) if std else 0,'outcome':std.metadata['media_enrichment']['outcome'] if std else None,'content_evidence':std.metadata['content_evidence']['kind'] if std else None,'document_count':std.metadata['media_enrichment'].get('documents_extracted') if std else None})
  with repo._connect() as db:out['counts']={name:db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ['raw_messages','standard_messages','stream_items']}
 finally:await hub.close();await adapter.close();repo.close()
 print(json.dumps(out))
asyncio.run(main())
