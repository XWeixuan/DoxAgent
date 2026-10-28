from datetime import timedelta
from types import SimpleNamespace
import sqlite3

from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
from doxagent.message_bus_v2.collection_plane.config import Configuration
from doxagent.message_bus_v2.collection_plane.worker import Collector
from doxagent.message_bus_v2.collection_plane.bridge import CaptureBridge
from doxagent.message_bus_v2.schema import PollResult, utc_now
from doxagent.message_bus_v2.poll_trace import request_started
from tests.test_content_enrichment_hub import _setup, _message


def setup_plane(tmp_path):
    repo, bus, source, binding = _setup(tmp_path)
    source = source.model_copy(
        update={
            "scheduler_constraints": source.scheduler_constraints.model_copy(
                update={"minimum_request_gap_seconds": 0}
            )
        }
    )
    cfg = Configuration(
        sources={source.source_id: source},
        bindings={binding.binding_id: binding},
        seeds={
            binding.binding_id: repo.get_poll_state(binding).model_copy(
                update={"bootstrap_complete": True}
            )
        },
        realtime=True,
    )
    store = AcquisitionStore(tmp_path / "acquisition.sqlite3", initialize=True)
    message = _message(123)

    class Adapter:
        calls = 0

        async def poll(self, context):
            async with context.request_permit():
                self.calls += 1
                event = request_started("GET", "https://example.com/list",admitted=True)
                event.update(status_code=200, response_received_at=utc_now().isoformat())
            return PollResult(messages=[message])

    adapter = Adapter()
    adapters = SimpleNamespace(resolve=lambda *a, **kw: adapter)
    worker = Collector(
        SimpleNamespace(message_bus_v2_sqlite_path=str(repo.path)),
        adapters,
        store=store,
        config_loader=lambda *a, **kw: cfg,
    )
    return repo, bus, worker, adapter, store


def test_production_switch_is_loaded_from_the_compose_environment(monkeypatch):
    from doxagent.settings import DoxAgentSettings
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED','true')
    assert DoxAgentSettings(_env_file=None).message_bus_v2_collection_plane_enabled


async def test_physical_units_have_distinct_stable_phases_without_losing_checkpoints(tmp_path):
    _, _, worker, _, _ = setup_plane(tmp_path)
    cfg = worker.config_loader()
    binding = next(iter(cfg.bindings.values()))
    second = binding.model_copy(update={'binding_id': 'SECOND:' + binding.source_id, 'ticker': 'MU'})
    cfg.bindings[second.binding_id] = second
    await worker.refresh(seeds=True)
    assert len(worker.desired) == 2
    phases = {key: value['state']['target_due_at'] for key, value in worker.states.items()}
    assert len(set(phases.values())) == 2
    first_key = next(iter(worker.states))
    worker.states[first_key]['state']['checkpoint'] = {'anchor': 'keep'}
    await worker.refresh()
    assert phases == {key: value['state']['target_due_at'] for key, value in worker.states.items()}
    # Repair the original synchronized phase once, retaining business cursor facts.
    worker.states[first_key]['state'].pop('schedule_phase_version')
    await worker.refresh()
    assert worker.states[first_key]['state']['checkpoint'] == {'anchor': 'keep'}
    assert worker.states[first_key]['state']['schedule_phase_version'] == 2


def test_real_configuration_loader_is_read_only_under_a_business_writer(tmp_path):
    from doxagent.message_bus_v2.collection_plane.config import load
    repo,bus,worker,adapter,store=setup_plane(tmp_path)
    settings=SimpleNamespace(message_bus_v2_sqlite_path=repo.path,
        persistent_runtime_v2_sqlite_path=tmp_path/'absent-runtime.sqlite3')
    lock=sqlite3.connect(repo.path,isolation_level=None);lock.execute('BEGIN IMMEDIATE')
    try:
        configuration=load(settings,seeds=True)
        assert configuration.bindings and configuration.sources and configuration.seeds
    finally:lock.rollback();lock.close()


async def test_acquisition_does_not_wait_for_business_writer_and_repeated_lists_are_thin(tmp_path):
    repo, bus, worker, adapter, store = setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    args = next(iter(worker.desired.values()))
    lock = sqlite3.connect(repo.path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        import asyncio

        await asyncio.wait_for(worker.acquire(*args), 1)
        await asyncio.wait_for(worker.acquire(*args), 1)
    finally:
        lock.rollback()
        lock.close()
    assert adapter.calls == 2
    assert len(store.pending()) == 1
    with store.connect() as db:
        cycles = db.execute("SELECT * FROM cycles WHERE status!='SCHEDULE'").fetchall()
        assert len(cycles) == 2
        assert all(r["status"] == "CAPTURED" for r in cycles)


async def test_bridge_crash_between_business_commit_and_ack_is_idempotent(tmp_path):
    repo, bus, worker, adapter, store = setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    await worker.acquire(*next(iter(worker.desired.values())))
    row = store.pending()[0]
    bridge = CaptureBridge(bus, store=store)
    prepared = bridge.prepare(row)
    bridge.commit(row, prepared)
    # Process lost after the business commit, before collector acknowledgement.
    bridge.commit(row, prepared)
    await bridge.once()
    assert store.pending() == []
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM collection_acceptances").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT count(*) FROM poll_captures WHERE capture_id=?", (row["capture_id"],)
            ).fetchone()[0]
            == 0
        )
        assert db.execute("SELECT count(*) FROM live_articles").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM live_deliveries").fetchone()[0] == 1


def test_existing_repository_opens_without_writing_or_ddl_under_writer_lock(tmp_path):
    repo, _, _, _ = _setup(tmp_path)
    lock = sqlite3.connect(repo.path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        from doxagent.message_bus_v2.repository import MessageBusV2Repository

        other = MessageBusV2Repository(repo.path)
        assert other.initialize is False
        assert other.list_sources()
    finally:
        lock.rollback()
        lock.close()


async def test_changed_input_survives_ack_and_restart_without_reemitting_old_list(tmp_path):
    import json

    repo, bus, worker, adapter, store = setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    args = next(iter(worker.desired.values()))
    await worker.acquire(*args)
    first = store.pending()[0]
    store.acknowledge(first["capture_id"])
    resumed = Collector(
        worker.settings, worker.adapters, store=store, config_loader=worker.config_loader
    )
    await resumed.refresh(seeds=True)
    await resumed.acquire(*next(iter(resumed.desired.values())))
    assert not store.pending()
    payload = json.loads(first["payload"])
    payload["result"]["messages"][0]["title"] = "Material change in investment guidance"
    payload["input_keys"] = ["a-new-semantic-version"]
    store.capture_page(*args[:3], payload, "changed", utc_now().isoformat(), "changed-run", [])
    assert len(store.pending()) == 1


async def test_disabled_unit_cannot_be_reactivated_by_inflight_finish(tmp_path):
    repo, bus, worker, adapter, store = setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    source, targets, unit, options = next(iter(worker.desired.values()))
    store.disable_unit(targets[0].binding_id, unit)
    row = worker.states[(targets[0].binding_id, unit)]
    store.finish(
        source,
        targets,
        unit,
        row["state"],
        row["generation"],
        run_id="late",
        due=utc_now(),
        dispatched=utc_now(),
        requests=[],
        valid=False,
    )
    assert store.states()[(targets[0].binding_id, unit)]["required"] == -1


def test_unknown_date_normalized_once_in_acquisition_store(tmp_path):
    *_, store = setup_plane(tmp_path)
    m = _message(901).model_copy(update={"publication_time_basis": "UNKNOWN_FIRST_SEEN"})
    first = store.normalize_unknown("source", [m], utc_now().isoformat())[0]
    later = store.normalize_unknown(
        "source",
        [m.model_copy(update={"published_at": utc_now() + timedelta(days=1)})],
        utc_now().isoformat(),
    )[0]
    assert first.published_at == later.published_at


def test_schema_install_is_explicit_and_idempotent(tmp_path, monkeypatch):
    from doxagent.message_bus_v2.collection_plane.install import install

    repo, *_ = setup_plane(tmp_path)
    monkeypatch.setenv("DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED", "true")
    settings = SimpleNamespace(message_bus_v2_sqlite_path=repo.path)
    assert install(settings)["schema"] == 2
    assert install(settings)["schema"] == 2
    with repo._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='v2_capture_poll_runs_INSERT'"
        ).fetchone()


async def test_registry_latency_does_not_block_event_loop_and_cancel_keeps_ownership():
    import asyncio, threading
    from doxagent.site_strategy.storage import registry_io

    entered = threading.Event()
    release = threading.Event()
    events = []

    def slow_write():
        entered.set()
        release.wait(2)
        events.append("commit")

    task = asyncio.create_task(registry_io(slow_write))
    await asyncio.to_thread(entered.wait, 1)
    await asyncio.wait_for(asyncio.sleep(0.01), 0.2)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()  # Keep identity gate until the owner write really ends.
    release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert events == ["commit"]


async def test_validated_page_survives_later_failure_and_has_live_cadence_proof(tmp_path,monkeypatch):
    import json
    from doxagent.message_bus_v2.collection_plane.observability import window
    repo,bus,worker,_,store=setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    args=next(iter(worker.desired.values()))
    epoch=utc_now()-timedelta(seconds=1)
    source,targets,unit,options=args
    state=worker.states[(targets[0].binding_id,unit)]['state']
    state['target_due_at']=epoch.isoformat()
    with store.connect(write=True) as db:db.execute("DELETE FROM cycles WHERE status='SCHEDULE'")
    store.schedule(source,targets[0],unit,epoch,True,epoch)
    class PartialAdapter:
        async def poll(self,context):
            event=request_started('GET','https://example.test/list',admitted=True)
            event.update(status_code=200,response_received_at=utc_now().isoformat())
            await context.on_page([_message(314)])
            raise TimeoutError('archive failed after a valid head')
    worker.adapters=SimpleNamespace(resolve=lambda *a,**kw:PartialAdapter())
    await worker.acquire(*args)
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED','true')
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH',str(repo.path))
    result,receipts=window(targets[0].ticker,epoch,utc_now(),targets[0].binding_id)
    assert result['fresh_cycles']==1
    assert len(receipts)==1
    assert store.pending()
    with store.connect() as db:
        proof=json.loads(db.execute("SELECT data_json FROM cycles WHERE status='CAPTURED'").fetchone()[0])
    assert proof['list_validated'] and proof['terminal_error']=='TimeoutError'


def test_retired_transport_headers_do_not_skip_business_and_archive_remains_complete(tmp_path,monkeypatch):
    from doxagent.v2_read.outbox import SourceOutbox
    repo,bus,worker,adapter,store=setup_plane(tmp_path)
    source=next(iter(worker.config.sources.values()),None) or repo.list_sources()[0]
    binding=repo.list_bindings()[0]
    outbox=SourceOutbox(repo.path,'bus');outbox.migrate()
    before=outbox.head()
    repo.polling.start('old-technical',source,binding,utc_now(),utc_now())
    repo.save_source(source.model_copy(update={'version':source.version+1}))
    business=outbox.head()
    repo.polling.start('last-technical',source,binding,utc_now(),utc_now())
    end=outbox.head()
    monkeypatch.setenv('DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED','true')
    assert outbox.coordinates(before)==[business,end]
    assert outbox.event(end)['row']=={}
    assert 'payload' in outbox.event(end,decode=False)


async def test_shared_capture_bridge_creates_one_article_and_target_without_run_relay(tmp_path):
    from doxagent.message_bus_v2.schema import AcquisitionMode
    repo,bus,worker,adapter,store=setup_plane(tmp_path)
    source=next(iter(worker.config_loader(None).sources.values())).model_copy(update={'acquisition_mode':AcquisitionMode.BY_DISTRIBUTION})
    repo.save_source(source)
    cfg=worker.config_loader(None);cfg.sources[source.source_id]=source
    async def poll_shared(context):
        async with context.request_permit():
            event=request_started('GET','https://example.test/shared',admitted=True)
            event.update(status_code=200,response_received_at=utc_now().isoformat())
        return PollResult(messages=[_message(610)])
    adapter.poll_shared=poll_shared
    await worker.refresh(seeds=True)
    await worker.acquire(*next(iter(worker.desired.values())))
    bridge=CaptureBridge(bus,store=store)
    assert await bridge.once()==1
    assert repo.list_enrichment_jobs()==[]
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0]==1
        target=db.execute('SELECT ticker,binding_id,state FROM live_deliveries').fetchone()
        assert target['ticker']=='MU' and target['state']=='WAIT_BODY'
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='distribution_runs'").fetchone()


async def test_collector_passes_real_due_and_demand_through_site_client(tmp_path):
    import httpx,json
    from tests.test_site_strategy_client import _client_with_transport,_access_request
    repo,bus,worker,adapter,store=setup_plane(tmp_path)
    wire=[]
    def respond(request):
        value=json.loads(request.read());wire.append(value)
        return httpx.Response(200,json={'request_id':value['request_id'],'operation_id':value['operation_id'],
            'disposition':'SUCCESS','site_id':'trendforce','runtime_key':'trendforce','strategy_revision':1})
    client=await _client_with_transport(httpx.MockTransport(respond))
    async def poll(context):
        await client.execute(_access_request())
        return PollResult()
    adapter.poll=poll
    try:
        await worker.refresh(seeds=True)
        await worker.acquire(*next(iter(worker.desired.values())))
        assert len(wire)==1
        assert wire[0]['recipe_parameters']['_poll_due_at']
        assert wire[0]['recipe_parameters']['_poll_rate']>0
        assert wire[0]['work_class']=='foreground'
    finally:await client.close()


async def test_acquisition_commit_contention_does_not_repeat_the_network(tmp_path):
    import asyncio
    repo,bus,worker,adapter,store=setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    lock=sqlite3.connect(store.path,isolation_level=None);lock.execute('BEGIN IMMEDIATE')
    task=asyncio.create_task(worker.acquire(*next(iter(worker.desired.values()))))
    try:
        await asyncio.sleep(.1)
        assert adapter.calls==1  # Request starts before its thin start record commits.
        assert not task.done()
    finally:lock.rollback();lock.close()
    await asyncio.wait_for(task,3)
    assert adapter.calls==1 and len(store.pending())==1


async def test_small_ledger_writes_serialize_without_losing_cycles_and_restart_fences_old_runs(tmp_path):
    import asyncio
    repo,bus,worker,adapter,store=setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    source,targets,unit,options=next(iter(worker.desired.values()))
    await asyncio.gather(*(asyncio.to_thread(store.start,source,targets,unit,'run-'+str(i),utc_now(),utc_now(),60) for i in range(100)))
    assert store.recover_previous_owner()==100
    assert store.recover_previous_owner()==0
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM cycles WHERE status='ABANDONED_GENERATION'").fetchone()[0]==100


async def test_failed_terminal_commit_does_not_turn_the_unit_into_a_100ms_refetch_loop(tmp_path,monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    repo,bus,worker,adapter,store=setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    for row in worker.states.values():row['state']['next_dispatch_at']=(utc_now()-timedelta(seconds=1)).isoformat()
    monkeypatch.setattr(worker,'refresh',AsyncMock())
    def failed(*a,**kw):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(store,'finish',failed)
    stop=asyncio.Event();run=asyncio.create_task(worker.run(stop))
    try:
        await asyncio.sleep(.45)
        assert adapter.calls==1
    finally:stop.set();await run
