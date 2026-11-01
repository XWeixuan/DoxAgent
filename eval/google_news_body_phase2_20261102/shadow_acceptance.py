"""Real production modules, isolated SQLite, deterministic discovery/body controls."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

root=Path(sys.argv[1])
import doxagent.content_enrichment as content
import doxagent.message_bus_v2 as messages
content.__path__.insert(0,str(root/'content_enrichment'))
messages.__path__.insert(0,str(root/'message_bus_v2'))

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
from doxagent.message_bus_v2.schema import RawMessageInput, UpdateActor, utc_now
from doxagent.monitoring.media_enrichment import MediaExtractionResult
from doxagent.site_strategy.scope import current_routing

BODY='\n\n'.join(['Micron announced a new manufacturing investment with multiple partners. The company expects the facility to begin production in 2028. The agreement includes a $250 million capital commitment, with payments tied to construction milestones. Management expects the project to improve production capacity by 18 percent.']*4)


async def one(site,native,order):
    directory=Path(tempfile.mkdtemp(prefix='google-phase2-shadow-'))/(site+'-'+order)
    directory.mkdir(parents=True,exist_ok=True)
    repo=MessageBusV2Repository(directory/'bus.sqlite3')
    bus=MessageBusV2Service(repo,enrichment_queue_enabled=True);bus.bootstrap();bus.start_ticker('MU')
    bus.live_queue=LiveQueue(bus)
    for sid in ('google_news_search_rss',native):
        bus.update_source(sid,{'enabled':True,'content_language':'en'},actor=UpdateActor.SYSTEM)
        if not repo.get_binding('MU:'+sid):bus.configure_binding(ticker='MU',source_id=sid,source_parameters={'search_terms':['Micron']},actor=UpdateActor.SYSTEM)
    calls=[]
    class Extractor:
        async def extract(self,record):
            calls.append((record.source_id,current_routing().origin_scope_id))
            return MediaExtractionResult(record=record,content=BODY,final_url=record.url,diagnostics={'outcome':'FULL'})
    hub=ContentEnrichmentHub(repo,bus,extractor=Extractor())
    url='https://www.'+site+'/articles/micron-factory-announcement-12345'
    async def resolve(wrapper):return url.replace('www.','')+'/?utm_source=google'+('&mod=google&siteid=rss' if site in ('barrons.com','marketwatch.com') else '')
    hub._google_url=resolve
    def capture(sid):
        google=sid=='google_news_search_rss'
        message=RawMessageInput(title='Micron manufacturing announcement',summary='Google title link snippet' if google else 'Native publisher synopsis',url='https://news.google.com/rss/articles/opaque123' if google else url+'?utm_source=native'+('&mod=native' if site in ('barrons.com','marketwatch.com') else ''),
            published_at=utc_now(),raw_payload={},metadata={'language':'en',**({'canonical_resolution':'pending_body','allowed_publisher_domains':[site]} if google else {}),'identity_evidence':{'url_kind':'article'}})
        source=bus.require_source(sid)
        live=bus.live_queue.repository
        live.accept(live.prepare_input(source,[('MU:'+sid,'MU',0)],[message],observed_at=utc_now().isoformat(),unit='HEAD'))
    async def drain():
        for _ in range(3):
            await hub.run_once();await LiveDeliveryWorker(bus).run_available()
    try:
        if order=='native-first':capture(native);await drain()
        capture('google_news_search_rss')
        if order=='simultaneous':capture(native)
        await drain();capture(native);await drain()
        with repo._connect() as db:
            published=[r[0] for r in db.execute('SELECT source_id FROM standard_messages')]
            streams=db.execute('SELECT count(*) FROM stream_items').fetchone()[0]
        assert calls==[(native,'source:'+native)],calls
        assert published==[native] and streams==1,(published,streams)
        return dict(site=site,order=order,body_calls=len(calls),source_id=published[0],streams=streams)
    finally:await hub.close()


async def main():
    out=[]
    for site,native,order in [('barrons.com','barrons_ticker_news','google-first'),('reuters.com','reuters_site_search','native-first'),('investing.com','investing_ticker_news','simultaneous'),('marketwatch.com','barrons_ticker_news','native-first')]:
        out.append(await one(site,native,order))
    print(json.dumps(out))

asyncio.run(main())
