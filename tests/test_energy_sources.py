import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.body_results import article_key
from doxagent.message_bus_v2.energy_body import EnergyBodyReader
from doxagent.message_bus_v2.energy_catalog import (
    CATALOG,
    POWER_BE,
    POWER_CEG,
    POWERENG_BE,
    POWERENG_CEG,
    SOURCES,
    endpoint_units,
    initial_sources,
)
from doxagent.message_bus_v2.energy_sources import EnergyAdapter, EnergyHttp, parse_listing
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import PollContext, SharedPollContext, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.technology_sources import Capture
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from scripts.ops.configure_energy_sources import configure
from tests.test_message_bus_monitoring_terms import _terms

FIXTURES = Path(__file__).parent / "fixtures/energy_sources"
NOW = datetime(2026, 10, 27, 13, tzinfo=UTC)


@asynccontextmanager
async def permit():
    yield


def definition(source_id):
    return next(source for source in initial_sources() if source.source_id == source_id)


def setup_bus(tmp_path, tickers=("CEG", "BE", "INTC", "MU", "COHR", "NBIS")):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    for ticker in tickers:
        bus.start_ticker(ticker)
        if ticker != "CEG":
            MonitoringTermsService(repo).apply(
                _terms().model_copy(update={"ticker": ticker}), actor="test"
            )
    return repo, bus


def context(source_id, unit, checkpoint=None, at=NOW):
    return SharedPollContext(
        run_id="test-energy",
        source=definition(source_id),
        acquisition_unit=unit,
        checkpoint=checkpoint or {},
        requested_at=at,
        request_permit=permit,
    )


@pytest.mark.parametrize(
    "sample",
    json.loads((FIXTURES / "manifest.json").read_text()),
    ids=lambda sample: sample["source_id"] + "-" + Path(sample["url"]).name,
)
def test_real_listing_contract_for_every_internal_endpoint(sample):
    spec = SOURCES[sample["source_id"]]
    rows = parse_listing(
        spec, (FIXTURES / sample["fixture"]).read_text(encoding="utf-8"), sample["url"], NOW
    )
    assert rows and all(row.title and spec.accepts(row.url) for row in rows)
    assert all(row.publication_time_basis != "UNKNOWN_FIRST_SEEN" for row in rows)
    assert all(row.body is None for row in rows)


def test_catalog_matches_external_granularity_and_internal_subscriptions():
    assert len(CATALOG) == 16 and sum(len(spec.urls) for spec in CATALOG) == 31
    assert set(SOURCES["power_magazine_energy"].urls_for("CEG")) == set(POWER_CEG)
    assert set(SOURCES["power_magazine_energy"].urls_for("BE")) == set(POWER_BE)
    assert set(SOURCES["power_engineering_energy"].urls_for("CEG")) == set(POWERENG_CEG)
    assert set(SOURCES["power_engineering_energy"].urls_for("BE")) == set(POWERENG_BE)
    assert SOURCES["dcd_energy_ticker"].urls_for("CEG") != SOURCES["dcd_energy_ticker"].urls_for(
        "BE"
    )
    assert len(SOURCES["doe_energy_news"].urls_for("CEG")) == 4
    assert len(SOURCES["doe_energy_news"].urls_for("BE")) == 3
    assert SOURCES["dcd_infrastructure_rss"].tickers == ("CEG", "BE", "INTC", "MU", "COHR", "NBIS")
    assert not any("SNOW" in spec.tickers for spec in CATALOG)
    assert all(
        source.site_id is None
        and source.default_polling_config.target_interval_seconds == 60
        and source.scheduler_constraints.max_concurrency == 2
        for source in initial_sources()
    )
    assert (
        len({source.scheduler_group for source in initial_sources() if "dcd_" in source.source_id})
        == 1
    )


def test_configuration_reserves_unregistered_ionq_and_preserves_existing_terms(tmp_path):
    repo, bus = setup_bus(tmp_path)
    before = {
        ticker: MonitoringTermsService(repo).get(ticker)[0]
        for ticker in ("BE", "MU", "INTC", "COHR", "NBIS")
    }
    first = configure(bus)
    assert len(first["bindings"]) == 28 and len(first["reserved"]) == 4
    assert first["initial_terms"] == [{"ticker": "CEG", "revision": 1}]
    assert repo.get_ticker_state("IONQ") is None
    assert all(row["action"] == "unchanged" for row in configure(bus)["bindings"])
    assert not configure(bus)["initial_terms"]
    assert {ticker: MonitoringTermsService(repo).get(ticker)[0] for ticker in before} == before
    bus.start_ticker("IONQ")
    next_run = configure(bus)
    assert len(next_run["bindings"]) == 32 and not next_run["reserved"]
    assert next_run["initial_terms"] == [{"ticker": "IONQ", "revision": 1}]
    repo.close()


def test_shared_endpoint_roster_excludes_unsubscribed_tickers(tmp_path):
    repo, bus = setup_bus(tmp_path)
    configure(bus)
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace())
    source = repo.get_source("power_engineering_energy")
    bindings = repo.list_bindings(source_id=source.source_id)
    plans = list(
        scheduler.distribution_work_units(
            source,
            bindings,
            NOW,
            poll_states={b.binding_id: repo.get_poll_state(b) for b in bindings},
        )
    )
    units = endpoint_units(source)
    rosters = {
        units[options["acquisition_unit"]]: {b.ticker for b in targets}
        for _, targets, options in plans
    }
    assert len(rosters) == 6
    assert rosters[POWERENG_CEG[0]] == {"CEG"}
    assert rosters[POWERENG_BE[0]] == {"BE"}
    assert rosters[POWERENG_CEG[-1]] == {"CEG", "BE"}
    power = repo.get_source("power_magazine_energy")
    be = repo.get_binding("BE:" + power.source_id)
    assert len(list(scheduler.work_units(power, be, repo.get_poll_state(be), NOW))) == 5
    repo.close()


def test_overlapping_power_feeds_have_same_due_phase_without_long_response_cache(tmp_path):
    repo, bus = setup_bus(tmp_path)
    configure(bus)
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace())
    source = repo.get_source("power_magazine_energy")
    bindings = repo.list_bindings(source_id=source.source_id)
    scheduler._initialize_due_slots([(source, b) for b in bindings], NOW)
    common = set(POWER_CEG) & set(POWER_BE)
    for unit, url in endpoint_units(source).items():
        if url in common:
            due = {repo.get_poll_unit_state(b, unit).next_dispatch_at for b in bindings}
            assert len(due) == 1
    repo.close()


async def test_energy_bad_200_keeps_validators_and_backoff_belongs_only_to_one_feed():
    source_id = "power_magazine_energy"
    source = definition(source_id)
    units = endpoint_units(source)
    first, other = list(units)[:2]
    raw = (FIXTURES / "manifest.json").read_text()
    row = next(row for row in json.loads(raw) if row["url"] == units[other])
    fixture = (FIXTURES / row["fixture"]).read_text(encoding="utf-8")
    http = SimpleNamespace(
        fetch=AsyncMock(
            return_value=Capture(
                200, units[first], "<html>Client Challenge</html>", {"etag": "bad"}
            )
        )
    )
    adapter = EnergyAdapter(http)
    failed = await adapter.poll_shared(context(source_id, first))
    assert failed.site_access_deferred and failed.failures[0].error_code == "ENERGY_ENDPOINT_FAILED"
    checkpoint = failed.next_checkpoint["endpoints"][units[first]]
    assert not checkpoint.get("baseline") and not checkpoint.get("etag")
    before = http.fetch.await_count
    deferred = await adapter.poll_shared(
        context(source_id, first, failed.next_checkpoint, NOW + timedelta(seconds=10))
    )
    assert deferred.site_access_deferred and http.fetch.await_count == before
    http.fetch.return_value = Capture(200, units[other], fixture, {})
    healthy = await adapter.poll_shared(context(source_id, other))
    assert healthy.messages and not healthy.failures


async def test_power_shared_http_request_survives_cancelled_ticker_and_reuses_slot():
    import asyncio

    release, started = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        started.set()
        await release.wait()
        return Capture(200, args[0], "<rss/>", {})

    base = SimpleNamespace(fetch=AsyncMock(side_effect=fetch))
    http = EnergyHttp(base)
    spec = SOURCES["power_magazine_energy"]
    task = asyncio.create_task(http.fetch(POWER_CEG[1], spec))
    await started.wait()
    sibling = asyncio.create_task(http.fetch(POWER_CEG[1], spec))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert (await sibling).status == 200
    assert (await http.fetch(POWER_CEG[1], spec)).status == 200
    assert base.fetch.await_count == 1
    await http.close()


async def test_public_transport_retries_only_transient_failures_and_empty_200_once():
    spec = SOURCES["nist_news"]
    empty = Capture(200, spec.urls[0], "", {})
    full = Capture(200, spec.urls[0], "<rss/>", {})
    base = SimpleNamespace(fetch=AsyncMock(side_effect=[empty, full]))
    assert (await EnergyHttp(base).fetch(spec.urls[0], spec)).text == "<rss/>"
    assert base.fetch.await_count == 2
    denied = SimpleNamespace(fetch=AsyncMock(return_value=Capture(403, spec.urls[0], "denied", {})))
    assert (await EnergyHttp(denied).fetch(spec.urls[0], spec)).status == 403
    assert denied.fetch.await_count == 1

    class TlsFailure(Exception):
        code = 35

    tls = SimpleNamespace(fetch=AsyncMock(side_effect=[TlsFailure(), full]))
    assert (await EnergyHttp(tls).fetch(spec.urls[0], spec)).status == 200
    assert tls.fetch.await_count == 2


def test_nrc_reports_are_separate_stable_articles_and_keep_full_event_text():
    raw = (FIXTURES / "nrc_events.html").read_text(encoding="utf-8")
    spec = SOURCES["nrc_event_notifications"]
    messages = parse_listing(spec, raw, spec.urls[0], NOW)
    assert len(messages) == 2 and len({m.external_id for m in messages}) == 2
    assert len({article_key(m, spec.source_id) for m in messages}) == 2
    first, second = messages
    assert (
        "Whitestone" in first.raw_payload["licensee"]
        and "SpectraTek" in second.raw_payload["licensee"]
    )
    assert "SpectraTek" not in first.raw_payload["event_html"]
    assert "DAMAGED GAUGE" not in second.raw_payload["event_html"]
    assert first.published_at == datetime(2026, 10, 16, 17, 42, tzinfo=UTC)
    assert first.url.endswith("20261026en?event=58499")
    revised = first.model_copy(update={"url": first.url.replace("20261026en", "20261027en")})
    assert article_key(revised, spec.source_id) == article_key(first, spec.source_id)


async def test_nrc_search_matches_licensee_field_and_native_body_never_fetches(tmp_path):
    repo, bus = setup_bus(tmp_path)
    configure(bus)
    source = repo.get_source("nrc_event_notifications")
    binding = repo.get_binding("CEG:" + source.source_id)
    raw = (FIXTURES / "nrc_events.html").read_text(encoding="utf-8")
    raw = raw.replace("Whitestone Associates Inc.", "Constellation Energy Generation, LLC")
    # A target mentioned only in another licensee's narrative must not match it.
    raw = raw.replace("LOST RADIOACTIVE MATERIAL", "Constellation Energy LOST RADIOACTIVE MATERIAL")
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, source.entry_url, raw, {})))
    adapter = EnergyAdapter(http)
    unit = next(iter(endpoint_units(source)))
    result = await adapter.poll(
        PollContext(
            source=source,
            binding=binding,
            ticker="CEG",
            acquisition_unit=unit,
            requested_at=NOW,
            request_permit=permit,
            query_plan={"queries": []},
        )
    )
    assert len(result.messages) == 1 and result.messages[0].external_id == "nrc-en-58499"
    message = result.messages[0]
    record = MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=source.source_id,
        ticker="CEG",
        title=message.title,
        body=None,
        url=message.url,
    )
    no_network = SimpleNamespace(
        fetch=AsyncMock(side_effect=AssertionError("native event fetched"))
    )
    extracted = await EnergyBodyReader(no_network).extract(
        SimpleNamespace(source=source, message=message), record
    )
    assert extracted.succeeded and extracted.diagnostics["outcome"] == "FULL"
    assert "Cs-137" in extracted.content and "20" in extracted.content
    assert "SpectraTek" not in extracted.content
    no_network.fetch.assert_not_awaited()
    forged = message.model_copy(update={"external_id": "nrc-en-58500"})
    with pytest.raises(ValueError, match="identity_mismatch"):
        await EnergyBodyReader(no_network).extract(
            SimpleNamespace(source=source, message=forged), record
        )
    repo.close()


async def test_real_hub_accepts_complete_nrc_event_and_article_once(tmp_path):
    repo, bus = setup_bus(tmp_path)
    configure(bus)
    source = repo.get_source("nrc_event_notifications")
    binding = repo.get_binding("CEG:" + source.source_id)
    raw = (
        (FIXTURES / "nrc_events.html")
        .read_text(encoding="utf-8")
        .replace("Whitestone Associates Inc.", "Constellation Energy Generation, LLC")
    )
    message = parse_listing(SOURCES[source.source_id], raw, source.entry_url, NOW)[0]
    message = message.model_copy(update={"published_at": utc_now()})
    bus.enqueue_enrichment(
        source=source, binding=binding, message=message, bootstrap=False, poll_run_id="nrc-native"
    )
    hub = ContentEnrichmentHub(repo, bus)
    try:
        assert await hub.run_once() == 1
        assert len(repo.list_standard(ticker="CEG")) == 1
        assert "Cs-137" in repo.list_standard(ticker="CEG")[0].body
        assert repo.latest_stream_offset("CEG") == 1
        from doxagent.message_bus_v2.schema import sha256_text

        payload = dict(message.raw_payload)
        payload["event_html"] += (
            "<p>UPDATE: The source has been recovered and restrictions lifted.</p>"
        )
        payload["content_hash"] = sha256_text(payload["event_html"])
        updated = message.model_copy(
            update={
                "raw_payload": payload,
                "metadata": {
                    **message.metadata,
                    "identity_evidence": {
                        **message.metadata["identity_evidence"],
                        "provider_version": payload["content_hash"],
                    },
                },
            }
        )
        bus.enqueue_enrichment(
            source=source,
            binding=binding,
            message=updated,
            bootstrap=False,
            poll_run_id="nrc-native-update",
        )
        assert await hub.run_once() == 1
        assert len(repo.list_standard(ticker="CEG")) == 1
        assert "restrictions lifted" in repo.list_standard(ticker="CEG")[0].body
        assert repo.latest_stream_offset("CEG") == 1
    finally:
        await hub.close()
