from types import SimpleNamespace
from datetime import timedelta
from email.utils import format_datetime
import httpx
import pytest
from doxagent.message_bus_v2.newswire_sources import initial_sources,parse_feed,NewswireAdapter
from doxagent.message_bus_v2.x_official import OfficialStream,initial_source,parse_post,TAG
from doxagent.message_bus_v2.schema import utc_now,UpdateActor,SharedPollContext
from doxagent.message_bus_v2.live import LiveDeliveryWorker
from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
from doxagent.message_bus_v2.collection_plane.bridge import CaptureBridge
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from tests.test_content_enrichment_hub import _setup
from tests.test_massive_distribution import terms


def event():
    return {'data':{'id':'99001','author_id':'19037711','text':'short',
        'note_tweet':{'text':'Micron official full post https://t.co/a','entities':{'urls':[{'url':'https://t.co/a','expanded_url':'https://micron.com/news'}]}},
        'created_at':utc_now().isoformat(),'lang':'en'},'includes':{'users':[{'id':'19037711','username':'MicronTech'}]}}


def test_sitemap_reads_every_unordered_entry_and_keeps_original_time():
    source=next(s for s in initial_sources() if s.source_id=='bloomberg_massive')
    rows=parse_feed('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:n="http://www.google.com/schemas/sitemap-news/0.9">'+''.join(
        f'<url><loc>https://www.bloomberg.com/news/articles/2026-11-04/{slug}</loc><n:news><n:title>{slug}</n:title><n:publication_date>{date}</n:publication_date></n:news></url>'
        for slug,date in [('a','2026-11-04T02:00:00Z'),('old','2026-11-03T00:00:00Z'),('b','2026-11-04T02:01:00Z')])+'</urlset>',source,{'format':'sitemap'},utc_now())
    assert [r.title for r in rows]==['a','old','b']
    assert rows[-1].published_at.hour==2
    with pytest.raises(ValueError):parse_feed('<html>Blocked</html>',source,{'format':'sitemap'},utc_now())


async def test_unsorted_rss_does_not_stop_at_old_article():
    from contextlib import asynccontextmanager
    source=next(s for s in initial_sources() if s.source_id=='bloomberg_massive')
    now=utc_now()
    feed='<rss><channel>'+''.join(f'<item><title>{slug}</title><link>https://www.bloomberg.com/news/articles/{slug}</link><pubDate>{format_datetime(date)}</pubDate></item>' for slug,date in [('company-first',now),('company-old',now-timedelta(days=1)),('hidden-new',now)])+'</channel></rss>'
    class Client:
        async def get(self,url,**kwargs):return httpx.Response(200,text=feed,request=httpx.Request('GET',url))
    @asynccontextmanager
    async def permit():yield
    adapter=NewswireAdapter();adapter.clients={'direct':Client()}
    context=SharedPollContext(run_id='r',source=source,acquisition_unit='TEST:WIRE:news',request_permit=permit)
    first=await adapter.poll_shared(context)
    assert len(first.messages)==3 and first.messages[-1].title=='hidden-new'
    again=await adapter.poll_shared(context.model_copy(update={'checkpoint':first.next_checkpoint}))
    assert not again.messages


async def test_official_post_capture_publish_without_body_worker_or_model(tmp_path):
    repo,bus,_,_=_setup(tmp_path);terms(repo)
    service=MonitoringTermsService(repo);rev,config=service.get('MU')
    service.apply(config.model_copy(update={'expected_revision':rev,'x_accounts':['microntech']}),actor='test')
    source=initial_source();repo.save_source(source)
    b=bus.configure_binding(ticker='MU',source_id=source.source_id,actor=UpdateActor.USER)
    store=AcquisitionStore(tmp_path/'acquisition.sqlite3',initialize=True)
    owner=SimpleNamespace(config=SimpleNamespace(sources={source.source_id:source},bindings={b.binding_id:b},
        terms={'MU':service.get('MU')},allowed=lambda *a,**kw:True),_control=None,store=store)
    stream=OfficialStream(owner);stream.session='test-session'
    await stream.capture(event());await stream.capture(event())
    assert len(store.pending(limit=10))==1
    bridge=CaptureBridge(bus,store=store);await bridge.once()
    live=bus.live_queue.repository
    assert not live.claim(limit=10)
    row=live.deliveries('PENDING')[0]
    assert live.get(row['article_key'])['outcome']=='NOT_APPLICABLE'
    worker=LiveDeliveryWorker(bus)
    async def no_model(*a,**kw):raise AssertionError('Model called')
    worker.classify=no_model
    await worker.run_available()
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM raw_messages').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM standard_messages').fetchone()[0]==1
    owner.config.allowed=lambda *a,**kw:False
    await stream.capture(event())
    assert not store.pending(limit=10)
    assert parse_post(event(),{'someone_else':[]}) is None


async def test_rule_reconcile_never_deletes_foreign_rules():
    requests=[]
    def handle(request):
        import json
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'foreign','tag':'other','value':'cats'},
            {'id':'own','tag':TAG+':old','value':'from:old'}]})
        requests.append(json.loads(request.content));return httpx.Response(201,json={'data':[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        stream=OfficialStream(None,client=client)
        await stream.reconcile({'microntech':[]})
    assert requests==[{'add':[{'value':'from:microntech','tag':TAG+':microntech'}]},{'delete':{'ids':['own']}}]


def test_accounts_versioned_normalized_and_readable_by_control(tmp_path):
    from doxagent.v2_control.monitoring_terms import MonitoringTermsControl
    repo,_,_,_=_setup(tmp_path);terms(repo)
    service=MonitoringTermsService(repo);rev,config=service.get('MU')
    normalized=type(config).model_validate({**config.model_dump(),'expected_revision':rev,'x_accounts':['@MicronTech','microntech']})
    assert normalized.x_accounts==['microntech']
    service.apply(normalized,actor='test')
    control=MonitoringTermsControl(repo.path)
    assert control.get_terms('MU')['configuration']['x_accounts']==['microntech']
    with pytest.raises(ValueError):type(config).model_validate({**config.model_dump(),'x_accounts':['https://x.com/other']})


async def test_bloomberg_has_seven_shared_units_and_x_has_no_poll_units(tmp_path):
    from doxagent.message_bus_v2.collection_plane.config import Configuration
    from doxagent.message_bus_v2.collection_plane.worker import Collector
    from doxagent.message_bus_v2.test_delivery import bindings
    source=next(s for s in initial_sources() if s.source_id=='bloomberg_massive')
    cfg=Configuration(sources={source.source_id:source},bindings={b.binding_id:b for b in bindings(source)},realtime=True)
    _,bus,_,_=_setup(tmp_path)
    x=initial_source();bus.repository.save_source(x)
    binding=bus.configure_binding(ticker='MU',source_id=x.source_id,actor=UpdateActor.USER)
    cfg.sources[x.source_id]=x;cfg.bindings[binding.binding_id]=binding
    collector=Collector(SimpleNamespace(message_bus_v2_sqlite_path=str(tmp_path/'collection.db')),
        SimpleNamespace(),store=AcquisitionStore(tmp_path/'acq.db',initialize=True),config_loader=lambda *a,**kw:cfg)
    await collector.refresh()
    assert len(collector.desired)==7
    assert all(value[0].source_id=='bloomberg_massive' and len(value[1])==10 for value in collector.desired.values())


def test_sitemap_and_rss_teasers_share_one_article_and_one_body_owner(tmp_path):
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.test_delivery import bindings
    from doxagent.message_bus_v2.schema import RawMessageInput
    repo,bus,_,_=_setup(tmp_path);source=next(s for s in initial_sources() if s.source_id=='bloomberg_massive')
    repo.save_source(source);binding=bindings(source)[0]
    live=LiveQueue(bus).repository
    now=utc_now();url='https://www.bloomberg.com/news/articles/2026-11-04/company-news'
    xml=f'<rss><channel><item><title>Important company news</title><link>{url}</link><description>RSS teaser</description><pubDate>{format_datetime(now)}</pubDate></item></channel></rss>'
    rss=parse_feed(xml,source,{'format':'rss','url':source.entry_url},now)[0]
    assert rss.summary is None and rss.raw_payload['listing_summary']=='RSS teaser'
    sitemap=RawMessageInput.model_validate({**rss.model_dump(),'raw_payload':{'sitemap':True}})
    for unit,message in [('TEST:WIRE:news',rss),('TEST:WIRE:sitemap',sitemap)]:
        live.accept(live.prepare_input(source,[(binding.binding_id,binding.ticker,0)],[message],observed_at=now.isoformat(),unit=unit))
    with repo._connect() as db:assert db.execute("SELECT count(*) FROM live_articles WHERE delivery_mode='test'").fetchone()[0]==1
    assert len(live.claim(limit=10))==1
