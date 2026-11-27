from datetime import UTC,datetime
from tests.test_runtime_orchestration_execution import runtime_at
from tests.test_persistent_runtime_v2 import _source
from doxagent.persistent_runtime_v2.resource_refs import backfill,pins
from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus


def test_live_pins_register_in_case_transaction_and_exit_without_context_scan(tmp_path):
    runtime,journal=runtime_at(tmp_path,[datetime(2026,9,8,12,tzinfo=UTC)])
    try:
        case=runtime.execute_message(_source())
        case.status=RuntimeCaseStatus.PENDING_RETRY
        runtime.repository.save_case(case)
        for _ in range(10):
            if backfill(journal,limit=1):break
        else:raise AssertionError('pin migration did not converge')
        assert case.version_pin.policy_set_version in pins(journal,case.ticker)
        # A Case flag alone cannot release a still-pending legacy consumer.
        from doxagent.persistent_runtime_v2.schema import RuntimeEffectStatus
        for effect in runtime.repository.list_effects(case.case_id):
            runtime.repository.save_effect(effect.model_copy(update={'status':RuntimeEffectStatus.COMPLETED}))
        case.status=RuntimeCaseStatus.COMPLETED
        runtime.repository.save_case(case)
        assert not pins(journal,case.ticker)
        with journal.transaction(write=False,expand=False) as db:
            assert not db.execute('SELECT 1 FROM runtime_case_resource_refs').fetchone()
    finally:runtime.close()


def test_transport_history_uses_the_real_control_repository(tmp_path):
    from types import SimpleNamespace
    from doxagent.persistent_runtime_v2.history_exit import collect
    from doxagent.ticker_initialization.repository import InitializationRepository
    runtime,journal=runtime_at(tmp_path,[datetime(2026,9,8,12,tzinfo=UTC)])
    try:
        for _ in range(10):
            if backfill(journal):break
        control=InitializationRepository(tmp_path/'empty-control.db')
        owner=SimpleNamespace(journal=journal,control=control,settings=SimpleNamespace())
        assert collect(owner)=={'next_seconds':900}
    finally:runtime.close()
