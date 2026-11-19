import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from doxagent.api_v2.background_queries import DeferredQueries


@pytest.mark.parametrize('catalog',[True,False])
async def test_live_directory_has_no_view_pin_but_scoped_reads_keep_it(catalog):
    service=DeferredQueries()
    result=(200,{'content-type':'application/json'},b'{"data":"complete"}')
    runner=SimpleNamespace(start=AsyncMock(),run=AsyncMock(side_effect=[result] if catalog else [
        'pin.'+str(int(time.time()+200)),result]))
    service.runner=runner
    job=dict(kind='http',method='GET',headers={},principal=SimpleNamespace(user_id='audit',tier='DEVELOPER'),
        url='/api/doxagent/v2/message-bus/source-catalog?refresh=1' if catalog else '/api/doxagent/v2/metrics?q=x&view_id=v')
    response=await service.submit(job)
    assert response.status_code==202
    entry=next(iter(service.jobs.values()));await entry['task']
    assert entry['state']=='SUCCEEDED'
    kinds=[c.args[0]['kind'] for c in runner.run.await_args_list]
    assert kinds==(['http'] if catalog else ['query_pin','http'])
    assert service.poll(job['principal'],entry['id']).status_code==200
    assert service.existing(job).status_code==200
    assert service.existing({**job,'url':job['url']+'2'}) is None


def test_directory_routes_once_to_existing_deferred_reader_without_foreground_work(tmp_path):
    from doxagent.api_v2.app import create_app,PREFIX
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_control.repository import ControlRepository
    from tests.test_source_catalog import seed,CALENDAR
    from tests.v2_backend.test_api import OfflineAuth
    store=seed(tmp_path);control=ControlRepository(RuntimeJournal(tmp_path/'runtime.db'));control.migrate()
    app=create_app(store=store,control=control,auth=OfflineAuth(),calendar=CALENDAR)
    with TestClient(app) as client:
        app.state.query_runner=SimpleNamespace(run=AsyncMock(side_effect=AssertionError('foreground catalog read')),
                                               close=AsyncMock())
        app.state.deferred_queries.existing=lambda _:None
        submit=AsyncMock(return_value=JSONResponse({'query_id':'a'*32,'state':'QUEUED',
            'result_path':'/queries/'+'a'*32},status_code=202))
        app.state.deferred_queries.submit=submit
        response=client.get(PREFIX+'/message-bus/source-catalog?refresh=1',headers={'Authorization':'Bearer offline'})
        assert response.status_code==202
        assert submit.await_count==1
