"""Regression of Nov 11 long-lived collection failure boundaries."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from lxml import html

from doxagent.message_bus_v2.energy_catalog import SOURCES
from doxagent.message_bus_v2.energy_sources import parse_listing,field
from doxagent.message_bus_v2.corporate_ir.http import PublicHttp
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.poll_trace import REQUESTS
from doxagent.message_bus_v2.publisher_http import joint_permits,http_budget
from doxagent.message_bus_v2.scheduler import POLL_TIMINGS,SchedulerGroupLimiter
from doxagent.message_bus_v2.schema import SchedulerConstraints
from tests.test_public_listing import Http,capture,context,NOW,RECIPES,ListingAdapter


def test_nrc_real_part21_has_reporter_without_fabricated_licensee():
    raw=Path('tests/fixtures/energy_sources/nrc_events_20261111.html').read_text(encoding='utf-8')
    spec=SOURCES['nrc_event_notifications'];messages=parse_listing(spec,raw,spec.urls[0],NOW)
    assert len(messages)==3
    part=next(m for m in messages if m.raw_payload['event_id']=='58409')
    assert part.raw_payload['reporting_org']=='ABB Inc'
    assert not part.raw_payload['licensee'] and not part.raw_payload['facility']
    assert 'ABB Inc' in part.title and part.raw_payload['event_html']
    assert not any(m.metadata.get('nrc_rejected_events') for m in messages)


def test_nrc_bad_event_isolated_and_real_fields_accept_wrapped_values():
    doc=html.fromstring('<div><strong>Licensee :</strong><span>Example Operator</span><br/><b>Facility:</b> Station</div>')
    assert field(doc,'Licensee')=='Example Operator' and field(doc,'Facility')=='Station'
    raw=Path('tests/fixtures/energy_sources/nrc_events_20261111.html').read_text(encoding='utf-8')
    raw=raw.replace('ABB Inc','')
    spec=SOURCES['nrc_event_notifications'];messages=parse_listing(spec,raw,spec.urls[0],NOW)
    assert len(messages)==2 and all(m.metadata['nrc_rejected_events']==['58409:issuer_missing'] for m in messages)


@pytest.mark.asyncio
async def test_ir_pool_failure_retires_only_bad_host_and_next_cycle_recovers():
    owner=PublicHttp();spec=next(s for s in initial_endpoints() if s.endpoint_id=='nbis.press')
    made=[]
    def client(host):
        if host not in owner.clients:
            fail=not made
            def send(request):
                if fail:raise httpx.PoolTimeout('exhausted old pool')
                return httpx.Response(200,text='valid response')
            owner.clients[host]=httpx.AsyncClient(transport=httpx.MockTransport(send))
            made.append(owner.clients[host])
        return owner.clients[host]
    owner._client=client
    healthy=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200)))
    owner.clients['healthy.example']=healthy
    try:
        with pytest.raises(httpx.PoolTimeout):await owner.fetch(spec)
        await asyncio.sleep(0)
        assert made[0].is_closed and not healthy.is_closed
        assert (await owner.fetch(spec)).body=='valid response'
        assert len(made)==2 and not owner._active and not owner._retired
    finally:await owner.close()


@pytest.mark.asyncio
async def test_ir_waiting_role_capacity_does_not_hold_host_or_replace_client():
    from urllib.parse import urlsplit
    owner=PublicHttp();spec=next(s for s in initial_endpoints() if s.endpoint_id=='nbis.press')
    role=http_budget('collector');taken=role._value
    for _ in range(taken):await role.acquire()
    task=asyncio.create_task(owner.fetch(spec));await asyncio.sleep(.01)
    assert owner.host_slots[urlsplit(spec.entry_url).hostname]._value==2
    task.cancel();await asyncio.gather(task,return_exceptions=True)
    assert not owner.clients and not owner._active
    for _ in range(taken):role.release()
    await owner.close()


@pytest.mark.asyncio
async def test_joint_cancel_and_opposite_order_keep_capacity_and_bounded_timing():
    timings={};token=POLL_TIMINGS.set(timings)
    a,b=asyncio.Semaphore(1),asyncio.Semaphore(1)
    async def request(slots):
        async with joint_permits(*slots):await asyncio.sleep(.001)
    try:
        await asyncio.wait_for(asyncio.gather(*(request((a,b) if i%2 else (b,a)) for i in range(120))),2)
        assert a._value==b._value==1
        await a.acquire();task=asyncio.create_task(request((a,b)));await asyncio.sleep(.01)
        task.cancel();await asyncio.gather(task,return_exceptions=True);a.release()
        assert a._value==b._value==1 and 'http_admission_ms' in timings
        assert len(timings)<5
    finally:POLL_TIMINGS.reset(token)


@pytest.mark.asyncio
async def test_quantum_head_commits_before_failed_supplement_and_next_cycle_keeps_coverage():
    first=capture('quantum_zeitgeist');http=Http(first,ValueError('page two temporarily unavailable'))
    adapter=ListingAdapter(SimpleNamespace(),http);ctx=context('quantum_zeitgeist');pages=[]
    async def on_page(messages,**evidence):pages.append((messages,evidence))
    ctx.on_page=on_page;events=[];token=REQUESTS.set(events)
    try:
        result=await adapter.poll_shared(ctx)
        assert pages and result.messages and not result.failures
        assert not result.window_done and result.window_coverage=='PARTIAL'
        cp=result.next_checkpoint['listings'][RECIPES['quantum_zeitgeist'][0].url]
        assert cp['supplement_pending'] and cp['supplement_error']
        assert result.acquisition_metadata['validated_request_ids']
        # The retained debt is attempted on the next ordinary cycle, no extra poll.
        ctx.checkpoint=result.next_checkpoint
        http2=Http(first,first);adapter.http=http2
        recovered=await adapter.poll_shared(ctx)
        assert recovered.window_done and not recovered.failures
        assert not recovered.next_checkpoint['listings'][RECIPES['quantum_zeitgeist'][0].url]['supplement_pending']
        assert not recovered.messages
    finally:REQUESTS.reset(token)

@pytest.mark.asyncio
async def test_joint_eligible_fifo_avoids_blocked_host_and_cancel_after_grant_leak():
    from doxagent.message_bus_v2.publisher_http import _ADMISSIONS
    host,other,role=asyncio.Semaphore(0),asyncio.Semaphore(1),asyncio.Semaphore(1)
    entered=[]
    async def run(slots,name):
        async with joint_permits(*slots):entered.append(name)
    waiting=asyncio.create_task(run((host,role),'blocked'));await asyncio.sleep(0)
    await run((other,role),'healthy')
    assert entered==['healthy'] and role._value==1
    host.release()
    _ADMISSIONS[asyncio.get_running_loop()].drain()
    waiting.cancel()  # Admission granted, task has not resumed yet.
    await asyncio.gather(waiting,return_exceptions=True)
    assert host._value==role._value==1
    assert not _ADMISSIONS[asyncio.get_running_loop()].pending


@pytest.mark.asyncio
async def test_joint_source_pacing_and_shrink_debt_are_preserved():
    source=SchedulerGroupLimiter(SchedulerConstraints(max_concurrency=2,minimum_request_gap_seconds=.01))
    role=asyncio.Semaphore(2);times=[];release=asyncio.Event()
    async def run():
        async with joint_permits(role,source=source):
            times.append(asyncio.get_running_loop().time());await release.wait()
    first=asyncio.create_task(run());second=asyncio.create_task(run())
    while len(times)<2:await asyncio.sleep(.005)
    assert times[1]-times[0]>=.009
    source._shrink_debt=1;release.set()
    await asyncio.gather(first,second)
    assert role._value==2 and source._semaphore._value==1 and source._shrink_debt==0

@pytest.mark.asyncio
async def test_network_deadline_excludes_queue_but_parent_still_bounds_operation():
    from doxagent.message_bus_v2.publisher_http import network_timeout
    role=asyncio.Semaphore(0)
    async def delayed_release():
        await asyncio.sleep(.08);role.release()
    releaser=asyncio.create_task(delayed_release())
    async with network_timeout(.04):
        async with joint_permits(role):await asyncio.sleep(.01)
    await releaser
    assert role._value==1
    # Real network work still cannot exceed its own budget after admission.
    with pytest.raises(TimeoutError):
        async with network_timeout(.02):
            async with joint_permits(role):await asyncio.sleep(.08)
    assert role._value==1
    await role.acquire()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(.04):
            async with network_timeout(.02):
                async with joint_permits(role):pytest.fail('unexpected admission')
    assert role._value==0
    role.release()

@pytest.mark.asyncio
async def test_calcalist_token_is_refreshed_when_own_transport_generation_changes():
    import time
    from doxagent.message_bus_v2.defense_sources import DefenseAdapter,ENDPOINTS,Capture
    e=ENDPOINTS['calcalist_search'];old,new=object(),object()
    bootstrap=Capture(200,e.urls[0],'{"cse_token":"new", "cselibVersion":"v1"}',{})
    query=Capture(200,e.urls[0],'{"results":[]}',{})
    http=SimpleNamespace(clients=SimpleNamespace(clients={(e.host,e.route):new}),
                         fetch=AsyncMock(side_effect=[bootstrap,query,query]))
    adapter=DefenseAdapter(http);adapter.cse=(time.monotonic()+300,{'cse_token':'old','cselibVersion':'v1'});adapter.cse_owner=old
    assert (await adapter._search(e,'Ondas')).status==200
    assert http.fetch.await_count==2 and adapter.cse_owner is new
    await adapter._search(e,'Ondas')
    assert http.fetch.await_count==3  # Stable generation reuses its token.

@pytest.mark.asyncio
async def test_shared_rss_local_network_budget_does_not_include_role_queue(monkeypatch):
    from doxagent.message_bus_v2.industry_sources import SharedFeedAdapter
    from doxagent.message_bus_v2.publisher_http import PublisherHttpx,PublisherClients
    from tests.test_message_bus_industry_sources import NOW as feed_now
    class Session:
        broken=False
        async def request(self,method,url,**kwargs):
            await asyncio.sleep(.01)
            return httpx.Response(200,request=httpx.Request(method,url),text='''<rss><channel><item>
              <title>Company news</title><link>https://publisher.test/news/one</link>
              <pubDate>2026-09-23T18:49:35Z</pubDate></item></channel></rss>''')
        async def close(self):pass
    @asynccontextmanager
    async def permit():yield
    limiter=SchedulerGroupLimiter(SchedulerConstraints(max_concurrency=1,minimum_request_gap_seconds=0))
    ctx=SimpleNamespace(source=SimpleNamespace(entry_url='https://publisher.test/rss',content_language='en',default_parameters={}),
                        request_permit=limiter.permit,requested_at=feed_now,window_start=None)
    client=PublisherHttpx();client.clients=PublisherClients(factory=Session)
    adapter=SharedFeedAdapter(client,'Publisher');adapter.NETWORK_TIMEOUT_SECONDS=.04
    role=http_budget('collector');n=role._value
    for _ in range(n):await role.acquire()
    task=asyncio.create_task(adapter.poll_shared(ctx));await asyncio.sleep(.08)
    assert not task.done() and limiter._semaphore._value==1
    for _ in range(n):role.release()
    assert (await asyncio.wait_for(task,.3)).messages
    await client.aclose();await adapter.close()

@pytest.mark.asyncio
async def test_ir_429_backoff_is_persisted_only_in_its_existing_lane():
    from doxagent.message_bus_v2.collection_plane.worker import Collector,recovery_checkpoint
    from doxagent.message_bus_v2.corporate_ir.http import Capture
    @asynccontextmanager
    async def lock(*args):yield
    spec=next(s for s in initial_endpoints() if s.endpoint_id=='be.press')
    http=SimpleNamespace(fetch=AsyncMock(return_value=Capture(429,spec.entry_url,'',{'retry-after':'120'},0)))
    adapter=SimpleNamespace(http=http,endpoint_lock=lock)
    binding=SimpleNamespace(binding_id='BE:corporate_ir')
    r=await Collector.ir(None,adapter,spec,binding,{})
    assert r.site_access_deferred and r.site_access_deferred_reason=='ir_http_429'
    lane=recovery_checkpoint({},r.next_checkpoint)
    assert lane['retry_at']
    again=await Collector.ir(None,adapter,spec,binding,lane)
    assert again.site_access_deferred_reason=='ir_provider_backoff' and http.fetch.await_count==1
    # Existing race checkpoint merge keeps this debt under RSS, not its HTTP sibling.
    assert not recovery_checkpoint({'http':{'baseline':True}},{'rss':lane})['http'].get('retry_at')


@pytest.mark.asyncio
async def test_network_deadline_excludes_source_capacity_wait_too():
    from doxagent.message_bus_v2.publisher_http import network_timeout
    limiter=SchedulerGroupLimiter(SchedulerConstraints(max_concurrency=1,minimum_request_gap_seconds=0))
    await limiter._semaphore.acquire()
    async def work():
        async with network_timeout(.04):
            async with limiter.permit():await asyncio.sleep(.01)
    task=asyncio.create_task(work());await asyncio.sleep(.08)
    assert not task.done()
    limiter._semaphore.release()
    await asyncio.wait_for(task,.3)
    assert limiter._semaphore._value==1

@pytest.mark.asyncio
async def test_local_adapter_capacity_is_joint_with_role_and_source():
    from doxagent.message_bus_v2.publisher_http import PublisherClients,source_http_scope
    class Session:
        broken=False
        async def request(self,*args,**kwargs):return 'response'
        async def close(self):pass
    client=PublisherClients(factory=Session)
    role=http_budget('collector');capacity=role._value
    for _ in range(capacity):await role.acquire()
    source=SchedulerGroupLimiter(SchedulerConstraints(max_concurrency=1,minimum_request_gap_seconds=0))
    local=asyncio.Semaphore(2)
    async def request():
        async with source_http_scope(source.permit,local):
            return await client.request('example','GET','https://example.test/')
    task=asyncio.create_task(request());await asyncio.sleep(.01)
    assert local._value==2 and source._semaphore._value==1
    for _ in range(capacity):role.release()
    assert await asyncio.wait_for(task,.2)=='response'
    assert local._value==2 and source._semaphore._value==1
    await client.close()


@pytest.mark.asyncio
async def test_supplement_wall_queue_timeout_preserves_head_and_debt(monkeypatch):
    import doxagent.message_bus_v2.public_listing as module
    monkeypatch.setattr(module,'PRIMARY_TIMEOUT_SECONDS',.04)
    first=capture('quantum_zeitgeist')
    class SlowSupplement(Http):
        async def fetch(self,*args,**kwargs):
            if self.calls:await asyncio.sleep(.2)
            return await super().fetch(*args,**kwargs)
    adapter=ListingAdapter(SimpleNamespace(),SlowSupplement(first,first))
    ctx=context('quantum_zeitgeist');ctx.on_page=AsyncMock();token=REQUESTS.set([])
    try:
        result=await asyncio.wait_for(adapter.poll_shared(ctx),.15)
        assert result.messages and not result.failures and result.window_coverage=='PARTIAL'
        assert result.next_checkpoint['listings'][RECIPES['quantum_zeitgeist'][0].url]['supplement_pending']
        assert ctx.on_page.await_count==1
        assert result.acquisition_metadata['endpoints'][0]['supplement']['error']=='supplement_admission_or_request_timeout'
    finally:REQUESTS.reset(token)


@pytest.mark.asyncio
async def test_shared_child_survives_callers_closed_network_timer_without_leaking():
    from doxagent.message_bus_v2.publisher_http import network_timeout
    role=asyncio.Semaphore(0)
    async def shared():
        async with joint_permits(role):return 'captured'
    async with network_timeout(1):
        task=asyncio.create_task(shared())
        await asyncio.sleep(.01)
    # Parent timer is EXITED, while its shared child is still waiting.
    role.release()
    try:
        assert await asyncio.wait_for(task,.2)=='captured'
    finally:
        assert role._value==1


@pytest.mark.asyncio
async def test_granted_permits_return_even_if_wait_boundary_raises(monkeypatch):
    from contextlib import contextmanager
    import doxagent.message_bus_v2.publisher_http as module
    @contextmanager
    def failing_boundary():
        yield
        raise RuntimeError('timer owner ended')
    monkeypatch.setattr(module,'network_wait',failing_boundary)
    slot=asyncio.Semaphore(1)
    with pytest.raises(RuntimeError,match='timer owner ended'):
        async with joint_permits(slot):pytest.fail('request cannot start')
    assert slot._value==1


@pytest.mark.asyncio
async def test_test_lane_cannot_occupy_production_reserve_and_total_is_unchanged():
    from doxagent.message_bus_v2.publisher_http import PublisherClients,source_http_scope
    entered=[];release=asyncio.Event()
    class Session:
        broken=False
        async def request(self,method,url,**kwargs):
            entered.append(url);await release.wait();return url
        async def close(self):pass
    client=PublisherClients(factory=Session)
    test_budget=http_budget('collector_test')
    source=SchedulerGroupLimiter(SchedulerConstraints(max_concurrency=2,minimum_request_gap_seconds=0))
    async def request(name,test=False):
        async with source_http_scope(source.permit,*((test_budget,) if test else ())):
            return await client.request(name,'GET',name)
    # Separate source constraints for production requests, as in production.
    @asynccontextmanager
    async def plain():yield
    async def production(i):
        async with source_http_scope(plain):return await client.request(str(i),'GET',str(i))
    tasks=[asyncio.create_task(request('test1',True)),asyncio.create_task(request('test2',True))]
    tasks += [asyncio.create_task(production(i)) for i in range(6)]
    try:
        async with asyncio.timeout(.5):
            while len(entered)<7:await asyncio.sleep(.001)
        assert len([x for x in entered if x.startswith('test')])==1
        assert http_budget('collector')._value==0 and test_budget._value==0
        assert http_budget('site_crawler')._value==2
        release.set();await asyncio.gather(*tasks)
        assert http_budget('collector')._value+http_budget('site_crawler')._value==9
        assert test_budget._value==1 and source._semaphore._value==2
    finally:
        release.set();await asyncio.gather(*tasks,return_exceptions=True);await client.close()


@pytest.mark.asyncio
async def test_digitimes_canonical_redirect_is_once_and_foreign_or_login_is_rejected():
    from doxagent.message_bus_v2.industry_sources import IndustryListingAdapter
    from tests.test_message_bus_industry_sources import NOW as industry_now
    from doxagent.message_bus_v2.poll_trace import request_started
    class Browser:
        def __init__(self,location):self.location=location;self.calls=[]
        async def get(self,url,**options):
            self.calls.append((url,options))
            status=302 if len(self.calls)==1 else 200
            e=request_started('GET',url,admitted=True)
            if e is not None:e.update(status_code=status,response_received_at=industry_now.isoformat())
            return status,url,{'location':self.location},"""<div class="more-news"><div class="date">Oct 29, 21:00</div>
            <a class="title" href="/news/a20261029VL201/chip-manufacturing.html">Chip announcement</a></div>"""
    @asynccontextmanager
    async def permit():yield
    ctx=SimpleNamespace(run_id='redirect',requested_at=industry_now,window_start=None,request_permit=permit)
    url='https://www.digitimes.com/topic/semiconductors/'
    events=[];token=REQUESTS.set(events)
    try:
        browser=Browser('/topic/semiconductors')
        result=await IndustryListingAdapter(browser,'digitimes')._get(ctx,url)
        assert result.messages and result.acquisition_metadata['canonical_redirects']==1
        assert result.acquisition_metadata['validated_request_ids']==[events[1]['physical_request_id']]
        assert len(browser.calls)==2 and browser.calls[0][1]['operation_id']!=browser.calls[1][1]['operation_id']
        for target in ['https://foreign.test/topic/semiconductors','/login','http://www.digitimes.com/topic/semiconductors']:
            browser=Browser(target)
            with pytest.raises(RuntimeError,match='publisher listing HTTP 302'):
                await IndustryListingAdapter(browser,'digitimes')._get(ctx,url)
            assert len(browser.calls)==1
    finally:REQUESTS.reset(token)


@pytest.mark.asyncio
async def test_cse_fresh_token_refusal_repairs_only_previously_good_context_once():
    from doxagent.message_bus_v2.defense_sources import DefenseAdapter,ENDPOINTS,Capture
    e=ENDPOINTS['calcalist_search'];old,new=object(),object()
    key=(e.host,e.route);owners={key:old};retired=[]
    def retire(k,expected):
        if owners.get(k) is not expected:return False
        owners[k]=new;retired.append(expected);return True
    boot=Capture(200,e.urls[0],'{"cse_token":"token", "cselibVersion":"version"}',{})
    ok=Capture(200,e.urls[0],'{"results":[]}',{});bad=Capture(403,e.urls[0],'Forbidden',{})
    http=SimpleNamespace(clients=SimpleNamespace(clients=owners,retire=retire),
        fetch=AsyncMock(side_effect=[boot,ok,bad,boot,bad,boot,ok,bad]))
    adapter=DefenseAdapter(http)
    assert (await adapter._search(e,'Ondas')).status==200
    assert (await adapter._search(e,'Ondas')).status==200
    assert retired==[old] and adapter.cse_owner is new
    with pytest.raises(RuntimeError,match='calcalist_search_http_403'):
        await adapter._search(e,'Ondas')
    assert retired==[old] and http.fetch.await_count==8  # Cooldown adds no new bootstrap.


@pytest.mark.asyncio
async def test_new_cse_context_refusal_does_not_churn_transports():
    from doxagent.message_bus_v2.defense_sources import DefenseAdapter,ENDPOINTS,Capture
    e=ENDPOINTS['calcalist_search'];owner=object();retire=AsyncMock()
    boot=Capture(200,e.urls[0],'{"cse_token":"token", "cselibVersion":"version"}',{})
    bad=Capture(403,e.urls[0],'Forbidden',{})
    http=SimpleNamespace(clients=SimpleNamespace(clients={(e.host,e.route):owner},retire=retire),
        fetch=AsyncMock(side_effect=[boot,bad]))
    with pytest.raises(RuntimeError,match='calcalist_search_http_403'):
        await DefenseAdapter(http)._search(e,'Ondas')
    assert not retire.called and http.fetch.await_count==2


@pytest.mark.asyncio
async def test_explicit_context_retirement_waits_for_idle_and_is_owner_fenced():
    from doxagent.message_bus_v2.publisher_http import PublisherClients
    entered=asyncio.Event();release=asyncio.Event();made=[]
    class Session:
        broken=False;closed=False
        def __init__(self):self.index=len(made);made.append(self)
        async def request(self,*args,**kwargs):
            if self.index==0:entered.set();await release.wait()
            return 'ok'
        async def close(self):self.closed=True
    client=PublisherClients(factory=Session)
    task=asyncio.create_task(client.request('old','GET','https://example.test/'))
    await entered.wait();old=client.clients['old']
    assert not client.retire('old',old) and not made[0].closed
    await client.request('healthy','GET','https://healthy.test/')
    release.set();await task
    assert client.retire('old',old)
    await client.request('old','GET','https://example.test/')
    assert client.clients['old'] is not old and not client.retire('old',old)
    await asyncio.sleep(0)
    assert made[0].closed and not made[1].closed
    await client.close()

@pytest.mark.asyncio
async def test_cse_negotiation_and_search_share_a_serial_context():
    from doxagent.message_bus_v2.defense_sources import DefenseAdapter,ENDPOINTS,Capture
    e=ENDPOINTS['calcalist_search'];active=0;peak=0;bootstraps=0
    async def fetch(url,*args,**kwargs):
        nonlocal active,peak,bootstraps
        if '/cse.js' in url:
            bootstraps+=1;await asyncio.sleep(.01)
            return Capture(200,url,'{"cse_token":"token", "cselibVersion":"version"}',{})
        active+=1;peak=max(peak,active)
        try:await asyncio.sleep(.01);return Capture(200,url,'{"results":[]}',{})
        finally:active-=1
    adapter=DefenseAdapter(SimpleNamespace(fetch=fetch))
    results=await asyncio.gather(*(adapter._search(e,q) for q in ['ONDS','A','B']))
    assert all(r.status==200 for r in results) and peak==1 and bootstraps==1
