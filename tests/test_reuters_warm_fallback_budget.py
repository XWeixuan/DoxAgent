"""A running successor must not be reclassified as a cold start on short BODY budgets."""
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import AccessRequest
from tests import test_browser_recovery as base


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('purpose', ['BODY', 'CRAWLER'])
async def test_active_fallback_can_execute_with_less_than_five_seconds(setup, purpose):
    repo, service, _, scope = setup
    await service.health.activate('scope:'+scope.scope_id, scope.fallback_identity_id)
    service._attempt = AsyncMock(return_value=(RuntimeResponse(status_code=200, final_url=base.URL, headers={}, body='<article>ok</article>'), None, 'ok', 1))
    service.failover.schedule_async = AsyncMock(return_value=None)
    result = await service._execute_uncached(AccessRequest(
        operation_id='warm-short-'+purpose, url=base.URL, purpose=purpose, mode='BROWSER',
        origin_scope_id=scope.scope_id, excluded_combinations=[scope.primary_identity_id],
        remaining_budget_ms=4000,
    ))
    assert result.disposition.value == 'SUCCESS'
    assert result.identity_id == scope.fallback_identity_id
    service._attempt.assert_awaited_once()
    service.failover.schedule_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_fallback_still_reserves_cold_start_budget(setup, monkeypatch):
    _, service, _, scope = setup
    monkeypatch.setattr(service.failover, 'candidate_status', lambda *a, **k: {'selectable':True, 'eligible':True})
    service.failover.schedule_async = AsyncMock(return_value=None)
    service._attempt = AsyncMock()
    result = await service._execute_uncached(AccessRequest(
        operation_id='cold-short', url=base.URL, purpose='BODY', mode='BROWSER',
        origin_scope_id=scope.scope_id, excluded_combinations=[scope.primary_identity_id],
        remaining_budget_ms=4000,
    ))
    assert result.reason_code == 'fallback_not_ready'
    service._attempt.assert_not_awaited()
