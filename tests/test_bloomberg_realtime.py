import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from urllib.parse import urlsplit

from doxagent.message_bus_v2.bloomberg_realtime import POLL_URL, TOPICS, parse_snapshot, poll_realtime, topic_checksum
from doxagent.message_bus_v2.poll_trace import REQUESTS
from tests.test_bloomberg_latest import context, source

FIXTURES=Path(__file__).parent/'fixtures/bloomberg_latest'


def snapshot():return json.loads((FIXTURES/'realtime.json').read_text(encoding='utf-8'))
def metadata():return json.loads((FIXTURES/'realtime_stories.json').read_text(encoding='utf-8'))


def adapter(*, fail_id=None, expired=False, bad_poll=None, age=0):
    seen=[];calls={'poll':0}
    async def request(ctx,url,recipe,headers):
        seen.append((url,dict(headers)))
        if url.endswith('/latest'):data={};status=200
        elif url.endswith('/sentry'):data={'token':'private-token'};status=200
        elif '/oscillator/poll/' in url:
            calls['poll']+=1
            data=snapshot() if bad_poll is None else bad_poll
            status=401 if expired and calls['poll']==1 else 200
        else:
            identifiers=urlsplit(url).path.rsplit('/',1)[-1].split(',')
            data=[{'status':'fulfilled','value':row} for row in metadata() if row['id'] in identifiers and row['id']!=fail_id];status=200
        events=REQUESTS.get()
        if events is not None:events.append(dict(physical_request_id=str(len(events)),status_code=status,
            role='listing',proof_basis='TRANSPORT_SEND',response_received_at=ctx.requested_at.isoformat()))
        return httpx.Response(status,json=data,headers={'age':str(age),'cache-control':'max-age=5'},request=httpx.Request('GET',url)),recipe['routes'][0]
    a=SimpleNamespace(listing_request=AsyncMock(side_effect=request),seen=seen)
    return a


def test_real_provider_snapshot_is_small_and_keeps_original_id_times():
    rows=parse_snapshot((FIXTURES/'realtime.json').read_text(encoding='utf-8'))
    assert len(rows)==5 and rows[0]['id']=='TOCNC6T9NJLT00'
    assert rows[0]['publishedAt']=='2026-11-11T19:29:06.220Z'
    assert str(topic_checksum(TOPICS)) in POLL_URL


async def test_fixed_http_session_real_id_lookup_dedup_and_no_secret_checkpoint():
    a=adapter();c=context().model_copy(update={'acquisition_unit':'TEST:WIRE:realtime'})
    first=await poll_realtime(a,c)
    assert len(first.messages)==5 and not first.failures
    assert first.messages[0].title=='Nikki Haley Rules Out 2028 Run After GOP Midterm Repudiation'
    assert all(m.body is None and m.summary is None for m in first.messages)
    assert all(m.metadata['discovery_method']=='bloomberg_realtime_http' for m in first.messages)
    assert all(m.publication_time_basis=='EXACT' for m in first.messages)
    assert a.seen[0][0].endswith('/latest') and a.seen[1][0].endswith('/sentry')
    assert len([u for u,h in a.seen if '/api/stories/' in u])==1
    second=await poll_realtime(a,c.model_copy(update={'checkpoint':first.next_checkpoint}))
    assert not second.messages and not second.failures
    assert len(a.seen)==5  # Four first-round requests, only one subsequent snapshot.
    assert 'private-token' not in json.dumps(first.model_dump(mode='json'))


async def test_expected_token_expiration_refreshes_once_without_waiting_a_new_poll():
    a=adapter(expired=True)
    result=await poll_realtime(a,context())
    assert not result.failures and len(result.messages)==5
    assert len([u for u,h in a.seen if u.endswith('/sentry')])==2
    assert len([u for u,h in a.seen if '/oscillator/poll/' in u])==2


async def test_partial_metadata_failure_keeps_pending_and_commits_healthy_siblings():
    missing=snapshot()[0]['topicsData']['LATEST_STORIES']['data'][0]['id']
    callback=AsyncMock();a=adapter(fail_id=missing)
    result=await poll_realtime(a,context().model_copy(update={'on_page':callback}))
    assert result.failures[0].error_code=='BLOOMBERG_REALTIME_FAILED' and len(result.messages)==4
    assert missing in result.next_checkpoint['pending'] and missing not in result.next_checkpoint['seen']
    callback.assert_awaited_once()
    assert callback.call_args.kwargs['checkpoint']==result.next_checkpoint
    next_result=await poll_realtime(adapter(),context(checkpoint=result.next_checkpoint))
    assert not next_result.failures and len(next_result.messages)==1 and not next_result.next_checkpoint['pending']


@pytest.mark.parametrize('bad',[{},[],[{'providerName':'challenge'}]])
async def test_invalid_snapshot_cannot_advance_or_borrow_session_html_proof(bad):
    token=REQUESTS.set([])
    try:
        result=await poll_realtime(adapter(bad_poll=bad),context(checkpoint={'seen':{'retained':'stamp'}}))
        assert result.failures and result.next_checkpoint=={'seen':{'retained':'stamp'}}
        assert not result.acquisition_metadata['validated_request_ids']
        assert not any(e.get('list_validated') for e in REQUESTS.get())
    finally:REQUESTS.reset(token)


async def test_stale_snapshot_fails_and_never_fetches_rss_or_a_browser():
    a=adapter(age=90)
    result=await poll_realtime(a,context())
    assert result.failures and not result.messages
    assert all('rss' not in u and 'sitemap' not in u for u,h in a.seen)


async def test_only_valid_snapshot_and_matching_metadata_are_transport_proof():
    token=REQUESTS.set([])
    try:
        result=await poll_realtime(adapter(),context())
        assert len(result.acquisition_metadata['validated_request_ids'])==2
        assert all(not e.get('list_validated') for e in REQUESTS.get()[:2])
        assert all(e['list_validated'] for e in REQUESTS.get()[2:])
    finally:REQUESTS.reset(token)


def test_realtime_snapshot_and_full_list_have_independent_cadence():
    from doxagent.message_bus_v2.polling_cadence import unit_interval
    assert unit_interval(source(),'TEST:WIRE:realtime',30)==10
    assert unit_interval(source(),'TEST:WIRE:latest',30)==30
