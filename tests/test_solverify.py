import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.datadome_solver import Solverify, SolverError, TwoCaptcha
from tests.test_datadome_solver import setup, solver_setup

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36'
TASK = '89757d0e-9260-410e-9cfa-50678f7d31f1'


@pytest.mark.asyncio
async def test_wire_translation_uses_actual_browser_and_normalizes_vendor_states(monkeypatch):
    request = AsyncMock(return_value={'taskId': TASK})
    monkeypatch.setattr(TwoCaptcha, 'request', request)
    client = Solverify()
    await client.request('private', 'createTask', task={'type': 'DataDomeSliderTask',
        'userAgent': UA, 'proxyPort': 18791, 'websiteURL': 'https://www.reuters.com/', 'captchaUrl': 'https://geo.captcha-delivery.com/captcha/?t=fe&cid=c&initialCid=i&hash=h'})
    wire = request.await_args.kwargs['task']
    assert wire['type'] == 'datadome' and wire['useragent'] == UA and wire['proxyPort'] == '18791'
    assert 'userAgent' not in wire
    for actual, normalized in [('pending', 'processing'), ('processing', 'processing'), ('completed', 'ready')]:
        request.return_value = {'status': actual, 'cost': 0, 'solution': {'useragent': UA}}
        data = await client.request('private', 'getTaskResult', taskId=TASK)
        assert data['status'] == normalized and data['cost'] == 0
    request.return_value = {'status': 'failed', 'errorCode': 'ERROR_PROXY_CONNECTION_FAILED'}
    with pytest.raises(SolverError, match='ERROR_PROXY_CONNECTION_FAILED'):
        await client.request('private', 'getTaskResult', taskId=TASK)


def test_cookie_requires_exact_ua_and_cannot_inject_or_shadow_domain():
    solution = {'useragent': UA, 'cookies': {'datadome': 'opaque~value_123'}}
    assert 'Domain=.reuters.com; Path=/' in Solverify.cookie(solution, 'https://www.reuters.com/', UA)
    for bad in ['value;other=1', 'value\r\nX:1', '', 'x' * 16385]:
        with pytest.raises(SolverError, match='INVALID_PROVIDER_COOKIE'):
            Solverify.cookie({**solution, 'cookies': {'datadome': bad}}, 'https://www.reuters.com/', UA)
    with pytest.raises(SolverError, match='USER_AGENT_MISMATCH'):
        Solverify.cookie(solution, 'https://www.reuters.com/', 'Mac Chrome')


def enable(solver, recovery, identity, browser):
    config = {**solver.config(), 'sites': ['reuters'], 'solverify_api_key': 'private-new',
              'provider_by_identity': {identity.identity_id: 'solverify'}}
    solver.config = lambda: config
    solver.coordinator.service.resolve = lambda url: SimpleNamespace(site_id='reuters')
    browser.inspect.return_value.update(website='https://www.reuters.com/', user_agent=UA, url='https://geo.captcha-delivery.com/captcha/?t=fe&cid=c&initialCid=i&hash=h')
    return recovery.model_copy(update={'site_id': 'reuters'}), config


@pytest.mark.asyncio
async def test_pending_uuid_keeps_original_provider_after_config_rollback(solver_setup):
    solver, recovery, scope, identity, egress, browser = solver_setup
    recovery, config = enable(solver, recovery, identity, browser)
    solver.solverify.request = AsyncMock(return_value={'taskId': TASK})
    assert await solver.tick(recovery, scope, identity, egress)
    config['provider_by_identity'] = {}  # Operator rollback must not send UUID to 2Captcha.
    solver.solverify.request.return_value = {'status': 'ready', 'cost': 0,
        'solution': {'cookies': {'datadome': 'opaque'}, 'useragent': UA}}
    assert await solver.tick(recovery, scope, identity, egress)
    solver.provider.request.assert_not_awaited()
    job = solver.jobs(recovery.recovery_id)[0]
    assert job['provider'] == 'solverify' and job['task_id'] == TASK and job['cost'] == 0
    assert job['status'] == 'APPLIED' and job['verification_state'] == 'PENDING'
    assert 'opaque' not in json.dumps(job)
    assert 'Domain=.reuters.com' in browser.apply.await_args.args[0]


@pytest.mark.asyncio
async def test_wrong_solved_ua_is_not_applied_or_rebought(solver_setup):
    solver, recovery, scope, identity, egress, browser = solver_setup
    recovery, _ = enable(solver, recovery, identity, browser)
    solver.solverify.request = AsyncMock(return_value={'taskId': TASK})
    await solver.tick(recovery, scope, identity, egress)
    solver.solverify.request.return_value = {'status': 'ready', 'cost': '0.001',
        'solution': {'cookies': {'datadome': 'opaque'}, 'useragent': 'Mac Chrome'}}
    assert not await solver.tick(recovery, scope, identity, egress)
    browser.apply.assert_not_awaited()
    job = solver.jobs(recovery.recovery_id)[0]
    assert job['reason'] == 'SOLVERIFY_USER_AGENT_MISMATCH' and job['cost'] == '0.001'
    assert not await solver.tick(recovery, scope, identity, egress)
    assert solver.solverify.request.await_count == 2


def test_legacy_task_uses_2captcha_even_when_identity_override_enabled(solver_setup):
    solver, _, _, identity, _, _ = solver_setup
    config = {**solver.config(), 'provider_by_identity': {identity.identity_id: 'solverify'}}
    provider, key, name = solver.provider_for(config, job={'task_id': 123}, identity_id=identity.identity_id)
    assert provider is solver.provider and key == 'test-key' and name == '2captcha'
