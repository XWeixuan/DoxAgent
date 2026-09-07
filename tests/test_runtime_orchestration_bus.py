import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from tests.test_message_bus_v2 import _bus
from tests.test_persistent_runtime_v2 import _source
from tests.test_runtime_orchestration_execution import runtime_at


def test_closed_poll_once_and_slow_source_never_blocks_loop(tmp_path):
    async def scenario():
        now = [datetime(2026, 9, 6, 6, tzinfo=UTC)]
        journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
        bus, service = _bus(tmp_path / "bus.db")
        service.start_ticker("MU")
        binding = bus.list_bindings(ticker="MU", active_only=True)[0]
        source = bus.get_source(binding.source_id)
        gate = asyncio.Event()
        calls = []

        async def poll(*args):
            calls.append(args)
            await gate.wait()
            return SimpleNamespace(error_code=None)

        scheduler = SimpleNamespace(
            repository=bus,
            _poll=poll,
            _eligible_bindings=lambda *args, **kwargs: [(source, binding)],
            _initialize_due_slots=lambda *args: None,
            _update_capacity_alerts=lambda *args: None,
        )
        owner = BusOrchestration(journal)
        await owner.run_once(scheduler)
        assert not calls  # No background continuous Poll on a closed day.
        journal.put_task("sweep", "MU", "SWEEP", {"cutoff": now[0].isoformat()})
        await asyncio.wait_for(owner.run_once(scheduler), timeout=1)
        await owner.run_once(scheduler)
        assert len(calls) == 1
        assert journal.tasks(kind="SOURCE_SWEEP")[0]["status"] == "RUNNING"
        gate.set()
        await asyncio.gather(*owner._inflight.values())
        await owner.run_once(scheduler)
        await owner.run_once(scheduler)
        assert len(calls) == 1
        assert journal.tasks(kind="SOURCE_SWEEP")[0]["status"] == "SUCCEEDED"

    asyncio.run(scenario())


def test_sweep_waits_for_inbox_highwater_and_does_not_consume_retry(tmp_path):
    now = [datetime(2026, 9, 6, 6, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    coordinator = RuntimeCoordinator(runtime, journal)
    try:
        journal.put_task(
            "sweep",
            "MU",
            "SWEEP",
            {"day": "2026-09-06", "cutoff": now[0].isoformat(), "closed_cycle_id": "2026-09-05"},
        )
        journal.put_task("source", "MU", "SOURCE_SWEEP", {})
        journal.finish(journal.claim("source"), stream_highwater=9)
        journal.set("sweep_roster", "sweep", {"source_tasks": ["source"]})
        coordinator._execute(journal.claim("sweep"))
        assert journal.get_task("sweep")["status"] == "PENDING"
        assert journal.get_task("sweep")["failures"] == 0
        assert journal.get("sweep_members", "sweep") is None
        journal.set("inbox_highwater", "MU", 9)
        now[0] += timedelta(seconds=6)
        coordinator._execute(journal.claim("sweep"))
        assert journal.get_task("sweep")["status"] == "SUCCEEDED"
        assert journal.get_task("maintain:sweep")
    finally:
        coordinator.close()
        runtime.close()


def test_old_unadmitted_backlog_cannot_release_new_day_trade(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    coordinator = RuntimeCoordinator(runtime, journal)
    try:
        source = _source()  # Frozen older bus event time.
        identity = coordinator.accept(source, stream_offset=5)
        assert coordinator.accept(source, stream_offset=5) == identity
        coordinator._execute(journal.claim(identity))
        runtime.process_pending_effects()
        assert journal.values("trade_intents")[0]["status"] == "EXPIRED_SEMANTIC_DAY"
        assert runtime.repository.list_consumed_policy_revisions("MU") == set()
        assert journal.get_task(identity)["status"] == "SUCCEEDED"
    finally:
        coordinator.close()
        runtime.close()
