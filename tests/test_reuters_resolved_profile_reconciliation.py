import pytest
from doxagent.site_strategy.governance_models import MaintenanceSession
from tests import test_browser_recovery as base

@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)

@pytest.mark.parametrize('blocked',['none','open_login','unresolved','unknown'])
def test_only_ended_challenge_reservation_can_be_released(setup,blocked):
    repo,service,store,scope=setup
    recovery,_,_=base.retained(setup,human=True)
    task=store.current_challenge(recovery.identity_id)
    store.save_challenge(task.model_copy(update={'state':'RESOLVED'}))
    store.save_recovery(recovery.model_copy(update={'phase':'BACKOFF' if blocked=='unresolved' else 'RESOLVED',
        'challenge_task_id':None}),expected_generation=recovery.generation)
    identity=repo.get_identity(recovery.identity_id);profile=repo.get_profile(identity.profile_id)
    repo.save_profile(profile.model_copy(update={'operational_state':'MAINTENANCE',
        'maintenance_session_id':'unknown' if blocked=='unknown' else task.task_id}))
    if blocked=='open_login':
        store.save_session(MaintenanceSession(kind='LOGIN',scope_id=scope.scope_id,
            identity_id=identity.identity_id,site_id=scope.owner_site_id,state='OPEN'))
    assert store.reconcile_resolved_profile(profile.profile_id)==(blocked=='none')
    after=repo.get_profile(profile.profile_id)
    assert (after.maintenance_session_id is None)==(blocked=='none')
    assert not store.reconcile_resolved_profile(profile.profile_id)
