"""Dual-active dispatch with existing budgets; no publisher/provider traffic."""
import asyncio
import collections
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.budget import JointBudget
from doxagent.site_strategy.governance_models import dual_active_crawler
from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import AccessRequest, SitePurpose, utc_now
from tests import test_browser_recovery as base


@pytest.mark.asyncio
async def test_idle_members_rotate_and_reservations_do_not_leak():
    budget = JointBudget()
    counts = collections.Counter()
    for _ in range(20):
        async with budget.route_members("site", [("a", 2, 1500), ("b", 1, 3000)]) as member:
            counts[member] += 1
    assert counts == {"a": 10, "b": 10}
    assert not budget._routing_pending


@pytest.mark.asyncio
async def test_protected_member_takes_less_burst_work_and_single_member_still_works():
    budget = JointBudget()
    budget.identity_interval = lambda identity, interval: 10000 if identity == "a" else interval
    hold = asyncio.Event()
    counts = collections.Counter()

    async def worker():
        async with budget.route_members("site", [("a", 1, 1500), ("b", 1, 1500)]) as member:
            counts[member] += 1
            await hold.wait()

    tasks = [asyncio.create_task(worker()) for _ in range(12)]
    while sum(counts.values()) < 12:
        await asyncio.sleep(0)
    assert 0 < counts["a"] < counts["b"]
    hold.set()
    await asyncio.gather(*tasks)
    async with budget.route_members("site", [("a", 1, 1500)]) as member:
        assert member == "a"
    assert not budget._routing_pending


@pytest.mark.asyncio
async def test_cancelled_assignment_is_released_without_a_physical_permit():
    budget = JointBudget()
    ready = asyncio.Event()

    async def worker():
        async with budget.route_members("site", [("a", 1, 1500)]):
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not budget._routing_pending and budget._active == 0


@pytest.mark.asyncio
async def test_two_members_share_the_same_site_rate_and_concurrency_limit():
    budget = JointBudget(max_concurrency=4)
    times, active, peak = [], 0, 0

    async def worker(identity):
        nonlocal active, peak
        async with budget.permit(SitePurpose.CRAWLER, site_key="site", identity_id=identity,
            site_max_concurrency=2, site_min_interval_ms=50, identity_max_concurrency=2,
            identity_min_interval_ms=0, timeout_seconds=3):
            active += 1
            peak = max(peak, active)
            times.append(time.monotonic())
            await asyncio.sleep(.08)
            active -= 1

    await asyncio.gather(*(worker("a" if i % 2 else "b") for i in range(8)))
    assert peak <= 2
    assert min(b - a for a, b in zip(times, times[1:])) >= .04
    assert budget._active == 0


def test_shared_clock_combines_healthy_capacity_without_resizing_members():
    members = [("main", 2, 1500), ("backup", 1, 3000)]
    assert JointBudget.combined_interval_ms(1500, members) == 1000
    assert JointBudget.combined_interval_ms(1500, members[:1]) == 1500
    assert JointBudget.combined_interval_ms(1500, members[1:]) == 1500
    assert JointBudget.combined_interval_ms(0, members) == 0
    assert members == [("main", 2, 1500), ("backup", 1, 3000)]


@pytest.mark.parametrize("site,source,purpose,enabled", [
    ("reuters", "reuters_site_search", "CRAWLER", True),
    ("reuters", "reuters_site_search", "BODY", False),
    ("reuters", None, "CRAWLER", False),
    ("investing", "investing_ticker_news", "CRAWLER", False),
])
def test_routing_mode_is_source_and_purpose_scoped(site, source, purpose, enabled):
    scope = SimpleNamespace(owner_site_id=site, source_id=source,
                            verification_plan={"crawler_routing": "dual_active"})
    assert dual_active_crawler(scope, purpose) is enabled


@pytest.fixture
def setup(tmp_path, monkeypatch):
    generator = base.setup.__wrapped__(tmp_path)
    repo, service, store, _ = next(generator)
    scope = store.get_scope("source:reuters_site_search")
    scope = store.apply_scope(scope.model_copy(update={"verification_plan": {
        **scope.verification_plan, "crawler_routing": "dual_active"}}), scope.revision)
    request = AccessRequest(operation_id="routing", url="https://www.reuters.com/site-search/?query=Micron",
        purpose="CRAWLER", mode="BROWSER", origin_scope_id=scope.scope_id,
        recipe_parameters={"query": "Micron"})
    resolved = service._resolve_request(request)
    resolved = resolved.model_copy(update={"access": resolved.access.model_copy(update={
        "min_interval_ms": 0, "max_concurrency": 4})})
    monkeypatch.setattr(service, "_resolve_request", lambda _: resolved)
    monkeypatch.setattr(service.failover, "candidate_status", lambda *a, **k: {
        "selectable": True, "eligible": True, "proof_ready": True, "skip_reasons": []})
    monkeypatch.setattr(service.failover, "usable", lambda *a, **k: True)
    monkeypatch.setattr(service, "_auth_required", lambda *a: False)
    service.budgets.joint.identity_interval = lambda *a: 0
    attempts = []

    async def attempt(request, resolved, combination, profile, egress):
        attempts.append(combination.identity_id)
        await asyncio.sleep(.01)
        return RuntimeResponse(200, request.url, {}, body="<article>normal</article>"), None, "ok", 10

    service._attempt = AsyncMock(side_effect=attempt)
    yield repo, service, store, scope, request, resolved, attempts
    try:
        next(generator)
    except StopIteration:
        pass


@pytest.mark.asyncio
async def test_service_dispatches_both_members_without_route_generation_flapping(setup):
    repo, service, _, scope, request, _, attempts = setup
    generations = []
    for i in range(8):
        result = await service._execute_uncached(request.model_copy(update={"operation_id": str(i)}))
        assert result.disposition.value == "SUCCESS"
        assert result.stage_timings["routing_balanced"] == 1
        generations.append(repo.get_runtime("scope:" + scope.scope_id).generation)
    assert set(attempts) == {scope.primary_identity_id, scope.fallback_identity_id}
    assert len(set(generations)) == 1
    assert not service.budgets.joint._routing_pending


@pytest.mark.asyncio
async def test_source_combines_existing_page_slots_and_restores_single_member_envelope(setup, monkeypatch):
    repo, service, _, scope, request, resolved, _ = setup
    for member, limit, interval in [(scope.primary_identity_id, 2, 1500),
                                     (scope.fallback_identity_id, 1, 3000)]:
        identity = repo.get_identity(member)
        repo.apply_identity(identity.model_copy(update={'access': identity.access.model_copy(update={
            'max_concurrency': limit, 'min_interval_ms': interval})}),
            expected_revision=identity.revision, actor='test')
    resolved = resolved.model_copy(update={'access': resolved.access.model_copy(update={
        'max_concurrency': 2, 'min_interval_ms': 1500})})
    monkeypatch.setattr(service, '_resolve_request', lambda _: resolved)
    original = service._execute_uncached_selected
    envelopes = []

    async def observe(request, *, resolved, preferred_identity=None):
        envelopes.append((resolved.access.max_concurrency, resolved.access.min_interval_ms))
        return await original(request, resolved=resolved, preferred_identity=preferred_identity)

    monkeypatch.setattr(service, '_execute_uncached_selected', observe)
    await service._execute_uncached(request)
    body = await service._execute_uncached(request.model_copy(update={'purpose': SitePurpose.BODY}))
    assert 'routing_balanced' not in body.stage_timings
    monkeypatch.setattr(service.failover, 'candidate_status', lambda _, identity, purpose: {
        'selectable': identity == scope.primary_identity_id, 'eligible': False})
    await service._execute_uncached(request)
    assert envelopes == [(3, 1000), (3, 1000), (2, 1500)]
    assert resolved.access.max_concurrency == 2 and resolved.access.min_interval_ms == 1500
    assert service.budgets.joint.max_concurrency == 4  # The process-wide ceiling is unchanged.


@pytest.mark.asyncio
async def test_fast_head_estimate_cannot_serialize_the_three_existing_pool_slots(setup):
    repo, service, _, scope, request, resolved, _ = setup
    for member, limit in [(scope.primary_identity_id, 2), (scope.fallback_identity_id, 1)]:
        identity = repo.get_identity(member)
        repo.apply_identity(identity.model_copy(update={'access': identity.access.model_copy(update={
            'max_concurrency': limit})}), expected_revision=identity.revision, actor='test')
    service.budgets.joint._costs[resolved.runtime_key] = .01
    entered, release = asyncio.Event(), asyncio.Event()
    active = collections.Counter()
    peak = 0

    async def hold(request, resolved, combination, profile, egress):
        nonlocal peak
        active[combination.identity_id] += 1
        peak = max(peak, sum(active.values()))
        if peak == 3:
            entered.set()
        await release.wait()
        active[combination.identity_id] -= 1
        return RuntimeResponse(200, request.url, {}, body='<article>normal</article>'), None, 'ok', 10

    service._attempt = AsyncMock(side_effect=hold)
    tasks = [asyncio.create_task(service._execute_uncached(request.model_copy(update={
        'operation_id': str(i), 'recipe_parameters': {'query': 'Micron', '_poll_rate': .8}}))) for i in range(3)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert active[scope.primary_identity_id] == 2 and active[scope.fallback_identity_id] == 1
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert peak == 3 and not service.budgets.joint._routing_pending


@pytest.mark.asyncio
async def test_member_blocked_after_assignment_is_not_contacted(setup, monkeypatch):
    _, service, _, scope, request, _, attempts = setup
    calls = collections.Counter()

    def status(_, identity, purpose):
        calls[identity] += 1
        ready = identity != scope.primary_identity_id or calls[identity] < 3
        return {"selectable": ready, "eligible": ready, "proof_ready": ready, "skip_reasons": []}

    monkeypatch.setattr(service.failover, "candidate_status", status)
    result = await service._execute_uncached(request)
    assert result.disposition.value == "SUCCESS"
    assert attempts == [scope.fallback_identity_id]


@pytest.mark.asyncio
async def test_source_dedup_remains_outside_identity_selection(setup, monkeypatch):
    repo, service, _, _, request, _, attempts = setup
    monkeypatch.setattr(repo.storage, "diagnostics", lambda: {"unavailable": False})
    await service._execute_uncached(request)  # Establish the existing route generation.
    attempts.clear()
    result = await asyncio.gather(service.execute(request), service.execute(request))
    assert all(r.disposition.value == "SUCCESS" for r in result), [(r.disposition, r.reason_code) for r in result]
    assert len(attempts) == 1
    assert not service.budgets.joint._routing_pending


@pytest.mark.asyncio
async def test_dual_active_canary_never_stops_existing_chrome_or_switches_healthy_route(setup, monkeypatch):
    repo, service, _, scope, request, resolved, _ = setup
    await service.health.candidates(resolved.runtime_key, resolved.access.combinations,
                                    excluded=set(), purpose=SitePurpose.CRAWLER)
    before = repo.get_runtime(resolved.runtime_key).active_combination_id
    service.runtime.browser_runtimes.stop_identity = AsyncMock()
    monkeypatch.setattr("doxagent.site_strategy.failover.validate_crawler_capture", lambda *a: True)
    result = await service.failover._canary(scope, scope.fallback_identity_id, SitePurpose.CRAWLER, primary=False)
    assert result["ready"]
    service.runtime.browser_runtimes.stop_identity.assert_not_awaited()
    assert repo.get_runtime(resolved.runtime_key).active_combination_id == before


def test_routing_flag_can_rollback_without_resizing_existing_members(setup):
    repo, _, store, scope, *_ = setup
    identity = repo.get_identity(scope.fallback_identity_id)
    updated = repo.apply_identity(identity.model_copy(update={"access": identity.access.model_copy(
        update={"min_interval_ms": identity.access.min_interval_ms + 17})}),
        expected_revision=identity.revision, actor="test")
    restored = store.apply_scope(scope.model_copy(update={"verification_plan": {
        k: v for k, v in scope.verification_plan.items() if k != "crawler_routing"}}), scope.revision)
    assert not dual_active_crawler(restored, "CRAWLER")
    assert repo.get_identity(identity.identity_id).access == updated.access


def test_routing_only_scope_update_preserves_business_proof_but_contract_change_invalidates(setup):
    from doxagent.site_strategy.governance_repository import capability_fingerprint

    repo, _, store, scope, *_ = setup
    identity = repo.get_identity(scope.primary_identity_id)
    egress = repo.get_egress(identity.egress_id)
    before = capability_fingerprint(repo, scope, identity, egress)
    plan = dict(scope.verification_plan)
    plan.pop('crawler_routing')
    restored = store.apply_scope(scope.model_copy(update={'verification_plan': plan}), scope.revision)
    assert capability_fingerprint(repo, restored, identity, egress) == before
    changed = store.apply_scope(restored.model_copy(update={'verification_plan': {
        **restored.verification_plan, 'contract': 'different-article-contract'}}), restored.revision)
    assert '_routing_capability_revision' not in changed.verification_plan
    assert capability_fingerprint(repo, changed, identity, egress) != before


@pytest.mark.asyncio
async def test_first_late_business_use_anchors_ramp_without_rewriting_recovery_history(setup, monkeypatch):
    repo, service, store, scope, request, _, _ = setup
    start = utc_now() - timedelta(minutes=30)
    state = repo.get_identity_runtime(scope.primary_identity_id)
    state = state.model_copy(update={"recovered_at": start, "last_recovery_id": "old",
        "probation_until": start + timedelta(minutes=5), "natural_success_at": None,
        "capability_windows": {"CRAWLER": {"recovery_id": "old", "recovered_at": start.isoformat(),
            "probation_until": (start + timedelta(minutes=5)).isoformat(), "capability_epoch": 1}}})
    repo.save_identity_runtime(state, expected_generation=state.generation)
    await service._execute_uncached(request)
    state = repo.get_identity_runtime(scope.primary_identity_id)
    assert state.recovered_at == start
    first = state.capability_windows["CRAWLER"]["first_identity_use_at"]
    from datetime import datetime
    clock = [datetime.fromisoformat(first)]
    monkeypatch.setattr("doxagent.site_strategy.service.utc_now", lambda: clock[0])
    assert service._identity_navigation_interval(scope.primary_identity_id, 1500) == 10000
    clock[0] += timedelta(seconds=150)
    assert 1500 < service._identity_navigation_interval(scope.primary_identity_id, 1500) < 10000
    clock[0] += timedelta(seconds=150)
    assert service._identity_navigation_interval(scope.primary_identity_id, 1500) == 1500
    monkeypatch.setattr("doxagent.site_strategy.governance_repository.utc_now", lambda: clock[0])
    with repo.transaction() as connection:
        store._record_capability_relapse(connection, SimpleNamespace(assessment={"kind": "INTERACTIVE"},
            identity_id=scope.primary_identity_id, recovery_id="new", site_id="reuters",
            affected_contexts=[{"purpose": "CRAWLER"}]))
    relapse = repo.get_identity_runtime(scope.primary_identity_id).recent_relapses[-1]
    assert relapse["within_recovery_window"] and relapse["seconds_to_recurrence"] > 1800


@pytest.mark.asyncio
async def test_faulted_member_leaves_and_recovered_member_rejoins_normal_dispatch(setup, monkeypatch):
    _, service, _, scope, request, _, attempts = setup
    ready = {scope.primary_identity_id}
    monkeypatch.setattr(service.failover, "candidate_status", lambda _, identity, purpose: {
        "selectable": identity in ready, "eligible": identity in ready,
        "proof_ready": identity in ready, "skip_reasons": []})
    for _ in range(3):
        await service._execute_uncached(request)
    assert set(attempts) == {scope.primary_identity_id}
    ready.add(scope.fallback_identity_id)
    attempts.clear()
    for _ in range(6):
        await service._execute_uncached(request)
    assert set(attempts) == ready
    ready.remove(scope.primary_identity_id)
    attempts.clear()
    for _ in range(3):
        await service._execute_uncached(request)
    assert set(attempts) == ready
    assert not service.budgets.joint._routing_pending


@pytest.mark.asyncio
async def test_body_keeps_existing_route_and_has_no_balanced_reservation(setup):
    _, service, _, scope, request, _, attempts = setup
    for _ in range(3):
        result = await service._execute_uncached(request.model_copy(update={"purpose": SitePurpose.BODY}))
        assert result.disposition.value == "SUCCESS"
        assert "routing_balanced" not in result.stage_timings
    assert set(attempts) == {scope.primary_identity_id}


@pytest.mark.asyncio
async def test_no_healthy_member_never_creates_a_browser_attempt(setup, monkeypatch):
    _, service, _, _, request, _, attempts = setup
    monkeypatch.setattr(service.failover, "candidate_status", lambda *a, **k: {
        "selectable": False, "eligible": False, "proof_ready": False, "skip_reasons": []})
    service.failover.schedule_async = AsyncMock(return_value=None)
    result = await service._execute_uncached(request)
    assert result.disposition.value != "SUCCESS"
    assert attempts == [] and not service.budgets.joint._routing_pending
