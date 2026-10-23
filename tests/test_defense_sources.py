from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.content_enrichment.quality import choose_candidate, inspect_html, title_match
from doxagent.message_bus_v2.defense_body import DefenseBodyReader
from doxagent.message_bus_v2.defense_sources import (
    CATALOG,
    ENDPOINTS,
    Capture,
    DefenseAdapter,
    initial_sources,
    parse_listing,
    parse_search,
)
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import RawMessageInput, SharedPollContext, UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from scripts.ops.configure_defense_sources import configure

FIXTURES = Path(__file__).parent / "fixtures" / "defense_sources"
NOW = datetime(2026, 10, 24, tzinfo=UTC)
FILES = dict(
    zip(
        [e.source_id for e in CATALOG],
        [
            "aviation_rocketlab.html",
            "aviation_feed.xml",
            "breaking_de.xml",
            "scoop_space.xml",
            "air_space.xml",
            "spacecom.xml",
            "spacenews.xml",
            "ssc.html",
            "nasa.json",
            "dod_contracts.xml",
            "dod_releases_de.xml",
            "iridium.html",
            "calcalist.jsonp",
            "scoop_drones.xml",
            "unmanned_systems.xml",
            "shephard.xml",
            "army_releases.html",
            "army_feed.xml",
        ],
        strict=True,
    )
)


@asynccontextmanager
async def permit():
    yield


def context(source_id, checkpoint=None, at=NOW):
    return SharedPollContext(
        run_id="test",
        source=next(s for s in initial_sources() if s.source_id == source_id),
        checkpoint=checkpoint or {},
        requested_at=at,
        request_permit=permit,
    )


@pytest.mark.parametrize("source_id", FILES)
def test_real_endpoint_contracts(source_id):
    e = ENDPOINTS[source_id]
    raw = (FIXTURES / FILES[source_id]).read_text(encoding="utf8")
    rows = (
        parse_search(raw, e, NOW)[0]
        if e.parser == "calcalist"
        else parse_listing(e, raw, e.urls[0], NOW)
    )
    assert rows
    assert all(m.url.startswith("https://" + e.host + "/") for m in rows)
    assert all(m.title and m.published_at <= NOW for m in rows)
    assert (
        all(m.publication_time_basis == "DATE" for m in rows)
        if e.parser in {"ssc", "army", "iridium"}
        else True
    )
    assert all(m.body is None for m in rows)
    if source_id == "spacenews_rss":
        assert rows[0].metadata["defense_media_format"] == "video_or_podcast"


def test_same_site_shares_scheduler_and_no_default_ticker_fanout():
    sources = {s.source_id: s for s in initial_sources()}
    assert len(sources) == 18
    assert (
        sources["aviationweek_rss"].scheduler_group
        == sources["aviationweek_rocketlab"].scheduler_group
    )
    assert (
        sources["defensescoop_drones"].scheduler_group
        == sources["defensescoop_space"].scheduler_group
    )
    assert sources["army_news_rss"].scheduler_group == sources["army_news_releases"].scheduler_group
    assert sources["dod_releases"].scheduler_group == sources["dod_contracts"].scheduler_group
    assert all(s.scheduler_constraints.max_concurrency == 2 for s in sources.values())
    assert all(s.default_polling_config.target_interval_seconds == 60 for s in sources.values())
    assert sources["dod_contracts"].distribution_policy.require_complete_body


async def test_sibling_feed_failure_preserves_healthy_rows_and_validator():
    e = ENDPOINTS["defensescoop_drones"]
    raw = (FIXTURES / "scoop_drones.xml").read_text(encoding="utf8")

    async def fetch(url, endpoint, **kwargs):
        return (
            Capture(503, url, "unavailable", {})
            if "counter-drone" in url
            else Capture(200, url, raw, {"etag": "healthy"})
        )

    adapter = DefenseAdapter(SimpleNamespace(fetch=fetch))
    result = await adapter.poll_shared(context(e.source_id))
    assert len(result.messages) == 2
    assert len(result.failures) == 1 and result.window_coverage == "PARTIAL"
    assert result.next_checkpoint["endpoints"][e.urls[0]]["etag"] == "healthy"
    assert "baseline" not in result.next_checkpoint["endpoints"][e.urls[1]]


async def test_publisher_rate_limit_defers_without_busy_retry():
    e = ENDPOINTS["spacenews_rss"]
    http = SimpleNamespace(
        fetch=AsyncMock(return_value=Capture(429, e.urls[0], "limited", {"retry-after": "180"}))
    )
    result = await DefenseAdapter(http).poll_shared(context(e.source_id))
    assert result.site_access_deferred and not result.window_done
    assert result.site_access_retry_not_before == NOW + timedelta(seconds=180)
    assert http.fetch.await_count == 1 and not result.messages


async def test_304_requires_parsed_baseline_and_unknown_date_never_moves():
    e = ENDPOINTS["shephard_uncrewed"]
    raw = (FIXTURES / "shephard.xml").read_text(encoding="utf8")
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(304, e.urls[0], "", {})))
    adapter = DefenseAdapter(http)
    with pytest.raises(RuntimeError, match="304_without_baseline"):
        await adapter.poll_shared(context(e.source_id))
    http.fetch.return_value = Capture(200, e.urls[0], raw, {"etag": "abc"})
    first = await adapter.poll_shared(context(e.source_id))
    later = await adapter.poll_shared(
        context(e.source_id, first.next_checkpoint, NOW + timedelta(days=1))
    )
    assert first.messages[0].published_at == later.messages[0].published_at
    http.fetch.return_value = Capture(304, e.urls[0], "", {})
    result = await adapter.poll_shared(context(e.source_id, later.next_checkpoint))
    assert not result.messages and not result.failures
    assert http.fetch.call_args.kwargs["headers"] == {"If-None-Match": "abc"}


async def test_unchanged_json_rows_skip_bus_intake_but_changes_and_heartbeat_remain():
    e = ENDPOINTS["nasa_2026_releases"]
    raw = (FIXTURES / "nasa.json").read_text(encoding="utf8")
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, e.urls[0], raw, {})))
    adapter = DefenseAdapter(http)
    first = await adapter.poll_shared(context(e.source_id))
    second = await adapter.poll_shared(
        context(e.source_id, first.next_checkpoint, NOW + timedelta(seconds=60))
    )
    assert first.messages and not second.messages
    assert second.acquisition_metadata["endpoints"][0]["unchanged_items"] == len(first.messages)
    import json

    changed = json.loads(raw)
    changed[0]["content"]["rendered"] += "<p>A new mission milestone was announced.</p>"
    http.fetch.return_value = Capture(200, e.urls[0], json.dumps(changed), {})
    third = await adapter.poll_shared(
        context(e.source_id, second.next_checkpoint, NOW + timedelta(seconds=120))
    )
    assert len(third.messages) == 1
    assert (
        third.messages[0].metadata["identity_evidence"]["provider_version"]
        != first.messages[0].metadata["identity_evidence"]["provider_version"]
    )
    heartbeat = await adapter.poll_shared(
        context(e.source_id, third.next_checkpoint, NOW + timedelta(minutes=5))
    )
    assert len(heartbeat.messages) == len(first.messages)


def test_hebrew_headline_identity_is_not_empty_or_universal():
    assert title_match(
        "אונדס זכתה בחוזה חדש למערכת רחפנים", "אונדס זכתה בחוזה חדש למערכת רחפנים | כלכליסט"
    )
    assert not title_match("אונדס זכתה בחוזה חדש", "מחירי הדירות עולים בתל אביב")


def terms(ticker):
    literal = "Ondas" if ticker == "ONDS" else "Rocket Lab"
    return TickerMonitoringTerms.model_validate(
        {
            "ticker": ticker,
            "expected_revision": 0,
            "l1_concepts": [
                {
                    "concept_id": "company",
                    "expressions": {k: literal for k in ["en", "ko", "zh-Hant"]},
                }
            ],
            "l2": {
                k: {"groups": [{"id": "company", "any": [{"literal": literal}]}]}
                for k in ["en", "ko", "zh-Hant"]
            },
            "definition": {"relevant": literal, "irrelevant": "unrelated"},
        }
    )


def test_subscription_migration_is_atomic_idempotent_and_hebrew_is_scoped(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    svc = MonitoringTermsService(repo)
    for ticker in ["RKLB", "ONDS"]:
        bus.start_ticker(ticker)
        svc.apply(terms(ticker), actor="test")
    result = configure(bus)
    assert len(result["bindings"]) == 20
    assert (
        len([b for b in repo.list_bindings() if b.source_id in ENDPOINTS and b.ticker == "RKLB"])
        == 12
    )
    assert (
        len([b for b in repo.list_bindings() if b.source_id in ENDPOINTS and b.ticker == "ONDS"])
        == 8
    )
    assert "he" in svc.required_languages("ONDS")
    assert "he" not in svc.required_languages("RKLB")
    assert "he" not in svc.required_languages("MU")
    again = configure(bus)
    assert again["terms"] is None
    assert all(b["action"] == "unchanged" for b in again["bindings"])
    assert svc.get("ONDS")[1].l1_concepts[0].expressions["he"] == "אונדס"


def test_shared_state_survives_a_new_run_without_leaking_to_sweep(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    bus.start_ticker("RKLB")
    binding = bus.configure_binding(
        ticker="RKLB", source_id="spacenews_rss", actor=UpdateActor.SYSTEM
    )
    scheduler = GlobalPollScheduler.__new__(GlobalPollScheduler)
    scheduler.repository = repo
    source = repo.get_source(binding.source_id)
    state = {"endpoints": {source.entry_url: {"baseline": True, "etag": "v1"}}}
    scheduler._record_distribution_poll(
        source,
        [(binding, 1, {})],
        {"created_at": NOW.isoformat(), "mode": "REALTIME", "coverage": "COMPLETE"},
        acquisition_checkpoint=state,
    )
    saved = repo.get_poll_state(binding).checkpoint["defense_public"]
    assert saved == {"source_version": source.version, "checkpoint": state}
    scheduler._record_distribution_poll(
        source,
        [(binding, 1, {})],
        {
            "created_at": (NOW + timedelta(seconds=1)).isoformat(),
            "mode": "CLOSED_SWEEP",
            "coverage": "PARTIAL",
        },
        acquisition_checkpoint={"wrong": True},
    )
    assert repo.get_poll_state(binding).checkpoint["defense_public"] == saved


async def test_teaser_and_paywall_never_become_full_or_invoke_browser():
    title = "Rocket Lab wins new satellite agreement"
    url = "https://aviationweek.com/space/test"
    raw = (
        "<html><h1>"
        + title
        + '</h1><script type="application/ld+json">{"@type":"NewsArticle","headline":"'
        + title
        + '","isAccessibleForFree":false}</script><article>'
        + "<p>Subscribe to read this article.</p></article></html>"
    )
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, url, raw, {})))
    reader = DefenseBodyReader(http)
    job = SimpleNamespace(
        source=next(s for s in initial_sources() if s.source_id == "aviationweek_rocketlab"),
        message=RawMessageInput(
            title=title, url=url, raw_payload={"description": "A very long teaser. " * 200}
        ),
    )
    record = MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=job.source.source_id,
        ticker="RKLB",
        title=title,
        body="",
        url=url,
    )
    result = await reader.extract(job, record)
    assert not result.succeeded
    assert http.fetch.await_count == 1


async def test_official_nasa_full_field_reuses_without_detail_request():
    title = "NASA announces new launch contract"
    url = "https://www.nasa.gov/news-release/test/"
    http = SimpleNamespace(fetch=AsyncMock(side_effect=AssertionError("must reuse official API")))
    message = RawMessageInput(
        title=title,
        url=url,
        raw_payload={
            "article_url": url,
            "article_html": (
                "<p>NASA awarded a new launch agreement for scientific payloads. "
                "The mission will carry instruments into orbit and provide new "
                "observations for researchers.</p>"
            )
            * 12,
        },
    )
    job = SimpleNamespace(
        source=next(s for s in initial_sources() if s.source_id == "nasa_2026_releases"),
        message=message,
    )
    record = MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=job.source.source_id,
        ticker="RKLB",
        title=title,
        body="",
        url=url,
    )
    result = await DefenseBodyReader(http).extract(job, record)
    assert result.succeeded and result.diagnostics["outcome"] in {"FULL", "SHORT_FULL"}
    assert result.extraction_method == "defense_official_article_api"
    http.fetch.assert_not_awaited()


async def test_missing_feed_date_is_replaced_only_with_exact_article_evidence():
    title = "Satellite company announces new contract"
    url = "https://www.shephardmedia.com/news/uv-online/test/"
    raw = (
        "<html><h1>"
        + title
        + '</h1><script type="application/ld+json">{"@type":"NewsArticle","headline":"'
        + title
        + '","datePublished":"2026-05-20T14:55:00+01:00"}</script><article><p>'
        + (
            "The satellite company announced a new contract with a government agency. "
            "The customer will deploy the system after qualification testing. "
        )
        * 12
        + "</p></article></html>"
    )
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, url, raw, {})))
    job = SimpleNamespace(
        source=next(s for s in initial_sources() if s.source_id == "shephard_uncrewed"),
        message=RawMessageInput(title=title, url=url, raw_payload={}),
    )
    record = MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=job.source.source_id,
        ticker="ONDS",
        title=title,
        body="",
        url=url,
    )
    result = await DefenseBodyReader(http).extract(job, record)
    assert result.succeeded
    assert result.diagnostics["publisher_published_at"] == "2026-05-20T14:55:00+01:00"


def test_initial_company_terms_are_created_only_when_absent(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repo)
    bus.bootstrap()
    for ticker in ["RKLB", "ONDS"]:
        bus.start_ticker(ticker)
    first = configure(bus)
    assert {r["ticker"] for r in first["initial_terms"]} == {"RKLB", "ONDS"}
    assert len(first["bindings"]) == 20
    second = configure(bus)
    assert second["initial_terms"] == [] and second["terms"] is None


@pytest.mark.parametrize("source_id", ["breakingdefense_space", "calcalist_search"])
async def test_primary_body_is_selected_instead_of_structured_sidebar(source_id):
    e = ENDPOINTS[source_id]
    title = "Rocket Lab wins a new launch contract"
    body = (
        "The government awarded Rocket Lab a new launch contract for scientific satellites. "
        "The mission will deploy several payloads after the qualification campaign. "
    ) * 15
    primary = (
        '<div class="post-single__content"><p>' + body + "</p></div>"
        if source_id == "breakingdefense_space"
        else '<div id="ArticleBodyComponent"><span>' + body + "</span></div>"
    )
    raw = (
        "<html><h1>"
        + title
        + "</h1>"
        + primary
        + '<aside><div class="article-body"><p>Sidebar preview. '
        + "Read this unrelated defense story for a short overview. " * 4
        + "</p></div></aside></html>"
    )
    url = "https://" + e.host + "/article/example"
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, url, raw, {})))
    message = RawMessageInput(title=title, url=url, raw_payload={})
    job = SimpleNamespace(
        source=next(s for s in initial_sources() if s.source_id == source_id), message=message
    )
    record = MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=source_id,
        ticker=e.tickers[0],
        title=title,
        body="",
        url=url,
    )
    result = await DefenseBodyReader(http).extract(job, record)
    assert result.succeeded
    assert "government awarded" in result.content
    assert "Sidebar preview" not in result.content


def test_view_original_image_is_not_a_publisher_redirect():
    title = "Army announces a new unmanned systems program"
    body = (
        "The Army has announced an unmanned systems program to support field units. "
        "Engineers will evaluate the platform during exercises before procurement. "
    ) * 12
    raw = (
        "<html><h1>"
        + title
        + '</h1><div class="article-body"><p>'
        + body
        + '</p><a href="https://api.army.mil/images/original.jpg">View original</a></div></html>'
    )
    info = inspect_html(raw, "https://www.army.mil/article/123/example", title)
    assert info.publisher_links == []
    assert choose_candidate(info, title)[0] is not None


async def test_feed_video_intro_is_not_a_complete_transcript():
    e = ENDPOINTS["spacenews_rss"]
    title = "Interview about new satellite networks"
    url = "https://spacenews.com/interview-about-new-satellite-networks/"
    raw = (
        "<h1>"
        + title
        + "</h1><article><p>"
        + (
            "This interview introduces the company's satellite network program. "
            "The discussion covers development schedules and new customers. "
        )
        * 12
        + "</p></article>"
    )
    message = RawMessageInput(
        title=title, url=url, raw_payload={}, metadata={"defense_media_format": "video_or_podcast"}
    )
    job = SimpleNamespace(
        source=next(s for s in initial_sources() if s.source_id == e.source_id), message=message
    )
    http = SimpleNamespace(fetch=AsyncMock(return_value=Capture(200, url, raw, {})))
    record = MediaEnrichmentRecord(
        standard_message_id="s",
        raw_message_id="r",
        source_id=e.source_id,
        ticker="RKLB",
        title=title,
        body="",
        url=url,
    )
    result = await DefenseBodyReader(http).extract(job, record)
    assert not result.succeeded and result.reason == "unsupported_media"
