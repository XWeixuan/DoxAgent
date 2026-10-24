import asyncio,json
from pathlib import Path
from datetime import datetime,UTC,timedelta
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.admission import AdmissionContext
from doxagent.message_bus_v2.sec_edgar.adapter import SecEdgarAdapter
from doxagent.message_bus_v2.sec_edgar.body import SecBodyReader
from doxagent.message_bus_v2.schema import PollContext
from contextlib import asynccontextmanager
@asynccontextmanager
async def permit():yield
async def main():
 repo=MessageBusV2Repository('eval/sec_edgar_20261024/isolated-acceptance.sqlite3');bus=MessageBusV2Service(repo,enrichment_queue_enabled=True);bus.bootstrap();adapter=SecEdgarAdapter();hub=ContentEnrichmentHub(repo,bus);hub.sec_body_reader=SecBodyReader();out=[]
 try:
  for ticker,key,form in [('RKLB','rklb','8-K'),('NBIS','nbis','6-K'),('RKLB','rklb','424B5')]:
   bus.start_ticker(ticker);source=bus.require_source('sec_edgar');binding=repo.get_binding(ticker+':sec_edgar');d=json.load(open('eval/sec_edgar_20261024/'+key+'.json'));r=d['filings']['recent'];i=next(i for i,f in enumerate(r['form']) if f==form);at=datetime.fromisoformat(r['acceptanceDateTime'][i].replace('Z','+00:00'));ctx=PollContext(ticker=ticker,source=source,binding=binding,requested_at=at+timedelta(seconds=15),window_start=at-timedelta(minutes=1),request_permit=permit);poll=await adapter.poll(ctx);m=next(m for m in poll.messages if m.external_id==r['accessionNumber'][i]);admission=AdmissionContext(mode='CLOSED_SWEEP',sweep_id='isolated-canary',source_task_id=m.external_id,window_start=at-timedelta(minutes=1),cutoff=at+timedelta(minutes=1));m=m.model_copy(update={'admission_context':admission});job,created=bus.enqueue_enrichment(source=source,binding=binding,message=m,bootstrap=False,poll_run_id='isolated-canary');await hub.run_once();messages=repo.list_standard(ticker=ticker);std=next((s for s in messages if s.external_id==m.external_id),None);out.append({'ticker':ticker,'form':form,'accession':m.external_id,'standard_created':bool(std),'title':std.title if std else None,'chars':len(std.body) if std else 0,'body_outcome':std.metadata.get('media_enrichment',{}).get('outcome') if std else None,'content_evidence':std.metadata.get('content_evidence',{}).get('kind') if std else None});print(json.dumps(out[-1]),flush=True)
  with repo._connect() as db:print(json.dumps({'raw_count':db.execute('select count(*) from raw_messages').fetchone()[0],'standard_count':db.execute('select count(*) from standard_messages').fetchone()[0],'stream_count':db.execute('select count(*) from stream_items').fetchone()[0]}))
 finally:await hub.close();await adapter.close();repo.close()
 Path('eval/sec_edgar_20261024/isolated-pipeline-receipt.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
asyncio.run(main())
