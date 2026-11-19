"""A completed incident must not mint another human task during cleanup."""
import pytest

from tests import test_browser_recovery as base
from doxagent.site_strategy.verification import VerificationReport


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


@pytest.mark.parametrize('target_state',['LIVE','CLOSED'])
def test_terminal_cleanup_does_not_recreate_waiting_or_lost_challenge(setup,target_state):
    repo,service,store,scope=setup
    recovery,target,context=base.retained(setup,human=True)
    recovery=store.save_recovery(recovery.model_copy(update={
        'manual_review_reason':'automatic_verification_stalled',
    }),expected_generation=recovery.generation)
    task_id=recovery.challenge_task_id
    report=VerificationReport(challenge='CLEARED',crawler='READY',authentication='NOT_REQUIRED',
                              reason='listing_contract_verified',runtime_instance_id='chrome')
    claimed=store.claim_recovery(recovery.identity_id,owner='test')
    ended=store.finish_recovery(claimed,report,context=context,resolved_by='fresh_probe')
    assert ended.phase=='RESOLVED'
    assert store.get_challenge(task_id).state=='RESOLVED'
    before=[v.task_id for v in store.list_challenges(include_resolved=True)]
    store.save_target(store.get_target(target.allocation_id).model_copy(update={'state':target_state}))
    saved=store.save_recovery(ended.model_copy(update={'cleanup_pending':target_state=='LIVE'}),
                              expected_generation=ended.generation)
    assert saved.phase=='RESOLVED'
    assert saved.manual_review_reason=='automatic_verification_stalled'
    assert [v.task_id for v in store.list_challenges(include_resolved=True)]==before
    assert store.current_challenge(recovery.identity_id) is None
    route=repo.get_runtime(f'scope:{scope.scope_id}')
    assert route.combinations[recovery.identity_id].manual_attention_required is False
