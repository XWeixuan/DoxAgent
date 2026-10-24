import asyncio
import threading
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from doxagent.message_bus_v2.capacity import required_capacity
from doxagent.message_bus_v2.polling import CaptureIntake
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler, SchedulerGroupLimiter
from doxagent.message_bus_v2.schema import PollResult, PollStatus, SchedulerConstraints, utc_now
from tests.test_content_enrichment_hub import _message, _setup


async def test_read_snapshot_does_not_queue_behind_writer(tmp_path):
    repo, bus, _, binding = _setup(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def write():
        entered.set()
        assert release.wait(3)

    task = asyncio.create_task(bus.storage.call(write))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        value = await asyncio.wait_for(
            bus.storage.read(repo.read_only, repo.get_poll_state, binding), 0.5
        )
        assert value.binding_id == binding.binding_id
        with pytest.raises(Exception, match="readonly"):
            await bus.storage.read(repo.read_only, repo.save_poll_state, value)
    finally:
        release.set()
        await task


async def test_request_permit_does_not_wait_for_observation_persistence():
    release = asyncio.Event()
    entered = []

    async def persist():
        await release.wait()

    limiter = SchedulerGroupLimiter(
        SchedulerConstraints(max_concurrency=2, minimum_request_gap_seconds=0), on_request=persist
    )

    async def request(name):
        async with limiter.permit():
            entered.append(name)

    try:
        await asyncio.wait_for(asyncio.gather(request("first"), request("second")), 0.5)
        assert entered == ["first", "second"]
    finally:
        release.set()
        await asyncio.sleep(0)


async def test_capture_replay_chunks_and_fences_old_claim_without_network(tmp_path):
    repo, bus, source, binding = _setup(tmp_path)
    journal = repo.polling
    at = utc_now()
    prepared = journal.prepare(
        source=source,
        binding=binding,
        result=PollResult(messages=[_message(20000 + i) for i in range(33)]),
        run_id="crash-replay",
        observed_at=at,
        checkpoint={"page": 4},
    )
    journal.commit(prepared)
    assert repo.get_poll_state(binding).checkpoint == {"page": 4}
    assert repo.list_enrichment_jobs() == []
    capture_id, old = journal.claim()
    row = journal.load(capture_id)
    piece = journal.prepare_piece(row)
    # A restarted owner reclaims after expiry; an old owner cannot advance it.
    with repo.transaction() as db:
        db.execute(
            "UPDATE poll_captures SET lease_until=?", ((at - timedelta(seconds=1)).isoformat(),)
        )
    _, newer = journal.claim()
    journal.accept_piece(bus, piece, old)
    assert journal.load(capture_id)["intake_position"] == 0
    journal.accept_piece(bus, piece, newer)
    assert journal.load(capture_id)["intake_position"] == 16
    worker = CaptureIntake(bus)
    try:
        receipt = await asyncio.wait_for(worker.wait("crash-replay"), 10)
        assert receipt.queued_count == receipt.collected_count == 33
        assert len(repo.list_enrichment_jobs()) == 33
        assert repo.get_poll_state(binding).collected_count == 33
        assert journal.run_receipt("crash-replay").queued_count == 33
    finally:
        await worker.close()


async def test_transport_request_times_are_not_invented_from_dummy_poll(tmp_path):
    repo, bus, source, binding = _setup(tmp_path)

    class Adapter:
        async def poll(self, context):
            return PollResult()

    scheduler = GlobalPollScheduler(
        repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None)
    )
    try:
        result = await scheduler._poll(source, binding, utc_now())
        with repo._connect() as db:
            import json

            row = db.execute(
                "SELECT data_json FROM poll_runs WHERE run_id=?", (result.poll_run_id,)
            ).fetchone()
        assert json.loads(row[0])["requests"] == []
    finally:
        await scheduler.close()


@pytest.mark.parametrize("message_count", [0, 1])
async def test_captured_poll_does_not_enqueue_an_empty_health_intake(
    tmp_path, monkeypatch, message_count
):
    repo, bus, source, binding = _setup(tmp_path)
    original, accepted = bus._accept_poll_result, []

    def accept(**kwargs):
        accepted.append(len(kwargs["result"].messages))
        return original(**kwargs)

    monkeypatch.setattr(bus, "_accept_poll_result", accept)

    class Adapter:
        async def poll(self, context):
            return PollResult(messages=[_message(87000)] if message_count else [])

    scheduler = GlobalPollScheduler(
        repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None)
    )
    try:
        result = await scheduler._poll(source, binding, utc_now())
        assert accepted == ([1] if message_count else [])
        assert result.collected_count == message_count and result.queued_count == message_count
        assert repo.get_poll_state(binding).status.value == "succeeded"
    finally:
        await scheduler.close()


@pytest.mark.parametrize("partial", [False, True])
async def test_deferred_reason_survives_empty_and_partial_intake_into_run_ledger(tmp_path, partial):
    repo, bus, source, binding = _setup(tmp_path)

    class Adapter:
        async def poll(self, context):
            return PollResult(
                messages=[_message(86000)] if partial else [],
                site_access_deferred=True,
                site_access_deferred_reason="identity_budget_deferred",
                site_access_retry_not_before=utc_now() + timedelta(seconds=2),
                window_done=False,
            )

    scheduler = GlobalPollScheduler(
        repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None)
    )
    try:
        result = await scheduler._poll(source, binding, utc_now())
        assert result.site_access_deferred_reason == "identity_budget_deferred"
        with repo._connect() as db:
            row = db.execute(
                "SELECT status,reason FROM poll_runs WHERE run_id=?", (result.poll_run_id,)
            ).fetchone()
        assert tuple(row) == ("DEFERRED", "identity_budget_deferred")
        assert result.queued_count == int(partial)
    finally:
        await scheduler.close()


async def test_real_httpx_transport_hook_records_request_and_body(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from doxagent.message_bus_v2.poll_trace import REQUESTS, install

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    token = REQUESTS.set(events)
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            install(client)
            await client.get(f"http://127.0.0.1:{server.server_port}/list?token=secret")
        assert len(events) == 1
        assert events[0]["request_started_at"] <= events[0]["response_received_at"]
        assert events[0]["endpoint"] == f"127.0.0.1:{server.server_port}/list"
        assert "secret" not in str(events)
    finally:
        REQUESTS.reset(token)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join()


@pytest.mark.parametrize("tickers", [1, 9, 12, 20])
def test_capacity_uses_effective_demand_and_caps_not_nine(tickers):
    limit, deficit = required_capacity(tickers / 60, 12, ceiling=8)
    assert limit == max(1, __import__("math").ceil(tickers * 12 / 60 / 0.75))
    assert not deficit


def test_adding_tickers_keeps_existing_phase_and_due_units_are_independent(tmp_path):
    repo, bus, source, binding = _setup(tmp_path)
    source = repo.get_source("yahoo_finance_news")
    binding = repo.get_binding("MU:yahoo_finance_news")
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace())
    now = utc_now()
    state = repo.get_poll_state(binding).model_copy(
        update={
            "next_dispatch_at": now + timedelta(seconds=10),
            "target_due_at": now + timedelta(seconds=10),
            "units": {
                "SUPPLEMENT_HEAD": {"next_dispatch_at": (now - timedelta(seconds=1)).isoformat()}
            },
        }
    )
    repo.save_poll_state(state)
    scheduler._initialize_due_slots([(source, binding)], now)
    assert repo.get_poll_state(binding).next_dispatch_at == state.next_dispatch_at
    units = list(scheduler.work_units(source, binding, state, now))
    assert [u[1]["acquisition_unit"] for u in units] == ["SUPPLEMENT_HEAD"]


def test_all_cycles_count_deferrals_failures_missing_and_window_edges():
    from doxagent.message_bus_v2.cadence import cadence

    start = utc_now().replace(microsecond=0)

    def row(second, status, **extra):
        at = (start + timedelta(seconds=second)).isoformat()
        return {
            "run_id": str(second),
            "binding_id": "MU:source",
            "unit": "HEAD",
            "due_at": at,
            "dispatch_at": at,
            "interval_seconds": 60,
            "status": status,
            **extra,
        }

    runs = [
        row(0, "SCHEDULE", reason="ENABLED", epoch_at=start.isoformat()),
        row(0, "CAPTURED", requests=[{"request_started_at": start.isoformat()}]),
        row(60, "DEFERRED"),
        row(
            180,
            "FAILED",
            requests=[{"request_started_at": (start + timedelta(seconds=180)).isoformat()}],
        ),
        row(
            240,
            "CAPTURED",
            requests=[{"request_started_at": (start + timedelta(seconds=241)).isoformat()}],
        ),
    ]
    captures = [
        {
            "run_id": str(second),
            "binding_id": "MU:source",
            "unit": "HEAD",
            "observed_at": (start + timedelta(seconds=second + 2)).isoformat(),
        }
        for second in (0, 180, 240)
    ]
    result = cadence(runs, captures, start, start + timedelta(seconds=300))
    assert result["expected_cycles"] == 5 and result["fresh_cycles"] == 2
    assert result["missing_cycles"] == 1 and result["coverage"] == 0.4
    assert result["units"][0]["statuses"]["DEFERRED"] == 1
    assert result["units"][0]["fresh_gap_seconds"]["max"] == 240
    assert result["units"][0]["refresh_age_seconds"] == 58
    runs.extend(
        [
            row(120, "SCHEDULE", reason="PAUSED", epoch_at=start.isoformat()),
            row(180, "SCHEDULE", reason="ENABLED", epoch_at=start.isoformat()),
        ]
    )
    paused = cadence(runs, captures, start, start + timedelta(seconds=300))
    assert paused["expected_cycles"] == 4 and paused["missing_cycles"] == 0


def test_new_schedule_does_not_attribute_pre_activation_silence_to_latency():
    from doxagent.message_bus_v2.cadence import cadence

    now = utc_now()
    activation = now - timedelta(seconds=20)
    schedule = {
        "run_id": "epoch",
        "binding_id": "MU:x",
        "unit": "HEAD",
        "due_at": activation.isoformat(),
        "dispatch_at": activation.isoformat(),
        "interval_seconds": 30,
        "status": "SCHEDULE",
        "reason": "ENABLED",
        "epoch_at": activation.isoformat(),
    }
    result = cadence([schedule], [], now - timedelta(minutes=30), now)
    assert result["expected_cycles"] == 1
    assert result["fresh_gap_seconds"]["max"] == 20


async def test_cdc_keeps_capture_payload_private_and_exposes_exact_clock(tmp_path):
    from doxagent.api_v2.news_timeliness import poll_cadence
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.projectors import DomainProjectors
    from doxagent.v2_read.repository import ReadStore

    repo, bus, source, binding = _setup(tmp_path)
    outbox = SourceOutbox(repo.path, "bus")
    outbox.migrate()
    now = utc_now()
    journal = repo.polling
    journal.schedules([(source, binding, "HEAD", now, True)], now)
    journal.start("request", source, binding, now, now)
    journal.update_run(
        "request", status="CAPTURED", requests=[{"request_started_at": now.isoformat()}]
    )
    journal.commit(
        journal.prepare(
            source=source,
            binding=binding,
            result=PollResult(messages=[_message(50000, body="private full article " * 3000)]),
            run_id="request",
            observed_at=now,
        )
    )
    events = [
        e for e in outbox.read(0, limit=500) if e["table_name"] in {"poll_runs", "poll_captures"}
    ]
    assert events and all("payload_json" not in e["row"] for e in events)
    repo.polling.claim()
    assert len(
        [e for e in outbox.read(0, limit=500) if e["table_name"] in {"poll_runs", "poll_captures"}]
    ) == len(events)
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    mapper = DomainProjectors(store)
    for event in events:
        records, metrics = mapper(event)
        store.ingest(
            "bus", str(event["seq"]), records, contributions=metrics, position=event["seq"]
        )
    with store.connect() as db:
        seq = store.highwater(db)
    metrics = poll_cadence(store, binding.ticker, seq, now, now + timedelta(seconds=5))
    assert metrics["expected_cycles"] == metrics["fresh_cycles"] == 1
    assert metrics["coverage"] == 1


async def test_shared_capture_replay_keeps_late_subscriber_and_one_job_per_article(tmp_path):
    from doxagent.message_bus_v2.schema import UpdateActor
    from tests.test_message_bus_distribution import _terms

    repo, bus, _, _ = _setup(tmp_path)
    source = bus.update_source("ctee_semiconductor", {"enabled": True}, actor=UpdateActor.SYSTEM)
    rows = [_message(60000 + i) for i in range(33)]

    class Adapter:
        calls = 0

        async def poll_shared(self, context):
            self.calls += 1
            return PollResult(
                messages=rows,
                next_checkpoint={"head": 1},
                window_done=True,
                window_coverage="COMPLETE",
            )

    adapter = Adapter()
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace(resolve=lambda *a, **kw: adapter))
    scheduler._long_running = True
    bindings = []
    for ticker in ["MU", "NVDA", "INTC"]:
        if ticker != "MU":
            bus.start_ticker(ticker)
        bindings.append(
            bus.configure_binding(
                ticker=ticker, source_id=source.source_id, actor=UpdateActor.SYSTEM
            )
        )
        scheduler.terms.apply(_terms(ticker, ticker), actor="test")
    start = scheduler.intake.start
    scheduler.intake.start = lambda: None
    try:
        now = utc_now()
        result = await scheduler._poll_distribution(source, bindings[:2], now)
        assert result.collected_count == 33 and not repo.list_enrichment_jobs()
        assert scheduler.distribution.run(result.poll_run_id)["status"] == "CAPTURED"
        await scheduler._poll_distribution(source, bindings, now)
        assert adapter.calls == 1
        scheduler.intake.start = start
        receipt = await asyncio.wait_for(scheduler.intake.wait(result.poll_run_id), 15)
        assert receipt.queued_count == 33
        with repo._connect() as db:
            assert db.execute("SELECT count(*) FROM distribution_deliveries").fetchone()[0] == 99
            assert db.execute("SELECT count(*) FROM content_enrichment_jobs").fetchone()[0] == 33
        assert scheduler.distribution.run(result.poll_run_id)["status"] == "DONE"
    finally:
        await scheduler.close()


async def test_twenty_tickers_capture_while_sibling_and_intake_are_stalled(tmp_path):
    import time

    from doxagent.message_bus_v2.poll_trace import install

    repo, bus, source, binding = _setup(tmp_path)
    source = source.model_copy(
        update={
            "scheduler_constraints": SchedulerConstraints(
                max_concurrency=8, minimum_request_gap_seconds=0
            )
        }
    )
    repo.save_source(source)
    release = asyncio.Event()

    async def transport(request):
        await request.extensions["trace"]("http11.send_request_headers.started", {})
        if request.url.path == "/T0":
            await release.wait()
        await asyncio.sleep(0.005)
        return httpx.Response(200, text="[]")

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        install(client)

        class Adapter:
            async def poll(self, context):
                async with context.request_permit():
                    await client.get("https://example.test/" + context.ticker)
                await context.on_page([_message(70000 + int(context.ticker[1:]))])
                return PollResult(next_checkpoint={"fresh": True})

        scheduler = GlobalPollScheduler(
            repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None)
        )
        scheduler._long_running = True
        start = scheduler.intake.start
        scheduler.intake.start = lambda: None
        bindings = [
            binding.model_copy(update={"binding_id": f"T{i}:{source.source_id}", "ticker": f"T{i}"})
            for i in range(20)
        ]
        for item in bindings:
            repo.save_binding(item)
        tasks = [asyncio.create_task(scheduler._poll(source, b, utc_now())) for b in bindings]
        clock = time.monotonic()
        try:
            healthy = await asyncio.wait_for(asyncio.gather(*tasks[1:]), 5)
            assert not tasks[0].done()
            assert len(healthy) == 19 and not repo.list_enrichment_jobs()
            assert all(not r.error_code for r in healthy)
            # A healthy ticker starts another real request without waiting for T0 or intake.
            second = await asyncio.wait_for(scheduler._poll(source, bindings[1], utc_now()), 3)
            assert not second.error_code and time.monotonic() - clock < 5
            with repo._connect() as db:
                captures = db.execute(
                    "SELECT count(*) FROM poll_captures WHERE request_started_at IS NOT NULL"
                ).fetchone()[0]
                assert captures >= 20
            scheduler.intake.start = start
            receipt = await asyncio.wait_for(scheduler.intake.wait(healthy[0].poll_run_id), 10)
            assert receipt.queued_count == 1
            job = next(
                j for j in repo.list_enrichment_jobs() if j.job_id == receipt.enrichment_job_ids[0]
            )
            assert job.poll_run_id == healthy[0].poll_run_id
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await scheduler.close()


def test_interval_change_rephases_units_without_erasing_checkpoint(tmp_path):
    repo, bus, _, _ = _setup(tmp_path)
    source = repo.get_source("yahoo_finance_news")
    binding = repo.get_binding("MU:yahoo_finance_news")
    now = utc_now()
    state = repo.get_poll_state(binding).model_copy(
        update={
            "schedule_interval_seconds": 120,
            "next_dispatch_at": now + timedelta(seconds=120),
            "units": {
                "SUPPLEMENT_HEAD": {
                    "checkpoint": {"cursor": "keep"},
                    "next_dispatch_at": (now + timedelta(seconds=120)).isoformat(),
                }
            },
        }
    )
    repo.save_poll_state(state)
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace())
    scheduler._initialize_due_slots([(source, binding)], now)
    saved = repo.get_poll_unit_state(binding, "SUPPLEMENT_HEAD")
    assert saved.next_dispatch_at == now and saved.checkpoint == {"cursor": "keep"}


def test_unknown_calendar_still_counts_expected_cycles_and_unproven_capture_is_not_fresh():
    from doxagent.message_bus_v2.cadence import cadence

    now = utc_now()
    row = {
        "run_id": "unknown",
        "binding_id": "MU:x",
        "unit": "HEAD",
        "due_at": now.isoformat(),
        "dispatch_at": now.isoformat(),
        "interval_seconds": 30,
        "status": "SCHEDULE",
        "reason": "ELIGIBILITY_UNKNOWN",
        "epoch_at": now.isoformat(),
    }
    result = cadence(
        [row],
        [
            {
                "run_id": "old-cache",
                "binding_id": "MU:x",
                "unit": "HEAD",
                "observed_at": (now + timedelta(seconds=5)).isoformat(),
            }
        ],
        now,
        now + timedelta(seconds=60),
    )
    assert result["expected_cycles"] == 2 and result["fresh_cycles"] == 0
    assert result["fresh_gap_seconds"]["max"] == 60 and result["eligibility_unknown"] == 1


async def test_distribution_fills_freed_slot_before_slow_article_finishes():
    from unittest.mock import AsyncMock, Mock

    from doxagent.message_bus_v2.distribution import DistributionWorker
    from doxagent.message_bus_v2.storage_lane import StorageLane

    repo = SimpleNamespace(expire_runs=lambda: 0, claim_deliveries=lambda **kw: [])
    worker = DistributionWorker(repo, SimpleNamespace(storage=StorageLane()), None, concurrency=2)

    def row(name):
        return {"article_id": name, "terms_revision": 1}

    repo.claim_deliveries = Mock(side_effect=[[row("slow"), row("fast")], [row("third")]])
    release = asyncio.Event()
    entered = []

    async def article(rows):
        entered.append(rows[0]["article_id"])
        if rows[0]["article_id"] == "slow":
            await release.wait()

    worker._article = AsyncMock(side_effect=article)
    try:
        await worker.run_available()
        await asyncio.sleep(0.01)
        await worker.run_available()
        await asyncio.sleep(0.01)
        assert entered == ["slow", "fast", "third"] and not release.is_set()
        assert repo.claim_deliveries.call_args.kwargs["article_limit"] == 1
    finally:
        release.set()
        await worker.close()


@pytest.mark.parametrize("mode", ["cpu", "intake"])
async def test_cpu_pressure_preserves_new_articles_and_leaves_rechecks_pending(
    tmp_path, monkeypatch, mode
):
    from doxagent.message_bus_v2 import pressure

    repo, bus, source, binding = _setup(tmp_path)
    jobs = [
        bus.enqueue_enrichment(
            source=source,
            binding=binding,
            message=_message(80000 + i),
            bootstrap=False,
            poll_run_id="cpu",
        )[0]
        for i in range(2)
    ]
    with repo.transaction() as db:
        db.execute(
            "UPDATE content_enrichment_jobs SET "
            "data_json=json_set(data_json,'$.work_kind','recheck') WHERE job_id=?",
            (jobs[0].job_id,),
        )
    if mode == "intake":
        capture_id = repo.polling.commit(
            repo.polling.prepare(
                source=source,
                binding=binding,
                result=PollResult(messages=[_message(80002)]),
                run_id="backlog",
                observed_at=utc_now(),
            )
        )
        with repo.transaction() as db:
            db.execute(
                "UPDATE poll_captures SET committed_at=? WHERE capture_id=?",
                ((utc_now() - timedelta(seconds=4)).isoformat(), capture_id),
            )
        assert repo.polling.backlogged()
    monkeypatch.setattr(pressure, "cpu_pressure", lambda: 30 if mode == "cpu" else 0)
    assert pressure.background_allowed() is (mode == "intake")
    claimed = repo.claim_enrichment_jobs(limit=8, allow_rechecks=pressure.background_allowed())
    assert [job.job_id for job in claimed] == [jobs[1].job_id]
    assert (
        next(j for j in repo.list_enrichment_jobs() if j.job_id == jobs[0].job_id).status.value
        == "queued"
    )


def test_new_head_intake_precedes_older_history_without_losing_history(tmp_path):
    repo, bus, source, binding = _setup(tmp_path)
    journal = repo.polling
    ids = [
        journal.commit(
            journal.prepare(
                source=source,
                binding=binding,
                result=PollResult(messages=[_message(85000 + i)]),
                run_id=f"intake-order-{i}",
                observed_at=utc_now(),
                unit=unit,
            )
        )
        for i, unit in enumerate(["TAIL:SWEEP", "HEAD"])
    ]
    capture_id, token = journal.claim()
    assert capture_id == ids[1]
    journal.accept_piece(bus, journal.prepare_piece(journal.load(capture_id)), token)
    assert journal.claim()[0] == ids[0]


async def test_read_only_snapshot_stays_consistent_across_concurrent_write(tmp_path):
    repo, bus, source, _ = _setup(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def snapshot():
        first = repo.get_source(source.source_id).version
        entered.set()
        assert release.wait(3)
        return first, repo.get_source(source.source_id).version

    read = asyncio.create_task(bus.storage.read(repo.read_only, snapshot))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        await bus.storage.call(
            repo.save_source, source.model_copy(update={"version": source.version + 1})
        )
    finally:
        release.set()
    assert await read == (source.version, source.version)
    assert repo.get_source(source.source_id).version == source.version + 1


def test_yahoo_new_timestamp_work_does_not_wait_for_supplement_or_inherit_its_checkpoint(tmp_path):
    repo, bus, _, _ = _setup(tmp_path)
    source = repo.get_source("yahoo_finance_news")
    binding = repo.get_binding("MU:yahoo_finance_news")
    now = utc_now()
    url = "https://finance.yahoo.com/news/new.html"
    state = repo.get_poll_state(binding).model_copy(
        update={
            "target_due_at": now + timedelta(seconds=30),
            "next_dispatch_at": now + timedelta(seconds=30),
            "units": {
                "SUPPLEMENT_HEAD": {
                    "checkpoint": {"pending_timestamp_rows": [{"link": url}]},
                    "next_dispatch_at": (now + timedelta(seconds=30)).isoformat(),
                }
            },
        }
    )
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace())
    units = list(scheduler.work_units(source, binding, state, now))
    assert [options["acquisition_unit"] for _, options in units] == ["TAIL:YAHOO_TIME"]
    assert units[0][1]["checkpoint"]["pending_timestamp_rows"] == [{"link": url}]


def test_in_progress_cycle_is_separate_but_failed_deferred_and_overdue_still_count():
    from doxagent.message_bus_v2.cadence import cadence

    at = utc_now()

    def row(second, status, **extra):
        instant = (at + timedelta(seconds=second)).isoformat()
        return dict(
            run_id=str(second),
            binding_id="MU:x",
            unit="HEAD",
            due_at=instant,
            dispatch_at=instant,
            interval_seconds=60,
            status=status,
            **extra,
        )

    requests = [{"request_started_at": at.isoformat()}]
    runs = [
        row(0, "SCHEDULE", reason="ENABLED", epoch_at=at.isoformat()),
        row(0, "CAPTURED", requests=requests),
        row(60, "DISPATCHED"),
    ]
    captures = [
        dict(
            run_id="0",
            binding_id="MU:x",
            unit="HEAD",
            observed_at=(at + timedelta(seconds=2)).isoformat(),
        )
    ]
    result = cadence(runs, captures, at, at + timedelta(seconds=70))
    assert result["expected_cycles"] == 2 and result["settled_cycles"] == 1
    assert (
        result["pending_cycles"] == 1
        and result["coverage"] == 1
        and result["all_cycle_coverage"] == 0.5
    )
    for status in ["FAILED", "DEFERRED", "CANCELLED"]:
        runs[-1] = row(60, status)
        result = cadence(runs, captures, at, at + timedelta(seconds=70))
        assert result["pending_cycles"] == 0 and result["coverage"] == 0.5
    result = cadence(runs[:2], captures, at, at + timedelta(seconds=80))
    assert result["missing_cycles"] == 1 and result["coverage"] == 0.5


def test_old_shared_capture_never_creates_another_physical_request_observation():
    from doxagent.message_bus_v2.poll_trace import REQUESTS
    from doxagent.site_strategy.client import _observed

    result = SimpleNamespace(
        served_from_shared_capture=False,
        operation_id="one",
        site_id="site",
        identity_id="identity",
        disposition=SimpleNamespace(value="SUCCESS"),
        attempts=[
            SimpleNamespace(
                stage_timings={
                    "request_0_started_epoch_ms": 1700000000000,
                    "request_0_body_epoch_ms": 1700000000100,
                    "request_1_started_epoch_ms": 1700000000010,
                    "request_1_body_epoch_ms": 1700000000200,
                }
            )
        ],
    )
    events = []
    token = REQUESTS.set(events)
    try:
        _observed(result)
        assert len(events) == 2 and all(r["response_received_at"] for r in events)
        result.served_from_shared_capture = True
        _observed(result)
        assert len(events) == 2
    finally:
        REQUESTS.reset(token)


def test_retention_keeps_unresolved_replayed_input_and_last_fresh_boundary(tmp_path):
    from dataclasses import replace

    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.projectors import DomainProjectors
    from doxagent.v2_read.repository import ReadStore

    repo, bus, source, binding = _setup(tmp_path)
    journal, now = repo.polling, utc_now()
    identities = {}
    for name, days, status in [
        ("old", 5, "COMPLETED"),
        ("boundary", 4, "COMPLETED"),
        ("pending", 6, "PENDING"),
        ("failed", 6, "FAILED"),
        ("replayed", 6, "COMPLETED"),
    ]:
        at = now - timedelta(days=days)
        journal.start(name, source, binding, at, at)
        journal.update_run(name, status="CAPTURED")
        capture = journal.prepare(
            source=source,
            binding=binding,
            result=PollResult(messages=[_message(82000)] if status != "COMPLETED" else []),
            run_id=name,
            observed_at=at,
        )
        identities[name] = journal.commit(replace(capture, request_started_at=at.isoformat()))
        with repo.transaction() as db:
            db.execute(
                "UPDATE poll_captures SET status=?,completed_at=? WHERE capture_id=?",
                (
                    status,
                    now.isoformat() if name == "replayed" else at.isoformat(),
                    identities[name],
                ),
            )
    outbox = SourceOutbox(repo.path, "bus")
    outbox.migrate()
    journal.maintain(now=now)
    assert journal.load(identities["old"]) is None
    assert all(
        journal.load(identities[name]) is not None
        for name in ["boundary", "pending", "failed", "replayed"]
    )
    deleted = [
        event for event in outbox.read(0, limit=500) if event["table_name"] == "poll_captures"
    ]
    assert len(deleted) == 1 and deleted[0]["operation"] == "DELETE"
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records, _ = DomainProjectors(store)(deleted[0])
    assert records[0]["data"] is None


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("change_source", [False, True])
async def test_late_old_generation_cannot_overwrite_new_checkpoint_interval_or_health(
    tmp_path, fail, change_source
):
    repo, bus, source, binding = _setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    class Adapter:
        async def poll(self, context):
            entered.set()
            await release.wait()
            if fail:
                raise RuntimeError("old generation failed")
            return PollResult(messages=[_message(83000)], next_checkpoint={"old": True})

    scheduler = GlobalPollScheduler(
        repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None)
    )
    task = asyncio.create_task(scheduler._poll(source, binding, utc_now()))
    try:
        await entered.wait()
        current_source = source.model_copy(update={"version": source.version + int(change_source)})
        current_binding = binding.model_copy(
            update={
                "version": binding.version + int(not change_source),
                "polling": binding.polling.model_copy(update={"target_interval_seconds": 10}),
            }
        )
        repo.save_source(current_source)
        repo.save_binding(current_binding)
        due = utc_now() + timedelta(seconds=10)
        state = repo.get_poll_state(current_binding).model_copy(
            update={
                "source_definition_version": current_source.version,
                "binding_version": current_binding.version,
                "checkpoint": {"new": True},
                "target_due_at": due,
                "next_dispatch_at": due,
                "schedule_interval_seconds": 10,
                "status": PollStatus.SUCCEEDED,
            }
        )
        repo.save_poll_unit_state(state, "HEAD")
        release.set()
        result = await asyncio.wait_for(task, 10)
        assert bool(result.error_code) == fail
        latest = repo.get_poll_unit_state(current_binding, "HEAD")
        assert latest.checkpoint == {"new": True} and latest.next_dispatch_at == due
        assert latest.last_error_code is None and latest.status.value == "succeeded"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await scheduler.close()
