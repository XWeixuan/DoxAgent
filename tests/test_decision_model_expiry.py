from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2 import live
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.schema import RawMessageInput
from tests.test_message_bus_distribution import _terms


@pytest.fixture
def decision(monkeypatch):
    clock = [datetime(2026, 11, 4, 12, tzinfo=UTC)]
    monkeypatch.setattr(live, "utc_now", lambda: clock[0])
    source = next(s for s in initial_sources() if s.source_id == "ctee_semiconductor")
    row = dict(
        article_key="article",
        ticker="MU",
        terms_revision=1,
        body_digest="body",
        first_seen_at=clock[0].isoformat(),
        first_discovered_at=clock[0].isoformat(),
        due_at=clock[0].isoformat(),
    )
    message = RawMessageInput(
        url="https://publisher.test/article",
        raw_payload={},
        title="Unrelated company news",
        body="Complete public article.",
        metadata={
            "v2_body_completion": {"succeeded": True},
            "media_enrichment": {"outcome": "FULL"},
        },
    )
    facts = (message, source, SimpleNamespace(enabled=True), (1, _terms("MU", "Micron")))
    worker = object.__new__(live.LiveDeliveryWorker)
    worker.repository = SimpleNamespace(decision=object())
    worker.bus = SimpleNamespace(
        storage=SimpleNamespace(
            call=AsyncMock(return_value=True), prepare=AsyncMock(return_value=[(row, *facts)])
        )
    )
    worker.publish = AsyncMock()
    worker.classifier = SimpleNamespace(_jev=AsyncMock(return_value=({"MU": 0.1}, {}, {})))
    worker._prepare_batch = object()
    worker.active_articles = {"article"}
    return worker, row, facts, clock


@pytest.mark.parametrize(
    "seconds,expected", [(599, "L2_DUE"), (600, "IRRELEVANT"), (36000, "IRRELEVANT")]
)
async def test_model_window_boundary(decision, seconds, expected):
    worker, row, facts, clock = decision
    clock[0] += timedelta(seconds=seconds)
    await worker.process(row, facts)
    call = worker.bus.storage.call.await_args
    assert call.args[2] == expected
    if expected == "IRRELEVANT":
        assert call.kwargs["error"] == "DECISION_MODEL_EXPIRED"
    worker.publish.assert_not_awaited()


async def test_expired_queued_model_is_not_called(decision):
    worker, row, facts, clock = decision
    clock[0] += timedelta(minutes=11)
    await worker.classify([row])
    worker.classifier._jev.assert_not_awaited()
    assert worker.bus.storage.call.await_args.kwargs["error"] == "DECISION_MODEL_EXPIRED"


async def test_timely_positive_model_result_publishes(decision):
    worker, row, facts, clock = decision
    clock[0] += timedelta(seconds=599)

    async def classify(message, definitions, *, on_positive):
        await on_positive("MU", 0.9, 1)
        return {"MU": 0.9}, {}, {}

    worker.classifier._jev.side_effect = classify
    await worker.classify([row])
    worker.publish.assert_awaited_once()


@pytest.mark.parametrize("result", ["positive", "negative", "failure"])
async def test_inflight_result_crossing_deadline_cannot_publish_or_retry(decision, result):
    worker, row, facts, clock = decision
    clock[0] += timedelta(seconds=599)

    async def classify(message, definitions, *, on_positive):
        clock[0] += timedelta(seconds=2)
        if result == "positive":
            await on_positive("MU", 0.9, 1)
            return {"MU": 0.9}, {}, {}
        return (
            ({"MU": 0.1}, {}, {}) if result == "negative" else ({}, {}, {"MU": "JEV_TIME_BUDGET"})
        )

    worker.classifier._jev.side_effect = classify
    await worker.classify([row])
    worker.publish.assert_not_awaited()
    assert worker.bus.storage.call.await_args.args[2] == "IRRELEVANT"
    assert worker.bus.storage.call.await_args.kwargs["error"] == "DECISION_MODEL_EXPIRED"


@pytest.mark.parametrize("accepted_at,allowed", [(599, True), (601, False)])
async def test_only_timely_cached_model_result_survives_window(decision, accepted_at, allowed):
    worker, row, facts, clock = decision
    row.update(
        decision_digest="body",
        jev_result=0.9,
        due_at=(clock[0] + timedelta(seconds=accepted_at)).isoformat(),
    )
    clock[0] += timedelta(minutes=11)
    await worker.process(row, facts)
    assert worker.publish.await_count == int(allowed)


async def test_source_takeover_does_not_reset_window(decision):
    worker, row, facts, clock = decision
    clock[0] += timedelta(minutes=11)
    row["first_discovered_at"] = clock[0].isoformat()
    await worker.process(row, facts)
    assert worker.bus.storage.call.await_args.kwargs["error"] == "DECISION_MODEL_EXPIRED"


@pytest.mark.parametrize(
    "regex_hit,cached_old_terms", [(False, False), (True, False), (False, True)]
)
async def test_real_config_recovery_keeps_expired_model_blocked_but_allows_regex(
    tmp_path, monkeypatch, regex_hit, cached_old_terms
):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
    from doxagent.message_bus_v2.schema import UpdateActor
    from tests.test_content_enrichment_hub import _GovernedExtractor, _message, _setup

    repo, bus, _, _ = _setup(tmp_path)
    source = bus.update_source("ctee_semiconductor", {"enabled": True}, actor=UpdateActor.SYSTEM)
    binding = bus.configure_binding(
        ticker="MU", source_id=source.source_id, actor=UpdateActor.SYSTEM
    )
    MonitoringTermsService(repo).apply(
        _terms("MU", "Micron" if regex_hit else "UNMATCHABLE_COMPANY"), actor="test"
    )
    bus.live_queue = live.LiveQueue(bus)
    queue = bus.live_queue.repository
    now = live.utc_now()
    message = _message(9921).model_copy(
        update={"title": "Micron announces manufacturing expansion"}
    )
    captured = queue.prepare_input(
        source, [(binding.binding_id, "MU", 1)], [message], observed_at=now.isoformat(), unit="HEAD"
    )
    queue.accept(captured)
    hub = ContentEnrichmentHub(repo, bus, extractor=_GovernedExtractor())
    try:
        await hub.run_once()
        with repo.transaction() as db:
            db.execute(
                "UPDATE live_deliveries SET state='CONFIG_INCOMPLETE',last_error='JEV_HTTP_403'"
            )
            if cached_old_terms:
                db.execute(
                    "UPDATE live_deliveries SET state='PENDING',regex_result=0,jev_result=.9,"
                    "decision_digest=(SELECT body_digest FROM live_articles LIMIT 1)"
                )
        if cached_old_terms:
            terms = _terms("MU", "DIFFERENT_COMPANY").model_copy(update={"expected_revision": 1})
            MonitoringTermsService(repo).apply(terms, actor="test")
        monkeypatch.setattr(live, "utc_now", lambda: now + timedelta(minutes=11))
        for _ in range(2):
            # A new worker resumes CONFIG_INCOMPLETE on startup, using persisted anchors.
            worker = live.LiveDeliveryWorker(bus, jev=SimpleNamespace(classify=AsyncMock()))
            worker.classifier._jev = AsyncMock(side_effect=AssertionError("Expired model called"))
            await worker.run_available()
            worker.classifier._jev.assert_not_awaited()
            await worker.close()
        assert len(repo.read_stream("MU")) == int(regex_hit)
        with repo._connect() as db:
            state = db.execute("SELECT state,last_error FROM live_deliveries").fetchone()
        assert tuple(state) == (
            ("PUBLISHED", None) if regex_hit else ("IRRELEVANT", "DECISION_MODEL_EXPIRED")
        )
    finally:
        await hub.close()
