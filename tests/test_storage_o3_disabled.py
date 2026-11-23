import asyncio
import sqlite3
from types import SimpleNamespace
import pytest

from doxagent.persistent_runtime_v2.maintenance import RuntimeMaintenance
from tests.test_runtime_orchestration_recovery import maintenance_fixture


def test_control_authority_rejects_late_old_o3_but_allows_o2_only(tmp_path):
    runtime,journal,settings,control=maintenance_fixture(tmp_path)
    try:
        with control._write() as db:
            db.execute('UPDATE maintenance_control SET enabled=0 WHERE id=1')
        base=control.active_revision('MU')
        with pytest.raises(sqlite3.IntegrityError,match='O3_MAINTENANCE_DISABLED'):
            control.activate_runtime_bundle(ticker='MU',identity='late',base=base,event_ref={'version':2},policy_ref={'version':2},metadata={'maintenance_id':'old'})
        result=control.activate_runtime_bundle(ticker='MU',identity='o2',base=base,event_ref={'version':2},policy_ref=base['artifacts']['document3'],metadata={'maintenance_id':'new','o3_outcome':'SKIPPED_BY_CONTROL'})
        assert result['artifacts']['document3']==base['artifacts']['document3']
    finally:
        runtime.close()


def test_unchanged_d3_release_does_not_reapply_authorization(tmp_path):
    from doxagent.persistent_runtime_v2.policy_consumption import admit_revision, get_value, put_value
    from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository

    runtime, journal, settings, control = maintenance_fixture(tmp_path)
    try:
        repo = SQLiteDocument3PolicyRepository(settings.codex_runtime_sqlite_path)
        base = control.active_revision('MU')
        admit_revision(journal, revision=base, policy_repository=repo)
        with journal.transaction() as db:
            put_value(db, 'policy_direction_usage', 'business-sentinel', {'consumed': True, 'usage_generation': 4})
        new = {**base, 'revision_id': 'o2-only', 'base_revision': 'base',
               'runtime_metadata': {'o3_outcome': 'SKIPPED_BY_CONTROL'}}
        result = admit_revision(journal, revision=new, policy_repository=repo)
        assert result['reused_release']
        with journal.transaction(write=False) as db:
            assert get_value(db, 'policy_authorization_head', 'MU') == 'o2-only'
            assert get_value(db, 'policy_direction_usage', 'business-sentinel')['usage_generation'] == 4
    finally:
        runtime.close()


def test_o3_off_continues_o2_and_reconciles_without_consuming_o3(tmp_path, monkeypatch):
    runtime, journal, settings, control = maintenance_fixture(tmp_path)
    with control._write() as db:
        db.execute("UPDATE maintenance_control SET enabled=0 WHERE id=1")
    calls = []

    class O2:
        async def run(self, **kwargs):
            calls.append("o2")
            return None, None, None, {}

    def forbidden(*args, **kwargs):
        raise AssertionError("O3 disabled must not prepare O3 work or full Reference")

    monkeypatch.setattr("doxagent.persistent_runtime_v2.reference_capture.freeze_before", forbidden)
    m = RuntimeMaintenance(settings, runtime, journal, worker_factory=lambda: SimpleNamespace(),
                           o2_factory=lambda *_: O2(), o3_factory=forbidden)
    task = journal.claim("maintain")
    consumed = []
    monkeypatch.setattr(runtime.repository, "mark_daily_records_processed", lambda **kw: consumed.append(kw))
    try:
        base = control.active_revision("MU")
        result = asyncio.run(m(task))
        assert result["revision_id"] != "base"
        current = control.active_revision("MU")
        assert current["artifacts"]["document3"] == base["artifacts"]["document3"]
        assert current["runtime_metadata"]["o3_outcome"] == "SKIPPED_BY_CONTROL"
        assert task["receipt"].get("o3") is None
        assert journal.get("o3_pending", "MU")["pending"]
        assert all(not x["trade_record_ids"] and not x["badcase_ids"] for x in consumed)
        assert asyncio.run(m(task))["reconciled"]
        assert calls == ["o2"]
        assert current["artifacts"]["event_library"]["root"] == settings.event_library_root
        assert not list(tmp_path.glob("runtime_artifacts/*/events"))
    finally:
        runtime.close()


def test_o3_off_supplement_never_hydrates_parent_or_w3_context(tmp_path,monkeypatch):
    from datetime import timedelta
    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from tests.test_persistent_runtime_v2 import _source
    runtime,journal,settings,control=maintenance_fixture(tmp_path)
    try:
        case=runtime.execute_message(_source())
        with journal.transaction() as db:
            db.execute("INSERT INTO runtime_v2_candidates VALUES(?,?,?,?,?,?,?,'PENDING','{}',?)",
                       ('pending','E-new','MU',str(case.trading_date),case.source.source_message_id,0,'new',journal.clock().isoformat()))
        journal.put_task('old','MU','MAINTENANCE',{'day':str(case.trading_date)})
        lease=journal.claim('old');journal.finish(lease)
        journal.set('case_maintenance_owner',case.case_id,'old')
        dirty={'case_id':case.case_id,'ticker':'MU'};journal.set('dirty_case',case.case_id,dirty)
        coordinator=RuntimeCoordinator.__new__(RuntimeCoordinator)
        coordinator.runtime=runtime;coordinator.journal=journal;coordinator.maintain=SimpleNamespace(o3_enabled=False)
        coordinator._schedule=lambda identity,ticker,kind,inputs:journal.put_task(identity,ticker,kind,inputs)
        def forbidden(*a,**kw):raise AssertionError('hydrated heavy historical state')
        monkeypatch.setattr(runtime.repository,'get_case',forbidden)
        monkeypatch.setattr(runtime.repository,'list_effects',forbidden)
        monkeypatch.setattr(journal,'get_task',forbidden)
        coordinator._schedule_case_supplement(dirty)
        with journal.transaction(write=False,expand=False) as db:
            assert db.execute("SELECT 1 FROM runtime_tasks WHERE id LIKE 'supplement:%'").fetchone()
        assert journal.get('dirty_case',case.case_id) is None
    finally:runtime.close()
