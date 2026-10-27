import asyncio
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.message_bus_v2.schema import PollResult, identity_key_for, utc_now
from tests.test_content_enrichment_hub import _message, _setup


async def test_google_lists_47_wrappers_without_resolving_any_before_capture(tmp_path, monkeypatch):
    from tests.test_message_bus_v2_news_sources import _context, NOW
    from doxagent.message_bus_v2.news_adapters import GoogleNewsSearchRssAdapter
    from doxagent.settings import DoxAgentSettings

    resolve = AsyncMock(side_effect=AssertionError("listing waited for URL decoding"))
    monkeypatch.setattr("doxagent.message_bus_v2.google_news.resolve_google_news_urls", resolve)
    items = "".join(
        f"<item><guid>{i}</guid><title>Micron {i}</title>"
        f"<link>https://news.google.com/rss/articles/{i}</link><pubDate>{NOW.isoformat()}</pubDate>"
        '<source url="https://example.com">Publisher</source></item>'
        for i in range(47)
    )
    context, _ = _context(
        tmp_path, "google_news_search_rss", {"search_terms": ["Micron"], "domains": []}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="<rss><channel>" + items + "</channel></rss>")
        )
    ) as client:
        result = await GoogleNewsSearchRssAdapter(DoxAgentSettings(_env_file=None), client).poll(
            context
        )
    assert len(result.messages) == 47 and resolve.await_count == 0
    assert all(m.metadata["canonical_resolution"] == "pending_body" for m in result.messages)


async def test_google_resolution_is_single_flight_and_survives_restart(tmp_path, monkeypatch):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.repository import MessageBusV2Repository

    repo, bus, _, _ = _setup(tmp_path)
    resolver = AsyncMock(
        return_value={"https://news.google.com/articles/x": "https://example.com/article/new"}
    )
    monkeypatch.setattr("doxagent.message_bus_v2.google_news.resolve_google_news_urls", resolver)
    hub = ContentEnrichmentHub(repo, bus)
    try:
        values = await asyncio.gather(
            *(hub._google_url("https://news.google.com/articles/x") for _ in range(20))
        )
        assert len(set(values)) == 1 and resolver.await_count == 1
        reopened = MessageBusV2Repository(repo.path)
        assert reopened.polling.google_url("https://news.google.com/articles/x") == values[0]
    finally:
        await hub.close()


@pytest.mark.parametrize("position", [0, 465, 930])
def test_new_candidate_precedes_930_old_items_and_order_survives_restart(tmp_path, position):
    from doxagent.message_bus_v2.deduplication import stable_fingerprint
    from doxagent.message_bus_v2.repository import MessageBusV2Repository

    repo, _, source, binding = _setup(tmp_path)
    old = [_message(300000 + i) for i in range(930)]
    with repo.transaction() as db:
        db.executemany(
            "INSERT INTO message_observations VALUES(?,?,?,?,?,?,?,1,NULL,NULL,NULL,?)",
            [
                (
                    binding.ticker,
                    source.source_id,
                    identity_key_for(source.source_id, m),
                    str(i),
                    stable_fingerprint(m),
                    utc_now().isoformat(),
                    utc_now().isoformat(),
                    "{}",
                )
                for i, m in enumerate(old)
            ],
        )
    new = _message(400000)
    items = [*old[:position], new, *old[position:]]
    capture = repo.polling.prepare(
        source=source,
        binding=binding,
        result=PollResult(messages=items),
        run_id="930-and-new",
        observed_at=utc_now(),
    )
    repo.polling.commit(capture)
    restarted = MessageBusV2Repository(repo.path)
    row = restarted.polling.load(capture.capture_id)
    piece = restarted.polling.prepare_piece(row)
    assert piece[4].messages[0].external_id == new.external_id
    assert sorted(row["decoded_payload"]["intake_order"]) == list(range(931))
    assert len(row["decoded_payload"]["result"]["messages"]) == 931
    assert restarted.polling.load(capture.capture_id)["decoded_payload"] is row["decoded_payload"]


def test_auxiliary_request_and_late_old_cycle_do_not_fill_future_cycle():
    from doxagent.message_bus_v2.cadence import cadence

    at = utc_now().replace(microsecond=0)

    def run(name, due, dispatch, status, **extra):
        return {
            "run_id": name,
            "binding_id": "MU:s",
            "unit": "HEAD",
            "interval_seconds": 30,
            "due_at": (at + timedelta(seconds=due)).isoformat(),
            "dispatch_at": (at + timedelta(seconds=dispatch)).isoformat(),
            "status": status,
            **extra,
        }

    requests = [
        {"role": "url_resolution", "request_started_at": (at + timedelta(seconds=32)).isoformat()}
    ]
    result = cadence(
        [
            run("schedule", 0, 0, "SCHEDULE", reason="ENABLED"),
            run("late", 0, 31, "CAPTURED", requests=requests),
        ],
        [
            {
                "run_id": "late",
                "binding_id": "MU:s",
                "unit": "HEAD",
                "observed_at": (at + timedelta(seconds=33)).isoformat(),
            }
        ],
        at,
        at + timedelta(seconds=60),
    )
    assert result["expected_cycles"] == 2 and result["fresh_cycles"] == 0
    assert result["units"][0]["dispatched_cycles"] == 1
    assert result["units"][0]["request_cycles"] == 0


async def test_sqlite_busy_begin_releases_lane_for_other_short_work(tmp_path):
    repo, bus, _, _ = _setup(tmp_path)
    blocker = sqlite3.connect(repo.path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")

    def insert():
        with repo.transaction() as db:
            db.execute("INSERT OR REPLACE INTO message_first_seen VALUES('new','now')")

    task = asyncio.create_task(bus.storage.call(insert))
    try:
        await asyncio.sleep(0.02)
        assert await asyncio.wait_for(bus.storage.call(lambda: "free"), 0.5) == "free"
        assert not task.done()
    finally:
        blocker.rollback()
        blocker.close()
    await asyncio.wait_for(task, 2)
    with repo._connect() as db:
        assert (
            db.execute("SELECT first_seen_at FROM message_first_seen WHERE id='new'").fetchone()[0]
            == "now"
        )


async def test_twenty_tickers_are_sent_in_two_provider_batches_without_dropping_answers():
    from doxagent.message_bus_v2.jev import BailianDecisionClient
    from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms

    batches = []

    def respond(request):
        payload = json.loads(request.content)
        keys = list(payload["questions"])
        batches.append(keys)
        assert len(keys) <= 16
        return httpx.Response(
            200, json={"answers": {k: {"type": "noul", "noul": 0.9} for k in keys}}
        )

    definitions = {
        f"T{i}": TickerMonitoringTerms.model_validate(
            {
                "ticker": f"T{i}",
                "expected_revision": 0,
                "l1_concepts": [{"concept_id": "company", "expressions": {"en": "chip"}}],
                "l2": {"en": {"groups": [{"id": "direct", "any": [{"literal": "chip"}]}]}},
                "definition": {"relevant": "chip", "irrelevant": "other"},
            }
        )
        for i in range(20)
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await BailianDecisionClient("test", client=client).classify(
            _message(500000), definitions
        )
    assert sorted(map(len, batches)) == [4, 16] and set(result) == set(definitions)


async def test_browser_waiters_yield_instead_of_occupying_all_body_workers():
    from doxagent.content_enrichment.pipeline import ArticlePipeline
    from doxagent.content_enrichment.quality import Inspection
    from doxagent.content_enrichment.transport import Observation

    entered = []
    release = asyncio.Event()

    async def read(url, **kwargs):
        entered.append(url)
        await release.wait()
        return Observation(url, "", 200), {}

    pipeline = ArticlePipeline(SimpleNamespace(), browser=SimpleNamespace(read=read))
    first = [
        asyncio.create_task(
            pipeline._managed_browser_read(f"https://example.com/{i}", Inspection(), {}, "browser")
        )
        for i in range(2)
    ]
    await asyncio.sleep(0)
    late = await asyncio.wait_for(
        pipeline._managed_browser_read("https://example.com/third", Inspection(), {}, "browser"),
        0.1,
    )
    assert late[0].reason == "site_budget_deferred" and len(entered) == 2
    release.set()
    await asyncio.gather(*first)


async def test_consumable_receipt_requires_committed_members_and_complete_body(tmp_path):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.polling import CaptureIntake
    from tests.test_body_completion_receipts import Extractor

    repo, bus, source, binding = _setup(tmp_path)
    capture = repo.polling.prepare(
        source=source,
        binding=binding,
        result=PollResult(messages=[_message(600000)]),
        run_id="readable",
        observed_at=utc_now(),
    )
    repo.polling.commit(capture)
    intake, hub = CaptureIntake(bus), ContentEnrichmentHub(repo, bus, extractor=Extractor())
    try:
        await asyncio.wait_for(intake.wait("readable"), 5)
        await hub.run_once()
        with repo._connect() as db:
            row = db.execute(
                "SELECT data_json FROM audit_log WHERE entity_type='message_delivery'"
            ).fetchone()
        proof = json.loads(row[0])["payload"]
        assert proof["pipeline_trace"]["listing_received_at"] <= proof["readable_at"]
        assert proof["streams"][0]["body_outcomes"] == ["FULL"]
        assert not proof["consumer_claimed"]
        assert repo.read_stream("MU")[0].members
    finally:
        await intake.close()
        await hub.close()


async def test_future_identical_input_reuses_receipt_and_independent_pump_preserves_recheck(tmp_path):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.polling import CaptureIntake
    from tests.test_body_completion_receipts import Extractor
    repo,bus,source,binding = _setup(tmp_path)
    message = _message(600001)
    intake, hub = CaptureIntake(bus), ContentEnrichmentHub(repo,bus,extractor=Extractor())
    def capture(run):
        prepared = repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=[message]),
                                        run_id=run,observed_at=utc_now())
        repo.polling.commit(prepared)
        return prepared.capture_id
    try:
        capture('first')
        await intake.wait('first')
        await hub.run_once()
        capture('second')
        await intake.wait('second')
        third = capture('third')
        with repo._connect() as db:
            reused = db.execute('SELECT reused_from FROM poll_capture_inputs WHERE capture_id=?',(third,)).fetchone()[0]
        assert reused and repo.polling.load(third)['status'] == 'COMPLETED'
        with repo.transaction() as db:
            due=(utc_now()-timedelta(seconds=1)).isoformat()
            db.execute("UPDATE article_body_results SET next_check_at=?,data_json=json_set(data_json,'$.next_check_at',?)",(due,due))
        fourth = capture('due')
        assert repo.polling.load(fourth)['status'] == 'COMPLETED'
        from doxagent.content_enrichment.rechecks import enqueue_due
        assert enqueue_due(repo)==1
        assert repo.list_enrichment_jobs()[0].work_kind=='recheck'
        assert repo.latest_stream_offset('MU') == 1
    finally:
        await intake.close()
        await hub.close()


async def test_l2_wait_is_durable_and_does_not_hold_regex_article_slot(tmp_path):
    from doxagent.message_bus_v2.admission import AdmissionContext
    from doxagent.message_bus_v2.distribution import DistributionWorker
    from doxagent.message_bus_v2.distribution_repository import DistributionRepository
    from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
    from doxagent.message_bus_v2.schema import UpdateActor
    from tests.test_message_bus_distribution import _terms

    repo,bus,_,_ = _setup(tmp_path)
    source = bus.update_source('ctee_semiconductor', {'enabled':True, 'distribution_policy':{'jev_enabled':True}},actor=UpdateActor.SYSTEM)
    terms = MonitoringTermsService(repo)
    roster=[]
    for ticker,literal in [('MU','Micron'),('NVDA','Nvidia')]:
        if ticker != 'MU':bus.start_ticker(ticker)
        binding=bus.configure_binding(ticker=ticker,source_id=source.source_id,actor=UpdateActor.SYSTEM)
        terms.apply(_terms(ticker,literal),actor='test')
        roster.append((binding,1,AdmissionContext().model_dump(mode='json')))
    distribution=DistributionRepository(repo)
    run=distribution.get_or_create_run(work_key='l2-continuation',source=source,mode='REALTIME',window_start=None,cutoff=None,roster=roster)
    message=_message(600002).model_copy(update={'title':'Micron memory news','body':None})
    distribution.ingest(run['run_id'],distribution.claim_run(run['run_id']),source,[message],checkpoint={},coverage='COMPLETE',done=True,deadline_seconds=180,pipeline_version='body_v2.2')
    job=repo.claim_enrichment_jobs(limit=1)[0]
    distribution.finalize_article(job,message.model_copy(update={'body':'Micron has expanded chip production.'}),None)
    rows=distribution.claim_deliveries(article_limit=1)
    jev=SimpleNamespace(classify=AsyncMock(return_value={'MU':1.,'NVDA':1.}))
    worker=DistributionWorker(distribution,bus,terms,jev=jev)
    await asyncio.wait_for(worker._article(rows,offload_decisions=True),3)
    assert repo.latest_stream_offset('MU') == 1 and jev.classify.await_count == 0
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM distribution_jev_continuations WHERE state='PENDING'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM distribution_deliveries WHERE state='L2_WAIT'").fetchone()[0] == 1
    restarted=DistributionWorker(DistributionRepository(repo),bus,terms,jev=jev)
    try:
        await restarted.run_available()
        await asyncio.gather(*restarted._active_decisions)
        assert repo.latest_stream_offset('NVDA') == 1
        assert distribution.pending_for_run(run['run_id'],'NVDA') == []
    finally:
        await restarted.close()


@pytest.mark.parametrize('fallback', [False,True])
async def test_barrons_public_channels_and_one_browser_fallback(tmp_path, fallback):
    from doxagent.message_bus_v2.barrons_sources import render_news_feeds
    from doxagent.message_bus_v2.industry_sources import IndustryListingAdapter
    from tests.test_message_bus_v2_news_sources import _context
    context,_ = _context(tmp_path,'barrons_ticker_news',{})
    sent=[]
    def payload(channel):
        return json.dumps({'blocks':[{'$type':'News.MoreHeadlinesCard','contentFeed':channel,'blocks':[]}]})
    async def get(url,**kwargs):
        channel='BarronsDowJonesNetwork' if 'BarronsDowJonesNetwork' in url else 'Barrons'
        sent.append(channel)
        return httpx.Response(200,text='invalid' if fallback else payload(channel),headers={'Age':'1'})
    async def browser_get(url,**kwargs):
        channel=kwargs['recipe_parameters']['channel']
        return 200,url,{},render_news_feeds([{'status':200,'text':payload(channel)}],channels=[channel])
    browser=SimpleNamespace(get=AsyncMock(side_effect=browser_get))
    adapter=IndustryListingAdapter(browser,'barrons')
    adapter._public_session=SimpleNamespace(get=get)
    for unit in ('HEAD','OTHER_HEAD'):
        result=await adapter.poll(context.model_copy(update={'acquisition_unit':unit}))
        assert result.messages == []
    assert sent == (['Barrons'] if fallback else ['Barrons','BarronsDowJonesNetwork'])
    assert browser.get.await_count == (2 if fallback else 0)


async def test_yahoo_origin_cards_preserve_unknown_timestamp_without_aux_fetch():
    from doxagent.site_strategy.origin_listing import yahoo_rows
    url='https://finance.yahoo.com/news/test-fast-origin-20261026.html'
    tag=json.dumps({'yModuleName':'qsp-news','yDestinationContentUUID':'new-card'})
    html=f'''<div data-testid="storyitem"><a href="{url}" data-yga='{tag}'><h3>New card</h3></a></div>'''
    page=SimpleNamespace(evaluate=AsyncMock(side_effect=AssertionError('listing probed article timestamp')))
    rows,metadata=await yahoo_rows(page,html,'MU',{'max_probes':0})
    assert rows == [] and metadata['pending_timestamp_rows'][0]['link'] == url
    assert page.evaluate.await_count == 0


async def test_newswire_urls_are_independent_and_html_is_not_empty_rss_success(tmp_path):
    from doxagent.message_bus_v2.adapters import NewswireRSSAdapter
    from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
    from doxagent.message_bus_v2.schema import sha256_text
    from doxagent.settings import DoxAgentSettings
    from tests.test_message_bus_v2_news_sources import _context, NOW
    urls=['https://example.com/good.xml','https://example.com/bad.xml']
    context,repo=_context(tmp_path,'newswire_rss',{'rss_urls':urls})
    from doxagent.message_bus_v2.service import MessageBusV2Service
    scheduler=GlobalPollScheduler(repo,MessageBusV2Service(repo),SimpleNamespace())
    try:
        units=list(scheduler.work_units(context.source,context.binding,repo.get_poll_state(context.binding),NOW,include_all=True))
        assert {o['acquisition_unit'] for _,o in units} == {'FEED:'+sha256_text(u)[:24] for u in urls}
        sent=[]
        def response(request):
            sent.append(str(request.url))
            return httpx.Response(200,text=('<rss><channel><item><guid>x</guid><title>Good</title><link>https://example.com/article/x</link>'
                f'<pubDate>{NOW.isoformat()}</pubDate></item></channel></rss>' if request.url.path.endswith('good.xml') else '<html><title>Verify identity</title></html>'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            adapter=NewswireRSSAdapter(DoxAgentSettings(_env_file=None),client)
            good=await adapter.poll(context.model_copy(update={'acquisition_unit':'FEED:'+sha256_text(urls[0])[:24]}))
            bad=await adapter.poll(context.model_copy(update={'acquisition_unit':'FEED:'+sha256_text(urls[1])[:24]}))
        assert len(good.messages)==1 and not good.failures
        assert not bad.messages and bad.failures[0].error_code=='RSS_PARSE_ERROR'
        assert sent==urls
    finally:
        await scheduler.close()


def test_google_legacy_serial_listing_capacity_migrates_once_without_changing_custom_limits(tmp_path):
    from doxagent.message_bus_v2.schema import UpdateActor
    repo,bus,_,_=_setup(tmp_path)
    bus.update_source('google_news_search_rss',{'scheduler_constraints':{'max_concurrency':1,'minimum_request_gap_seconds':1}},actor=UpdateActor.SYSTEM)
    bus.bootstrap()
    current=repo.get_source('google_news_search_rss')
    assert current.scheduler_constraints.max_concurrency==4 and current.scheduler_constraints.minimum_request_gap_seconds==0
    version=current.version
    bus.bootstrap()
    assert repo.get_source(current.source_id).version==version
    bus.update_source(current.source_id,{'scheduler_constraints':{'max_concurrency':2,'minimum_request_gap_seconds':2}},actor=UpdateActor.SYSTEM)
    bus.bootstrap()
    assert repo.get_source(current.source_id).scheduler_constraints.minimum_request_gap_seconds==2


async def test_ibkr_each_period_has_real_sdk_receipt_and_slow_ticker_does_not_hold_global_lock():
    import threading
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsGateway
    from doxagent.message_bus_v2.poll_trace import REQUESTS
    from doxagent.settings import DoxAgentSettings
    gateway=IbkrNewsGateway(DoxAgentSettings(_env_file=None))
    slow_started, release=threading.Event(), threading.Event()
    app=SimpleNamespace(providers=[('TEST','Test')],errors=[],historical_events={},historical_rows={})
    calls=[]
    def historical(request_id,conid,providers,start,end,*args):
        assert start < end
        calls.append(conid)
        if conid == 1:
            slow_started.set()
            assert release.wait(3)
        app.historical_rows[request_id]=[{'_hasMore':False}]
        app.historical_events[request_id].set()
    app.reqHistoricalNews=historical
    gateway._ensure=lambda:app
    gateway._subscribe=lambda app,ticker:False
    gateway._conid=lambda app,ticker:1 if ticker=='MU' else 2
    now=utc_now()
    receipts=[]
    token=REQUESTS.set(receipts)
    try:
        slow=asyncio.create_task(asyncio.to_thread(gateway.poll,'MU',now-timedelta(minutes=5),now))
        await asyncio.to_thread(slow_started.wait,1)
        fast=await asyncio.wait_for(asyncio.to_thread(gateway.poll,'INTC',now-timedelta(minutes=5),now),.5)
        assert fast[0] == [] and not slow.done()
        release.set()
        await slow
        await asyncio.to_thread(gateway.poll,'INTC',now-timedelta(minutes=5),now)
        assert calls == [1,2,2]
        assert len(receipts)==3 and all(r['status']=='CALLBACK_COMPLETE' for r in receipts)
    finally:
        release.set()
        REQUESTS.reset(token)


async def test_ibkr_callback_keeps_full_body_and_callback_time_without_waiting_for_next_poll(tmp_path):
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsAdapter, IbkrNewsGateway
    from doxagent.settings import DoxAgentSettings
    from tests.test_message_bus_v2_news_sources import _context
    context,_ = _context(tmp_path,'ibkr_news',{})
    settings=DoxAgentSettings(_env_file=None)
    gateway=IbkrNewsGateway(settings)
    gateway._app=SimpleNamespace(providers=[('TEST','Publisher')])
    gateway._article=lambda *args:(0,'The company announced a complete new investment agreement. '*10)
    at=utc_now()
    gateway._stream['MU']=[{'time':int(at.timestamp()),'providerCode':'TEST','articleId':'new',
        'headline':'New investment','transport':'tick_292','callback_at':at.isoformat()}]
    on_page=AsyncMock()
    adapter=IbkrNewsAdapter(settings,gateway)
    await adapter._deliver_ticks(context.model_copy(update={'on_page':on_page}))
    assert on_page.await_count==1 and on_page.call_args.kwargs['observed_at']==at
    assert on_page.call_args.kwargs['bootstrap'] is False
    assert on_page.call_args.args[0][0].body.startswith('The company announced')
    assert gateway._stream['MU']==[]


async def test_ibkr_queued_callback_stops_starting_requests_after_override_expiry(tmp_path):
    from doxagent.message_bus_v2.collection_override import CollectionStopped
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsAdapter, IbkrNewsGateway
    from doxagent.settings import DoxAgentSettings
    from tests.test_message_bus_v2_news_sources import _context
    context,_ = _context(tmp_path,'ibkr_news',{})
    gateway=IbkrNewsGateway(DoxAgentSettings(_env_file=None))
    gateway._app=SimpleNamespace(providers=[])
    gateway._article=lambda *args:(_ for _ in ()).throw(AssertionError('new SDK request after expiry'))
    gateway._stream['MU']=[{'providerCode':'TEST','articleId':'new','callback_at':utc_now().isoformat()}]
    adapter=IbkrNewsAdapter(DoxAgentSettings(_env_file=None),gateway)
    def revoked():
        raise CollectionStopped('TEMPORARY_COLLECTION_ENDED')
    on_page=AsyncMock()
    await adapter._deliver_ticks(context.model_copy(update={'on_page':on_page}),SimpleNamespace(check=revoked))
    assert not on_page.await_count and gateway._stream['MU']


async def test_trendforce_press_public_list_does_not_require_unhealthy_browser(tmp_path):
    from doxagent.message_bus_v2.industry_sources import IndustryListingAdapter
    from tests.test_message_bus_v2_news_sources import _context
    context,_=_context(tmp_path,'trendforce_press_releases',{})
    adapter=IndustryListingAdapter(None,'trendforce_press')
    url='https://www.trendforce.com/presscenter/news'
    html='<title>Press Center</title><div><h3><a href="/presscenter/news/20260922-13249.html">DRAM module revenue increases on AI demand</a></h3></div>'
    adapter._public_session=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=200,url=url,text=html)))
    result=await adapter._get(context,url)
    assert len(result.messages)==1 and result.acquisition_metadata['listing_transport']=='public_http'


async def test_google_verified_alias_reuses_full_and_keeps_article_once(tmp_path,monkeypatch):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.schema import UpdateActor
    from tests.test_body_completion_receipts import Extractor
    repo,bus,_,_=_setup(tmp_path)
    binding=bus.configure_binding(ticker='MU',source_id='google_news_search_rss',source_parameters={'search_terms':['Micron'],'domains':[]},actor=UpdateActor.SYSTEM)
    source=repo.get_source(binding.source_id)
    target='https://example.test/article/publisher'
    async def resolve(client,urls,permit):
        return {url:target for url in urls}
    monkeypatch.setattr('doxagent.message_bus_v2.google_news.resolve_google_news_urls',resolve)
    extractor=Extractor()
    hub=ContentEnrichmentHub(repo,bus,extractor=extractor)
    base=_message(600010).model_copy(update={'metadata':{'provider':'google_news','canonical_resolution':'pending_body',
        'identity_evidence':{'id_kind':'stable_article','url_kind':'article','mutable':True}}})
    try:
        for index in range(2):
            message=base.model_copy(update={'external_id':f'guid-{index}','url':f'https://news.google.com/articles/{index}'})
            bus.enqueue_enrichment(source=source,binding=binding,message=message,bootstrap=False,poll_run_id=f'google-{index}')
            await hub.run_once()
        assert extractor.calls==1 and repo.latest_stream_offset('MU')==1
        assert {repo.polling.google_url(f'https://news.google.com/articles/{i}') for i in range(2)}=={target}
    finally:
        await hub.close()


async def test_dispatch_ticks_reuse_query_plans_until_real_terms_revision_changes(tmp_path):
    from unittest.mock import Mock
    from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
    from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
    from doxagent.message_bus_v2.schema import UpdateActor
    from tests.test_message_bus_distribution import _terms
    repo,bus,_,_=_setup(tmp_path)
    bus.configure_binding(ticker='MU',source_id='google_news_search_rss',source_parameters={'search_terms':['Micron'],'domains':[]},actor=UpdateActor.SYSTEM)
    scheduler=GlobalPollScheduler(repo,bus,SimpleNamespace())
    scheduler.terms.get=Mock(wraps=scheduler.terms.get)
    try:
        await scheduler.prepare_cycle(utc_now())
        first=scheduler.terms.get.call_count
        await scheduler.prepare_cycle(utc_now())
        assert first and scheduler.terms.get.call_count==first
        MonitoringTermsService(repo).apply(_terms('MU','Micron'),actor='test')
        await scheduler.prepare_cycle(utc_now())
        assert scheduler.terms.get.call_count>first
        assert scheduler._query_plans[('google_news_search_rss','MU')]['terms_revision']==1
    finally:
        await scheduler.close()


async def test_shared_trendforce_press_selects_its_http_capable_adapter():
    from doxagent.message_bus_v2.adapters import SiteStrategyCrawlerAdapter
    from doxagent.message_bus_v2.schema import PollResult
    press = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult(messages=[])))
    news = SimpleNamespace(poll_shared=AsyncMock(side_effect=AssertionError("wrong column")))
    client = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(
        crawler=SimpleNamespace(ref="builtin:trendforce_listings@1"),site_id="trendforce",strategy_revision=1)))
    registry = SimpleNamespace(crawler_plane=SimpleNamespace(site_access_client=client),
        _builtins={"trendforce_news":news,"trendforce_press_releases":press})
    context = SimpleNamespace(source=SimpleNamespace(source_id="trendforce_press_releases",entry_url="https://www.trendforce.com/presscenter/news"))
    await SiteStrategyCrawlerAdapter(registry).poll_shared(context)
    press.poll_shared.assert_awaited_once_with(context)


def test_cadence_links_shared_physical_capture_to_each_frozen_consumer():
    from doxagent.message_bus_v2.cadence import cadence
    at = utc_now().replace(microsecond=0)
    runs=[]
    for ticker, version in (("MU",1),("BE",2)):
        base=dict(binding_id=ticker+":feed",source_id="feed",unit="SHARED",source_version=1,binding_version=version,interval_seconds=30,due_at=at.isoformat(),dispatch_at=at.isoformat())
        runs += [{**base,"run_id":ticker+"schedule","status":"SCHEDULE","reason":"ENABLED"},
            {**base,"run_id":ticker,"status":"CAPTURED","shared_run_id":"physical-shared",
             "requests":[{"request_started_at":(at+timedelta(seconds=1)).isoformat(),"role":"listing"}]}]
    capture=dict(capture_id="capture",run_id="physical-shared",binding_id="MU:feed",source_id="feed",unit="SHARED",source_version=1,binding_version=1,observed_at=(at+timedelta(seconds=2)).isoformat())
    result=cadence(runs,[capture],at,at+timedelta(seconds=30))
    assert result["fresh_cycles"]==2 and result["expected_cycles"]==2
    # A missing physical receipt or another source version cannot fill the gap.
    assert cadence(runs,[],at,at+timedelta(seconds=30))["fresh_cycles"]==0
    assert cadence(runs,[{**capture,"source_version":99}],at,at+timedelta(seconds=30))["fresh_cycles"]==0


def test_new_capture_passes_old_daily_work_after_restart_without_dropping_inputs(tmp_path):
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.schema import PollResult
    repo,bus,source,binding=_setup(tmp_path)
    now=utc_now()
    old=_message(701).model_copy(update={"published_at":now-timedelta(days=30)})
    old_capture=repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=[old]*80),run_id="old-work",observed_at=now)
    repo.polling.commit(old_capture)
    fresh=repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=[_message(702)]),run_id="new-work",observed_at=now)
    repo.polling.commit(fresh)
    restarted=MessageBusV2Repository(repo.path)
    assert restarted.polling.claim()[0]==fresh.capture_id
    row=restarted.polling.load(old_capture.capture_id)
    assert len(row["decoded_payload"]["result"]["messages"])==80
    assert restarted.polling.claim()[0]==old_capture.capture_id


def test_schedule_tracks_unit_phase_changes_without_writing_each_period(tmp_path):
    from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
    from doxagent.message_bus_v2.schema import PollState
    repo,bus,source,binding=_setup(tmp_path)
    at=utc_now().replace(microsecond=0)
    journal=repo.polling
    journal.schedules([(source,binding,"HEAD",at,True)],at)
    journal.schedules([(source,binding,"HEAD",at+timedelta(seconds=binding.polling.target_interval_seconds),True)],at+timedelta(seconds=1))
    with repo._connect() as db:
        assert db.execute("select count(*) from poll_runs where status='SCHEDULE'").fetchone()[0]==1
    journal.schedules([(source,binding,"HEAD",at+timedelta(seconds=3),True)],at+timedelta(seconds=2))
    with repo._connect() as db:
        assert db.execute("select count(*) from poll_runs where status='SCHEDULE'").fetchone()[0]==2
    state=repo.get_poll_state(binding).model_copy(update={"target_due_at":at,"units":{"OTHER_HEAD":{"target_due_at":(at+timedelta(seconds=7)).isoformat()}}})
    assert GlobalPollScheduler.schedule_epoch(state,"OTHER_HEAD")==at+timedelta(seconds=7)


async def test_idle_fetch_documents_match_identity_slots_and_freeze_then_resume():
    import time
    from doxagent.site_strategy.fetch_anchors import FetchAnchor,FetchAnchors
    pool=FetchAnchors()
    identity=SimpleNamespace(identity_id="existing",revision=1,access=SimpleNamespace(max_concurrency=2))
    egress=SimpleNamespace(egress_id="approved")
    anchors=[];commands=[]
    for i in range(3):
        async def send(method,params):commands.append((method,params["state"]))
        session=SimpleNamespace(send=send,detach=AsyncMock())
        page=SimpleNamespace(context=SimpleNamespace(new_cdp_session=AsyncMock(return_value=session)),is_closed=lambda:False)
        anchors.append(FetchAnchor(SimpleNamespace(__aexit__=AsyncMock()),page,SimpleNamespace(blocked=False,close=AsyncMock()),None,1,"approved",time.monotonic()))
    assert pool.put(identity,anchors[0]) and pool.put(identity,anchors[1])
    assert not pool.put(identity,anchors[2])
    taken=await pool.take(identity,egress)
    assert taken in anchors[:2] and not taken.frozen
    assert ("Page.setWebLifecycleState","active") in commands
    assert pool.put(identity,taken)
    await pool.close()
    for anchor in anchors[:2]:anchor.lease.__aexit__.assert_awaited_once()
