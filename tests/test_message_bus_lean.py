import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from doxagent.site_strategy.budget import JointBudget
from doxagent.site_strategy.schema import SitePurpose
from doxagent.message_bus_v2.publisher_http import PublisherClients, PublisherSession


async def test_joint_failure_before_admission_does_not_leak_any_counter():
    budget = JointBudget(max_concurrency=2)

    def broken(*_):
        raise sqlite3.OperationalError("database is locked")

    budget.identity_interval = broken
    with pytest.raises(sqlite3.OperationalError):
        async with budget.permit(
            SitePurpose.BODY,
            site_key="a",
            identity_id="a",
            site_max_concurrency=2,
            site_min_interval_ms=0,
            identity_max_concurrency=2,
            identity_min_interval_ms=0,
            timeout_seconds=1,
        ):
            pytest.fail("failed state lookup cannot admit work")
    assert budget._active == budget._body_active == 0
    assert not any(budget._site_active.values())
    assert not any(budget._identity_active.values())
    assert not budget._waiters


async def test_slow_identity_state_read_does_not_block_unrelated_admission():
    import threading

    budget = JointBudget(max_concurrency=2)
    release = threading.Event()

    def interval(identity, value):
        if identity == "slow":
            release.wait(2)
        return value

    budget.identity_interval = interval

    async def work(identity):
        async with budget.permit(
            SitePurpose.CRAWLER,
            site_key=identity,
            identity_id=identity,
            site_max_concurrency=2,
            site_min_interval_ms=0,
            identity_max_concurrency=2,
            identity_min_interval_ms=0,
            timeout_seconds=1,
        ):
            return identity

    slow = asyncio.create_task(work("slow"))
    try:
        assert await asyncio.wait_for(work("fast"), 0.5) == "fast"
    finally:
        release.set()
        await slow


async def test_curl_setup_failure_returns_handle_capacity(monkeypatch):
    from curl_cffi.requests import AsyncSession

    async def bad_setup(self, **_):
        await self.pop_curl()
        raise ValueError("bad options")

    monkeypatch.setattr(AsyncSession, "_request_once", bad_setup)
    session = PublisherSession(max_clients=2)
    try:
        for _ in range(4):
            with pytest.raises(ValueError):
                await session.request("GET", "https://example.test/")
            assert session.pool.qsize() == 2
    finally:
        await session.close()


async def test_curl_cleanup_failure_discards_handle_without_losing_slot():
    session = PublisherSession(max_clients=2)
    session.pool.get_nowait()

    class Broken:
        def clean_handles_and_buffers(self):
            raise RuntimeError("curl cleanup failed")

        def close(self):
            self.closed = True

    curl = Broken()
    session.release_curl(curl)
    assert curl.closed and session.broken
    assert session.pool.qsize() == 2
    await session.close()


async def test_publisher_client_recovery_does_not_replace_unrelated_publisher():
    class Session:
        broken = False
        closed = False

        async def request(self, method, url, **_):
            if "broken" in url:
                self.broken = True
            return SimpleNamespace(status_code=200)

        async def close(self):
            self.closed = True

    clients = PublisherClients(factory=Session)
    await clients.request("healthy", "GET", "https://healthy.test/")
    good = clients.clients["healthy"].session
    await clients.request("bad", "GET", "https://broken.test/")
    assert "bad" not in clients.clients
    await clients.request("bad", "GET", "https://recovered.test/")
    assert clients.clients["healthy"].session is good and not good.closed
    await clients.close()


async def test_empty_failure_checkpoint_advances_across_restart(tmp_path):
    from tests.test_message_collection_plane import setup_plane
    from doxagent.message_bus_v2.schema import AcquisitionFailure, PollResult

    repo, bus, worker, adapter, store = setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    source, targets, unit, options = next(iter(worker.desired.values()))

    async def fail(context):
        count = context.checkpoint.get("failures", 0) + 1
        return PollResult(
            messages=[],
            next_checkpoint={"failures": count},
            failures=[
                AcquisitionFailure(
                    source_id=source.source_id,
                    binding_id=targets[0].binding_id,
                    ticker=targets[0].ticker,
                    error_code="TEMPORARY",
                    error_message="temporary",
                    raw_hash="fixture",
                    original_payload={},
                )
            ],
        )

    adapter.poll = fail
    await worker.acquire(source, targets, unit, options)
    assert store.states()[(targets[0].binding_id, unit)]["state"]["checkpoint"]["failures"] == 1
    await worker.refresh(seeds=True)
    await worker.acquire(source, targets, unit, options)
    assert store.states()[(targets[0].binding_id, unit)]["state"]["checkpoint"]["failures"] == 2


def test_lease_renewal_does_not_expand_job_or_read_native_content(tmp_path, monkeypatch):
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.content_enrichment.schema import EnrichmentJob
    from doxagent.message_bus_v2.schema import utc_now
    from datetime import timedelta

    repo, _, source, binding = _setup(tmp_path)
    now = utc_now()
    repo.enqueue_enrichment_job(
        EnrichmentJob(
            job_id="lease",
            intake_key="lease",
            not_before=now,
            poll_run_id="test",
            source=source,
            binding=binding,
            message=_message(1),
            deadline_at=now + timedelta(seconds=180),
        )
    )
    job = repo.claim_enrichment_jobs(limit=1, now=now)[0]
    monkeypatch.setattr(repo.content, "text", lambda *_: pytest.fail("lease hydrated job"))
    repo.renew_enrichment_claim(job.job_id, job.claim_token)
    with pytest.raises(RuntimeError, match="lease_lost"):
        repo.renew_enrichment_claim(job.job_id, "stale-owner")


async def test_live_article_fetches_once_and_publishes_without_legacy_relays(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.schema import utc_now

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    rows = live.prepare_input(
        source,
        [(binding.binding_id, binding.ticker, 0)],
        [_message(150)],
        observed_at=utc_now().isoformat(),
        unit="HEAD",
    )
    live.accept(rows, capture_id="one")
    live.accept(rows, capture_id="repeated")
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM live_articles").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM content_enrichment_jobs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM poll_captures").fetchone()[0] == 0
    hub = ContentEnrichmentHub(repo, bus, extractor=_GovernedExtractor())
    assert await hub.run_once() == 1
    worker = LiveDeliveryWorker(bus)
    assert await worker.run_available() == 1
    assert len(bus.pending_stream("live-test", "MU")) == 1
    assert await hub.run_once() == 0
    assert await worker.run_available() == 0
    assert live.get(rows[0]["key"])["outcome"] == "FULL"
    assert not list(live.spool.glob("*.json"))


async def test_live_body_result_survives_commit_failure_without_refetch(tmp_path, monkeypatch):
    from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.schema import utc_now

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    rows = live.prepare_input(
        source,
        [(binding.binding_id, binding.ticker, 0)],
        [_message(151)],
        observed_at=utc_now().isoformat(),
        unit="HEAD",
    )
    live.accept(rows)

    class Extractor(_GovernedExtractor):
        calls = 0

        async def extract(self, record):
            self.calls += 1
            return await super().extract(record)

    extractor = Extractor()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    commit = live.commit_result

    def busy(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(live, "commit_result", busy)
    await hub.run_once()
    assert extractor.calls == 1 and list(live.spool.glob("*.json"))
    with repo.transaction() as db:
        db.execute("UPDATE live_articles SET lease_until='2000-01-01'")
    monkeypatch.setattr(live, "commit_result", commit)
    assert await hub.run_once() == 0
    assert extractor.calls == 1 and live.get(rows[0]["key"])["outcome"] == "FULL"


async def test_curl_evidence_is_emitted_at_actual_wire_send():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from doxagent.message_bus_v2.poll_trace import REQUESTS

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    token = REQUESTS.set(events)
    clients = PublisherClients()
    try:
        response = await clients.request(
            "local", "GET", f"http://127.0.0.1:{server.server_port}/feed"
        )
        assert response.status_code == 200
        assert response.bus_sent_at and len(events) == 1
        assert events[0]["proof_basis"] == "TRANSPORT_SEND"
        assert events[0]["physical_request_id"] == response.bus_request_event["physical_request_id"]
    finally:
        await clients.close()
        REQUESTS.reset(token)
        await asyncio.to_thread(server.shutdown)
        server.server_close()


async def test_twenty_tickers_share_one_body_and_article_once_update(
    tmp_path, monkeypatch, record_property
):
    from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.schema import utc_now

    repo, bus, source, binding = _setup(tmp_path)
    from contextlib import contextmanager
    from contextvars import ContextVar
    import time

    writing = ContextVar("test_business_writer", default=False)
    transaction = repo.transaction
    durations = []

    @contextmanager
    def measured_transaction():
        started = time.monotonic()
        with transaction() as db:
            token = writing.set(True)
            try:
                yield db
            finally:
                writing.reset(token)
        durations.append((time.monotonic() - started) * 1000)

    monkeypatch.setattr(repo, "transaction", measured_transaction)
    for name in ("put", "read_all"):
        original = getattr(repo.content.files, name)

        def checked(*args, _operation=original, **kwargs):
            assert not writing.get(), "Native I/O under business writer"
            return _operation(*args, **kwargs)

        monkeypatch.setattr(repo.content.files, name, checked)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    targets = []
    for i in range(20):
        ticker = f"TEST{i}"
        sibling = binding.model_copy(
            update={"binding_id": f"{ticker}:{source.source_id}", "ticker": ticker}
        )
        repo.save_binding(sibling)
        targets.append((sibling.binding_id, ticker, 0))
    message = _message(160)
    rows = live.prepare_input(
        source, targets, [message], observed_at=utc_now().isoformat(), unit="SHARED"
    )
    live.accept(rows)

    class Extractor(_GovernedExtractor):
        calls = 0

        async def extract(self, record):
            self.calls += 1
            value = await super().extract(record)
            from dataclasses import replace
            from tests.test_message_bus_v2_deduplication import BODY

            return replace(value, content=BODY * 100)

    extractor = Extractor()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    assert await hub.run_once() == 1
    worker = LiveDeliveryWorker(bus)
    assert await worker.run_available() == 20
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM stream_items").fetchone()[0] == 20
        assert db.execute("SELECT count(*) FROM live_articles").fetchone()[0] == 1
        assert (
            db.execute("SELECT sum(length(data_json)) FROM standard_messages").fetchone()[0]
            < 600_000
        )
        standard_id = db.execute(
            "SELECT standard_message_id FROM standard_messages LIMIT 1"
        ).fetchone()[0]
    assert len(repo.get_standard(standard_id).body) > 100_000
    assert extractor.calls == 1
    # A material change checks the article once; a changed real body revises all
    # existing publications without another downstream stream item.
    changed = message.model_copy(update={"title": message.title + " updated"})
    live.accept(
        live.prepare_input(
            source, targets, [changed], observed_at=utc_now().isoformat(), unit="SHARED"
        )
    )
    claims = await bus.live_queue.claim(1)
    assert len(claims) == 1
    row = live.get(rows[0]["key"])
    completed = live.load(row["result_ref"])
    from doxagent.message_bus_v2.schema import RawMessageInput

    updated = RawMessageInput.model_validate(completed).model_copy(
        update={"body": completed["body"] + " Important new detail."}
    )
    await bus.live_queue.finish(claims[0], updated)
    assert await worker.run_available() == 20
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM stream_items").fetchone()[0] == 20
        assert db.execute("SELECT count(*) FROM raw_messages").fetchone()[0] == 20
        assert db.execute("SELECT count(*) FROM message_content_revisions").fetchone()[0] == 40
    corrected = message.model_copy(update={"title": "Corrected material headline"})
    live.accept(
        live.prepare_input(
            source, targets, [corrected], observed_at=utc_now().isoformat(), unit="SHARED"
        )
    )
    owner = (await bus.live_queue.claim(1))[0]
    fixed = updated.model_copy(update={"title": corrected.title})
    await bus.live_queue.finish(owner, fixed)
    assert await worker.run_available() == 20
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM stream_items").fetchone()[0] == 20
        assert db.execute("SELECT count(*) FROM message_content_revisions").fetchone()[0] == 60
    assert repo.get_standard(standard_id).title == corrected.title
    ordered = sorted(durations)
    record_property("writer_transactions", len(durations))
    record_property("writer_max_ms", max(durations))
    record_property("writer_p99_ms", ordered[max(0, int(len(ordered) * 0.99) - 1)])
    assert max(durations) < 1000


def test_native_cache_does_not_repeat_decode_and_detects_changed_chunk(tmp_path, monkeypatch):
    import json
    from doxagent.v2_read.native_content import NativeContent, MARKER
    from doxagent.v2_read.content_files import ContentUnavailable

    codec = NativeContent(tmp_path / "bus.sqlite3", cache_bytes=32 * 1024 * 1024)
    value = {"text": "x" * 200000}
    raw = json.dumps(value).encode()
    identity = codec.files.put(raw)
    ref = {MARKER: identity, "bytes": len(raw)}
    first = codec.decode(ref)
    first["text"] = "mutated"
    read = codec.files.read_all
    monkeypatch.setattr(codec.files, "read_all", lambda *_: pytest.fail("warm value re-read"))
    assert codec.decode(ref) == value
    monkeypatch.setattr(codec.files, "read_all", read)
    (codec.files._path(identity) / "0").write_bytes(b"corrupt")
    with pytest.raises(ContentUnavailable):
        codec.decode(ref)


def test_caller_observation_and_ambiguous_requests_do_not_prove_freshness():
    from doxagent.message_bus_v2.poll_trace import validate_listing

    a = {
        "physical_request_id": "a",
        "response_received_at": "now",
        "status_code": 200,
        "proof_basis": "CALLER_OBSERVATION",
    }
    b = {**a, "physical_request_id": "b", "proof_basis": "TRANSPORT_SEND"}
    c = {**b, "physical_request_id": "c"}
    assert validate_listing([a]) == []
    assert validate_listing([b, c]) == []
    assert validate_listing([b, c], ["b"]) == [b] and not c.get("list_validated")


async def test_frozen_legacy_job_transfers_once_and_retains_wait_contract(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.content_enrichment.service import ContentEnrichmentHub

    repo, bus, source, binding = _setup(tmp_path)
    job, _ = bus.enqueue_enrichment(
        source=source, binding=binding, message=_message(180), bootstrap=False, poll_run_id="frozen"
    )
    bus.live_queue = LiveQueue(bus)
    hub = ContentEnrichmentHub(repo, bus, extractor=_GovernedExtractor())
    assert await hub.run_once() == 1
    assert repo.pending_enrichment_ids([job.job_id])
    worker = LiveDeliveryWorker(bus)
    await worker.run_available()
    assert repo.pending_enrichment_ids([job.job_id]) == []
    assert len(bus.pending_stream("legacy-transfer", "MU")) == 1
    assert await hub.run_once() == 0


async def test_live_projection_preserves_body_cohort_and_stream_wire(tmp_path):
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.v2_read.repository import ReadStore
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.projector import ProjectionWorker
    from doxagent.v2_read.projectors import DomainProjectors
    from doxagent.api_v2.news_timeliness import body_cohort
    from tests.test_content_enrichment_hub import _message, _GovernedExtractor
    from doxagent.message_bus_v2.schema import utc_now

    repo = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    source_outbox = SourceOutbox(repo.path, "bus")
    source_outbox.migrate()
    bus = MessageBusV2Service(repo, enrichment_queue_enabled=True)
    bus.bootstrap()
    bus.start_ticker("MU")
    from doxagent.v2_control.mirror import apply

    with repo.transaction() as db:
        apply(
            db,
            {
                "ticker": "MU",
                "epoch": 1,
                "revision": 1,
                "mode": "MESSAGE_MONITORING",
                "analysis_allowed": True,
            },
        )
    source = bus.require_source("finnhub_company_news")
    binding = repo.get_binding("MU:finnhub_company_news")
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    live.accept(
        live.prepare_input(
            source,
            [(binding.binding_id, "MU", 0)],
            [_message(181)],
            observed_at=utc_now().isoformat(),
            unit="HEAD",
        )
    )
    hub = ContentEnrichmentHub(repo, bus, extractor=_GovernedExtractor())
    await hub.run_once()
    await LiveDeliveryWorker(bus).run_available()
    store = ReadStore(tmp_path / "read.sqlite3")
    store.migrate()
    projector = ProjectionWorker(store, [source_outbox], DomainProjectors(store))
    for _ in range(30):
        projector.tick(limit=500)
        if store.position("bus") >= source_outbox.head():
            break
    with store.connect() as db:
        seq = store.highwater(db)
    cohort = body_cohort(store, "MU", seq, {}, None)
    assert cohort["total"] == cohort["full"] == 1 and cohort["ratio"] == 1
    with repo._connect() as db:
        standard = db.execute("SELECT standard_id FROM live_deliveries").fetchone()[0]
    assert store.get("message", "MU", standard)


def test_failure_recovery_cannot_advance_uncommitted_nested_cursor():
    from doxagent.message_bus_v2.collection_plane.worker import recovery_checkpoint

    old = {"endpoints": {"feed": {"cursor": "committed", "failures": 1}}}
    proposed = {
        "endpoints": {"feed": {"cursor": "not-persisted", "failures": 2, "retry_at": "later"}}
    }
    assert recovery_checkpoint(old, proposed) == {
        "endpoints": {"feed": {"cursor": "committed", "failures": 2, "retry_at": "later"}}
    }


async def test_legacy_cross_source_keeps_publication_and_unchanged_revision(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message
    from tests.test_message_bus_v2_deduplication import BODY
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.message_bus_v2.schema import utc_now
    from datetime import timedelta

    repo, bus, source, binding = _setup(tmp_path)
    first_seen = utc_now() - timedelta(minutes=5)
    old_source = bus.require_source("benzinga_news")
    old_binding = repo.get_binding("MU:benzinga_news")
    original = _message(192, body=BODY)
    receipt = await bus.accept_message(
        source=old_source,
        binding=old_binding,
        message=original,
        bootstrap=False,
        collected_at=first_seen,
        trusted_enrichment=True,
    )
    assert receipt.raw_message_id
    known = repo.get_raw(receipt.raw_message_id)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    rows = live.prepare_input(
        source,
        [(binding.binding_id, "MU", 0)],
        [original.model_copy(update={"body": None, "summary": "Discovery summary only"})],
        observed_at=utc_now().isoformat(),
        unit="HEAD",
    )
    live.accept(rows)
    assert live.get(rows[0]["key"])["first_seen_at"] == known.first_seen_at.isoformat()
    from doxagent.content_enrichment.service import ContentEnrichmentHub

    class NoFetch:
        async def extract(self, record):
            raise AssertionError("legacy FULL body was downloaded again")

    assert await ContentEnrichmentHub(repo, bus, extractor=NoFetch()).run_once() == 1
    assert live.get(rows[0]["key"])["cache_total"] == 1
    await LiveDeliveryWorker(bus).run_available()
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM stream_items").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM raw_messages").fetchone()[0] == 1
        raw = repo.get_raw(receipt.raw_message_id)
        assert (
            raw.metadata["message_version"]["content_revision"]
            == known.metadata["message_version"]["content_revision"]
        )
        assert raw.source_id == old_source.source_id


async def test_resolved_wrapper_joins_existing_body_owner(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import utc_now

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    direct = _message(193)
    wrapper = direct.model_copy(
        update={"url": "https://news.google.com/rss/articles/one", "external_id": "wrapper"}
    )
    rows = live.prepare_input(
        source,
        [(binding.binding_id, "MU", 0)],
        [direct, wrapper],
        observed_at=utc_now().isoformat(),
        unit="HEAD",
    )
    live.accept(rows)
    jobs = await bus.live_queue.claim(2)
    wrapped = next(j for j in jobs if j.message.url == wrapper.url)
    direct_job = next(j for j in jobs if j.message.url == direct.url)
    assert live.reserve_resolved(wrapped, direct_job.message)
    assert live.get(direct_job.article_id)["claim_token"] == direct_job.claim_token
    assert live.pending_ids(["live:" + wrapped.article_id + ":MU"])
    assert live.get(wrapped.article_id)["state"] == "MERGED"


async def test_late_alias_result_preserves_active_material_update(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import utc_now

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    direct = _message(195)
    wrapped = direct.model_copy(
        update={"url": "https://news.google.com/rss/articles/late", "external_id": "late"}
    )
    live.accept(
        live.prepare_input(
            source,
            [(binding.binding_id, "MU", 0)],
            [direct, wrapped],
            observed_at=utc_now().isoformat(),
            unit="HEAD",
        )
    )
    owners = await bus.live_queue.claim(2)
    original = next(j for j in owners if j.message.url == direct.url)
    alias = next(j for j in owners if j.message.url == wrapped.url)
    with repo.transaction() as db:
        db.execute(
            "UPDATE live_articles SET work_kind='content_update' WHERE article_key=?",
            (original.article_id,),
        )
    completed = original.message.model_copy(
        update={
            "body": "Complete publisher body",
            "metadata": {
                **original.message.metadata,
                "media_enrichment": {"succeeded": True, "outcome": "FULL"},
            },
        }
    )
    await bus.live_queue.finish(alias, completed)
    target = live.get(original.article_id)
    assert target["state"] == "FETCHING" and target["claim_token"] == original.claim_token
    assert target["result_ref"] is None
    assert live.pending_ids(["live:" + alias.article_id + ":MU"])
    await bus.live_queue.finish(original, completed)
    assert live.get(original.article_id)["outcome"] == "FULL"


async def test_unchanged_recheck_retains_result_ref_and_native_files(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.schema import utc_now, RawMessageInput

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    rows = live.prepare_input(
        source,
        [(binding.binding_id, "MU", 0)],
        [_message(194)],
        observed_at=utc_now().isoformat(),
        unit="HEAD",
    )
    live.accept(rows)
    await ContentEnrichmentHub(repo, bus, extractor=_GovernedExtractor()).run_once()
    await LiveDeliveryWorker(bus).run_available()
    key = rows[0]["key"]
    prior = live.get(key)
    before = {str(p) for p in repo.content.files.root.rglob("*") if p.is_file()}
    with repo.transaction() as db:
        db.execute(
            "UPDATE live_articles SET next_check_at='2000-01-01' WHERE article_key=?", (key,)
        )
    live.enqueue_checks()
    job = (await bus.live_queue.claim(1))[0]
    message = RawMessageInput.model_validate(live.load(prior["result_ref"]))
    await bus.live_queue.finish(job, message)
    assert live.get(key)["result_ref"] == prior["result_ref"]
    assert {str(p) for p in repo.content.files.root.rglob("*") if p.is_file()} == before
    assert await LiveDeliveryWorker(bus).run_available() == 0


async def test_httpx_publisher_supports_timeout_objects_and_actual_wire_evidence():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from httpx import Timeout
    from doxagent.message_bus_v2.publisher_http import PublisherHttpx
    from doxagent.message_bus_v2.poll_trace import REQUESTS

    cookies = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            cookies.append(self.headers.get("Cookie"))
            body = b"<rss><channel/></rss>"
            self.send_response(200)
            self.send_header("Set-Cookie", "publisher=one; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PublisherHttpx(trust_env=False)
    events = []
    token = REQUESTS.set(events)
    try:
        for _ in range(2):
            response = await client.get(
                f"http://127.0.0.1:{server.server_port}/feed", timeout=Timeout(2)
            )
            assert "<rss>" in response.text
        assert len(client.clients.clients) == 1
        assert cookies == [None, "publisher=one"]
        assert len(events) == 2 and all(
            e["proof_basis"] == "TRANSPORT_SEND" and e.get("response_received_at") for e in events
        )
    finally:
        REQUESTS.reset(token)
        await client.aclose()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(2)


def test_ibkr_news_wire_evidence_is_inside_socket_lock(monkeypatch):
    from types import SimpleNamespace
    from threading import Lock
    from ibapi.client import EClient
    from ibapi import comm
    from doxagent.message_bus_v2.ibkr_news import IbkrNewsGateway, _NEWS_SEND

    lock = Lock()
    sent = []

    def send(packet):
        assert lock.locked()
        sent.append(packet)
        return len(packet)

    def connect(app, *args, **kwargs):
        app.conn = SimpleNamespace(
            lock=lock, socket=SimpleNamespace(send=send), isConnected=lambda: True
        )
        app.nextValidId(1)

    monkeypatch.setattr(EClient, "connect", connect)
    monkeypatch.setattr(EClient, "run", lambda app: None)
    monkeypatch.setattr(EClient, "reqNewsProviders", lambda app: app.newsProviders([]))
    gateway = IbkrNewsGateway(
        SimpleNamespace(
            ibkr_tws_enabled=True,
            ibkr_tws_host="unused",
            ibkr_tws_port=0,
            ibkr_news_client_id=1,
            ibkr_tws_timeout_seconds=1,
        )
    )
    app = gateway._ensure()
    trace = {}
    token = _NEWS_SEND.set(trace)
    try:
        app.sendMsg("historical-news\0")
        assert sent == [comm.make_msg("historical-news\0")]
        assert trace["proof_basis"] == "TRANSPORT_SEND" and trace["request_started_at"]
        app.conn.socket.send = lambda packet: 0
        with pytest.raises(ConnectionError, match="incomplete_socket_write"):
            app.sendMsg("historical-news\0")
    finally:
        _NEWS_SEND.reset(token)
        gateway._thread.join(1)


async def test_old_unpublished_recheck_executes_once_without_publishing_expired_news(tmp_path):
    from datetime import timedelta
    from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
    from doxagent.content_enrichment.schema import EnrichmentJob
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.message_bus_v2.schema import utc_now
    import json

    repo, bus, source, binding = _setup(tmp_path)
    now = utc_now()
    old = now - timedelta(days=2)
    message = _message(197).model_copy(update={"published_at": old})
    with repo.transaction() as db:
        db.execute(
            "INSERT INTO article_body_results VALUES(?,?,?,?,?)",
            (
                "legacy-recheck",
                old.isoformat(),
                "READY",
                now.isoformat(),
                json.dumps({"first_observed_at": old.isoformat()}),
            ),
        )
    repo.enqueue_enrichment_job(
        EnrichmentJob(
            job_id="old-recheck",
            intake_key="old-recheck",
            poll_run_id="old",
            source=source,
            binding=binding,
            message=message,
            work_kind="recheck",
            body_result_key="legacy-recheck",
            not_before=now,
            deadline_at=now + timedelta(seconds=180),
        )
    )
    bus.live_queue = LiveQueue(bus)
    hub = ContentEnrichmentHub(repo, bus, extractor=_GovernedExtractor())
    assert await hub.run_once() == 1
    with repo._connect() as db:
        article = db.execute(
            "SELECT state,outcome,first_seen_at,fetch_total FROM live_articles"
        ).fetchone()
        assert article["state"] == "READY" and article["outcome"] == "FULL"
        assert article["first_seen_at"] == old.isoformat() and article["fetch_total"] == 1
        assert db.execute("SELECT state FROM live_deliveries").fetchone()[0] == "ADMISSION_REJECTED"
    assert await LiveDeliveryWorker(bus).run_available() == 0
    assert repo.latest_stream_offset("MU") == 0
    assert await hub.run_once() == 0


def test_expired_unadmitted_input_does_not_publish_native_or_article_work(tmp_path, monkeypatch):
    from datetime import timedelta
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import utc_now

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository

    def forbidden(*args, **kwargs):
        raise AssertionError("expired input created a Native document")

    monkeypatch.setattr(live, "put", forbidden)
    rows = live.prepare_input(
        source,
        [(binding.binding_id, "MU", 0)],
        [
            _message(198, body="Old article body " * 20000).model_copy(
                update={"published_at": utc_now() - timedelta(days=2)}
            )
        ],
        observed_at=utc_now().isoformat(),
        unit="HEAD",
    )
    assert rows == []
    live.accept(rows, capture_id="expired-acked")
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM live_articles").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM collection_acceptances").fetchone()[0] == 1


async def test_new_article_bypasses_old_body_and_delivery_backlog(tmp_path):
    from datetime import timedelta
    from tests.test_content_enrichment_hub import _setup, _message
    from tests.test_message_bus_v2_deduplication import BODY
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
    from doxagent.message_bus_v2.schema import utc_now

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    old = utc_now() - timedelta(days=1)
    backlog = [
        _message(3000 + i, body=BODY).model_copy(update={"published_at": old}) for i in range(40)
    ]
    live.accept(
        live.prepare_input(
            source,
            [(binding.binding_id, "MU", 0)],
            backlog,
            observed_at=old.isoformat(),
            unit="HEAD",
        )
    )
    message = _message(4000, body=BODY)
    new = live.prepare_input(
        source,
        [(binding.binding_id, "MU", 0)],
        [message],
        observed_at=utc_now().isoformat(),
        unit="HEAD",
    )
    live.accept(new)
    claim = (await bus.live_queue.claim(1, allow_background=False))[0]
    assert claim.article_id == new[0]["key"]
    with repo.transaction() as db:
        db.execute(
            "UPDATE live_articles SET state='READY',result_ref=input_ref,body_digest=article_key,revision=1,outcome='FULL',claim_token=NULL,lease_until=NULL"
        )
        db.execute("UPDATE live_deliveries SET state='PENDING'")
        db.execute(
            "UPDATE live_deliveries SET due_at=? WHERE article_key!=?",
            (old.isoformat(), new[0]["key"]),
        )
    rows = live.deliveries()
    assert rows[0]["article_key"] == new[0]["key"] and rows[0]["foreground"]
    worker = LiveDeliveryWorker(bus)
    assert await worker.run_available() == 2
    with repo._connect() as db:
        assert (
            db.execute(
                "SELECT state FROM live_deliveries WHERE article_key=?", (new[0]["key"],)
            ).fetchone()[0]
            == "PUBLISHED"
        )
        assert (
            db.execute("SELECT count(*) FROM live_deliveries WHERE state='PUBLISHED'").fetchone()[0]
            == 2
        )


async def test_delivery_time_slice_yields_before_a_new_article_arrives(tmp_path, monkeypatch):
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker

    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    worker = LiveDeliveryWorker(bus)
    rows = [{"foreground": 1, "article_key": str(i), "ticker": "MU"} for i in range(32)]
    monkeypatch.setattr(
        worker.repository,
        "deliveries",
        lambda state="PENDING", **kw: rows if state == "PENDING" else [],
    )
    calls = []

    async def slow(row):
        calls.append(row["article_key"])
        await asyncio.sleep(0.03)

    monkeypatch.setattr(worker, "process", slow)
    assert await worker.run_available(max_seconds=0.01) == 1
    assert calls == ["0"]
