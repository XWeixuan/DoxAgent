from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.datadome_solver import Solverify, SolverError, TwoCaptcha, provider_failure
from doxagent.site_strategy.perimeterx_browser import PerimeterXChallenge, application_id, cookie_parameters
from doxagent.site_strategy.challenge_detection import assess
from doxagent.site_strategy.solver_forensics import response_summary
from tests.test_datadome_solver import setup, solver_setup
from tests.test_solverify import UA, TASK

PX_HTML = '<html><title>Are you a robot?</title><script>window._pxAppId="PXExample123";</script><div id="px-captcha">Press &amp; Hold</div></html>'
ARTICLE = 'https://www.bloomberg.com/news/articles/2026-11-18/example'


@pytest.mark.asyncio
async def test_invalid_datadome_network_fixture_is_rejected_before_http(monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(TwoCaptcha, 'request', request)
    with pytest.raises(SolverError, match='CHALLENGE_INPUT_INVALID'):
        await Solverify().request('private', 'createTask', task={'captchaUrl':'https://geo.captcha-delivery.com/captcha/'})
    request.assert_not_awaited()


def test_bloomberg_px_detection_uses_observed_app_id():
    assert application_id(PX_HTML) == 'PXExample123'
    assert assess(PX_HTML).provider == 'HUMAN_PERIMETERX'
    assert assess(PX_HTML).interaction == 'PRESS_AND_HOLD'
    normal = '<html><article>Normal article</article><script src="https://captcha.px-cdn.net/PXExample123/captcha.js"></script></html>'
    assert assess(normal).kind == 'NONE'


@pytest.mark.asyncio
async def test_px_wire_contract_has_no_datadome_parameters(monkeypatch):
    request = AsyncMock(return_value={'taskId':TASK})
    monkeypatch.setattr(TwoCaptcha, 'request', request)
    await Solverify().request('private', 'createTask', task={'challengeFamily':'PERIMETERX',
        'websiteURL':ARTICLE, 'websiteKey':'PXExample123', 'userAgent':UA, 'proxyPort':18791})
    task = request.await_args.kwargs['task']
    assert task['type'] == 'perimeterx' and task['websiteKey'] == 'PXExample123'
    assert task['useragent'] == UA and task['proxyPort'] == '18791'
    assert 'captchaUrl' not in task and 'uuid' not in task and 'vid' not in task


def test_px_cookie_write_is_publisher_scoped_and_preserves_auth():
    result = cookie_parameters({'useragent':UA, 'cookies':{'_px3':'a:b/c=|+', '_pxvid':'v', 'ArcP':'ignore'}}, ARTICLE, UA)
    assert [r['name'] for r in result] == ['_px3','_pxvid']
    assert all(r['domain'] == '.bloomberg.com' and r['path'] == '/' for r in result)
    for value in ['bad;inject=x', 'bad\r\nX: value']:
        with pytest.raises(ValueError):
            cookie_parameters({'useragent':UA, 'cookies':{'_px3':value}}, ARTICLE, UA)
    with pytest.raises(ValueError, match='USER_AGENT_MISMATCH'):
        cookie_parameters({'useragent':'different', 'cookies':{'_px3':'v'}}, ARTICLE, UA)


@pytest.mark.asyncio
async def test_px_apply_returns_original_target_to_article():
    params = cookie_parameters({'useragent':UA,'cookies':{'_px3':'p','_pxvid':'v'}}, ARTICLE, UA)
    calls = []
    async def call(method, args=None):
        calls.append((method,args))
        if method == 'DOM.getDocument':return {'root':{'documentURL':'https://www.bloomberg.com/tosv2.html'}}
        if method == 'Network.getCookies':return {'cookies':params}
        return {'success':True}
    @asynccontextmanager
    async def connection():yield call
    browser = object.__new__(PerimeterXChallenge);browser.connection=connection
    await browser.apply(params, ARTICLE)
    assert len([m for m,p in calls if m == 'Network.setCookie']) == 2
    assert ('Page.navigate', {'url':ARTICLE}) in calls
    assert not any(m == 'Page.reload' for m,p in calls)


@pytest.mark.asyncio
async def test_px_recovery_submits_once_and_waits_for_business_proof(solver_setup):
    solver,r,scope,identity,egress,browser=solver_setup
    r=r.model_copy(update={'site_id':'bloomberg_latest_lab','assessment':{'provider':'HUMAN_PERIMETERX'}})
    cfg={**solver.config(),'sites':['bloomberg_latest_lab'],'solverify_api_key':'new-private',
         'provider_by_site':{'bloomberg_latest_lab':'solverify'}}
    solver.config=lambda:cfg
    solver.coordinator.service.resolve=lambda url:SimpleNamespace(site_id=r.site_id)
    solver.perimeterx_browser_factory=lambda *a:browser
    browser.inspect.return_value.update(website=ARTICLE,url='https://www.bloomberg.com/tosv2.html',
        user_agent=UA,website_key='PXExample123',family='PERIMETERX')
    solver.solverify.request=AsyncMock(return_value={'taskId':TASK})
    assert await solver.tick(r,scope,identity,egress)
    sent=solver.solverify.request.await_args.kwargs['task']
    assert sent['challengeFamily']=='PERIMETERX' and sent['websiteKey']=='PXExample123'
    solver.solverify.request.return_value={'status':'ready','cost':0.0006,
        'solution':{'useragent':UA,'cookies':{'_px3':'opaque','_pxvid':'v'}}}
    r=r.model_copy(update={'assessment':{'provider':'UNKNOWN'}})
    assert await solver.tick(r,scope,identity,egress)
    job=solver.jobs(r.recovery_id)[0]
    assert job['status']=='APPLIED' and job['verification_state']=='PENDING'
    assert job['challenge_family']=='PERIMETERX' and job['provider']=='solverify'
    assert browser.apply.await_args.args[0][0]['domain']=='.bloomberg.com'
    assert not await solver.tick(r,scope,identity,egress)
    assert solver.solverify.request.await_count==2
    browser.prepare_retry.assert_not_awaited()
    from doxagent.site_strategy.datadome_solver import record_solver_diagnostic
    record_solver_diagnostic(solver.coordinator.service.repository, identity.identity_id, 'BODY',
        {'http_status':403, 'challenge':'BLOCKED'}, capture=SimpleNamespace(
            final_url=ARTICLE, status_code=403, headers={}, body=PX_HTML, provenance=None))
    job=solver.jobs(r.recovery_id)[0]
    assert job['failure_stage']=='SITE_ACCEPTANCE' and job['status']=='APPLIED'
    assert PX_HTML not in str(job)


@pytest.mark.parametrize('code,description,expected',[
    ('ERROR_TASK_FAILED','Task failed to solve','PROVIDER_UNDETERMINED'),
    ('ERROR_TASK_FAILED','Proxy connection refused','PROXY_NETWORK'),
    ('ERROR_TASK_FAILED','Proxy authentication 407 rejected','PROXY_NETWORK'),
    ('ERROR_CAPTCHA_UNSOLVABLE','No solution','PROVIDER_DECODE')])
def test_failure_layers_require_evidence(code,description,expected):
    err=provider_failure(code,description,cost=0)
    assert err.failure_stage==expected and err.cost==0
    assert description not in str(err)


def test_site_rejection_is_distinct_from_proxy_407():
    assert response_summary(ARTICLE,403,{},PX_HTML)['layer']=='PERIMETERX'
    assert response_summary(ARTICLE,407,{'Proxy-Authenticate':'Basic'})['layer']=='PROXY_NETWORK'


def test_reuters_default_does_not_retarget_legacy_pending_job(solver_setup):
    solver,_,_,identity,_,_=solver_setup
    cfg={**solver.config(),'solverify_api_key':'new','provider_by_site':{'reuters':'solverify'}}
    assert solver.provider_for(cfg,identity_id=identity.identity_id,site_id='reuters')[2]=='solverify'
    assert solver.provider_for(cfg,job={'task_id':123},site_id='reuters')[2]=='2captcha'


@pytest.mark.asyncio
async def test_result_application_failure_is_not_provider_decode_failure(solver_setup):
    solver,r,scope,identity,egress,browser=solver_setup
    await solver.tick(r,scope,identity,egress)
    solver.provider.request.return_value={'status':'ready','cost':0,'solution':{'cookie':'datadome=opaque'}}
    browser.apply.side_effect=RuntimeError('solver_cookie_not_visible_to_publisher')
    assert not await solver.tick(r,scope,identity,egress)
    job=solver.jobs(r.recovery_id)[0]
    assert job['failure_stage']=='RESULT_APPLICATION'
    assert job['cookie_scope_verified'] is False and job['verification_state']=='INDETERMINATE'


@pytest.mark.asyncio
@pytest.mark.parametrize('visible',[True,False])
async def test_native_px_inspection_requires_visible_owned_widget(visible):
    async def call(method, args=None):
        if method=='DOM.getDocument':return {'root':{'nodeId':1,'documentURL':'https://www.bloomberg.com/tosv2.html'}}
        if method=='DOM.getOuterHTML':return {'outerHTML':PX_HTML}
        if method=='Runtime.evaluate':return {'result':{'value':{'visible':visible,'appId':'PXExample123'}}}
    @asynccontextmanager
    async def connection():yield call
    browser=object.__new__(PerimeterXChallenge)
    browser.connection=connection;browser.check=lambda:None;browser.user_agent=UA
    browser.recovery=SimpleNamespace(original_url=ARTICLE)
    scene=await browser.inspect()
    assert scene['state']==('SOLVABLE' if visible else 'UNAVAILABLE')
    assert scene['website']==ARTICLE and scene['website_key']=='PXExample123'


@pytest.mark.asyncio
async def test_bloomberg_snapshot_preserves_visible_challenge_instead_of_article_behind_it():
    from doxagent.site_strategy.bloomberg_document import capture_document
    page=SimpleNamespace(url=ARTICLE,evaluate=AsyncMock(return_value={'challenge':True}),
                         content=AsyncMock(return_value=PX_HTML))
    html,url=await capture_document(page)
    assert html==PX_HTML and url==ARTICLE
    assert page.evaluate.await_count==1
