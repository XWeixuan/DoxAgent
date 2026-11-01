"""Production bad cases for the explicitly selected November lean repairs."""
import asyncio
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.publisher_http import PublisherClients
from doxagent.message_bus_v2.poll_trace import TRANSPORT_PROGRESS


async def test_pre_send_stall_retires_only_affected_publisher_once_until_progress():
    sessions = []
    class Session:
        traced_transport = True
        broken = False
        def __init__(self):
            self.closed = False
            sessions.append(self)
        async def request(self, method, url, **kwargs):
            if 'good' in url:
                return 'ok'
            TRANSPORT_PROGRESS.get()['phase'] = 'connection.start_tls.started'
            await asyncio.Event().wait()
        async def close(self):
            self.closed = True
    clients = PublisherClients(factory=Session)
    try:
        assert await clients.request('good', 'GET', 'https://good') == 'ok'
        good = clients.clients['good']
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await clients.request('bad', 'GET', 'https://bad', timeout=.01)
        assert 'bad' not in clients.clients and clients.clients['good'] is good
        await asyncio.sleep(0)
        assert sessions[1].closed
        for _ in range(3):
            with pytest.raises(TimeoutError):
                await clients.request('bad', 'GET', 'https://bad', timeout=.01)
        assert len(sessions) == 3  # not one replacement per failed cycle
        assert clients.clients['bad'].slots._value == 2
    finally:
        await clients.close()


async def test_retired_transport_waits_for_other_active_request_to_finish():
    entered, release = asyncio.Event(), asyncio.Event()
    class Session:
        traced_transport = True
        broken = False
        closed = False
        async def request(self, method, url, **kwargs):
            if url.endswith('/held'):
                entered.set()
                await release.wait()
                assert not self.closed
                return 'finished'
            await asyncio.Event().wait()
        async def close(self): self.closed = True
    clients = PublisherClients(factory=Session)
    held = asyncio.create_task(clients.request('same', 'GET', 'https://p/held'))
    await entered.wait()
    old = clients.clients['same']
    try:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await clients.request('same', 'GET', 'https://p/fail', timeout=.01)
        assert old.retired and not old.session.closed
        release.set()
        assert await held == 'finished'
        await asyncio.sleep(0)
        assert old.session.closed
    finally:
        release.set()
        await clients.close()


async def test_sent_slow_response_and_admission_wait_do_not_retire_transport():
    class Session:
        traced_transport = True
        broken = False
        async def request(self, *args, **kwargs):
            TRANSPORT_PROGRESS.get()['sent'] = True
            await asyncio.Event().wait()
        async def close(self): pass
    clients = PublisherClients(factory=Session)
    try:
        for _ in range(3):
            with pytest.raises(TimeoutError):
                await clients.request('p', 'GET', 'https://p', timeout=.01)
        client = clients.clients['p']
        assert not client.retired
        await client.slots.acquire(); await client.slots.acquire()
        try:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(.01):
                    await clients.request('p', 'GET', 'https://p', timeout=.01)
            assert clients.clients['p'] is client and not client.retired
        finally:
            client.slots.release(); client.slots.release()
    finally:
        await clients.close()


@pytest.mark.parametrize('persistent', [False, True])
async def test_builtin_feed_timeout_has_at_most_one_retry(persistent):
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    import httpx
    from doxagent.message_bus_v2.industry_sources import SharedFeedAdapter
    calls = []
    async def get(url, **kwargs):
        calls.append(url)
        if persistent or len(calls) == 1:
            raise TimeoutError('outer publisher request deadline')
        return httpx.Response(200, request=httpx.Request('GET', url), text='''
            <rss><channel><item><title>Actual company announcement</title>
            <link>https://publisher.test/news/one</link>
            <pubDate>Sun, 01 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>''')
    @asynccontextmanager
    async def permit():
        yield
    context = SimpleNamespace(
        source=SimpleNamespace(entry_url='https://publisher.test/rss', content_language='en', default_parameters={}),
        request_permit=permit, requested_at=datetime(2026, 11, 1, tzinfo=UTC), window_start=None)
    adapter = SharedFeedAdapter(SimpleNamespace(get=get), 'Publisher')
    if persistent:
        with pytest.raises(TimeoutError):
            await adapter.poll_shared(context)
    else:
        assert len((await adapter.poll_shared(context)).messages) == 1
    assert len(calls) == 2


@pytest.mark.parametrize('require_full', [False, True])
async def test_capability_failure_has_first_result_and_durable_resume_without_publisher_spin(tmp_path, require_full):
    from datetime import timedelta
    from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.monitoring.media_enrichment import MediaExtractionResult, FetchAttempt
    from doxagent.message_bus_v2.schema import utc_now
    repo, bus, source, binding = _setup(tmp_path)
    from doxagent.message_bus_v2.schema import DistributionPolicy
    source = source.model_copy(update={'distribution_policy': DistributionPolicy(require_complete_body=require_full)})
    repo.save_source(source)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    rows = live.prepare_input(source, [(binding.binding_id, 'MU', 0)],
        [_message(9101), _message(9102)], observed_at=utc_now().isoformat(), unit='HEAD')
    live.accept(rows)
    class Blocked:
        calls = 0
        async def extract(self, record):
            self.calls += 1
            return MediaExtractionResult(record=record, reason='site_recovery_pending',
                attempts=(FetchAttempt('browser', record.url, reason='site_recovery_pending', latency_ms=80),))
    extractor = Blocked()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    try:
        assert await hub.run_once() == 2
        assert extractor.calls == 1
        for item in rows:
            article = live.get(item['key'])
            assert article['state'] == 'READY' and article['initial_outcome'] == 'UNAVAILABLE'
            assert article['initial_result_at'] and article['initial_request_at'] is None
            assert article['next_check_at'] > (utc_now() + timedelta(seconds=50)).isoformat()
        worker = LiveDeliveryWorker(bus)
        await worker.run_available()
        before = len(repo.read_stream('MU'))
        assert before == (0 if require_full else 2)
        assert all(live.get(item['key'])['state'] == 'READY' for item in rows)
        with repo.transaction() as db:
            db.execute("UPDATE live_articles SET next_check_at=?", ((utc_now()-timedelta(seconds=1)).isoformat(),))
        assert live.enqueue_checks() == 2
        assert all(live.get(item['key'])['work_kind']=='capability_resume' for item in rows)
        # Restart the Hub; due_at is durable, and completion never rewrites first failure.
        recovered = ContentEnrichmentHub(repo, bus, extractor=_GovernedExtractor())
        try:
            assert (await recovered.run_once()) + (await recovered.run_once()) == 2
            assert all(live.get(item['key'])['outcome']=='FULL' for item in rows)
            assert all(live.get(item['key'])['initial_outcome']=='UNAVAILABLE' for item in rows)
            await worker.run_available()
            assert len(repo.read_stream('MU')) == 2
        finally: await recovered.close()
    finally: await hub.close()


async def test_first_listing_update_does_not_reset_download_budget_or_age(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import utc_now
    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    message = _message(9103)
    def prepare(m): return live.prepare_input(source, [(binding.binding_id,'MU',0)], [m], observed_at=utc_now().isoformat(),unit='HEAD')
    rows=prepare(message);live.accept(rows)
    first=(await bus.live_queue.claim(1))[0]
    live.defer(first.model_copy(update={'execution_spent_ms':1234, 'fetch_round':1, 'not_before':utc_now()}))
    live.accept(prepare(message.model_copy(update={'title':'A material correction to this headline'})))
    article=live.get(rows[0]['key'])
    assert article['work_kind']=='new_article'
    assert article['execution_spent_ms']==1234 and article['fetch_round']==1 and article['attempt']==1
    assert article['first_fetch_at'] and article['first_seen_at']==rows[0]['first_seen']


async def test_capability_obligation_survives_old_check_expiry_without_displacing_new_article(tmp_path):
    from datetime import timedelta
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import utc_now
    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    now = utc_now()
    rows = live.prepare_input(source, [(binding.binding_id, 'MU', 0)],
        [_message(9104), _message(9105), _message(9106)], observed_at=now.isoformat(), unit='HEAD')
    live.accept(rows)
    with repo.transaction() as db:
        for row, reason in zip(rows, ['site_recovery_pending', 'http_error']):
            db.execute("UPDATE live_articles SET state='READY',first_seen_at=?,next_check_at=?,last_error=? WHERE article_key=?",
                ((now-timedelta(days=2)).isoformat(), (now-timedelta(seconds=1)).isoformat(), reason, row['key']))
    assert live.enqueue_checks() == 1
    assert live.get(rows[0]['key'])['work_kind'] == 'capability_resume'
    assert live.get(rows[1]['key'])['next_check_at'] is None
    assert live.claim(limit=1)[0][0] == rows[2]['key']
    assert live.claim(limit=1, background=True)[0][0] == rows[0]['key']


@pytest.mark.parametrize('token_error', [False, True])
async def test_calcalist_refreshes_only_explicit_invalid_token_once(token_error):
    from doxagent.message_bus_v2.defense_sources import DefenseAdapter, ENDPOINTS
    class Http:
        boots = 0
        queries = 0
        async def fetch(self, url, endpoint):
            if 'cse.js?' in url:
                self.boots += 1
                return SimpleNamespace(status=200,text='{"cse_token":"t'+str(self.boots)+'","cselibVersion":"v"}')
            self.queries += 1
            text='{"error":{"message":"expired cse token"}}' if token_error else '{"error":{"message":"permission denied"}}'
            return SimpleNamespace(status=403,text=text)
    http=Http();adapter=DefenseAdapter(http)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='calcalist_search_http_403'):
            await adapter._search(ENDPOINTS['calcalist_search'],'Ondas')
    assert http.boots == (3 if token_error else 1)
    assert http.queries == (4 if token_error else 2)


async def test_ibkr_historical_timeout_releases_request_but_preserves_partial_news():
    import threading
    from datetime import timedelta
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsGateway, NewsRequestTimeout
    from doxagent.message_bus_v2.schema import utc_now
    from doxagent.settings import DoxAgentSettings
    gateway=IbkrNewsGateway(DoxAgentSettings(_env_file=None, ibkr_tws_timeout_seconds=1))
    app=SimpleNamespace(providers=[('T','Publisher')],errors=[],historical_events={},historical_rows={})
    gateway._ensure=lambda:app;gateway._subscribe=lambda *a:None;gateway._conid=lambda *a:1
    def send(request_id,*args):
        app.historical_rows[request_id].append({'articleId':'partial','providerCode':'T','time':utc_now().isoformat()})
        app.historical_events[request_id]=SimpleNamespace(wait=lambda timeout:False)
    app.reqHistoricalNews=send
    with pytest.raises(NewsRequestTimeout): gateway._poll('MU',utc_now()-timedelta(minutes=5),utc_now())
    assert not app.historical_events and not app.historical_rows
    assert gateway._stream['MU'][0]['articleId']=='partial'


async def test_ibkr_fast_body_is_captured_before_slow_body_and_new_tick_is_not_lost(tmp_path):
    import threading
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsGateway,IbkrNewsAdapter
    from doxagent.settings import DoxAgentSettings
    from doxagent.message_bus_v2.schema import utc_now
    from tests.test_message_bus_v2_news_sources import _context
    context,_=_context(tmp_path,'ibkr_news',{})
    settings=DoxAgentSettings(_env_file=None)
    gateway=IbkrNewsGateway(settings);gateway._app=SimpleNamespace(providers=[('T','Publisher')])
    release=threading.Event();captured=asyncio.Event();seen=[]
    at=utc_now()
    def row(key):return dict(time=int(at.timestamp()),providerCode='T',articleId=key,headline=key,callback_at=at.isoformat())
    gateway._stream['MU']=[row('slow'),row('fast')]
    def body(app,provider,identity):
        if identity=='slow':release.wait(3)
        return 0,'A complete corporate news report. '*40
    gateway._article=body
    async def on_page(messages,**kwargs):
        seen.extend(m.external_id for m in messages)
        if 'T:fast' in seen and not captured.is_set():
            gateway._stream['MU'].append(row('new-tick'))
            captured.set()
    adapter=IbkrNewsAdapter(settings,gateway)
    work=asyncio.create_task(adapter._deliver_ticks(context.model_copy(update={'on_page':on_page})))
    try:
        await asyncio.wait_for(captured.wait(),1)
        assert not work.done() and 'T:slow' not in seen
        release.set();await asyncio.wait_for(work,3)
        assert set(seen)=={'T:fast','T:slow','T:new-tick'}
        assert not gateway._stream['MU']
    finally:release.set();await work


async def test_ibkr_failed_article_does_not_discard_other_completed_articles(tmp_path):
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsGateway, IbkrNewsAdapter
    from doxagent.settings import DoxAgentSettings
    from doxagent.message_bus_v2.schema import utc_now
    from tests.test_message_bus_v2_news_sources import _context
    context, _ = _context(tmp_path, 'ibkr_news', {})
    settings = DoxAgentSettings(_env_file=None)
    gateway = IbkrNewsGateway(settings)
    gateway._app = SimpleNamespace(providers=[('T', 'Publisher')])
    at = utc_now()
    gateway._stream['MU'] = [dict(time=int(at.timestamp()), providerCode='T', articleId=key,
        headline=key, callback_at=at.isoformat()) for key in ('failed', 'complete')]
    def body(app, provider, identity):
        if identity == 'failed':
            raise ConnectionError('one article failed')
        return 0, 'Complete news report. ' * 40
    gateway._article = body
    seen = []
    async def on_page(messages, **kwargs):
        seen.extend(m.external_id for m in messages)
    adapter = IbkrNewsAdapter(settings, gateway)
    await adapter._deliver_ticks(context.model_copy(update={'on_page': on_page}))
    assert seen == ['T:complete']
    assert [r['articleId'] for r in gateway._stream['MU']] == ['failed']
    assert set(gateway._delivered['MU']) == {'T:complete'}


async def test_ibkr_selected_rows_remain_pending_until_capture_commits(tmp_path):
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsGateway, IbkrNewsAdapter
    from doxagent.settings import DoxAgentSettings
    from tests.test_message_bus_v2_news_sources import _context
    context, _ = _context(tmp_path, 'ibkr_news', {})
    settings = DoxAgentSettings(_env_file=None)
    gateway = IbkrNewsGateway(settings)
    app = SimpleNamespace(providers=[('T', 'Publisher')], errors=[], historical_events={}, historical_rows={})
    gateway._app = app
    gateway._ensure = lambda: app
    gateway._subscribe = lambda *args: None
    gateway._conid = lambda *args: 1
    def send(request_id, *args):
        app.historical_rows[request_id].extend([
            dict(providerCode='T', articleId='one', time=int(context.requested_at.timestamp()), headline='News'),
            {'_hasMore': False}])
        app.historical_events[request_id].set()
    app.reqHistoricalNews = send
    gateway.poll = gateway._poll
    gateway._article = lambda *args: (0, 'Complete news report. ' * 40)
    async def failed_capture(*args, **kwargs):
        raise OSError('local capture unavailable')
    adapter = IbkrNewsAdapter(settings, gateway)
    with pytest.raises(OSError):
        await adapter.poll(context.model_copy(update={'on_page': failed_capture}))
    assert not gateway._delivered['MU'] and gateway._stream['MU']
    seen = []
    async def capture(messages, **kwargs):
        seen.extend(m.external_id for m in messages)
        # A partial capture must not move the whole historical coverage cursor.
        assert kwargs['checkpoint'].get('last_sdk_until') == context.checkpoint.get('last_sdk_until')
    result = await adapter.poll(context.model_copy(update={'on_page': capture}))
    assert seen == ['T:one'] and result.window_coverage == 'COMPLETE'
    assert result.next_checkpoint['last_sdk_until'] == context.requested_at.isoformat()
    assert not gateway._stream['MU'] and 'T:one' in gateway._delivered['MU']


def test_history_matching_skips_source_body_and_unused_native_evidence(tmp_path):
    import json
    from doxagent.v2_read.native_content import NativeContent
    from doxagent.persistent_runtime_v2.w3_context import _history_match_state,event_matches,actual_policy_hits
    codec=NativeContent(tmp_path/'runtime.sqlite3')
    case={'source':{'body':'very large article '*30000},'irrelevant_tool_result':'tool '*30000,
          'w1_final':{'result':'OLD','reference_ids':['event-1']},
          'w2_final':{'policy_hit':True,'policy_ids':['P1']},
          'version_pin':{'event_library_root':'root'},'frozen_inputs':{'provisional':[]}}
    raw=json.loads(codec.encode(case));reads=[];read=codec.files.read_all
    def observe(key):reads.append(key);return read(key)
    codec.files.read_all=observe
    thin=_history_match_state(raw,codec)
    assert event_matches(thin)==event_matches(case) and actual_policy_hits(thin)==actual_policy_hits(case)
    assert reads==[] and 'source' not in thin


def test_bailian_incomplete_stream_has_thin_finish_reason_without_hidden_retry():
    from dataclasses import replace
    from tests.test_w2_r1_openrouter import request
    from doxagent.persistent_runtime_v2.bailian_chat import complete
    from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
    calls=[]
    def create(**kwargs):
        calls.append(1)
        choice=SimpleNamespace(delta=SimpleNamespace(content='{}'),finish_reason='length')
        class Stream(list):
            def __enter__(self): return self
            def __exit__(self,*args): pass
        return Stream([SimpleNamespace(id='stream-id',usage=None,choices=[choice])])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(RuntimeResponsesError) as exc:
        complete(client,replace(request(),reasoning_effort='high'))
    assert exc.value.usage['finish_reason']=='length' and exc.value.usage['response_id']=='stream-id'
    assert exc.value.usage['elapsed_ms']>=0 and calls==[1]


from tests.test_site_strategy import site_service as _site_service_fixture

site_service = _site_service_fixture


async def test_investing_optional_article_login_wall_is_not_sitewide_auth_failure(site_service,monkeypatch):
    from doxagent.site_strategy.schema import AccessRequest,SitePurpose,AccessMode,AccessDisposition
    from doxagent.site_strategy.runtime import RuntimeResponse
    calls=[]
    async def runtime(request,*args):
        calls.append(request.url)
        return RuntimeResponse(200,request.url,{},'<article><h1>Article</h1><p>Create a free account to continue</p></article>')
    def poison(*args):raise AssertionError('article login wall invalidated shared public identity')
    monkeypatch.setattr(site_service.runtime,'execute',runtime)
    monkeypatch.setattr(site_service,'_mark_identity_auth',poison)
    result=await site_service.execute(AccessRequest(operation_id='optional-article-wall',purpose=SitePurpose.BODY,
        mode=AccessMode.BROWSER,url='https://www.investing.com/news/stock-market-news/article-12345'))
    assert result.disposition==AccessDisposition.AUTH_REQUIRED and result.reason_code=='login_required'
    assert result.body and len(calls)==1
