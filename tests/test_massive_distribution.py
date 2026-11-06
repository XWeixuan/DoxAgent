from pathlib import Path

import pytest

from doxagent.message_bus_v2.newswire_sources import initial_sources, parse_feed, parse_access
from doxagent.message_bus_v2.schema import utc_now, UpdateActor
from doxagent.message_bus_v2.test_delivery import bindings
from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor


def terms(repo):
    return MonitoringTermsService(repo).apply(TickerMonitoringTerms.model_validate(dict(ticker='MU',expected_revision=0,
        l1_concepts=[dict(concept_id='micron',expressions={'en':'Micron'})],
        l2={'en':{'groups':[{'id':'company','any':[{'literal':'Micron'}]}]}},
        definition={'relevant':'Micron','irrelevant':'Other topics'})),actor='test')


@pytest.mark.parametrize('test_first',[True,False])
async def test_same_url_isolated_final_sink_without_real_subscription(tmp_path,test_first):
    repo,bus,production,binding=_setup(tmp_path)
    revision=terms(repo)
    source=next(s for s in initial_sources() if s.source_id=='prnewswire_massive')
    source=source.model_copy(update={'distribution_policy':source.distribution_policy.model_copy(update={'test_tickers':['MU']})})
    repo.save_source(source)
    assert repo.get_binding('MU:'+source.source_id) is None
    bus.live_queue=LiveQueue(bus);live=bus.live_queue.repository
    message=_message(710).model_copy(update={'title':'Micron manufacturing investment announced'})
    extractor=_GovernedExtractor();hub=ContentEnrichmentHub(repo,bus,extractor=extractor)
    try:
        for test in ([True,False] if test_first else [False,True]):
            s=source if test else production;b=bindings(source)[0] if test else binding
            rows=live.prepare_input(s,[(b.binding_id,'MU',revision)],[message],observed_at=utc_now().isoformat(),unit='WIRE:all')
            assert bool(rows[0]['key'].startswith('test_')) is test
            live.accept(rows)
            await hub.run_once()
            worker=LiveDeliveryWorker(bus)
            async def forbidden(*a,**kw):raise AssertionError('Decision Model was called')
            worker.classify=forbidden
            await worker.run_available()
        with repo._connect() as db:
            assert db.execute('SELECT count(*) FROM test_publications').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM standard_messages').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM raw_messages').fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM ticker_source_bindings WHERE source_id=?",(source.source_id,)).fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM live_articles WHERE delivery_mode='test'").fetchone()[0]==1
        # Redelivery never creates another BODY owner/publication.
        live.accept(rows)
        await hub.run_once();await worker.run_available()
        with repo._connect() as db:assert db.execute('SELECT count(*) FROM test_publications').fetchone()[0]==1
        row=live.deliveries('TEST_PUBLISHED')[0]
        message,s,b,found=worker._prepare(row)
        with repo._connect() as db:
            before=[tuple(r) for r in db.execute("SELECT * FROM artifact_ref WHERE owner_type='test_publications' ORDER BY owner_id,artifact_id")]
        prepared=live.prepare_publication(bus,row,s,b,message)
        live.publish(prepared);live.publish(prepared)
        with repo._connect() as db:
            after=[tuple(r) for r in db.execute("SELECT * FROM artifact_ref WHERE owner_type='test_publications' ORDER BY owner_id,artifact_id")]
            assert before==after
    finally:await hub.close()


def test_bw_full_window_and_language_are_not_silently_dropped():
    source=initial_sources()[0]
    fixture=Path('eval/businesswire-regex-screen-20261103/original-4078-feed.xml')
    if not fixture.exists():pytest.skip('optional real research fixture')
    rows=parse_feed(fixture.read_text(encoding='utf-8'),source,source.default_parameters['endpoints'][0],utc_now())
    assert len(rows)==4078
    assert any(r.metadata['language']=='zh-Hant' for r in rows)
    assert all(r.url.startswith('https://www.businesswire.com/') for r in rows)
    assert all(r.body is None for r in rows)


def test_access_parser_rejects_blog_and_challenge():
    with pytest.raises(ValueError):parse_access('<html>Just a moment...</html>',utc_now())
    text='''<div class="nr-card-body"><h2 class="nr-card-title"><a href="https://www.accessnewswire.com/newsroom/en/technology/contract-123">New contract</a></h2><p class="nr-card-meta">November 3, 2026 8:40 AM</p><p class="nr-card-summary">Company signs contract.</p></div>'''
    rows=parse_access(text,utc_now())
    assert len(rows)==1 and rows[0].published_at.hour==13


def test_real_subscription_to_test_source_is_rejected(tmp_path):
    repo,bus,_,_=_setup(tmp_path)
    with pytest.raises(ValueError,match='test source targets'):
        bus.configure_binding(ticker='MU',source_id='businesswire_massive',actor=UpdateActor.USER)


async def test_ten_targets_use_five_physical_units_and_calendar_is_unchanged(tmp_path):
    from types import SimpleNamespace
    from doxagent.message_bus_v2.collection_plane.config import Configuration
    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    from doxagent.message_bus_v2.collection_plane.worker import Collector
    sources=[s for s in initial_sources() if s.source_id!="bloomberg_massive"]
    cfg=Configuration(sources={s.source_id:s for s in sources},
        bindings={b.binding_id:b for s in sources for b in bindings(s)},realtime=False)
    collector=Collector(SimpleNamespace(message_bus_v2_sqlite_path=str(tmp_path/'bus.db')),
        SimpleNamespace(),store=AcquisitionStore(tmp_path/'acquisition.db',initialize=True),
        config_loader=lambda *a,**kw:cfg)
    await collector.refresh()
    assert len(collector.desired)==5
    assert all(len(value[1])==10 for value in collector.desired.values())
    assert cfg.realtime is False


async def test_304_preserves_checkpoint_and_bad_feed_does_not_become_empty_success():
    import httpx
    from contextlib import asynccontextmanager
    from doxagent.message_bus_v2.newswire_sources import NewswireAdapter
    from doxagent.message_bus_v2.schema import SharedPollContext
    @asynccontextmanager
    async def permit():yield
    class Client:
        status=304
        body='<html>Challenge</html>'
        async def get(self,url,**kw):
            return httpx.Response(self.status,text=self.body,request=httpx.Request('GET',url))
    client=Client();adapter=NewswireAdapter();adapter.clients={'direct':client}
    source=initial_sources()[0]
    source.default_parameters['discovery_mode']='rss'
    context=SharedPollContext(run_id='test',source=source,acquisition_unit='TEST:WIRE:all',
        checkpoint={'etag':'old','seen':{'article':'hash'}},request_permit=permit)
    result=await adapter.poll_shared(context)
    assert result.next_checkpoint==context.checkpoint and not result.messages
    client.status=200
    with pytest.raises((ValueError,RuntimeError)):
        await adapter.poll_shared(context)
    from email.utils import format_datetime
    client.body=f'<rss><channel><item><title>Company contract</title><link>https://www.businesswire.com/news/home/123/en/</link><pubDate>{format_datetime(context.requested_at)}</pubDate></item></channel></rss>'
    first=await adapter.poll_shared(context)
    assert first.acquisition_metadata['window_overflow_risk'] is True
    assert first.acquisition_metadata['response_bytes']>0
    context=context.model_copy(update={'checkpoint':first.next_checkpoint,'force_refresh':True})
    second=await adapter.poll_shared(context)
    assert second.acquisition_metadata['window_overlap']==1
    assert second.acquisition_metadata['window_overflow_risk'] is False


async def test_massive_body_fallback_never_opens_browser():
    from types import SimpleNamespace
    from doxagent.content_enrichment.pipeline import ArticlePipeline
    from doxagent.content_enrichment.transport import Observation
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord, DomainFetchController
    phases=[]
    async def fetch(url,attempts,*,phase='direct',**kw):
        phases.append(phase)
        return Observation(url,'',403,'http_403')
    async def browser(*a,**kw):raise AssertionError('test BODY opened a browser')
    pipeline=ArticlePipeline(SimpleNamespace(fetch=fetch,controller=DomainFetchController()),
                             browser=SimpleNamespace(read=browser))
    record=MediaEnrichmentRecord(standard_message_id='t',raw_message_id='t',source_id='businesswire_massive',
        ticker='MU',title='Micron investment',body='',url='https://www.businesswire.com/news/home/123/en',raw_url=None,source_name='Business Wire')
    await pipeline.extract(record)
    assert phases==['direct','reader']


@pytest.mark.parametrize('outcome,error',[('UNAVAILABLE','http_403'),('PARTIAL','subscription_required')])
def test_test_publisher_access_wall_defers_without_failure_or_production_block(tmp_path,outcome,error):
    repo,bus,production,binding=_setup(tmp_path)
    bus.live_queue=LiveQueue(bus);live=bus.live_queue.repository
    source=initial_sources()[0];b=bindings(source)[0]
    keys=[]
    for n in range(4):
        message=_message(800+n)
        rows=live.prepare_input(source,[(b.binding_id,b.ticker,0)],[message],observed_at=utc_now().isoformat(),unit='WIRE:all')
        live.accept(rows);keys.append(rows[0]['key'])
    with repo.transaction() as db:
        db.executemany("UPDATE live_articles SET state='READY',outcome=?,last_error=?,checked_at=? WHERE article_key=?",
                       [(outcome,error,utc_now().isoformat(),k) for k in keys[:3]])
    assert live.claim(limit=4)==[]
    pending=live.get(keys[-1]);assert pending['state']=='FETCH_DUE' and pending['attempt']==0
    assert pending['last_error']=='test_publisher_cooldown'
    rows=live.prepare_input(production,[(binding.binding_id,binding.ticker,0)],[_message(804)],observed_at=utc_now().isoformat(),unit='HEAD')
    live.accept(rows)
    assert live.claim(limit=1)[0][0]==rows[0]['key']


async def test_exact_complete_body_duplicate_across_test_urls_is_not_republished(tmp_path):
    repo,bus,_,_=_setup(tmp_path);revision=terms(repo)
    bus.live_queue=LiveQueue(bus);live=bus.live_queue.repository
    source=initial_sources()[1];binding=next(b for b in bindings(source) if b.ticker=='MU')
    hub=ContentEnrichmentHub(repo,bus,extractor=_GovernedExtractor())
    try:
        for n in (900,901):
            message=_message(n).model_copy(update={'title':'Micron announces manufacturing expansion'})
            live.accept(live.prepare_input(source,[(binding.binding_id,'MU',revision)],[message],observed_at=utc_now().isoformat(),unit='WIRE:all'))
            await hub.run_once();await LiveDeliveryWorker(bus).run_available()
        with repo._connect() as db:
            assert db.execute('SELECT count(*) FROM test_publications').fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM live_deliveries WHERE state='TEST_DUPLICATE'").fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM raw_messages').fetchone()[0]==0
    finally:await hub.close()
