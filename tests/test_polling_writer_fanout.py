import asyncio
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import PollResult, UpdateActor, utc_now
from tests.fixtures.message_bus_v2 import _bus
from tests.test_message_bus_distribution import _terms


async def test_dispatch_heartbeat_lifecycle_does_not_need_bus_writer(tmp_path):
    import threading

    repo, bus = _bus(tmp_path / 'bus.db')
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace())
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        assert release.wait(3)
    async def cycle():
        return []
    scheduler._run_admitted_once = cycle
    writer = asyncio.create_task(bus.storage.call(blocked))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        assert await asyncio.wait_for(scheduler.run_once(), .5) == []
    finally:
        release.set()
        await writer


@pytest.mark.parametrize("count", [2, 20])
async def test_shared_fetch_writer_queue_visits_do_not_scale_with_tickers(tmp_path, count):
    repo, bus = _bus(tmp_path / 'bus.db')
    source = bus.update_source('ctee_semiconductor', {'enabled': True}, actor=UpdateActor.SYSTEM)
    terms = MonitoringTermsService(repo)
    bindings = []
    for i in range(count):
        ticker = f'T{i}'
        bus.start_ticker(ticker)
        bindings.append(bus.configure_binding(ticker=ticker, source_id=source.source_id, actor=UpdateActor.SYSTEM))
        terms.apply(_terms(ticker, 'Company'), actor='test')
    visits = []
    original = bus.storage.call
    async def write(operation, *args, **kwargs):
        visits.append(operation.__name__)
        return await original(operation, *args, **kwargs)
    bus.storage.call = write
    at_fetch = []
    class Adapter:
        async def poll_shared(self, context):
            at_fetch.extend(visits)
            return PollResult(window_done=True, window_coverage='COMPLETE')
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None))
    scheduler._long_running = True
    try:
        await scheduler._poll_distribution(source, bindings, utc_now())
        assert at_fetch == ['start_runs', '_prepare_distribution_run', 'claim_run']
        assert visits.count('finish_runs') == 1
        with repo._connect() as db:
            rows = db.execute("SELECT status FROM poll_runs WHERE unit='SHARED'").fetchall()
        assert len(rows) == count and all(r[0] == 'CAPTURED' for r in rows)
    finally:
        await scheduler.close()


def test_identical_first_seen_is_not_an_update_but_earlier_evidence_is(tmp_path):
    from datetime import timedelta

    from tests.test_content_enrichment_hub import _message

    repo, _ = _bus(tmp_path / 'bus.db')
    message = _message(123)
    with repo.transaction() as db:
        db.executescript("CREATE TABLE seen_updates(n INTEGER); INSERT INTO seen_updates VALUES(0); "
                         "CREATE TRIGGER seen_changed AFTER UPDATE ON message_first_seen "
                         "BEGIN UPDATE seen_updates SET n=n+1; END;")
    repo.resolve_first_seen_many([message], 'MU:source')
    repo.resolve_first_seen_many([message], 'MU:source')
    earlier = message.model_copy(update={'published_at': message.published_at-timedelta(minutes=1)})
    repo.resolve_first_seen_many([earlier], 'MU:source')
    with repo._connect() as db:
        assert db.execute('SELECT n FROM seen_updates').fetchone()[0] == 1
