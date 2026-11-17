"""Business gates, bounded resources, and real publication receipts."""

from dataclasses import replace
from time import time

import pytest

from doxagent.content_enrichment.publisher_cleanup import clean_message, clean_text
from doxagent.content_enrichment.quality import inspect_html
from doxagent.message_bus_v2.advanced_deduplication import (
    AdvancedDeduplication,
    Features,
    features,
    loose_url,
)
from doxagent.message_bus_v2.manifests import initial_sources


def f(**patch):
    return replace(Features(("MU", "production", "en", "public"), "test", "", "", ""), **patch)


@pytest.mark.parametrize("gate", ["url", "title", "body"])
def test_each_gate_independently_and_no_transitive_reference(gate):
    engine = AdvancedDeduplication()
    incoming = f(**{gate: "alpha beta " * 100})
    assert engine.check(incoming)[0] is None
    engine.remember(incoming, "published", time())
    assert engine.check(incoming)[0] == (gate, "published")
    assert len(engine.records) == 1  # Checks never admit suppressed inputs.


def test_threshold_and_length_bound():
    engine = AdvancedDeduplication()
    engine.remember(f(body="a" * 1000), "published", time())
    assert engine.check(f(body="a" * 950 + "b" * 50))[0] == ("body", "published")
    assert engine.check(f(body="a" * 949 + "b" * 51))[0] is None
    assert engine.check(f(body="a" * 1100))[0]  # 9% length difference can exceed 95%.


def test_scope_and_time_isolation():
    engine = AdvancedDeduplication(ttl=10)
    first = f(title="same")
    engine.remember(first, "published", time())
    for bucket in [
        ("IONQ", "production", "en", "public"),
        ("MU", "test", "en", "public"),
        ("MU", "production", "en", "private"),
        ("MU", "production", "ko", "public"),
    ]:
        assert engine.check(replace(first, bucket=bucket))[0] is None
    assert engine.check(first, now=time() + 11)[0] is None


def test_capacity_and_shared_body_release():
    engine = AdvancedDeduplication(cap=2, ticker_cap=2, body_bytes=100000)
    for i in range(3):
        engine.remember(f(title=str(i), body="same body"), str(i), time())
    assert len(engine.records) == 2 and len(engine.pool) == 1
    engine.body_limit = 0
    engine.remember(f(title="next", body="next body"), "next", time())
    assert engine.body_bytes == 0 and not engine.pool
    assert engine.check(f(title="next"))[0] == ("title", "next")


def test_budget_does_not_disable_exact_gates():
    engine = AdvancedDeduplication(budget=0)
    engine.remember(f(title="same", body="body"), "published", time())
    assert engine.check(f(body="body"))[0] is None
    assert engine.metrics["body_skipped_budget"] == 1
    assert engine.check(f(title="same"))[0] == ("title", "published")


def test_warm_ticker_does_not_wait_for_another_tickers_cold_load():
    from threading import Event, Thread

    engine = AdvancedDeduplication()
    engine.warmed['MU'] = float('inf')
    finished = Event()
    with engine.warm_lock:  # Another ticker owns the cold preload lock.
        thread = Thread(target=lambda: (engine.warm(None, 'MU'), finished.set()))
        thread.start()
        completed_while_cold_locked = finished.wait(1)
    thread.join(2)
    assert completed_while_cold_locked


def test_compatibility_finalization_prepares_outside_commit_lock(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from doxagent.message_bus_v2.schema import PublicationMode, StreamingConfig
    from tests.test_content_enrichment_hub import _setup

    repo, _, source, _ = _setup(tmp_path)
    engine = repo.advanced_dedup
    engine.warmed['MU'] = float('inf')
    incoming = SimpleNamespace(ticker='MU', source_id=source.source_id,
                               standard_message_id='new', title='stale title',
                               metadata={}, url='https://example.test/new', body='')
    committed = SimpleNamespace(**{**vars(incoming), 'title': 'retained current title',
                                   'standard_message_id': 'retained'})
    prepared = {'message': committed}
    def prepare(**kwargs):
        assert not engine.commit_lock._is_owned()
        return prepared
    def commit(value):
        assert engine.commit_lock._is_owned()
        assert value is prepared
        from doxagent.message_bus_v2.schema import utc_now
        return SimpleNamespace(published_at=utc_now())
    monkeypatch.setattr(repo, 'prepare_finalization', prepare)
    monkeypatch.setattr(repo, 'commit_finalization', commit)
    repo.finalize_standard(raw=None, message=incoming,
                           streaming=StreamingConfig(publication_mode=PublicationMode.IMMEDIATE))
    assert engine.check(features(committed, source))[0] == ('url', 'retained')
    assert engine.records['retained'][0].title == 'retained current title'
    assert 'new' not in engine.records


def test_defaults_ibkr_and_verified_google_suffix():
    from doxagent.message_bus_v2.schema import RawMessageInput

    sources = {s.source_id: s for s in initial_sources()}
    assert not sources["ibkr_news"].advanced_dedup_enabled
    assert sources["google_news_search_rss"].advanced_dedup_enabled
    message = RawMessageInput(
        url="https://example.com/a",
        title="A - actual title - Publisher",
        publisher_name="Publisher",
        raw_payload={},
    )
    # Feature extraction normally receives ticker-local Raw/Standard.
    from types import SimpleNamespace

    raw = SimpleNamespace(**message.model_dump(), ticker="MU")
    assert features(raw, sources["google_news_search_rss"]).title == "a - actual title"
    assert features(raw, sources["ibkr_news"]) is None


def test_loose_urls_keep_identity_query_and_numeric_only_ids():
    assert loose_url(
        "https://www.investing.com/news/stock-market-news/a-long-enough-article-slug-here-123456"
    ) == loose_url(
        "https://www.investing.com/news/stock-market-news/a-long-enough-article-slug-here-654321"
    )
    assert loose_url("https://example.com/article?id=1") != loose_url(
        "https://example.com/article?id=2"
    )
    assert loose_url("https://example.com/123456") != loose_url("https://example.com/654321")


def test_publisher_boundary_cleaning_and_reuse():
    qz = (
        "See today’s quantum computing news on Quantum Zeitgeist for the latest "
        "breakthroughs in qubits, hardware, algorithms, and industry deals."
    )
    text = "Research quantum computing\n\nSource: https://arxiv.org/a\n\n" + qz
    url = "https://quantumzeitgeist.com/a/"
    clean = clean_text(text, url)
    assert "Source:" in clean and "Research quantum computing" in clean and qz not in clean
    assert clean_text(clean, url) == clean
    assert clean_text(text, "https://unrelated.test/a") == text
    from doxagent.message_bus_v2.schema import RawMessageInput

    msg = RawMessageInput(
        url=url,
        body=text,
        raw_payload={},
        metadata={"media_enrichment": {"http_not_modified": True}},
    )
    assert clean_message(msg).metadata["media_enrichment"]["http_not_modified"] is False
    html = (
        "<html><head><title>Article</title></head><body><article><h1>Article</h1>"
        '<div class="article-body"><p>Actual research '
        + ("technical evidence " * 100)
        + '</p><div class="w-block-auto_featured_content"><h2>More in Cooling</h2>'
        "<p>UNRELATED DATA CENTER RECOMMENDATION</p></div></div></article></body></html>"
    )
    info = inspect_html(html, "https://www.datacenterdynamics.com/en/news/a/", "Article")
    assert info.candidates and all("UNRELATED DATA CENTER" not in c.text for c in info.candidates)
    assert "more information in the report" in clean_text(
        "more information in the report", "https://datacenterdynamics.com/a"
    )


async def test_live_exact_title_suppression_and_restart_warm(tmp_path):
    from doxagent.message_bus_v2.live import LiveQueue
    from tests.test_content_enrichment_hub import _setup
    from tests.test_cross_source_live_dedup import Extractor, capture, drain, input_message

    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    pipeline = (repo, bus, Extractor())
    capture(
        pipeline,
        "finnhub_company_news",
        input_message(9901, "https://publisher.test/a", body="first actual body"),
    )
    await drain(pipeline)
    pipeline[2].content = (
        "Entirely different semiconductor fabrication facilities expand capacity. " * 100
    )
    repo.advanced_dedup = AdvancedDeduplication()  # Same durable DB, cold process cache.
    capture(
        pipeline,
        "finnhub_company_news",
        input_message(9902, "https://publisher.test/b", body="second distinct body"),
    )
    await drain(pipeline)
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM stream_items").fetchone()[0] == 1
        row = db.execute(
            "SELECT last_error FROM live_deliveries WHERE state='DEDUPLICATED'"
        ).fetchone()
        assert row and row[0].startswith("ADV_DUPLICATE:title:")


async def test_buffer_gate_filters_members_and_empty_batch(tmp_path):
    from doxagent.message_bus_v2.schema import PublicationMode, StreamingConfig
    from tests.test_content_enrichment_hub import _message, _setup

    repo, bus, source, binding = _setup(tmp_path)
    binding = binding.model_copy(
        update={"streaming": StreamingConfig(publication_mode=PublicationMode.BUFFERED)}
    )
    repo.save_binding(binding)
    for i in range(2):
        message = _message(8800 + i, body=f"Different original evidence {i}").model_copy(
            update={"title": "Same title"}
        )
        await bus.accept_message(source=source, binding=binding, message=message, bootstrap=False)
    items = bus.flush_binding(binding.binding_id, force=True)
    assert len(items) == 1 and items[0].member_count == 1
    assert len(repo.read_stream("MU")[0].members) == 1
    assert not repo.list_buffer(binding.binding_id)
    # Another buffered copy must not produce an empty Stream item.
    await bus.accept_message(
        source=source,
        binding=binding,
        message=_message(8803, body="Third distinct evidence").model_copy(
            update={"title": "Same title"}
        ),
        bootstrap=False,
    )
    assert bus.flush_binding(binding.binding_id, force=True) == []
    assert repo.latest_stream_offset("MU") == 1
    assert not repo.list_buffer(binding.binding_id)


async def test_live_prepare_race_retries_without_admitting_uncommitted_reference(tmp_path):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.live import LiveDeliveryWorker, LiveQueue
    from tests.test_content_enrichment_hub import _setup
    from tests.test_cross_source_live_dedup import Extractor, capture, input_message

    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    extractor = Extractor()
    pipeline = (repo, bus, extractor)
    capture(pipeline, "finnhub_company_news", input_message(8810, "https://publisher.test/first"))
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    await hub.run_once()
    worker = LiveDeliveryWorker(bus)
    row = worker.repository.deliveries()[0]
    message, source, binding, _ = worker._prepare(row)
    prepared = worker.repository.prepare_publication(bus, row, source, binding, message)
    assert not repo.advanced_dedup.records
    feature, _ = prepared["advanced"]
    repo.advanced_dedup.remember(feature, "competing-committed", time())
    assert worker.repository.publish(prepared) is False
    assert repo.latest_stream_offset("MU") == 0
    await hub.close()
