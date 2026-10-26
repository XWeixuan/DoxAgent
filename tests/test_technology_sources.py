from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.content_enrichment.transport import REVALIDATION
from doxagent.message_bus_v2.body_results import article_key
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import RawMessageInput, SharedPollContext
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.technology_body import TechnologyBodyReader
from doxagent.message_bus_v2.technology_sources import (
    CATALOG,
    LEGACY_CATALOG,
    SOURCES,
    Capture,
    TechnologyAdapter,
    TechnologyHttp,
    endpoint_units,
    initial_sources,
    parse_listing,
)
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from scripts.ops.configure_technology_sources import configure
from tests.test_message_bus_monitoring_terms import _terms

FIXTURES = Path(__file__).parent / "fixtures/technology_sources"
NOW = datetime(2026, 10, 26, tzinfo=UTC)


@asynccontextmanager
async def permit():
    yield


def context(source_id, checkpoint=None, at=NOW, unit="SHARED"):
    return SharedPollContext(
        run_id="test",
        source=source_definition(source_id),
        acquisition_unit=unit,
        requested_at=at,
        checkpoint=checkpoint or {},
        request_permit=permit,
    )


def source_definition(source_id):
    spec = SOURCES[source_id]
    base = next(s for s in initial_sources() if SOURCES[s.source_id].domain == spec.domain)
    return base.model_copy(update={"source_id": source_id, "entry_url": spec.urls[0]})


def fixture(source_id):
    return (
        FIXTURES / (source_id + (".html" if SOURCES[source_id].parser != "rss" else ".xml"))
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("spec", LEGACY_CATALOG, ids=lambda s: s.source_id)
def test_real_publisher_listing_contracts(spec):
    messages = parse_listing(spec, fixture(spec.source_id), spec.urls[0], NOW)
    assert messages and all(spec.accepts(m.url) for m in messages)
    assert all(m.title and m.body is None for m in messages)
    assert all(m.published_at <= NOW and m.metadata["language"] == spec.language for m in messages)
    if spec.parser == "sdxcentral":
        assert all("/news/" in m.url and m.publication_time_basis == "DATE" for m in messages)
    if spec.domain == "technews.tw":
        assert "�" not in messages[0].title


def test_catalog_shares_publisher_schedule_and_has_no_browser_or_global_subscription():
    sources = initial_sources()
    assert len(sources) == 13
    assert len({s.source_id for s in sources}) == len(sources)
    for domain in {s.domain for s in CATALOG}:
        assert (
            len({s.scheduler_group for s in sources if SOURCES[s.source_id].domain == domain}) == 1
        )
    assert sum(len(spec.urls) for spec in CATALOG) == 26
    assert len(SOURCES["siliconangle_software"].urls) == 3
    assert len(SOURCES["siliconangle_ai"].urls) == 1
    assert all(s.distribution_policy.require_complete_body for s in sources)
    assert all(s.scheduler_constraints.max_concurrency == 2 for s in sources)
    assert all(s.default_polling_config.target_interval_seconds == 60 for s in sources)
    assert all(s.site_id is None for s in sources)
    assert SOURCES["quantum_zeitgeist"].transport == "urllib"
    assert "off-prem%20OR%20tag:off_prem" in SOURCES["register_cloud"].urls[0]


async def test_conditional_poll_and_content_update_do_not_repeat_unchanged_articles():
    spec = SOURCES["quantum_computing_report"]
    raw = fixture(spec.source_id)
    http = SimpleNamespace(
        fetch=AsyncMock(return_value=Capture(200, spec.urls[0], raw, {"etag": "v1"}))
    )
    adapter = TechnologyAdapter(http)
    first = await adapter.poll_shared(context(spec.source_id))
    assert len(first.messages) == 2
    second = await adapter.poll_shared(
        context(spec.source_id, first.next_checkpoint, NOW + timedelta(seconds=60))
    )
    assert not second.messages
    assert http.fetch.call_args.kwargs["headers"] == {"If-None-Match": "v1"}
    changed = raw.replace("<description>", "<description>New financing terms announced. ", 1)
    assert changed != raw
    http.fetch.return_value = Capture(200, spec.urls[0], changed, {"etag": "v2"})
    third = await adapter.poll_shared(
        context(spec.source_id, second.next_checkpoint, NOW + timedelta(seconds=120))
    )
    assert len(third.messages) == 1
    heartbeat = await adapter.poll_shared(
        context(spec.source_id, third.next_checkpoint, NOW + timedelta(minutes=5))
    )
    assert len(heartbeat.messages) == 2
    assert http.fetch.call_args.kwargs["headers"] == {}
    http.fetch.return_value = Capture(304, spec.urls[0], "", {})
    fourth = await adapter.poll_shared(
        context(spec.source_id, heartbeat.next_checkpoint, NOW + timedelta(minutes=6))
    )
    assert not fourth.messages and not fourth.failures


async def test_bad_200_does_not_poison_validators_and_backoff_survives_next_run():
    spec = SOURCES["venturebeat_data"]
    http = SimpleNamespace(
        fetch=AsyncMock(
            return_value=Capture(200, spec.urls[0], fixture(spec.source_id), {"etag": "good"})
        )
    )
    adapter = TechnologyAdapter(http)
    first = await adapter.poll_shared(context(spec.source_id))
    http.fetch.return_value = Capture(
        200, spec.urls[0], "<html>Verification page</html>", {"etag": "bad"}
    )
    failed = await adapter.poll_shared(
        context(spec.source_id, first.next_checkpoint, NOW + timedelta(seconds=60))
    )
    assert failed.site_access_deferred and failed.failures
    state = failed.next_checkpoint["endpoints"][spec.urls[0]]
    assert state["etag"] == "good" and state["failures"] == 1
    http.fetch.reset_mock()
    backed_off = await adapter.poll_shared(
        context(spec.source_id, failed.next_checkpoint, NOW + timedelta(seconds=61))
    )
    assert backed_off.site_access_deferred and not backed_off.messages
    http.fetch.assert_not_awaited()


async def test_304_without_baseline_cannot_be_success():
    spec = SOURCES["siliconangle_ai"]
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(304, spec.urls[0], "", {})))
    result = await TechnologyAdapter(http).poll_shared(context(spec.source_id))
    assert (
        result.site_access_deferred and "304_without_baseline" in result.failures[0].error_message
    )
    assert http.fetch.await_count == 2


async def test_slow_column_does_not_delay_another_column_on_same_publisher():
    import asyncio

    release = asyncio.Event()

    async def fetch(url, spec, **kwargs):
        if spec.source_id == "siliconangle_cloud":
            await release.wait()
        return Capture(200, url, fixture(spec.source_id), {})

    adapter = TechnologyAdapter(SimpleNamespace(fetch=fetch))
    slow = asyncio.create_task(adapter.poll_shared(context("siliconangle_cloud")))
    healthy = await asyncio.wait_for(adapter.poll_shared(context("siliconangle_ai")), 2)
    assert healthy.messages and not slow.done()
    release.set()
    assert (await slow).messages


def test_cross_column_article_identity_reuses_download_but_changed_message_is_visible():
    spec = SOURCES["siliconangle_ai"]
    message = parse_listing(spec, fixture(spec.source_id), spec.urls[0], NOW)[0]
    assert article_key(message, "siliconangle_ai") == article_key(message, "siliconangle_cloud")
    changed = message.model_copy(update={"summary": "New material agreement"})
    assert article_key(changed, "siliconangle_ai") == article_key(message, "siliconangle_ai")


def body_job(source_id="register_software", title="Company announces a new cloud platform"):
    source = source_definition(source_id)
    url = "https://" + SOURCES[source_id].domain + "/2026/10/23/example/"
    message = RawMessageInput(title=title, url=url, published_at=NOW, raw_payload={})
    record = MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=source_id,
        ticker="SNOW",
        title=title,
        body=None,
        url=url,
    )
    return SimpleNamespace(source=source, message=message), record


async def test_full_body_and_paid_preview_remain_distinct():
    job, record = body_job()
    body = (
        "The company announced a new cloud platform. "
        "Customers will deploy the service next quarter. " * 18
    )
    raw = "<h1>" + job.message.title + "</h1><article><p>" + body + "</p></article>"
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, record.url, raw, {})))
    reader = TechnologyBodyReader(http)
    result = await reader.extract(job, record)
    assert result.succeeded and result.diagnostics["outcome"] == "FULL"
    http.fetch.return_value = Capture(
        200, record.url, raw.replace("</p>", " Subscribe to continue.</p>"), {}
    )
    result = await reader.extract(job, record)
    assert not result.succeeded and result.reason == "subscription_required"


async def test_body_revalidation_retains_original_acquisition_time():
    job, record = body_job()
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(304, record.url, "", {})))
    token = REVALIDATION.set(
        {
            "content": "Verified complete article.",
            "outcome": "SHORT_FULL",
            "acquired_at": "2026-10-25T00:00:00Z",
            "validators": {"etag": "v1"},
        }
    )
    try:
        result = await TechnologyBodyReader(http).extract(job, record)
    finally:
        REVALIDATION.reset(token)
    assert result.succeeded and result.content == "Verified complete article."
    assert result.diagnostics["body_acquired_at"] == "2026-10-25T00:00:00Z"


@pytest.mark.parametrize("mismatch", [None, "canonical", "time"])
async def test_observed_publisher_headline_revision_requires_matching_url_and_publication(mismatch):
    job, record = body_job(
        "siliconangle_security",
        "Three insights you may have missed from theCUBE’s coverage of Okta’s Oktane event",
    )
    url = "https://siliconangle.com/2026/10/23/ai-agent-governance-takes-shape-oktas-oktane-event-oktane/"
    job.message = job.message.model_copy(
        update={"url": url, "published_at": datetime(2026, 10, 24, 3, 52, 19, tzinfo=UTC)}
    )
    from dataclasses import replace

    record = replace(record, url=url)
    raw = (FIXTURES / "siliconangle-headline-revision.html").read_text(encoding="utf-8")
    if mismatch == "canonical":
        raw = raw.replace(url, "https://siliconangle.com/unrelated/")
    elif mismatch == "time":
        job.message = job.message.model_copy(update={"published_at": NOW})
    reader = TechnologyBodyReader(
        SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, url, raw, {})))
    )
    result = await reader.extract(job, record)
    assert result.succeeded is (mismatch is None)
    if mismatch is None:
        assert result.diagnostics["publisher_title"].startswith("AI agent governance takes shape")


def test_configuration_is_idempotent_preserves_terms_and_reserves_ionq(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    service = MessageBusV2Service(repo)
    service.bootstrap()
    for ticker in ("SNOW", "MU", "INTC", "COHR", "NBIS", "BE"):
        service.start_ticker(ticker)
        if ticker != "SNOW":
            MonitoringTermsService(repo).apply(
                _terms().model_copy(update={"ticker": ticker}), actor="test"
            )
    first = configure(service)
    assert len(first["bindings"]) == 34 and len(first["reserved"]) == 3
    assert first["initial_terms"] == [{"ticker": "SNOW", "revision": 1}]
    assert repo.get_ticker_state("IONQ") is None
    assert all(item["action"] == "unchanged" for item in configure(service)["bindings"])
    assert MonitoringTermsService(repo).get("MU")[0] == 1
    service.start_ticker("IONQ")
    assert not any(b.source_id.startswith("quantum_") for b in repo.list_bindings(ticker="IONQ"))
    later = configure(service)
    assert len(later["bindings"]) == 37 and not later["reserved"]
    assert later["initial_terms"] == [{"ticker": "IONQ", "revision": 1}]


async def test_shared_endpoint_backoff_is_durable_and_sweep_does_not_replace_live_checkpoint(
    tmp_path,
):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("SNOW")
    configure(bus)
    binding = repo.get_binding("SNOW:venturebeat_software")
    source = repo.get_source(binding.source_id)
    adapter = TechnologyAdapter(
        SimpleNamespace(
            fetch=AsyncMock(
                return_value=Capture(429, source.entry_url, "rate limited", {"retry-after": "120"})
            )
        )
    )
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace(resolve=lambda *a, **k: adapter))
    try:
        unit = next(iter(endpoint_units(source)))
        result = await scheduler._poll_distribution(source, [binding], NOW, acquisition_unit=unit)
        assert result.site_access_deferred
        state = repo.get_poll_unit_state(binding, unit)
        saved = state.checkpoint["technology_public"]
        assert state.next_dispatch_at == NOW + timedelta(seconds=120)
        assert saved["checkpoint"]["endpoints"][source.entry_url]["failures"] == 1
        # A new adapter/process receives the durable state and performs no premature request.
        http = SimpleNamespace(fetch=AsyncMock(side_effect=AssertionError("backoff ignored")))
        restarted = TechnologyAdapter(http)
        next_result = await restarted.poll_shared(
            context(source.source_id, saved["checkpoint"], NOW + timedelta(seconds=60), unit)
        )
        assert next_result.site_access_deferred
        http.fetch.assert_not_awaited()
        scheduler._record_distribution_poll(
            source,
            [(binding, 1, {})],
            {
                "created_at": (NOW + timedelta(seconds=180)).isoformat(),
                "mode": "CLOSED_SWEEP",
                "coverage": "COMPLETE",
            },
            acquisition_checkpoint={"sweep": True},
        )
        assert repo.get_poll_unit_state(binding, unit).checkpoint["technology_public"] == saved
    finally:
        repo.close()


async def test_http_pool_caps_same_publisher_across_separate_column_sources():
    import asyncio

    active = 0
    maximum = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    class Response:
        status_code = 200
        headers = {}

        async def aiter_content(self):
            await release.wait()
            yield b"<rss/>"

    class Session:
        @asynccontextmanager
        async def stream(self, *args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                entered.set()
            try:
                yield Response()
            finally:
                active -= 1

    http = TechnologyHttp(Session())
    specs = [SOURCES["siliconangle_ai"], SOURCES["siliconangle_cloud"]]
    tasks = [asyncio.create_task(http.fetch(specs[i % 2].urls[0], specs[i % 2])) for i in range(5)]
    await asyncio.wait_for(entered.wait(), 3)
    assert maximum == 2
    release.set()
    assert all(result.status == 200 for result in await asyncio.gather(*tasks))
    assert maximum == 2 and active == 0


async def test_grouped_endpoint_runs_commit_independently_and_keep_separate_backoff(
    tmp_path, monkeypatch
):
    import asyncio

    monkeypatch.setattr("doxagent.message_bus_v2.scheduler.utc_now", lambda: NOW)
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("SNOW")
    configure(bus)
    source = repo.get_source("siliconangle_software")
    binding = repo.get_binding("SNOW:" + source.source_id)
    units = list(endpoint_units(source))
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def fetch(url, spec, **kwargs):
        calls.append(url)
        urls = SOURCES[source.source_id].urls
        if url == urls[1]:
            started.set()
            await release.wait()
        if url == urls[2]:
            return Capture(429, url, "rate limited", {"retry-after": "120"})
        old = next(s for s in LEGACY_CATALOG if s.urls == (url,))
        return Capture(200, url, fixture(old.source_id), {"etag": old.source_id})

    adapter = TechnologyAdapter(SimpleNamespace(fetch=fetch))
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace(resolve=lambda *a, **k: adapter))
    slow = asyncio.create_task(
        scheduler._poll_distribution(source, [binding], NOW, acquisition_unit=units[1])
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        healthy = await asyncio.wait_for(
            scheduler._poll_distribution(source, [binding], NOW, acquisition_unit=units[0]), 3
        )
        assert healthy.collected_count and not slow.done()
        saved = repo.get_poll_unit_state(binding, units[0])
        assert saved.checkpoint["technology_public"]["checkpoint"]["endpoints"][source.entry_url][
            "etag"
        ]
        failed = await scheduler._poll_distribution(
            source, [binding], NOW, acquisition_unit=units[2]
        )
        assert failed.site_access_deferred
        assert repo.get_poll_unit_state(binding, units[2]).next_dispatch_at == NOW + timedelta(
            seconds=120
        )
        assert repo.get_poll_unit_state(binding, units[0]) == saved
        calls.clear()
        await scheduler._poll_distribution(
            source, [binding], NOW + timedelta(seconds=60), acquisition_unit=units[2]
        )
        assert not calls  # A new slot reuses the endpoint's durable backoff.
        release.set()
        assert (await slow).collected_count
        assert set(repo.get_poll_state(binding).units) == set(units)
        plans = list(
            scheduler.distribution_work_units(
                source,
                [binding],
                NOW + timedelta(seconds=61),
                poll_states={binding.binding_id: repo.get_poll_state(binding)},
            )
        )
        assert all(options["acquisition_unit"] != units[2] for _, _, options in plans)
        assert {options["acquisition_unit"] for _, _, options in plans} == set(units[:2])
    finally:
        release.set()
        await asyncio.gather(slow, return_exceptions=True)
        await scheduler.close()


def test_migration_keeps_endpoint_state_legacy_history_and_idempotence(tmp_path):
    from doxagent.message_bus_v2.schema import PollStatus, UpdateActor

    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("SNOW")
    bus.start_ticker("MU")
    MonitoringTermsService(repo).apply(_terms().model_copy(update={"ticker": "MU"}), actor="test")
    # Register the exact former public definitions, including the Wccftech collision.
    for old in LEGACY_CATALOG:
        source = source_definition(old.source_id).model_copy(
            update={
                "entry_url": old.urls[0],
                "display_name": old.name,
            }
        )
        if repo.get_source(old.source_id):
            if old.source_id == "wccftech_hardware":
                bus.update_source(
                    old.source_id,
                    {"entry_url": old.urls[0], "display_name": old.name},
                    actor=UpdateActor.SYSTEM,
                )
        else:
            bus.register_source(source)
        if old.source_id not in {
            "siliconangle_big_data",
            "siliconangle_cloud",
            "siliconangle_security",
            "wccftech_ai",
            "wccftech_industry",
            "wccftech_hardware",
        }:
            continue
        ticker = "SNOW" if old.domain == "siliconangle.com" else "MU"
        if ticker == "SNOW" and MonitoringTermsService(repo).get(ticker) is None:
            MonitoringTermsService(repo).apply(
                _terms().model_copy(update={"ticker": ticker}), actor="test"
            )
        binding = bus.configure_binding(
            ticker=ticker, source_id=old.source_id, actor=UpdateActor.SYSTEM
        )
        state = repo.get_poll_state(binding)
        repo.save_poll_state(
            state.model_copy(
                update={
                    "status": PollStatus.SUCCEEDED,
                    "bootstrap_complete": True,
                    "last_attempt_at": NOW,
                    "next_dispatch_at": NOW + timedelta(seconds=60),
                    "checkpoint": {
                        "technology_public": {
                            "source_version": source.version,
                            "checkpoint": {
                                "endpoints": {
                                    old.urls[0]: {
                                        "baseline": True,
                                        "etag": old.source_id,
                                        "seen": {"article": "signature"},
                                    }
                                }
                            },
                        }
                    },
                }
            )
        )
    old_versions = repo.list_source_revisions("siliconangle_cloud")
    report = configure(bus)
    assert len(report["migration"]["units"]) == 6
    assert len(report["migration"]["retired_bindings"]) == 5
    assert len(report["migration"]["retired_sources"]) == 19
    for group_id, ticker in (("siliconangle_software", "SNOW"), ("wccftech_hardware", "MU")):
        source = repo.get_source(group_id)
        binding = repo.get_binding(ticker + ":" + group_id)
        state = repo.get_poll_state(binding)
        assert len(state.units) == 3
        for unit, url in endpoint_units(source).items():
            saved = repo.get_poll_unit_state(binding, unit)
            cp = saved.checkpoint["technology_public"]
            assert cp["source_version"] == source.version
            assert cp["checkpoint"]["endpoints"][url]["seen"] == {"article": "signature"}
            assert saved.bootstrap_complete
    assert repo.get_binding("SNOW:siliconangle_cloud") is None
    assert repo.get_binding("SNOW:siliconangle_cloud", include_tombstoned=True).tombstoned_at
    assert not repo.get_source("siliconangle_cloud").enabled
    assert repo.list_source_revisions("siliconangle_cloud")[-len(old_versions) :] == old_versions
    assert all(not values for values in configure(bus)["migration"].values())
    assert len(SOURCES["siliconangle_cloud"].urls) == 1  # Old queued BODY still resolves.
    repo.close()


async def test_article_published_before_merge_is_not_republished_after_merge(tmp_path):
    from doxagent.message_bus_v2.schema import UpdateActor, utc_now

    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("SNOW")
    MonitoringTermsService(repo).apply(_terms().model_copy(update={"ticker": "SNOW"}), actor="test")
    old_source = source_definition("siliconangle_cloud")
    bus.register_source(old_source)
    old_binding = bus.configure_binding(
        ticker="SNOW", source_id=old_source.source_id, actor=UpdateActor.SYSTEM
    )
    now = utc_now()
    message = RawMessageInput(
        title="Snowflake announces cloud infrastructure agreement",
        body=(
            "Snowflake signed a five-year cloud infrastructure agreement. "
            "The company will invest $200 million and begin deployment in December. "
        )
        * 12,
        source="SiliconANGLE",
        url="https://siliconangle.com/2026/10/26/test-agreement/",
        published_at=now,
        raw_payload={},
        metadata={
            "body_access_scope": "public",
            "language": "en",
            "identity_evidence": {"url_kind": "article"},
        },
    )
    try:
        first = await bus.accept_message(
            source=old_source,
            binding=old_binding,
            message=message,
            bootstrap=False,
            collected_at=now,
        )
        assert first.decision.value == "inserted"
        configure(bus)
        source = repo.get_source("siliconangle_software")
        binding = repo.get_binding("SNOW:" + source.source_id)
        second = await bus.accept_message(
            source=source,
            binding=binding,
            message=message,
            bootstrap=False,
            collected_at=now,
        )
        assert second.decision.value == "duplicate"
        assert second.raw_message_id == first.raw_message_id
        assert repo.latest_stream_offset("SNOW") == 1
        assert len(repo.list_standard(ticker="SNOW")) == 1
        assert article_key(message, old_source.source_id) == article_key(message, source.source_id)
    finally:
        repo.close()
