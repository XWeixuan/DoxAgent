import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient

from doxagent.api_v2.openrouter_balance import OpenRouterBalanceMonitor
from doxagent.api_v2.app import PREFIX,create_app
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.repository import ReadStore
from tests.v2_backend.test_api import OfflineAuth


def test_shared_network_read_cache_zero_and_stale_value(tmp_path):
    key=tmp_path/'key';key.write_text('management-fixture')
    clock=[0];calls=[]
    async def handler(request):
        calls.append(request)
        assert request.url==httpx.URL('https://openrouter.ai/api/v1/credits')
        assert request.headers['authorization']=='Bearer management-fixture'
        await asyncio.sleep(0)
        if len(calls)>1:return httpx.Response(503,json={'error':'provider failure'})
        return httpx.Response(200,json={'data':{'total_credits':10,'total_usage':10}})
    async def check():
        monitor=OpenRouterBalanceMonitor(key_file=key,transport=httpx.MockTransport(handler),clock=lambda:clock[0])
        values=await asyncio.gather(*(monitor.status() for _ in range(8)))
        assert len(calls)==1
        assert all(v['status']=='AVAILABLE' and v['balance_usd']=='0' for v in values)
        clock[0]=299;await monitor.status();assert len(calls)==1
        clock[0]=301;stale=await monitor.status()
        assert stale['status']=='STALE' and stale['balance_usd']=='0'
        assert stale['checked_at']==values[0]['checked_at']
        assert 'management-fixture' not in str(stale)
        await monitor.status();assert len(calls)==2
    asyncio.run(check())


def test_missing_credentials_and_provider_failure_are_not_zero(tmp_path):
    async def check():
        monitor=OpenRouterBalanceMonitor(key_file=tmp_path/'missing')
        assert (await monitor.status())['status']=='NOT_CONFIGURED'
        key=tmp_path/'key';key.write_text('fixture')
        monitor=OpenRouterBalanceMonitor(key_file=key,transport=httpx.MockTransport(lambda r:httpx.Response(403)))
        value=await monitor.status()
        assert value['status']=='UNAVAILABLE' and value['balance_usd'] is None
    asyncio.run(check())


def test_balance_http_is_authenticated_and_does_not_use_sql_query_workers(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    control=ControlRepository(RuntimeJournal(tmp_path/'runtime.db'));control.migrate()
    app=create_app(store=store,control=control,auth=OfflineAuth())
    value={'status':'AVAILABLE','balance_usd':'13.946629063','checked_at':'2026-11-01T00:00:00Z'}
    app.state.openrouter_balance=SimpleNamespace(status=AsyncMock(return_value=value))
    runner=SimpleNamespace(run=AsyncMock(side_effect=AssertionError('network reads must bypass SQL workers')),close=AsyncMock())
    with TestClient(app) as client:
        app.state.query_runner=runner
        path=PREFIX+'/overview/openrouter-balance'
        assert client.get(path).status_code==401
        response=client.get(path,headers={'Authorization':'Bearer offline'})
        assert response.status_code==200,response.text
        assert response.json()['data']['data']==value
        assert response.json()['data']['coverage']['state']=='COMPLETE'
        assert response.json()['meta']['view_id'] is None
        runner.run.assert_not_awaited()
