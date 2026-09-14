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

        async def poll(*args, **kwargs):
            calls.append(args)
            await gate.wait()
            return SimpleNamespace(
                error_code=None,
                enrichment_job_ids=[],
                next_checkpoint={},
                window_done=True,
                window_coverage="UNKNOWN",
            )

        scheduler = SimpleNamespace(
            repository=bus,
            service=service,
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


def test_pending_sweep_does_not_block_realtime_poll(tmp_path):
    async def scenario():
        now = [datetime(2026, 9, 14, 6, 1, tzinfo=UTC)]
        journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
        bus, service = _bus(tmp_path / "bus.db")
        service.start_ticker("MU")
        binding = bus.list_bindings(ticker="MU", active_only=True)[0]
        source = bus.get_source(binding.source_id)
        calls = []

        async def poll(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(error_code=None)

        scheduler = SimpleNamespace(
            repository=bus,
            service=service,
            _poll=poll,
            _eligible_bindings=lambda *args, **kwargs: [(source, binding)],
            _initialize_due_slots=lambda *args: None,
            _update_capacity_alerts=lambda *args: None,
        )
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
        owner = BusOrchestration(journal)
        await owner.run_once(scheduler)
        await asyncio.gather(*owner._inflight.values())
        assert len(calls) == 1

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
        assert journal.get_task(identity) is None
        assert journal.get("message_admission_skips", identity)["reason"] == "EXPIRED_PUBLICATION"
        assert journal.values("trade_intents") == []
        assert runtime.repository.list_consumed_policy_revisions("MU") == set()
    finally:
        coordinator.close()
        runtime.close()


def test_sweep_waits_for_owned_jobs_across_restart(tmp_path):
    async def scenario():
        now = [datetime(2026, 9, 13, 6, tzinfo=UTC)]
        journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
        bus, service = _bus(tmp_path / "bus.db")
        service.start_ticker("MU")
        binding = bus.list_bindings(ticker="MU", active_only=True)[0]
        source = bus.get_source(binding.source_id)
        journal.put_task(
            "source",
            "MU",
            "SOURCE_SWEEP",
            {
                "cutoff": now[0].isoformat(),
                "binding": binding.model_dump(mode="json"),
                "source": source.model_dump(mode="json"),
            },
        )
        pending = ["job-1", "job-2"]
        calls = []

        async def poll(*args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                error_code=None,
                enrichment_job_ids=list(pending),
                next_checkpoint={},
                window_done=True,
                window_coverage="COMPLETE",
            )

        scheduler = SimpleNamespace(
            _poll=poll,
            service=service,
            repository=SimpleNamespace(
                pending_enrichment_ids=lambda ids: [x for x in ids if x in pending],
                latest_stream_offset=lambda ticker: 12,
            ),
        )
        await BusOrchestration(journal)._source(scheduler, journal.claim("source"))
        assert journal.get_task("source")["status"] == "PENDING"
        assert "stream_highwater" not in journal.get_task("source")["receipt"]
        pending.clear()
        now[0] += timedelta(seconds=6)
        await BusOrchestration(journal)._source(scheduler, journal.claim("source"))
        task = journal.get_task("source")
        assert task["status"] == "SUCCEEDED"
        assert task["receipt"]["stream_highwater"] == 12
        assert len(calls) == 1
        assert task["receipt"]["settlement"] == "INTAKE_TERMINAL"

    asyncio.run(scenario())


def test_semantic_weekend_window_does_not_change_trading_metrics():
    import pytest

    from doxagent.v2_read.calendar import PageCalendar

    calendar = PageCalendar()
    now = datetime(2026, 9, 13, 7, tzinfo=UTC)
    current = calendar.period("CURRENT_TRADING_DAY", now, semantic=True)
    assert current["current"]["trading_days"] == ["2026-09-13"]
    assert current["current"]["membership"] == "LISTED_SEMANTIC_DAYS"
    recent = calendar.period("TRADING_DAYS_7", now, semantic=True)
    assert recent["current"]["trading_days"] == [f"2026-09-{d:02}" for d in range(7, 14)]
    with pytest.raises(ValueError, match="NON_TRADING_DAY"):
        calendar.period("CURRENT_TRADING_DAY", now)
    assert "2026-09-13" not in calendar.period("TRADING_DAYS_7", now)["current"]["trading_days"]
