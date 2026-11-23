import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from doxagent.agent_mcp.client import ApiClient, ReadError
from doxagent.agent_mcp.service import AgentReads
from doxagent.api_v2.app import PREFIX
from tests.test_agent_mcp_readonly import TOKEN, backend  # noqa: F401

QUERY = 'a' * 32


@pytest.mark.asyncio
@pytest.mark.parametrize('args,budget', [({'section': 'sources'}, 45),
                                       ({'section': 'sources', 'cursor': 'page_x'}, 15),
                                       ({'section': 'summary'}, 15)])
async def test_catalog_deadline_covers_deferred_reader(monkeypatch, args, budget):
    deadlines = []
    timeout = asyncio.timeout
    def tracked(seconds):
        deadlines.append(seconds)
        return timeout(seconds)
    monkeypatch.setattr(asyncio, 'timeout', tracked)
    reads = AgentReads(None)
    reads._dispatch = AsyncMock(return_value={})
    assert await reads.call('doxagent_get_overview', args) == {}
    assert deadlines == [budget]


@pytest.mark.asyncio
async def test_client_polls_same_origin_and_preserves_final_live_metadata(monkeypatch):
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(202, json={'query_id': QUERY, 'result_path': '/queries/'+QUERY})
        return httpx.Response(200, json={'data': {'sources': []}},
                              headers={'x-doxagent-live-read-at': 'now'})

    api = ApiClient('http://local', TOKEN, transport=httpx.MockTransport(handler))
    try:
        assert await api.get('message-bus/source-catalog') == {
            'data': {'sources': []}, 'meta': {'read_at': 'now'}}
        assert [r.url.path for r in requests] == [PREFIX+'/message-bus/source-catalog',
                                                PREFIX+'/queries/'+QUERY, PREFIX+'/queries/'+QUERY]
        assert all(r.headers['authorization'] == 'Bearer '+TOKEN for r in requests)
    finally:
        await api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['https://evil.test/queries/'+QUERY, '//evil.test/'+QUERY,
                                  '/queries/../auth/me', '/queries/'+'b'*32])
async def test_client_rejects_untrusted_result_paths(path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(202, json={'query_id': QUERY, 'result_path': path})
    api = ApiClient('http://local', TOKEN, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ReadError, match='Invalid deferred'):
            await api.get('message-bus/source-catalog')
        assert len(calls) == 1
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_client_propagates_deferred_failure(monkeypatch):
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
    responses = iter([
        httpx.Response(202, json={'query_id': QUERY, 'result_path': '/queries/'+QUERY}),
        httpx.Response(504, json={'error': {'code': 'QUERY_TIMEOUT', 'message': 'timeout',
                                         'retryable': True, 'request_id': 'q'}})])
    api = ApiClient('http://local', TOKEN, transport=httpx.MockTransport(lambda r: next(responses)))
    try:
        with pytest.raises(ReadError) as error:
            await api.get('message-bus/source-catalog')
        assert error.value.code == 'QUERY_TIMEOUT' and error.value.retryable
        assert error.value.request_id == 'q'
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_pending_read_has_total_deadline(monkeypatch):
    from doxagent.agent_mcp import client
    clock = iter([0, 46])
    monkeypatch.setattr(client, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    api = ApiClient('http://local', TOKEN, transport=httpx.MockTransport(lambda r:
        httpx.Response(202, json={'query_id': QUERY, 'result_path': '/queries/'+QUERY})))
    try:
        with pytest.raises(ReadError) as error:
            await api.get('message-bus/source-catalog')
        assert error.value.code == 'QUERY_TIMEOUT' and error.value.retryable
    finally:
        await api.close()


def test_read_token_polls_only_owned_deferred_jobs(backend):  # noqa: F811
    app, _, _, _ = backend
    service = app.state.deferred_queries
    service.jobs['own'] = dict(id=QUERY, owner='agent-read', tier='AGENT_READ',
                              expires=time.time()+100, result=(200, {}, b'{"data":"own"}'))
    service.jobs['other'] = dict(id='b'*32, owner='someone-else', tier='DEVELOPER',
                                expires=time.time()+100, result=(200, {}, b'{"secret":true}'))
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer '+TOKEN}
        assert client.get(PREFIX+'/queries/'+QUERY, headers=headers).json() == {'data': 'own'}
        assert client.get(PREFIX+'/queries/'+'b'*32, headers=headers).status_code == 410
        assert client.get(PREFIX+'/queries/invalid', headers=headers).status_code == 403
        assert client.post(PREFIX+'/queries/'+QUERY, headers=headers).status_code == 403
        service.jobs.clear()
