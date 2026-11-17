import time

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
