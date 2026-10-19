"""Regression: cold inspection must not repeatedly kill its own controller."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.worker_runtime import WorkerSiteAccessRuntime


@pytest.mark.asyncio
async def test_cold_challenge_inspection_has_attach_and_classifier_budget():
    runtime = object.__new__(WorkerSiteAccessRuntime)
    runtime._binding = lambda token: ('reuters', 'identity', 'egress')
    runtime._maintenance_refs = lambda token: {'token': token, 'target_id': 'original'}
    runtime.workers = SimpleNamespace(command=AsyncMock(return_value={
        'status_code': 0, 'final_url': 'https://www.reuters.com/world/story/',
        'headers': {}, 'body': '<article>normal original</article>',
        'challenge_cleared': True,
    }))
    result = await runtime.inspect_challenge('private')
    assert result.challenge_cleared
    args, kwargs = runtime.workers.command.call_args
    assert args[2] == 'verify' and args[3]['inspect_only']
    assert args[3]['target_id'] == 'original'
    # Measured cold classifier 4.6s + CDP/page recovery exceeded the old 5s.
    assert kwargs['timeout'] == 15


@pytest.mark.asyncio
async def test_login_recovery_inspection_is_also_cold_start_bounded():
    runtime = object.__new__(WorkerSiteAccessRuntime)
    runtime._binding = lambda token: ('reuters', 'identity', 'egress')
    runtime._maintenance_refs = lambda token: {'token': token, 'target_id': 'original'}
    runtime.workers = SimpleNamespace(command=AsyncMock(return_value={'navigation_state': 'READY'}))
    assert (await runtime.inspect_login('private'))['navigation_state'] == 'READY'
    assert runtime.workers.command.call_args.kwargs['timeout'] == 15
