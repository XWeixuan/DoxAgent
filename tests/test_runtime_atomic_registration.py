import json
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, LeaseLost
from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry
from doxagent.persistent_runtime_v2.w3_contracts import W3_V6_CONTRACT


def ready(journal):
    registry = W3ThreadRegistry(journal)
    template, _ = registry.template('MU', 'public', 'same')
    registry.ready_template(template['record_id'], 'public-thread', template['lease_token'])
    return registry, registry.find_ready_template('public', 'same')


def register(registry, template, identity='case', frozen=None):
    return registry.register_trade_bundle(
        case_id=identity, ticker='MU', phases=('trade_decision:A', 'trade_decision:B'),
        template=template, compatibility='same', frozen=frozen or {'w3_global_input': {'old': 1}},
        accepted_at=registry.journal.clock(),
    )


def test_one_writer_preserves_partial_result_and_original_identity(tmp_path):
    journal = RuntimeJournal(tmp_path/'r.db')
    registry, template = ready(journal)
    count = []
    original = journal.transaction

    @contextmanager
    def tracked(*args, **kwargs):
        count.append(kwargs.get('write', True))
        with original(*args, **kwargs) as db:
            yield db

    journal.transaction = tracked
    stages, records, _ = register(registry, template)
    assert sum(count) == 1
    a, b = (records['trade_decision:'+label] for label in ('A', 'B'))
    assert a['record_id'] != b['record_id']
    registry.save_result('case', 'trade_decision:A', {'decision': 'NO_TRADE'},
                         thread_id='paid-thread', worker_job_id='paid-job')
    again, current, saved = register(registry, template, frozen={'w3_global_input': {'old': 2}})
    assert again['trade_decision:A']['worker_job_id'] == 'paid-job'
    assert again['trade_decision:A']['accepted_at'] == stages['trade_decision:A']['accepted_at']
    assert current['trade_decision:B']['record_id'] == b['record_id']
    assert saved['w3_global_input'] == {'old': 1}
    assert register(registry, template, 'other')[1]['trade_decision:A']['record_id'] != a['record_id']


def test_changed_template_rolls_back_all_registration_and_snapshots(tmp_path):
    journal = RuntimeJournal(tmp_path/'r.db')
    registry, template = ready(journal)
    with journal.transaction() as db:
        db.execute("UPDATE runtime_v2_w3_threads SET status='DELETING' WHERE record_id=?", (template['record_id'],))
    with pytest.raises(ValueError, match='Template changed'):
        register(registry, template)
    assert registry.registration('case') == ({}, {}, {})


def test_registration_rejects_lost_effect_fence(tmp_path):
    from doxagent.persistent_runtime_v2.fencing import write_scope
    from tests.test_w3_v5_execution import setup_case
    service, journal, _, case, _ = setup_case(tmp_path)
    registry, template = ready(journal)
    effect = next(e for e in service.repository.list_effects(case.case_id) if e.effect_type == 'route_to_w3')
    try:
        with write_scope('effect', effect, journal.clock), pytest.raises(LeaseLost):
            register(registry, template)
        assert not registry.stage('case', 'trade_decision')
    finally:
        service.close()


def test_real_entry_registration_reuses_frozen_origin_without_new_writer(tmp_path):
    from tests.test_w3_v5_execution import setup_case
    from doxagent.persistent_runtime_v2.w3_execution import W3Execution
    service, journal, agent, case, _ = setup_case(tmp_path)
    case.w3_contract_version = W3_V6_CONTRACT
    case.frozen_inputs['w3_trade_mode'] = 'dual_consensus'
    agent.entry_enabled = lambda _: True
    execution = W3Execution(service)
    _, _, compatibility, layout = execution._thread_signature(case)
    from doxagent.persistent_runtime_v2.journal import digest
    template, _ = execution.registry.template('MU', digest([None, compatibility, layout]), compatibility)
    execution.registry.ready_template(template['record_id'], 'public', template['lease_token'])
    try:
        before = datetime.now(UTC)
        first = execution._register_entry(case)
        origin = first[2]['w3_time_awareness']['started_at_utc']
        assert datetime.fromisoformat(origin) >= before
        assert origin != journal.clock().isoformat()
        original = journal.transaction

        @contextmanager
        def no_write(*args, **kwargs):
            assert not kwargs.get('write', True)
            with original(*args, **kwargs) as db:
                yield db

        journal.transaction = no_write
        second = execution._register_entry(case)
        assert second == first
        journal.transaction = original
        with journal.transaction() as db:
            db.execute(
                "DELETE FROM runtime_values WHERE namespace='w3_time_awareness' AND key=?",
                (case.case_id,),
            )
        journal.transaction = no_write
        # Missing time metadata alone must not acquire a writer/re-register W3.
        incomplete = execution._register_entry(case)
        assert 'w3_time_awareness' not in incomplete[2]
        assert incomplete[:2] == first[:2]
    finally:
        journal.transaction = original if 'original' in locals() else journal.transaction
        service.close()
