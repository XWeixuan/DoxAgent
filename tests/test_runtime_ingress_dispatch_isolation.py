from threading import Event
from types import SimpleNamespace

import pytest

from doxagent.runtime_scheduler.v2 import V2Scheduler


def test_slow_recovery_does_not_block_ingress_or_accumulate_dispatchers():
    scheduler = V2Scheduler.__new__(V2Scheduler)
    started, release = Event(), Event()
    calls = []

    def slow():
        calls.append("dispatch")
        started.set()
        release.wait(5)

    scheduler._recover_existing_once = slow
    scheduler.initialization_control_path = None
    scheduler._lifetime_heartbeat = True
    scheduler._run_admitted_once = lambda *a, **kw: calls.append("ingress") or []
    try:
        assert scheduler.run_due_once(include_detail=False) == []
        assert started.wait(1)
        for _ in range(10):
            assert scheduler.run_due_once(include_detail=False) == []
        assert calls.count("dispatch") == 1
        assert calls.count("ingress") == 11
    finally:
        release.set()
        scheduler._recovery_thread.join(2)
    scheduler._should_stop = lambda: True
    scheduler._start_existing_recovery()
    assert calls.count("dispatch") == 1


@pytest.mark.asyncio
async def test_coordinator_stream_acceptance_commits_without_synchronous_dispatch(tmp_path):
    from doxagent.message_bus_v2.schema import utc_now
    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.runtime_scheduler import InMemoryRuntimeSchedulerRepository
    from tests.fixtures.message_bus_v2 import _AcceptingRuntimeV2, _bus, _input

    now = utc_now()
    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    runtime = _AcceptingRuntimeV2()
    runtime.input_snapshot_loader = lambda ticker: SimpleNamespace(
        activation_revision_id="revision", index=object(), projection=object())
    runtime.journal = RuntimeJournal(tmp_path / "runtime.db")
    coordinator = object.__new__(RuntimeCoordinator)
    coordinator.journal = runtime.journal
    seen = []
    coordinator.reconcile = lambda ticker: None
    coordinator.accept_stream = lambda item: seen.append(item.item.stream_item_id)

    def forbidden(_):
        raise AssertionError("ingress must not wait for history/model dispatch")

    coordinator.tick = forbidden
    runtime.coordinator = coordinator
    scheduler = V2Scheduler(InMemoryRuntimeSchedulerRepository(), runtime_v2_service=runtime,
                            message_bus_v2_service=bus, message_bus_v2_enabled=True)
    scheduler.admit_activation("MU", "revision", now=now)
    binding = repo.get_binding("MU:benzinga_news")
    await bus.accept_message(source=repo.get_source(binding.source_id), binding=binding,
                             message=_input("fresh", published_at=now), bootstrap=False)
    scheduler.tick_ticker("MU", now=now, include_detail=False)
    assert len(seen) == 1
    assert bus.pending_stream("persistent_runtime_v2", "MU") == []
    assert runtime.journal.get("inbox_highwater", "MU") == 1
