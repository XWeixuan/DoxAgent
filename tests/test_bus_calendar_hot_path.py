import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from doxagent.message_bus_v2.schema import AcquisitionMode
from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration


def test_calendar_evaluated_once_per_tick_not_per_binding():
    async def scenario():
        now = datetime(2026, 10, 1, 11, tzinfo=UTC)
        journal = SimpleNamespace(clock=lambda: now, get=lambda *args: None,
                                  tasks=lambda **kwargs: [])
        owner = BusOrchestration(journal)
        calls = []
        owner.calendar = SimpleNamespace(is_session=lambda day: calls.append(day) or True)
        source = SimpleNamespace(acquisition_mode=AcquisitionMode.BY_TICKER)
        bindings = [(source, SimpleNamespace(ticker='MU', binding_id=str(i)))
                    for i in range(100)]
        scheduler = SimpleNamespace(
            _eligible_bindings=lambda *args, **kwargs: bindings,
            _initialize_due_slots=lambda *args: None,
            _update_capacity_alerts=lambda *args: None,
            repository=SimpleNamespace(get_poll_state=lambda binding:
                                       SimpleNamespace(next_dispatch_at=now + timedelta(days=1))),
            distribution_worker=None,
        )
        await owner.run_once(scheduler)
        assert len(calls) == 2
        await owner.run_once(scheduler)
        assert len(calls) == 4  # Re-read overrides on the next tick; no stale global cache.
    asyncio.run(scenario())


def test_calendar_failure_keeps_ticker_fallback_and_records_one_gap_per_ticker():
    async def scenario():
        now = datetime(2026, 10, 1, 11, tzinfo=UTC)
        gaps = []
        journal = SimpleNamespace(
            clock=lambda: now, tasks=lambda **kwargs: [], gap=lambda *args: gaps.append(args),
            get=lambda namespace, key, default=None:
            {'mode': 'REALTIME'} if namespace == 'schedule' and key == 'MU' else default,
        )
        owner = BusOrchestration(journal)
        def unavailable(day):
            raise RuntimeError('calendar unavailable')
        owner.calendar = SimpleNamespace(is_session=unavailable)
        source = SimpleNamespace(acquisition_mode=AcquisitionMode.BY_TICKER)
        bindings = [(source, SimpleNamespace(ticker=t, binding_id=f'{t}:{i}'))
                    for t in ['MU', 'BE'] for i in range(3)]
        polled = []
        async def poll(source, binding, attempted_at):
            polled.append(binding.ticker)
        scheduler = SimpleNamespace(
            _eligible_bindings=lambda *args, **kwargs: bindings,
            _initialize_due_slots=lambda *args: None, _update_capacity_alerts=lambda *args: None,
            repository=SimpleNamespace(get_poll_state=lambda binding:
                                       SimpleNamespace(next_dispatch_at=None)),
            distribution_worker=None, _poll=poll,
        )
        await owner.run_once(scheduler)
        await asyncio.gather(*owner._inflight.values())
        assert polled == ['MU'] * 3
        assert len(gaps) == 2
    asyncio.run(scenario())
