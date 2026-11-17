import asyncio,json,time,tempfile,hashlib
from pathlib import Path
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.live import LiveQueue,LiveDeliveryWorker
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService,TickerMonitoringTerms
from doxagent.message_bus_v2.schema import AcquisitionMode,RawMessageInput,utc_now
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.monitoring.media_enrichment import MediaExtractionResult,FetchAttempt

async def main():
 with tempfile.TemporaryDirectory() as directory:
  repo=MessageBusV2Repository(Path(directory)/'bus.sqlite3');bus=MessageBusV2Service(repo,enrichment_queue_enabled=True);bus.bootstrap();bus.start_ticker('MU')
  source=repo.get_source('finnhub_company_news').model_copy(update={'acquisition_mode':AcquisitionMode.MASSIVE_DISTRIBUTION});repo.save_source(source)
  base=repo.get_binding('MU:'+source.source_id);targets=[]
  for ticker in ['MU','INTC','BE','NBIS','COHR','IONQ','CEG','RKLB','ONDS','SNOW']:
   binding=base.model_copy(update={'ticker':ticker,'binding_id':ticker+':'+source.source_id});repo.save_binding(binding)
   rev=MonitoringTermsService(repo).apply(TickerMonitoringTerms.model_validate(dict(ticker=ticker,expected_revision=0,l1_concepts=[dict(concept_id='company',expressions={'en':'Micron'})],l2={'en':{'groups':[{'id':'company','any':[{'literal':'NEVER_MATCH_BENCHMARK'}]}]}},massive_l2={'en':{'groups':[{'id':'company','any':[{'literal':'NEVER_MATCH_BENCHMARK'}]}]}},definition={'relevant':'Micron','irrelevant':'Other'})),actor='test');targets.append((binding.binding_id,ticker,rev))
  seeds=json.loads(Path('/audit-seeds.json').read_text());bodies={f'https://benchmark.invalid/{i}':r['body'] for i,r in enumerate(seeds)}
  class Extractor:
   async def extract(self,record):
    return MediaExtractionResult(record=record,content=bodies[record.url],final_url=record.url,source_name='benchmark.invalid',attempts=(FetchAttempt('browser',record.url,status_code=200),),diagnostics={'outcome':'FULL'})
  bus.live_queue=LiveQueue(bus);live=bus.live_queue.repository
  from doxagent.v2_read.outbox import SourceOutbox
  SourceOutbox(repo.path,'bus').migrate()
  messages=[RawMessageInput(external_id=str(i),url=f'https://benchmark.invalid/{i}',title=r['title'],body=None,source='Benchmark',published_at=utc_now(),raw_payload={}) for i,r in enumerate(seeds)]
  live.accept(live.prepare_input(source,targets,messages,observed_at=utc_now().isoformat(),unit='HEAD'))
  hub=ContentEnrichmentHub(repo,bus,extractor=Extractor())
  try:
   for _ in range(200):
    await hub.run_once()
    with repo._connect() as db:pending=db.execute("SELECT count(*) FROM live_articles WHERE result_ref IS NULL").fetchone()[0]
    if not pending:break
   worker=LiveDeliveryWorker(bus);start=time.perf_counter();n=0
   for _ in range(300):
    processed=await worker.run_available(max_seconds=.25);n+=processed
    if not processed:break
   elapsed=time.perf_counter()-start
   with repo._connect() as db:states=dict(db.execute('SELECT state,count(*) FROM live_deliveries GROUP BY state'))
   print(json.dumps({'cdc_enabled':True,'articles':len(seeds),'targets':n,'seconds':elapsed,'per_second':n/elapsed,'states':states,'metrics':dict(getattr(worker,"work_metrics",{})),'body_chars':[len(r['body']) for r in seeds]}))
   assert states.get('IRRELEVANT')==len(seeds)*10,states
   assert n/elapsed>=16
  finally:await hub.close()
asyncio.run(main())

