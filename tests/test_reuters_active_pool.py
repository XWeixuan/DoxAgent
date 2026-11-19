"""Three identities share normal source work; admission and ownership stay authoritative."""
from collections import Counter
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from doxagent.site_strategy.governance_models import (
    SourceAccessScope, scope_identity_ids, parallel_source_runtime,
)
from doxagent.site_strategy.governance_repository import capability_fingerprint
from doxagent.site_strategy.schema import SitePurpose
from tests.test_reuters_dual_active import setup


def third_member(setup):
    repo, service, store, scope, request, resolved, attempts = setup
    main = repo.get_identity(scope.primary_identity_id)
    profile = repo.get_profile(main.profile_id)
    repo.save_profile(profile.model_copy(update={
        'profile_id': 'reuters-active-3', 'directory_key': 'reuters-active-3',
        'credential_ref': None, 'maintenance_session_id': None}))
    third = main.model_copy(update={
        'identity_id': 'reuters-active-3', 'profile_id': 'reuters-active-3',
        'owner_scope_id': None, 'credential_ref': None, 'revision': 0})
    repo.apply_identity(third, expected_revision=None, actor='test')
    plan = {**scope.verification_plan, 'crawler_routing': 'active_pool',
        'body_routing': 'active_pool', 'additional_active_identity_ids': [third.identity_id],
        'max_parallel_concurrency': 4}
    scope = store.apply_scope(SourceAccessScope.model_validate({
        **scope.model_dump(), 'verification_plan': plan}), scope.revision)
    # Re-resolve to exercise ScopeResolver, rather than supplying hand-built combinations.
    from doxagent.site_strategy.service import SiteStrategyService
    fresh = SiteStrategyService._resolve_request(service, request)
    fresh = fresh.model_copy(update={'access': fresh.access.model_copy(update={
        'min_interval_ms': 0, 'max_concurrency': 4})})
    service._resolve_request = lambda _: fresh
    return scope, third


@pytest.mark.asyncio
async def test_three_members_balance_listing_and_body_without_duplicate_attempts(setup):
    repo, service, store, _, request, _, attempts = setup
    before_scope = store.get_scope('source:reuters_site_search')
    main = repo.get_identity(before_scope.primary_identity_id)
    before = capability_fingerprint(repo, before_scope, main, repo.get_egress(main.egress_id))
    scope, third = third_member(setup)
    assert before == capability_fingerprint(repo, scope, main, repo.get_egress(main.egress_id))
    generations = []
    for purpose in (SitePurpose.CRAWLER, SitePurpose.BODY):
        attempts.clear()
        for i in range(9):
            result = await service._execute_uncached(request.model_copy(update={
                'purpose': purpose, 'operation_id': f'{purpose}-{i}'}))
            assert result.disposition.value == 'SUCCESS'
            assert result.stage_timings['routing_members'] == 3
            generations.append(repo.get_runtime('scope:' + scope.scope_id).generation)
        assert Counter(attempts) == {member: 3 for member in scope_identity_ids(scope)}
    assert len(set(generations)) == 1
    assert not service.budgets.joint._routing_pending
    owner = repo._connection.execute('select scope_id from scope_identity_owners where identity_id=?',
        (third.identity_id,)).fetchone()
    assert owner[0] == scope.scope_id
    assert service._identity_belongs_to_site('reuters', third.identity_id)


@pytest.mark.asyncio
async def test_unavailable_third_is_not_contacted_and_aggregate_cap_is_not_expanded(setup, monkeypatch):
    _, service, _, _, request, _, attempts = setup
    scope, third = third_member(setup)
    monkeypatch.setattr(service.failover, 'candidate_status', lambda _, member, purpose: {
        'selectable': member != third.identity_id, 'eligible': False,
        'skip_reasons': ['recovery_pending'] if member == third.identity_id else []})
    original = service._execute_uncached_selected
    caps = []
    async def observe(request, *, resolved, preferred_identity=None):
        caps.append(resolved.access.max_concurrency)
        return await original(request, resolved=resolved, preferred_identity=preferred_identity)
    monkeypatch.setattr(service, '_execute_uncached_selected', observe)
    for i in range(4):
        await service._execute_uncached(request.model_copy(update={'operation_id':str(i)}))
    assert third.identity_id not in attempts and len(attempts) == 4
    assert max(caps) <= 4


@pytest.mark.parametrize('extra', [['same'], ['a','b'], 'a', [None]])
def test_extra_member_contract_rejects_invalid_config(extra):
    with pytest.raises(ValidationError):
        SourceAccessScope(scope_id='source:reuters_site_search', source_id='reuters_site_search',
            owner_site_id='reuters', primary_identity_id='same', fallback_identity_id='backup',
            verification_plan={'crawler_routing':'active_pool', 'additional_active_identity_ids':extra})


def test_other_sources_cannot_enable_the_reuters_pool():
    scope = SimpleNamespace(owner_site_id='investing', source_id='investing_ticker_news',
        verification_plan={'crawler_routing':'active_pool', 'body_routing':'active_pool'})
    assert not parallel_source_runtime(scope, 'CRAWLER')
    assert not parallel_source_runtime(scope, 'BODY')


def test_third_requires_its_own_auth_and_capability_proof(setup, monkeypatch):
    repo, service, store, _, _, _, _ = setup
    scope, third = third_member(setup)
    # Use the real gate: no success inherited from Main, despite the shared egress.
    from doxagent.site_strategy.failover import FailoverCoordinator
    monkeypatch.setattr(service.failover, 'usable',
        FailoverCoordinator.usable.__get__(service.failover))
    status = FailoverCoordinator.candidate_status(service.failover, scope, third.identity_id,
                                                SitePurpose.BODY)
    assert not status['selectable'] and not status['proof_ready']
    assert 'auth_required' in status['skip_reasons'] or 'candidate_activation_required' in status['skip_reasons']
    assert repo.get_identity(third.identity_id).credential_ref is None


def test_third_cannot_be_reassigned_with_a_retained_owned_page(setup):
    repo, _, store, _, _, _, _ = setup
    scope, third = third_member(setup)
    from doxagent.site_strategy.governance_models import OwnedBrowserTarget
    store.save_target(OwnedBrowserTarget(identity_id=third.identity_id, browser_instance_id='native',
        worker_epoch=1, target_id='original', state='LIVE', owner_kind='CHALLENGE'))
    plan = dict(scope.verification_plan)
    plan.pop('additional_active_identity_ids')
    with pytest.raises(ValueError, match='live owned pages'):
        store.apply_scope(scope.model_copy(update={'verification_plan':plan}), scope.revision)


def test_configuration_preserves_existing_envelope_and_creates_no_credentials(setup):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('configure_pool',
        Path('scripts/ops/configure_reuters_active_pool.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo, _, _, scope, _, _, _ = setup
    main, backup = [repo.get_identity(member).model_dump(mode='json')
                    for member in scope_identity_ids(scope)]
    main['access'].update(max_concurrency=2, min_interval_ms=1500)
    backup['access'].update(max_concurrency=1, min_interval_ms=3000)
    identity, profile, updated = module.configuration(scope.model_dump(mode='json'), main, backup)
    assert updated.verification_plan['max_parallel_concurrency'] == 3
    assert updated.verification_plan['min_parallel_interval_ms'] == 1000
    assert identity.credential_ref is None and profile.auth_state.value == 'UNKNOWN'
    assert identity.profile_id != main['profile_id']
    assert identity.environment.model_dump(mode='json') == main['environment']
