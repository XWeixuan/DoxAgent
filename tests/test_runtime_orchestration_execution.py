import asyncio
from datetime import UTC, datetime, timedelta

from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus, RuntimeTechnicalStatus
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.trade_output import (
    IBKRPaperAdapter,
    LocalTradeSink,
    TradeOutputService,
)
from tests.test_persistent_runtime_v2 import _AnyPolicies, _AnyResponses, _FakeKnownEvents, _source


def runtime_at(tmp_path, now):
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
    repository = SQLitePersistentRuntimeV2Repository(journal.path)
    runtime = PersistentRuntimeV2Service(
        repository=repository,
        journal=journal,
        responses=_AnyResponses(),
        known_events=_FakeKnownEvents(),
        policies=_AnyPolicies(),
        dispatch_effects=False,
        retry_delays_seconds=(0, 0),
    )
    return runtime, journal


def test_trade_output_is_atomic_idempotent_and_expires_after_midnight(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        case = runtime.execute_message(_source())
        assert runtime.repository.list_consumed_policy_revisions("MU") == set()
        now[0] += timedelta(days=1)
        runtime.process_pending_effects()
        assert journal.values("trade_intents")[0]["status"] == "EXPIRED_SEMANTIC_DAY"
        assert runtime.repository.list_consumed_policy_revisions("MU") == set()
        assert runtime.repository.list_provisional("MU", case.trading_date)
        second = runtime.execute_message(
            _source().model_copy(update={"source_message_id": "second"})
        )
        runtime.process_pending_effects()
        assert len(runtime.repository.list_daily_trades("MU", second.trading_date)) == 1
        service = TradeOutputService(journal)
        assert asyncio.run(service.deliver(LocalTradeSink(journal))) == 1
        assert asyncio.run(service.deliver(LocalTradeSink(journal))) == 0
        assert not asyncio.run(IBKRPaperAdapter().readiness())["ready"]
    finally:
        runtime.close()


def test_closed_candidates_do_not_consume_policy_and_w1_replays_receipts(tmp_path):
    now = [datetime(2026, 9, 6, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        case = runtime.execute_message(
            _source(), mode="CLOSED", phase="W1", sweep_id="sweep", closed_cycle_id="2026-09-05"
        )
        successful = len(runtime.repository.list_turns(case.case_id))
        assert runtime.repository.list_provisional("MU", case.trading_date)
        runtime.execute_message(_source(), phase="ALL")
        assert len(runtime.repository.list_turns(case.case_id)) == successful + 1
        runtime.process_pending_effects()
        assert len(journal.values("candidates")) == 1
        assert not journal.values("trade_intents")
        assert runtime.repository.list_consumed_policy_revisions("MU") == set()
    finally:
        runtime.close()


def test_incomplete_round_reuses_pinned_inputs_and_successful_outputs(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        case = runtime.execute_message(_source())
        calls = len(runtime.repository.list_turns(case.case_id))
        # Crash after both receipts but before route/effects are durably confirmed.
        case.status, case.route = RuntimeCaseStatus.RUNNING, None
        runtime.repository.save_case(case)
        result = runtime.execute_message(_source())
        assert result.technical_status is RuntimeTechnicalStatus.OK
        assert len(runtime.repository.list_turns(case.case_id)) == calls
        assert result.version_pin == case.version_pin
    finally:
        runtime.close()


def test_holiday_schedule_idempotent_and_final_realtime_does_not_wait(tmp_path):
    now = [datetime(2026, 9, 4, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    coordinator = RuntimeCoordinator(runtime, journal)
    try:
        coordinator.reconcile("MU")
        now[0] = datetime(2026, 9, 8, 6, 1, tzinfo=UTC)
        coordinator.reconcile("MU")
        coordinator.reconcile("MU")
        assert coordinator.mode("MU") == ("REALTIME", None)
        assert len(journal.tasks(kind="SWEEP")) == 3
        assert len(journal.tasks(kind="MAINTENANCE")) == 1
        assert len(journal.tasks(kind="SELECTION")) == 1
        assert journal.tasks(kind="SELECTION")[0]["inputs"]["day"] == "2026-09-08"
    finally:
        coordinator.close()
        runtime.close()


def _wave_fixture(tmp_path, count):
    now = [datetime(2026, 9, 14, 8, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    coordinator = RuntimeCoordinator(runtime, journal)
    journal.put_task(
        "sweep",
        "MU",
        "SWEEP",
        {
            "day": "2026-09-14",
            "cutoff": now[0].isoformat(),
            "closed_cycle_id": "2026-09-13",
        },
    )
    journal.set("sweep_roster", "sweep", {"source_tasks": []})
    members = []
    for index in range(count):
        identity = f"case-{index:02}"
        journal.put_task(
            identity,
            "MU",
            "CASE",
            {
                "mode": "CLOSED",
                "sweep_id": "sweep",
                "stream_offset": index + 1,
            },
            max_failures=1,
        )
        members.append(identity)
    journal.set("sweep_members", "sweep", members)
    return runtime, journal, coordinator, members


def test_closed_sweep_processes_twenty_member_waves_before_starting_next_w1(tmp_path, monkeypatch):
    runtime, journal, coordinator, members = _wave_fixture(tmp_path, 45)
    calls = []

    def execute_case(task, *, phase="ALL"):
        calls.append((phase, task["id"]))
        if phase == "ALL":
            journal.finish(task)

    monkeypatch.setattr(coordinator, "_case", execute_case)
    try:
        coordinator._sweep(journal.claim("sweep"))
        first_next_wave = calls.index(("W1", members[20]))
        assert calls[:20] == [("W1", identity) for identity in members[:20]]
        assert calls[20:first_next_wave] == [("ALL", identity) for identity in members[:20]]
        assert [phase for phase, identity in calls if identity in members[20:40]][:20] == [
            "W1"
        ] * 20
        receipt = journal.get_task("sweep")["receipt"]
        assert receipt["wave_size"] == 20
        assert receipt["total_waves"] == 3
        assert receipt["completed_waves"] == 3
        assert receipt["wave_phase"] == "COMPLETE"
    finally:
        coordinator.close()
        runtime.close()


def test_closed_sweep_wave_isolates_w1_failure_and_resumes_from_w2_checkpoint(
    tmp_path, monkeypatch
):
    runtime, journal, coordinator, members = _wave_fixture(tmp_path, 20)
    calls = []

    def fail_one_case(task, *, phase="ALL"):
        calls.append((phase, task["id"]))
        if phase == "W1" and task["id"] == members[5]:
            raise RuntimeError("fixture W1 failure")
        if phase == "ALL":
            journal.finish(task)

    monkeypatch.setattr(coordinator, "_case", fail_one_case)
    try:
        coordinator._sweep(journal.claim("sweep"))
        assert ("ALL", members[5]) not in calls
        assert ("ALL", members[19]) in calls
        assert journal.get_task(members[5])["status"] == "FAILED"
        assert journal.get_task("sweep")["receipt"]["wave_w1_failed"] == 1
    finally:
        coordinator.close()
        runtime.close()

    runtime, journal, coordinator, members = _wave_fixture(tmp_path / "resume", 20)
    resumed_calls = []
    parent = journal.claim("sweep")
    for identity in members[:10]:
        journal.finish(journal.claim(identity))
    journal.checkpoint(
        parent,
        wave_size=20,
        total_waves=1,
        total_members=20,
        wave_index=1,
        wave_phase="W2",
        member_index=10,
        wave_w1_failed_members=[],
        wave_w2_failed_members=[],
        wave_w2_skipped_members=[],
    )

    def resume_case(task, *, phase="ALL"):
        resumed_calls.append((phase, task["id"]))
        journal.finish(task)

    monkeypatch.setattr(coordinator, "_case", resume_case)
    try:
        coordinator._sweep(parent)
        assert resumed_calls == [("ALL", identity) for identity in members[10:]]
    finally:
        coordinator.close()
        runtime.close()


def test_new_case_uses_new_prompt_but_old_case_and_cross_day_facts_keep_their_pin(tmp_path):
    from dataclasses import replace

    now = [datetime(2026, 9, 6, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        source = _source()
        old = runtime.execute_message(source, mode="CLOSED", phase="W1")
        original_core = old.frozen_inputs["prompts"]["core"]
        runtime.execution_bundles.publish(replace(runtime.prompts, core="NEW PROMPT REVISION"))
        now[0] += timedelta(days=1)
        new = runtime.execute_message(
            source.model_copy(update={"source_message_id": "new"}), mode="CLOSED", phase="W1"
        )
        assert new.frozen_inputs["prompts"]["core"] == "NEW PROMPT REVISION"
        assert any(
            item["trading_date"] == old.trading_date.isoformat()
            for item in new.frozen_inputs["provisional"]
        )
        runtime.execute_message(source)
        frozen_round = journal.get("round_inputs", f"{old.case_id}:W2:R1")
        assert frozen_round["instructions"].startswith(original_core)
        journal.set("visibility", "MU", {"day": new.trading_date.isoformat()})
        third = runtime.execute_message(
            source.model_copy(update={"source_message_id": "third"}), mode="CLOSED", phase="W1"
        )
        assert all(
            item["trading_date"] == new.trading_date.isoformat()
            for item in third.frozen_inputs["provisional"]
        )
        identifiers = [item["provisional_event_id"] for item in third.frozen_inputs["provisional"]]
        assert len(identifiers) == len(set(identifiers))
    finally:
        runtime.close()
