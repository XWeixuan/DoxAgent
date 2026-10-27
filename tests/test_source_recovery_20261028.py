from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import timedelta
from contextlib import asynccontextmanager
import json

import pytest

from doxagent.resource_safety import SafetyLevel
from doxagent.site_strategy.external_runtime import ExternalChromeRuntime
from doxagent.site_strategy.runtime import _RawBrowserCDP
from doxagent.site_strategy.schema import utc_now
from tests import test_datadome_solver as paid
from tests import test_browser_recovery as base


@pytest.mark.asyncio
async def test_original_viewer_does_not_depend_on_renderer_or_playwright(monkeypatch):
    from doxagent.site_strategy.maintenance_document import present_original_document
    import doxagent.site_strategy.runtime as module
    from doxagent.site_strategy.schema import BrowserRuntimeKind
    calls=[]
    class CDP:
        def __init__(self,url): pass
        async def start(self,**kwargs):assert kwargs=={'manage_popups':False}
        async def detach_transport(self):calls.append('detach')
        async def send(self,method,params=None,**kwargs):
            calls.append(method)
            if method=='Target.getTargetInfo':return {'targetInfo':{'type':'page'}}
            if method=='Target.attachToTarget':return {'sessionId':'child'}
            if method=='Runtime.runIfWaitingForDebugger':raise TimeoutError('renderer')
            return {}
    monkeypatch.setattr(module,'_RawBrowserCDP',CDP)
    client=SimpleNamespace(status=AsyncMock(return_value={'running':True,'instance_id':'i',
        'cdp_websocket_url':'ws://127.0.0.1:9222/browser'}),select_maintenance=AsyncMock())
    identity=SimpleNamespace(identity_id='reuters',runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME)
    session=SimpleNamespace(kind='CHALLENGE',state='OPEN',task_id='task',browser_instance_id='i',
        target_id='t',control_token='private')
    target=SimpleNamespace(owner_kind='CHALLENGE',state='LIVE',owner_ref='task',identity_id='reuters',
        browser_instance_id='i',target_id='t',allocation_id='a')
    result=await present_original_document(client,identity,session,target)
    assert result['target_id']=='t'
    client.select_maintenance.assert_awaited_once_with('reuters')
    assert 'Target.activateTarget' in calls and 'Target.detachFromTarget' in calls
    assert not any('DOM.' in method or 'navigate' in method for method in calls)
    target.owner_ref='new-task'
    with pytest.raises(RuntimeError,match='ownership'):
        await present_original_document(client,identity,session,target)


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


@pytest.fixture
def solver_setup(setup):
    yield from paid.solver_setup.__wrapped__(setup)


@pytest.mark.asyncio
async def test_cold_attach_reconciles_owned_business_before_playwright(monkeypatch):
    import doxagent.site_strategy.external_runtime as module

    order=[]
    class CDP:
        def __init__(self,url):self.url=url
        async def start(self,**kwargs):order.append(('start',kwargs))
        async def detach_transport(self):order.append(('detach',None))
        async def send(self,method,params=None):
            order.append((method,params))
            if method=='Target.getTargets':
                return {'targetInfos':[{'targetId':'business'},{'targetId':'human'}]}
            return {'success':True}
    monkeypatch.setattr(module,'_RawBrowserCDP',CDP)
    async def event(action,payload):
        if action=='list':
            return [{'allocation_id':'a','target_id':'business','owner_kind':'BUSINESS','state':'LIVE'},
                    {'allocation_id':'b','target_id':'human','owner_kind':'CHALLENGE','state':'LIVE'}]
        order.append((action,payload))
        return {'claimed':True}
    async def attach(*args,**kwargs):
        order.append(('attach',None))
        return SimpleNamespace(contexts=[object()])
    runtime=ExternalChromeRuntime(SimpleNamespace(ensure=AsyncMock(return_value=SimpleNamespace(
        cdp_http_url='http://127.0.0.1:9222',cdp_websocket_url='ws://127.0.0.1:9222/browser',
        instance_id='instance'))))
    runtime.start=AsyncMock()
    runtime.driver_state='READY'
    runtime._playwright=SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=attach))
    runtime._safety=SimpleNamespace(read=lambda:SimpleNamespace(level=SafetyLevel.NORMAL))
    runtime.target_owner=SimpleNamespace(event=event)
    await runtime._entry(SimpleNamespace(identity_id='site'),object())
    close=next(i for i,item in enumerate(order) if item[0]=='Target.closeTarget')
    assert close < next(i for i,item in enumerate(order) if item[0]=='attach')
    assert order[close][1]=={'targetId':'business'}
    assert ('start',{'manage_popups':False}) in order
    assert not any(item[0]=='Target.closeTarget' and item[1]['targetId']=='human' for item in order)


@pytest.mark.asyncio
async def test_cleanup_channel_close_does_not_change_autoattach():
    cdp=_RawBrowserCDP('ws://127.0.0.1:9222/browser')
    cdp._socket=SimpleNamespace(close=AsyncMock())
    cdp.send=AsyncMock()
    await cdp.detach_transport()
    cdp.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_preflight_failure_is_known_not_dispatched(solver_setup):
    solver,recovery,_,_,_,browser=solver_setup
    browser.prepare_retry.side_effect=RuntimeError('solver_retry_scene_changed')
    with pytest.raises(RuntimeError):
        await solver.prepare_scene(recovery,browser,{'state':'RETRY','url':paid.URL})
    saved=solver.coordinator.store.get_recovery(recovery.identity_id).scene_preparation
    assert saved['status']=='NOT_DISPATCHED'
    assert saved['attempt_no']==0
    assert saved['preflight_failures']==1
    solver.provider.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_free_action_requires_new_exact_visible_retry(solver_setup):
    solver,recovery,_,_,_,browser=solver_setup
    recovery.scene_preparation={'status':'UNCERTAIN','attempt_no':1,
        'started_at':(utc_now()-timedelta(seconds=61)).isoformat(),
        'instance_id':recovery.original_instance_id,'target_id':recovery.original_target_id}
    solver.coordinator.store.save_recovery(recovery,expected_generation=recovery.generation)
    scene={'state':'RETRY','scene_state':'RETRY_READY','url':paid.URL,'retry_control':{'x':2,'y':3}}
    assert await solver.prepare_scene(recovery,browser,scene)
    saved=solver.coordinator.store.get_recovery(recovery.identity_id)
    assert saved.scene_preparation['attempt_no']==2
    saved.scene_preparation['started_at']=(utc_now()-timedelta(seconds=61)).isoformat()
    solver.coordinator.store.save_recovery(saved,expected_generation=saved.generation)
    assert not await solver.prepare_scene(saved,browser,scene)
    solver.provider.request.assert_not_awaited()


@asynccontextmanager
async def permit():
    yield


@pytest.mark.asyncio
async def test_ctee_realtime_does_not_stick_at_old_exhausted_page():
    from doxagent.message_bus_v2.ctee import CteeSemiconductorAdapter
    browser=SimpleNamespace(get=AsyncMock(return_value=(200,'https://www.ctee.com.tw',{},'[]')))
    context=SimpleNamespace(checkpoint={'page':9},source=SimpleNamespace(default_parameters={'max_pages':8}),
        window_start=None,requested_at=utc_now(),run_id='run',request_permit=permit)
    result=await CteeSemiconductorAdapter(browser).poll_shared(context)
    assert browser.get.await_args.args[0].endswith('/semi/1')
    assert result.next_checkpoint=={'page':1}
    assert result.window_done


@pytest.mark.asyncio
async def test_ctee_closed_sweep_resumes_beyond_one_batch():
    from doxagent.message_bus_v2.ctee import CteeSemiconductorAdapter
    now=utc_now()
    rows=[{'hyperLink':'/news/20261028000001-430501','title':'New semiconductor news',
           'publishDatetime':now.isoformat(),'articleID':1}]*15
    browser=SimpleNamespace(get=AsyncMock(return_value=(200,'https://www.ctee.com.tw',{},json.dumps(rows))))
    context=SimpleNamespace(checkpoint={'page':9},source=SimpleNamespace(default_parameters={'max_pages':2}),
        window_start=now-timedelta(hours=1),requested_at=now,run_id='run',request_permit=permit)
    result=await CteeSemiconductorAdapter(browser).poll_shared(context)
    assert [call.args[0].rsplit('/',1)[1] for call in browser.get.await_args_list]==['9','10']
    assert result.next_checkpoint=={'page':11}
    assert not result.window_done


@pytest.mark.asyncio
async def test_trendforce_news_public_contract_does_not_require_browser():
    from doxagent.message_bus_v2.industry_sources import IndustryListingAdapter
    html='<div class="insight-list-item"><a class="title-link" href="https://www.trendforce.com/news/2026/10/28/news-memory-demand/">Memory demand improves</a></div>'
    adapter=IndustryListingAdapter(SimpleNamespace(get=AsyncMock()),'trendforce_news')
    adapter._public_session=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        status_code=200,url='https://www.trendforce.com/news/',text=html)))
    context=SimpleNamespace(request_permit=permit,requested_at=utc_now(),window_start=None)
    result=await adapter._get(context,'https://www.trendforce.com/news/')
    assert len(result.messages)==1
    assert result.acquisition_metadata['listing_transport']=='public_http'
    adapter.browser.get.assert_not_awaited()
