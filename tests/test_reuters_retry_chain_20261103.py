from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from doxagent.site_strategy.datadome_solver import retry_provider_bootstrap
from doxagent.site_strategy.schema import utc_now
from tests import test_datadome_solver as paid
from tests import test_browser_recovery as base


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


@pytest.fixture
def solver_setup(setup):
    yield from paid.solver_setup.__wrapped__(setup)


def bootstrap_scene():
    return {'url':'https://geo.captcha-delivery.com/captcha/?t=fe&cid=c&initialCid=i&hash=h',
        'user_agent':'Real Chrome','website':'https://www.reuters.com/site-search/?query=Micron',
        'scene_state':'SLIDER_LOADING','state':'UNAVAILABLE','loaded_frame_id':'frame','visible':True}


def action(recovery):
    return {'status':'CLICKED','preparation_version':2,'started_at':(utc_now()-timedelta(seconds=15)).isoformat(),
        'instance_id':recovery.original_instance_id,'target_id':recovery.original_target_id,'attempt_no':1}


@pytest.mark.parametrize('change',[
    {'visible':False},{'loaded_frame_id':None},{'scene_state':'DENIED'},
    {'url':'https://geo.captcha-delivery.com/captcha/?t=bv&cid=c&initialCid=i&hash=h'},
    {'url':'https://evil.com/captcha/?t=fe&cid=c&initialCid=i&hash=h'},
    {'url':'https://geo.captcha-delivery.com/interstitial/?t=fe&cid=c&initialCid=i&hash=h'},
    {'url':'https://geo.captcha-delivery.com/captcha/?t=fe&cid=c'},
])
def test_bootstrap_never_admits_hidden_denied_or_unobserved_frame(change):
    recovery=SimpleNamespace(site_id='reuters',original_instance_id='i',original_target_id='t')
    recovery.scene_preparation=action(recovery)
    assert retry_provider_bootstrap(recovery,bootstrap_scene())
    assert not retry_provider_bootstrap(recovery,{**bootstrap_scene(),**change})


@pytest.mark.parametrize('change',[
    {'status':'UNCERTAIN'},{'preparation_version':1},{'target_id':'other'},
    {'started_at':(utc_now()-timedelta(seconds=121)).isoformat()},
])
def test_bootstrap_requires_recent_acknowledged_exact_action(change):
    recovery=SimpleNamespace(site_id='reuters',original_instance_id='i',original_target_id='t')
    recovery.scene_preparation={**action(recovery),**change}
    assert not retry_provider_bootstrap(recovery,bootstrap_scene())


@pytest.mark.asyncio
async def test_known_old_no_input_latch_can_use_fixed_preflight_once(solver_setup):
    solver,recovery,_,_,_,browser=solver_setup
    store=solver.coordinator.store
    recovery=recovery.model_copy(update={'scene_preparation':{'status':'NOT_DISPATCHED',
        'attempt_no':0,'preflight_failures':3,'started_at':(utc_now()-timedelta(minutes=5)).isoformat()}})
    store.save_recovery(recovery,expected_generation=recovery.generation)
    assert await solver.prepare_scene(recovery,browser,{'state':'RETRY','scene_state':'RETRY_READY'})
    current=store.get_recovery(recovery.identity_id)
    assert current.scene_preparation['status']=='CLICKED'
    assert current.scene_preparation['preparation_version']==2
    assert not solver.jobs(recovery.recovery_id)


@pytest.mark.asyncio
async def test_provider_bootstrap_keeps_paid_job_deduplication(solver_setup,monkeypatch):
    import doxagent.site_strategy.datadome_solver as module
    solver,recovery,scope,identity,egress,browser=solver_setup
    browser.inspect=AsyncMock(return_value={**bootstrap_scene(),'website':'https://www.investing.com/'})
    monkeypatch.setattr(module,'retry_provider_bootstrap',lambda *_:True)
    assert await solver.tick(recovery,scope,identity,egress)
    jobs=solver.jobs(recovery.recovery_id)
    assert len(jobs)==1 and jobs[0]['status']=='PENDING'
    assert jobs[0]['admission']=='ACKNOWLEDGED_RETRY_PROVIDER_BOOTSTRAP'
    solver.provider.request.assert_awaited_once()
    solver.provider.request.return_value={'status':'processing'}
    assert await solver.tick(recovery,scope,identity,egress)
    assert len(solver.jobs(recovery.recovery_id))==1
