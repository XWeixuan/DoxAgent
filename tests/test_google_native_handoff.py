from dataclasses import replace

import pytest

from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.live import LiveDeliveryWorker, LiveQueue
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.site_strategy.scope import current_routing
from tests.test_content_enrichment_hub import _GovernedExtractor, _message, _setup
from tests.test_content_enrichment_pipeline import BODY


class Extractor(_GovernedExtractor):
    def __init__(self):
        self.calls = []

    async def extract(self, record):
        self.calls.append((record.source_id, current_routing().origin_scope_id))
        return replace(await super().extract(record), final_url=record.url, content=BODY)


@pytest.mark.parametrize(
    "site,native",
    [
        ("barrons.com", "barrons_ticker_news"),
        ("marketwatch.com", "barrons_ticker_news"),
        ("reuters.com", "reuters_site_search"),
        ("investing.com", "investing_ticker_news"),
    ],
)
@pytest.mark.parametrize("order", ["google-first", "native-first", "simultaneous"])
async def test_native_owns_one_body_and_one_publication_regardless_of_discovery_order(
    tmp_path, site, native, order
):
    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    for sid in ("google_news_search_rss", native):
        bus.update_source(
            sid, {"enabled": True, "content_language": "en"}, actor=UpdateActor.SYSTEM
        )
        if not repo.get_binding("MU:" + sid):
            bus.configure_binding(
                ticker="MU",
                source_id=sid,
                source_parameters={"search_terms": ["Micron"]},
                actor=UpdateActor.SYSTEM,
            )
    url = "https://www." + site + "/articles/micron-manufacturing-investment-12345"
    extractor = Extractor()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    live = bus.live_queue.repository

    def capture(sid, number):
        source = bus.require_source(sid)
        message = _message(number).model_copy(
            update={
                "url": (
                    url.replace("www.", "")
                    + "/?utm_source=google"
                    + (
                        "&mod=google&siteid=rss"
                        if site in {"barrons.com", "marketwatch.com"}
                        else ""
                    )
                )
                if sid == "google_news_search_rss"
                else url
                + "?utm_source=native"
                + ("&mod=native" if site in {"barrons.com", "marketwatch.com"} else ""),
                "title": "Micron announces manufacturing investment",
                "summary": "Google RSS title-link summary"
                if sid == "google_news_search_rss"
                else "Native publisher synopsis",
                "metadata": {"language": "en", "identity_evidence": {"url_kind": "article"}},
            }
        )
        return live.accept(
            live.prepare_input(
                source,
                [("MU:" + sid, "MU", 0)],
                [message],
                observed_at=utc_now().isoformat(),
                unit="HEAD",
            )
        )

    async def drain():
        for _ in range(3):
            await hub.run_once()
            await LiveDeliveryWorker(bus).run_available()

    try:
        if order == "native-first":
            capture(native, 1)
            await drain()
        capture("google_news_search_rss", 2)
        if order == "simultaneous":
            capture(native, 1)
        await drain()
        capture(native, 1)
        await drain()
        assert extractor.calls == [(native, "source:" + native)]
        with repo._connect() as db:
            rows = db.execute("SELECT source_id FROM standard_messages").fetchall()
            assert [r[0] for r in rows] == [native]
            assert db.execute("SELECT count(*) FROM stream_items").fetchone()[0] == 1
            assert (
                db.execute("SELECT count(*) FROM live_articles WHERE state!='MERGED'").fetchone()[0]
                == 1
            )
            assert db.execute(
                "SELECT count(*) FROM audit_log WHERE action='GOOGLE_NATIVE_HANDOFF'"
            ).fetchone()[0] == 1
            assert (
                db.execute(
                    "SELECT count(*) FROM live_origins WHERE source_id='google_news_search_rss'"
                ).fetchone()[0]
                >= 1
            )
    finally:
        await hub.close()


async def test_no_native_binding_leaves_google_work_and_does_not_create_subscription(tmp_path):
    from doxagent.message_bus_v2.google_handoff import prepare

    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    bus.configure_binding(
        ticker="MU",
        source_id="google_news_search_rss",
        source_parameters={"search_terms": ["Micron"]},
        actor=UpdateActor.SYSTEM,
    )
    source = bus.require_source("google_news_search_rss")
    source_native = bus.require_source("barrons_ticker_news")
    if source_native.enabled:
        bus.update_source(source_native.source_id, {"enabled": False}, actor=UpdateActor.SYSTEM)
    message = _message(1).model_copy(update={"url": "https://www.barrons.com/articles/story"})
    live = bus.live_queue.repository
    live.accept(
        live.prepare_input(
            source,
            [("MU:google_news_search_rss", "MU", 0)],
            [message],
            observed_at=utc_now().isoformat(),
            unit="HEAD",
        )
    )
    job = (await bus.live_queue.claim(1))[0]
    assert prepare(live, job, message.url) is None


async def test_google_exhausted_plan_does_not_restart_primary_and_fallback(tmp_path):
    from doxagent.monitoring.media_enrichment import MediaExtractionResult

    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    binding = bus.configure_binding(
        ticker="MU",
        source_id="google_news_search_rss",
        source_parameters={"search_terms": ["Micron"]},
        actor=UpdateActor.SYSTEM,
    )
    calls = []

    class Failed:
        async def extract(self, record):
            calls.append(record.url)
            return MediaExtractionResult(
                record=record, reason="transport_timeout", diagnostics={"outcome": "UNAVAILABLE"}
            )

    source = bus.require_source(binding.source_id)
    message = _message(1).model_copy(update={"url": "https://publisher.test/articles/one"})
    live = bus.live_queue.repository
    live.accept(
        live.prepare_input(
            source,
            [(binding.binding_id, "MU", 0)],
            [message],
            observed_at=utc_now().isoformat(),
            unit="HEAD",
        )
    )
    hub = ContentEnrichmentHub(repo, bus, extractor=Failed())
    try:
        await hub.run_once()
        await hub.run_once()
        assert len(calls) == 1
        with repo._connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM live_articles "
                    "WHERE state='READY' AND outcome='UNAVAILABLE'"
                ).fetchone()[0]
                == 1
            )
    finally:
        await hub.close()


def test_native_canonical_preserves_article_selecting_parameters():
    from doxagent.message_bus_v2.google_handoff import canonical_url

    assert (
        canonical_url("http://barrons.com/articles/story/?mod=rss&siteid=feed&article=1")
        == "https://www.barrons.com/articles/story?article=1"
    )
    assert canonical_url("https://www.barrons.com/articles/story?article=1") != canonical_url(
        "https://www.barrons.com/articles/story?article=2"
    )
    assert (
        canonical_url("http://other.example/story?mod=one") == "http://other.example/story?mod=one"
    )
