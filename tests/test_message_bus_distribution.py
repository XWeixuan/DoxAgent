from __future__ import annotations

import json
from datetime import timedelta

from doxagent.message_bus_v2.admission import AdmissionContext
from doxagent.message_bus_v2.distribution import DistributionWorker
from doxagent.message_bus_v2.distribution_repository import DistributionRepository
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import PollResult, RawMessageInput, UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service


def _terms(ticker: str, literal: str) -> TickerMonitoringTerms:
    return TickerMonitoringTerms.model_validate(
        {
            "ticker": ticker,
            "expected_revision": 0,
            "l1_concepts": [
                {
                    "concept_id": "company",
                    "expressions": {
                        "en": literal,
                        "zh-Hant": literal,
                        "ko": literal,
                    },
                }
            ],
            "l2": {
                "en": {"groups": [{"id": "direct", "any": [{"literal": literal}]}]},
                "zh-Hant": {"groups": [{"id": "direct", "any": [{"literal": literal}]}]},
                "ko": {"groups": [{"id": "direct", "any": [{"literal": literal}]}]},
            },
            "definition": {"relevant": literal, "irrelevant": "unrelated"},
        }
    )


async def test_shared_article_one_body_job_independent_ticker_decisions(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repository, enrichment_queue_enabled=True)
    bus.bootstrap()
    source = bus.update_source("ctee_semiconductor", {"enabled": True}, actor=UpdateActor.SYSTEM)
    terms = MonitoringTermsService(repository)
    distribution = DistributionRepository(repository)
    bindings = []
    for ticker, literal in [("MU", "Micron"), ("NVDA", "Nvidia")]:
        bus.start_ticker(ticker)
        bindings.append(
            bus.configure_binding(
                ticker=ticker, source_id=source.source_id, actor=UpdateActor.SYSTEM
            )
        )
        assert terms.apply(_terms(ticker, literal), actor="test") == 1
    admission = AdmissionContext().model_dump(mode="json")
    roster = [(binding, 1, admission) for binding in bindings]
    run = distribution.get_or_create_run(
        work_key="test:realtime:1",
        source=source,
        mode="REALTIME",
        window_start=None,
        cutoff=None,
        roster=roster,
    )
    token = distribution.claim_run(run["run_id"])
    assert token
    message = RawMessageInput(
        external_id="ctee-1",
        title="Micron memory expands",
        body=None,
        summary="HBM investments",
        source="CTEE",
        publisher_name="CTEE",
        url="https://www.ctee.com.tw/news/20260923701847-430501",
        published_at=utc_now(),
        raw_payload={"id": 1},
    )
    job_ids = distribution.ingest(
        run["run_id"],
        token,
        source,
        [message],
        checkpoint={},
        coverage="COMPLETE",
        done=True,
        deadline_seconds=180,
        pipeline_version="body_v2.2",
    )
    assert len(job_ids) == 1
    jobs = repository.claim_enrichment_jobs(limit=5)
    assert len(jobs) == 1 and jobs[0].binding is None
    enriched = message.model_copy(update={"body": "Micron memory production rose."})
    distribution.finalize_article(jobs[0], enriched, None)
    worker = DistributionWorker(distribution, bus, terms, jev=None)
    assert await worker.run_once() == 2
    assert distribution.pending_for_run(run["run_id"], "MU") == []
    assert distribution.pending_for_run(run["run_id"], "NVDA") == []
    decisions = distribution.decisions("MU") + distribution.decisions("NVDA")
    assert {item["final_result"] for item in decisions} == {"RELEVANT", "NOT_RELEVANT"}


async def test_expired_shared_article_skips_regex_and_jev_before_delivery(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repository, enrichment_queue_enabled=True)
    bus.bootstrap()
    source = bus.update_source("ctee_semiconductor", {"enabled": True}, actor=UpdateActor.SYSTEM)
    bus.start_ticker("MU")
    binding = bus.configure_binding(
        ticker="MU", source_id=source.source_id, actor=UpdateActor.SYSTEM
    )
    terms = MonitoringTermsService(repository)
    terms.apply(_terms("MU", "Micron"), actor="test")
    distribution = DistributionRepository(repository)
    run = distribution.get_or_create_run(
        work_key="expired-realtime",
        source=source,
        mode="REALTIME",
        window_start=None,
        cutoff=None,
        roster=[(binding, 1, AdmissionContext().model_dump(mode="json"))],
    )
    token = distribution.claim_run(run["run_id"])
    assert token
    message = RawMessageInput(
        external_id="old-1",
        title="Micron memory expands",
        body="Micron memory production rose.",
        source="CTEE",
        publisher_name="CTEE",
        url="https://www.ctee.com.tw/news/old-1",
        published_at=utc_now() - timedelta(hours=2),
        raw_payload={},
    )
    distribution.ingest(
        run["run_id"], token, source, [message], checkpoint={}, coverage="COMPLETE",
        done=True, deadline_seconds=180, pipeline_version="body_v2.2",
    )
    assert distribution.summary(source.source_id)["delivery_states"] == {
        "ADMISSION_REJECTED": 1
    }
    job = repository.claim_enrichment_jobs(limit=1)[0]
    distribution.finalize_article(job, message, None)
    bus.start_ticker("NVDA")
    late_binding = bus.configure_binding(
        ticker="NVDA", source_id=source.source_id, actor=UpdateActor.SYSTEM
    )
    distribution.attach_target(
        run["run_id"], late_binding, 1, AdmissionContext().model_dump(mode="json")
    )
    assert distribution.summary(source.source_id)["delivery_states"] == {
        "ADMISSION_REJECTED": 2
    }
    # A delivery queued by an older release must also be rejected before classification.
    with repository.transaction() as db:
        db.execute(
            "UPDATE distribution_deliveries SET state='PENDING' "
            "WHERE run_id=? AND ticker='MU'",
            (run["run_id"],),
        )

    class FakeJev:
        calls = 0

        async def classify(self, message, definitions):
            self.calls += 1
            return {"MU": 1.0}

    fake_jev = FakeJev()
    worker = DistributionWorker(distribution, bus, terms, jev=fake_jev)
    assert await worker.run_once() == 1
    assert fake_jev.calls == 0
    assert distribution.decisions("MU") == []
    assert distribution.summary(source.source_id)["delivery_states"] == {
        "ADMISSION_REJECTED": 2
    }


async def test_late_target_attached_before_next_page_receives_new_observations(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repository, enrichment_queue_enabled=True)
    bus.bootstrap()
    source = bus.update_source("ctee_semiconductor", {"enabled": True}, actor=UpdateActor.SYSTEM)
    distribution = DistributionRepository(repository)
    admission = AdmissionContext().model_dump(mode="json")
    bindings = []
    for ticker in ("MU", "NVDA"):
        bus.start_ticker(ticker)
        bindings.append(
            bus.configure_binding(
                ticker=ticker, source_id=source.source_id, actor=UpdateActor.SYSTEM
            )
        )
    run = distribution.get_or_create_run(
        work_key="shared-sweep",
        source=source,
        mode="CLOSED_SWEEP",
        window_start=utc_now(),
        cutoff=utc_now(),
        roster=[(bindings[0], 1, admission)],
    )
    token = distribution.claim_run(run["run_id"])
    assert token
    distribution.attach_target(run["run_id"], bindings[1], 1, admission)
    message = RawMessageInput(
        external_id="ctee-2",
        title="Micron and Nvidia",
        summary="chip news",
        source="CTEE",
        publisher_name="CTEE",
        url="https://www.ctee.com.tw/news/20260923701848-430501",
        published_at=utc_now(),
        raw_payload={"id": 2},
    )
    distribution.ingest(
        run["run_id"],
        token,
        source,
        [message],
        checkpoint={},
        coverage="COMPLETE",
        done=True,
        deadline_seconds=180,
        pipeline_version="body_v2.2",
    )
    assert len(distribution.pending_for_run(run["run_id"], "MU")) == 1
    assert len(distribution.pending_for_run(run["run_id"], "NVDA")) == 1
    frozen_roster = json.loads(distribution.run(run["run_id"])["roster_json"])
    assert {item["binding"]["ticker"] for item in frozen_roster} == {"MU", "NVDA"}


async def test_one_and_many_subscribers_use_one_shared_network_poll(tmp_path) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repository)
    bus.bootstrap()
    source = bus.update_source("ctee_semiconductor", {"enabled": True}, actor=UpdateActor.SYSTEM)

    class Adapter:
        calls = 0

        async def poll_shared(self, context):
            self.calls += 1
            return PollResult(window_done=True, window_coverage="COMPLETE")

    adapter = Adapter()

    class Registry:
        def resolve(self, adapter_ref, *, source_version):
            return adapter

    scheduler = GlobalPollScheduler(repository, bus, Registry())
    bindings = []
    for index in range(20):
        ticker = f"TEST{index}"
        bus.start_ticker(ticker)
        bindings.append(
            bus.configure_binding(
                ticker=ticker, source_id=source.source_id, actor=UpdateActor.SYSTEM
            )
        )
        scheduler.terms.apply(_terms(ticker, ticker), actor="test")
    when = utc_now()
    await scheduler._poll_distribution(source, bindings[:1], when)
    await scheduler._poll_distribution(source, bindings, when)
    assert adapter.calls == 1
    run = scheduler.distribution.status(source.source_id)[0]
    assert run["status"] == "DONE"
    states = [repository.get_poll_state(binding) for binding in bindings]
    assert all(state.status.value == "succeeded" for state in states)
    assert all(state.last_success_at is not None for state in states)
    assert all(state.target_due_at is not None for state in states)
    assert len({state.last_attempt_at for state in states}) == 1
