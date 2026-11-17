import time
from pathlib import Path

import pytest

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.live import LiveDeliveryWorker, LiveQueue
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.schema import AcquisitionMode, utc_now
from tests.test_content_enrichment_hub import _message, _setup
from tests.test_cross_source_live_dedup import Extractor


@pytest.mark.parametrize("count", [1, 10, 30])
async def test_one_preparation_and_one_negative_transaction_per_article(tmp_path, count):
    repo, bus, source, binding = _setup(tmp_path)
    source = source.model_copy(update={"acquisition_mode": AcquisitionMode.MASSIVE_DISTRIBUTION})
    repo.save_source(source)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    targets=[]
    for i in range(count):
        ticker = "MU" if i==0 else f"T{i}"
        b=binding.model_copy(update={"ticker": ticker, "binding_id":ticker+":"+source.source_id})
        repo.save_binding(b)
        revision=MonitoringTermsService(repo).apply(TickerMonitoringTerms.model_validate(dict(
            ticker=ticker,expected_revision=0,
            l1_concepts=[dict(concept_id="company",expressions={"en":"Micron"})],
            l2={"en":{"groups":[{"id":"company","any":[{"literal":"UNRELATED_NEVER_MATCH"}]}]}},
            massive_l2={"en":{"groups":[{"id":"company","any":[{"literal":"UNRELATED_NEVER_MATCH"}]}]}},
            definition={"relevant":"Micron","irrelevant":"Other"})),actor="test")
        targets.append((b.binding_id,ticker,revision))
    live.accept(live.prepare_input(
        source, targets, [_message(12345)], observed_at=utc_now().isoformat(), unit="HEAD"
    ))
    hub=ContentEnrichmentHub(repo,bus,extractor=Extractor())
    try:
        await hub.run_once()
        if count == 10:
            rows=live.deliveries()
            original=live.decision
            calls=[0]
            def interrupted(row, state, **kwargs):
                calls[0]+=1
                if calls[0]==2:
                    raise RuntimeError("interrupted batch")
                return original(row, state, **kwargs)
            live.decision=interrupted
            try:
                with pytest.raises(RuntimeError,match="interrupted batch"):
                    live.decisions([(r,"IRRELEVANT",{"regex":False}) for r in rows])
            finally:
                live.decision=original
            assert len(live.deliveries())==count
        worker=LiveDeliveryWorker(bus)
        # A deadline already spent by preparation must not discard prepared targets.
        started=time.perf_counter()
        processed=await worker.run_available(max_seconds=0)
        elapsed=time.perf_counter()-started
        assert processed==count
        assert worker.work_metrics["prepared_targets"]==count
        assert worker.work_metrics["decision_commits"]==1
        with repo._connect() as db:
            row = db.execute("SELECT count(*) FROM live_deliveries WHERE state='IRRELEVANT'").fetchone()
            assert row[0] == count
        assert worker.repository.deliveries()==[]
        print({"targets":count,"seconds":elapsed,"effective_per_second":count/elapsed})
    finally:
        await hub.close()


def test_acquisition_wal_lifetime_and_full_durability(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    store=AcquisitionStore(tmp_path/"acquisition.sqlite3",initialize=True,keep_wal=True)
    try:
        with store.connection_scope():
            with store.connect(write=True) as first:
                assert first.execute("PRAGMA synchronous").fetchone()[0]==2
            with store.connect(write=True) as second:
                assert first is second
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda i:store.meta(str(i),i),range(10)))
        assert store.get_meta('9')==9
        assert not store._anchor.in_transaction
        assert Path(str(store.path)+'-wal').exists()
    finally:
        store.close()
    assert store._anchor is None


async def test_storage_execute_keeps_static_defense_caller_contract():
    from doxagent.message_bus_v2.storage_lane import StorageLane
    assert await StorageLane._execute(lambda value:value+1,(3,),{})==4
    lane=StorageLane()
    assert await lane.read(lambda:5)==5
    assert lane.metrics['thread_cpu_s']>=0


async def test_reused_publication_keeps_positive_regex_receipt(tmp_path):
    from tests.test_cross_source_live_dedup import capture, drain, input_message
    from tests.test_massive_distribution import terms
    repo,bus,source,_=_setup(tmp_path)
    bus.live_queue=LiveQueue(bus)
    pipeline=(repo,bus,Extractor())
    capture(pipeline,source.source_id,input_message(987,'https://example.test/reused'))
    await drain(pipeline)
    repo.save_source(source.model_copy(update={'acquisition_mode':AcquisitionMode.MASSIVE_DISTRIBUTION, 'version':source.version+1}))
    terms(repo)
    with repo.transaction() as db:
        db.execute("UPDATE live_deliveries SET state='PENDING',regex_result=0")
    await LiveDeliveryWorker(bus).run_available()
    with repo._connect() as db:
        row=db.execute('SELECT state,regex_result FROM live_deliveries').fetchone()
        assert tuple(row)==('PUBLISHED',1)
        assert db.execute('SELECT count(*) FROM stream_items').fetchone()[0]==1
