import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.event_library.repository import EventLibraryRepository
from doxagent.persistent_runtime_v2.fencing import write_scope
from doxagent.persistent_runtime_v2.journal import LeaseLost
from doxagent.persistent_runtime_v2.maintenance import RuntimeMaintenance
from doxagent.persistent_runtime_v2.providers import RuntimeInputSnapshot
from doxagent.persistent_runtime_v2.selection import SelectionResult, WeekendSelection
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.schema import NodeSpec
from tests.test_persistent_runtime_v2 import _source
from tests.test_runtime_orchestration_execution import runtime_at


def test_late_task_cannot_write_case_or_policy(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        case = runtime.execute_message(_source())
        journal.put_task("task", "MU", "CASE", {})
        old = journal.claim("task", seconds=1)
        now[0] += timedelta(seconds=2)
        assert journal.claim("task")
        with write_scope("task", old, journal.clock), pytest.raises(LeaseLost):
            runtime.repository.save_case(case)
        assert not runtime.repository.list_consumed_policy_revisions("MU")
    finally:
        runtime.close()


def maintenance_fixture(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    settings = DoxAgentSettings(
        _env_file=None,
        ticker_initialization_control_path=str(tmp_path / "control.db"),
        persistent_runtime_v2_sqlite_path=str(journal.path),
        event_library_root=str(tmp_path / "events"),
    )
    events = EventLibraryRepository(tmp_path / "events/US/MU/event_library.sqlite3")
    events.ensure_empty_publication("MU")
    control = InitializationRepository(settings.ticker_initialization_control_path)
    control.submit("MU", now[0], [NodeSpec(key="activation", block="ACTIVATION")])
    lease = control.claim("test")
    control.stage_revision(
        lease,
        "base",
        {
            "document1": {"run_id": "d1"},
            "document2": {"run_id": "d2"},
            "event_library": {"version": 1},
            "document3": {"version": 1},
            "monitoring_configuration": {"initialization_id": "config"},
        },
    )
    control.activate(lease, "base")
    journal.put_task(
        "maintain",
        "MU",
        "MAINTENANCE",
        {"day": "2026-09-08", "cutoff": now[0].isoformat(), "scope": "DAILY"},
    )
    return runtime, journal, settings, control


def test_o2_receipt_survives_o3_failure_and_activation_crash(tmp_path):
    runtime, journal, settings, control = maintenance_fixture(tmp_path)
    calls = {"o2": 0, "o3": 0}

    class O2:
        async def run(self, **kwargs):
            calls["o2"] += 1
            return None, None, None, {}

    class O3:
        async def maintain(self, **kwargs):
            calls["o3"] += 1
            if calls["o3"] == 1:
                raise ValueError("isolated maintenance failure")
            return SimpleNamespace(status="NOOP", policy_set_version=1)

    maintenance = RuntimeMaintenance(
        settings,
        runtime,
        journal,
        worker_factory=lambda: SimpleNamespace(),
        o2_factory=lambda *_: O2(),
        o3_factory=lambda *_: O3(),
    )
    task = journal.claim("maintain")
    try:
        with pytest.raises(ValueError, match="isolated"):
            asyncio.run(maintenance(task))
        assert control.active_revision("MU")["revision_id"] == "base"
        assert task["receipt"]["o2"]["version"] == 1
        result = asyncio.run(maintenance(task))
        assert result["revision_id"] != "base"
        assert calls == {"o2": 1, "o3": 2}
        # Crash after control commit but before durable task completion.
        assert asyncio.run(maintenance(task))["reconciled"]
        assert calls == {"o2": 1, "o3": 2}
        assert control.active_revision("MU")["artifacts"]["monitoring_configuration"] == {
            "initialization_id": "config"
        }
    finally:
        runtime.close()


def test_candidate_selection_claims_only_winner_and_closes_others(tmp_path):
    now = [datetime(2026, 9, 6, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        for identity in ("a", "b"):
            runtime.execute_message(
                _source().model_copy(update={"source_message_id": identity}),
                mode="CLOSED",
                closed_cycle_id="2026-09-05",
            )
        runtime.process_pending_effects()
        candidates = journal.values("candidates")
        assert len(candidates) == 2
        now[0] = datetime(2026, 9, 8, 8, tzinfo=UTC)
        runtime.input_snapshot_loader = lambda ticker: RuntimeInputSnapshot(
            runtime.known_events.current_index(ticker), runtime.policies.current_projection(ticker)
        )
        journal.put_task("selection", "MU", "SELECTION", {"day": "2026-09-08", "sweep_id": "final"})
        journal.set(
            "selection_snapshot",
            "selection",
            {"candidate_ids": [item["candidate_id"] for item in candidates]},
        )

        async def judge(payload):
            return SelectionResult(
                selection_id="selection",
                candidate_id=candidates[0]["candidate_id"],
                reason="one winner",
            )

        selection = WeekendSelection(None, runtime, journal, judge=judge)
        task = journal.claim("selection")
        asyncio.run(selection(task))
        asyncio.run(selection(task))
        assert {item["status"] for item in journal.values("candidates")} == {
            "RELEASED",
            "NOT_SELECTED",
        }
        assert len(runtime.repository.list_consumed_policy_revisions("MU")) == 1
        assert len(journal.values("trade_intents")) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("expired", [False, True])
def test_zero_selection_or_expiry_closes_snapshot_without_policy_claim(tmp_path, expired):
    now = [datetime(2026, 9, 6, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    try:
        runtime.execute_message(_source(), mode="CLOSED", closed_cycle_id="weekend")
        runtime.process_pending_effects()
        candidate = journal.values("candidates")[0]
        now[0] = datetime(2026, 9, 9 if expired else 8, 12, tzinfo=UTC)
        runtime.input_snapshot_loader = lambda ticker: RuntimeInputSnapshot(
            runtime.known_events.current_index(ticker), runtime.policies.current_projection(ticker)
        )
        journal.put_task("zero", "MU", "SELECTION", {"day": "2026-09-08", "sweep_id": "final"})
        journal.set("selection_snapshot", "zero", {"candidate_ids": [candidate["candidate_id"]]})

        async def judge(payload):
            assert not expired
            return SelectionResult(
                selection_id="zero", candidate_id=None, reason="no suitable trade"
            )

        asyncio.run(WeekendSelection(None, runtime, journal, judge=judge)(journal.claim("zero")))
        assert journal.values("candidates")[0]["status"] == (
            "EXPIRED" if expired else "NOT_SELECTED"
        )
        assert not runtime.repository.list_consumed_policy_revisions("MU")
        assert not journal.values("trade_intents")
    finally:
        runtime.close()


def test_stale_maintenance_cas_preserves_newer_activation(tmp_path):
    runtime, _, _, control = maintenance_fixture(tmp_path)
    try:
        base = control.active_revision("MU")
        control.activate_runtime_bundle(
            ticker="MU",
            identity="newer",
            base=base,
            event_ref={"version": 1},
            policy_ref={"version": 2},
            metadata={"operator": "manual"},
        )
        with pytest.raises(Exception, match="superseded"):
            control.activate_runtime_bundle(
                ticker="MU",
                identity="stale",
                base=base,
                event_ref={"version": 2},
                policy_ref={"version": 3},
                metadata={},
            )
        assert control.active_revision("MU")["revision_id"] == "newer"
    finally:
        runtime.close()
