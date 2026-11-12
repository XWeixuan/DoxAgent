from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.transport import Observation
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.site_strategy.governance_models import MaintenanceSession
from doxagent.site_strategy.maintenance import MaintenanceController
from doxagent.site_strategy.schema import AccessMode, AccessRequest, SitePurpose
from tests.test_content_enrichment_pipeline import BODY, TITLE, article
from tests.test_site_maintenance_recovery import operation, setup


@pytest.mark.parametrize('style,complete',[('',True),('display:none',False),('height:5px;overflow:hidden',False)])
async def test_visible_paid_crawler_dom_is_full_but_hidden_or_clipped_is_not(style,complete):
    import json
    from pathlib import Path
    from playwright.async_api import async_playwright
    from doxagent.site_strategy.barrons_document import READ,snapshot
    from doxagent.content_enrichment.quality import inspect_html,choose_candidate
    url='https://www.barrons.com/articles/example'
    html=('<script type="application/ld+json">'+json.dumps({'@type':'NewsArticle',
        'isAccessibleForFree':False})+'</script><link rel="canonical" href="'+url+'">'
        '<article id="example"><h1>'+TITLE+'</h1><div class="crawler"><div class="paywall" style="'+style+'">'
        +''.join('<p data-type="paragraph">'+p+'</p>' for p in BODY.split('\n\n'))
        +'</div></div></article>')
    async with async_playwright() as pw:
        if not Path(pw.chromium.executable_path).exists():pytest.skip('local Chromium unavailable')
        browser=await pw.chromium.launch()
        try:
            page=await browser.new_page()
            await page.route(url,lambda route:route.fulfill(status=200,content_type='text/html',body=html))
            await page.goto(url)
            value=await page.evaluate(READ)
            assert value['complete'] is complete
            result=choose_candidate(inspect_html(snapshot(value),url,TITLE),TITLE)
            assert (result[1]=='FULL') is complete
            if complete:assert result[0].text==BODY
            else:assert result[2]=='subscription_required'
        finally:await browser.close()


async def test_http_refresh_preserves_barrons_feed_contract_without_duplicate_fetch(monkeypatch):
    import json
    from urllib.parse import parse_qs,urlsplit
    from doxagent.message_bus_v2.industry_sources import IndustryListingAdapter
    from doxagent.message_bus_v2.schema import utc_now
    from tests.test_barrons_native_news_capture import feeds
    @asynccontextmanager
    async def permit():
        yield
    adapter=IndustryListingAdapter(None,'barrons')
    get=AsyncMock(return_value=SimpleNamespace(status_code=200,text=feeds()[0]['text'],headers={}))
    monkeypatch.setattr(adapter,'_public_get',get)
    context=SimpleNamespace(acquisition_unit='HEAD',ticker='MU',request_permit=permit,
                            requested_at=utc_now(),window_start=None)
    result=await adapter._barrons_http(context)
    query=parse_qs(urlsplit(get.call_args.args[0]).query)
    assert query['channel']==['Barrons'] and query['chartingSymbol']==['stock///mu']
    assert query['pageNumber']==['0'] and query['count']==['40'] and query['_']
    assert result.messages and get.await_count==1


@pytest.mark.parametrize('explicit',[None,6])
async def test_single_authenticated_route_can_finish_without_eight_second_restart(monkeypatch,explicit):
    from doxagent.content_enrichment import pipeline
    caps=[]
    @asynccontextmanager
    async def budget(cap):
        caps.append(cap)
        yield
    monkeypatch.setattr(pipeline,'_stage_budget',budget)
    resolved=SimpleNamespace(body=SimpleNamespace(ref='builtin:barrons@1',
        parameters={} if explicit is None else {'browser_timeout_seconds':explicit},access_order=['browser']),
        auth=SimpleNamespace(body_requirement='required',requirement='required'),
        site_id='barrons',strategy_revision=11,runtime_key='barrons')
    browser=SimpleNamespace(site_managed=True,read=AsyncMock(return_value=(
        Observation('https://www.barrons.com/articles/example',article(),200),{})))
    result=await ArticlePipeline(SimpleNamespace(),browser=browser,
        site_client=SimpleNamespace(resolve=AsyncMock(return_value=resolved))).extract(
            MediaEnrichmentRecord('id','id','barrons_ticker_news','MU',TITLE,'',
                                  'https://www.barrons.com/articles/example'))
    assert result.succeeded and result.content==BODY
    assert caps==[15 if explicit is None else explicit]
    browser.read.assert_awaited_once()


@pytest.mark.parametrize('presence',[False,True,'unknown'])
async def test_close_login_releases_only_confirmed_lost_target(setup,monkeypatch,presence):
    repo,service,store,scope,_,_=setup
    identity=repo.get_identity(scope.primary_identity_id)
    session=store.save_session(MaintenanceSession(kind='LOGIN',scope_id=scope.scope_id,
        identity_id=identity.identity_id,site_id=scope.owner_site_id,control_token='login-reservation',state='RELEASED'))
    profile=repo.get_profile(identity.profile_id)
    from doxagent.site_strategy.schema import ProfileOperationalState
    repo.save_profile(profile.model_copy(update={'maintenance_session_id':session.control_token,
        'operational_state':ProfileOperationalState.MAINTENANCE}))
    controller=MaintenanceController(service)
    monkeypatch.setattr(controller,'_login_session_reusable',AsyncMock(
        side_effect=TimeoutError() if presence=='unknown' else None,return_value=presence))
    close=AsyncMock();monkeypatch.setattr(service.runtime,'close_login',close)
    if presence=='unknown':
        with pytest.raises(TimeoutError):
            await controller._dispatch(operation('CLOSE_LOGIN',session_id=session.session_id))
        assert store.get_session(session.session_id).state=='RELEASED'
        assert repo.get_profile(identity.profile_id).maintenance_session_id==session.control_token
        close.assert_not_awaited()
    else:
        result=await controller._dispatch(operation('CLOSE_LOGIN',session_id=session.session_id))
        assert result['session_closed']
        assert store.get_session(session.session_id).state=='CLOSED'
        assert repo.get_profile(identity.profile_id).operational_state is ProfileOperationalState.AVAILABLE
        assert repo.get_profile(identity.profile_id).auth_state==profile.auth_state
        assert close.await_count==int(presence)


async def test_scope_auth_filter_keeps_reason_instead_of_reporting_rate_limit(setup,monkeypatch):
    repo,service,store,scope,_,_=setup
    request=AccessRequest(operation_id='auth-filter',url='https://www.reuters.com/world/example/',
        purpose=SitePurpose.BODY,mode=AccessMode.BROWSER,origin_scope_id=scope.scope_id)
    resolved=service._resolve_request(request)
    combos=service._purpose_combinations(resolved,request.purpose)
    state=repo.get_runtime(resolved.runtime_key)
    monkeypatch.setattr(service.health,'candidates',AsyncMock(return_value=(state,combos,None)))
    monkeypatch.setattr(service.failover,'candidate_status',lambda *args,**kw:
        {'selectable':False,'eligible':False,'skip_reasons':['auth_required']})
    result=await service._execute_uncached_selected(request,resolved=resolved)
    assert result.disposition.value=='AUTH_REQUIRED'
    assert result.reason_code=='no_authenticated_profile_available'
    assert len(result.combination_skips)==len(combos)
