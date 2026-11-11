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
