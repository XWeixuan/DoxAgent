from datetime import timedelta
import sqlite3

import pytest

from scripts.ops.promote_reuters_identity import ARCHIVE, SOURCE, TRIAL, promote, restore
from doxagent.site_strategy.governance_models import BrowserRecovery, ChallengeTask, OwnedBrowserTarget, SourceAccessScope
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import BrowserIdentitySpec, BrowserProfile, ProxyEgress, utc_now


@pytest.fixture
def fixture(tmp_path):
    repo = SiteStrategyRepository(tmp_path / 'registry.sqlite3')
    store = GovernanceRepository(repo)
    for name, ip in [('old', '8.8.8.8'), ('backup', '8.8.4.4'), ('new', '8.8.8.8')]:
        repo.upsert_egress(ProxyEgress(egress_id=name, node_ref=name, node_fingerprint=name,
            listener_port=12345, endpoint='http://127.0.0.1:12345', observed_ip=ip))
        repo.save_profile(BrowserProfile(profile_id=name, site_id='reuters', directory_key=name, bound_egress_id=name))
        repo.apply_identity(BrowserIdentitySpec(identity_id=name, profile_id=name, egress_id=name),
                            expected_revision=None, actor='test')
        with repo.transaction() as db:
            db.execute('insert into egress_regions values(?,?,?,?)', (name, ip, 'DE', utc_now().isoformat()))
    store.apply_scope(SourceAccessScope(scope_id=SOURCE, source_id='reuters_site_search', owner_site_id='reuters',
        primary_identity_id='old', fallback_identity_id='backup', verification_plan={'crawler_routing':'dual_active'}), None)
    store.apply_scope(SourceAccessScope(scope_id=TRIAL, owner_site_id='reuters', primary_identity_id='new'), None)
    target = store.save_target(OwnedBrowserTarget(identity_id='old', browser_instance_id='instance', worker_epoch=1,
        target_id='original', owner_kind='BUSINESS', state='LIVE'))
    task = store.handoff_challenge(ChallengeTask(scope_id=SOURCE, identity_id='old', site_id='reuters', purpose='CRAWLER',
        operation_id='original-op', original_url='https://www.reuters.com/site-search/', original_target_id='original',
        browser_instance_id='instance', allocation_id=target.allocation_id, reason='captcha'))
    store.save_recovery(BrowserRecovery(identity_id='old', scope_id=SOURCE, site_id='reuters', phase='BACKOFF', handling_mode='HUMAN',
        original_allocation_id=target.allocation_id, original_target_id='original', original_instance_id='instance',
        challenge_task_id=task.task_id), expected_generation=None)
    yield repo, store, target, task
    repo.close()


def test_promote_preserves_original_and_backup_without_forging_proof(fixture):
    repo, store, target, task = fixture
    backup = repo.get_identity('backup').model_dump_json()
    previous_generation = store.get_recovery('old').generation
    assert promote(repo, source_revision=1, trial_revision=1)['apply'] is False
    assert store.get_scope(SOURCE).primary_identity_id == 'old'
    promote(repo, source_revision=1, trial_revision=1, apply=True)
    assert store.get_scope(SOURCE).primary_identity_id == 'new'
    assert store.get_scope(SOURCE).fallback_identity_id == 'backup'
    assert store.get_scope(SOURCE).verification_plan['_routing_capability_revision'] == 1
    assert store.get_scope(ARCHIVE).primary_identity_id == 'old'
    assert not store.get_scope(ARCHIVE).verification_plan['default_publisher']
    assert store.get_scope(TRIAL) is None
    assert store.get_target(target.allocation_id).target_id == 'original'
    assert store.get_target(target.allocation_id).state == 'LIVE'
    assert store.current_challenge('old').task_id == task.task_id
    assert store.current_challenge('old').scope_id == ARCHIVE
    recovery = store.get_recovery('old')
    assert recovery.scope_id == ARCHIVE and not recovery.automation_active
    assert recovery.original_instance_id == 'instance'
    assert recovery.generation == previous_generation + 1
    assert repo.get_identity('backup').model_dump_json() == backup
    assert repo.get_runtime('scope:'+SOURCE).combinations.get('new') is None
    with repo._lock:
        owners = dict(repo._connection.execute('select identity_id,scope_id from scope_identity_owners'))
    assert owners == {'old':ARCHIVE, 'new':SOURCE, 'backup':SOURCE}


@pytest.mark.parametrize('condition', ['stale', 'blocked', 'lease', 'same_exit'])
def test_unsafe_promotion_rolls_back_without_losing_original(fixture, condition):
    repo, store, target, task = fixture
    if condition == 'blocked':
        store.save_recovery(BrowserRecovery(identity_id='new', scope_id=TRIAL, site_id='reuters'), expected_generation=None)
    elif condition == 'lease':
        r = store.get_recovery('old')
        store.save_recovery(r.model_copy(update={'lease_owner':'other', 'lease_until':utc_now()+timedelta(seconds=20)}), expected_generation=r.generation)
    elif condition == 'same_exit':
        repo.upsert_egress(repo.get_egress('new').model_copy(update={'observed_ip':'8.8.4.4'}))
    with pytest.raises(ValueError):
        promote(repo, source_revision=2 if condition == 'stale' else 1, trial_revision=1, apply=True)
    assert store.get_scope(SOURCE).primary_identity_id == 'old'
    assert store.get_scope(TRIAL).primary_identity_id == 'new'
    assert store.get_scope(ARCHIVE) is None
    assert store.get_target(target.allocation_id).state == 'LIVE'
    assert store.current_challenge('old').scope_id == SOURCE


def test_mid_transaction_failure_restores_both_scopes_and_owners(fixture):
    repo, store, target, task = fixture
    with repo.transaction() as db:
        db.execute("create trigger fail_promotion before insert on scope_identity_owners "
                   "when NEW.identity_id='new' begin select raise(ABORT,'injected'); end")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        promote(repo, source_revision=1, trial_revision=1, apply=True)
    assert store.get_scope(SOURCE).primary_identity_id == 'old'
    assert store.get_scope(TRIAL).primary_identity_id == 'new'
    assert store.get_scope(ARCHIVE) is None
    assert store.current_challenge('old').scope_id == SOURCE
    with repo._lock:
        owners = dict(repo._connection.execute('select identity_id,scope_id from scope_identity_owners'))
    assert owners == {'old':SOURCE, 'new':TRIAL, 'backup':SOURCE}


def test_failed_live_trial_can_be_restored_without_clearing_cookies_or_targets(fixture):
    repo, store, target, task = fixture
    snapshot={'scopes':[store.get_scope(k).model_dump(mode='json') for k in (SOURCE,TRIAL)]}
    promote(repo,source_revision=1,trial_revision=1,apply=True)
    # A failed candidate must remain available for manual diagnosis after rollback.
    store.save_recovery(BrowserRecovery(identity_id='new',scope_id=SOURCE,site_id='reuters'),expected_generation=None)
    assert not restore(repo,snapshot,source_revision=2)['apply']
    restore(repo,snapshot,source_revision=2,apply=True)
    assert store.get_scope(SOURCE).primary_identity_id=='old'
    assert store.get_scope(SOURCE).fallback_identity_id=='backup'
    assert store.get_scope(TRIAL).primary_identity_id=='new'
    assert store.get_scope(ARCHIVE) is None
    assert store.get_recovery('old').automation_active
    assert not store.get_recovery('new').automation_active
    assert store.current_challenge('old').scope_id==SOURCE
    assert store.get_target(target.allocation_id).target_id=='original'
