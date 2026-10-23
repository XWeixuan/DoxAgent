import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from doxagent.api_v2.news_timeliness import body_cohort
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import PollResult
from doxagent.v2_read.repository import ReadStore
from tests.test_content_enrichment_hub import _message, _setup


async def test_first_page_is_durable_before_tail_and_is_not_queued_twice(tmp_path):
    repo, bus, source, binding = _setup(tmp_path)
    state = repo.get_poll_state(binding).model_copy(update={"bootstrap_complete": True})
    repo.save_poll_state(state)
    first, last = _message(800), _message(801)
    accepted, release = asyncio.Event(), asyncio.Event()

    class Adapter:
        async def poll(self, context):
            await context.on_page([first], checkpoint={"page": 2})
            accepted.set()
            await release.wait()
            return PollResult(messages=[first, last], next_checkpoint={"page": 3})

    scheduler = GlobalPollScheduler(
        repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None)
    )
    task = asyncio.create_task(scheduler._poll(source, binding, datetime.now(UTC)))
    await asyncio.wait_for(accepted.wait(), 2)
    assert len(repo.list_enrichment_jobs()) == 1
    assert repo.get_poll_state(binding).checkpoint == {"page": 2}
    release.set()
    result = await task
    assert result.queued_count == 2 and result.collected_count == 2
    assert len(repo.list_enrichment_jobs()) == 2
    assert len(set(result.enrichment_job_ids)) == 2
    assert repo.get_poll_state(binding).collected_count == 2


def test_unpublished_failure_is_in_new_article_cohort_and_cache_is_not_network(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_projection_indexes()
    now = datetime.now(UTC).isoformat()
    records = []
    for key, status, outcome in (("first", "READY", "FULL"), ("second", "FAILED", None)):
        records.extend(
            [
                {
                    "kind": "article_observation",
                    "ticker": "MU",
                    "id": key,
                    "parent": key,
                    "source_id": "feed",
                    "data": {"fetch_key": key, "first_observed_at": now},
                },
                {
                    "kind": "native:article_body_results",
                    "ticker": "",
                    "id": key,
                    "data": {"first_observed_at": now, "status": status, "outcome": outcome},
                },
            ]
        )
    records.append(
        {
            "kind": "body_attempt",
            "ticker": "MU",
            "id": "reuse",
            "data": {
                "body_result_key": "first",
                "network_attempted": False,
                "cache_reused": True,
                "succeeded": True,
            },
        }
    )
    store.ingest("test", "cohort", records)
    with store.connect() as db:
        seq = store.highwater(db)
    cohort = body_cohort(store, "MU", seq, {}, None)
    assert cohort["total"] == 2 and cohort["full"] == 1 and cohort["failed"] == 1
    assert cohort["ratio"] == 0.5
    assert cohort["cache_reuses"] == 1 and cohort["network_attempts"] == 0


async def test_real_cdc_projects_body_results_and_ignores_observation_lease_noise(tmp_path):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.body_results import ArticleBodyResults
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.projectors import DomainProjectors
    from tests.test_body_completion_receipts import Extractor

    repo, bus, source, binding = _setup(tmp_path)
    outbox = SourceOutbox(repo.path, "bus")
    outbox.migrate()
    job, _ = bus.enqueue_enrichment(
        source=source, binding=binding, message=_message(9004), bootstrap=False, poll_run_id="cdc"
    )
    events = outbox.read(0)
    cursor = max(e["seq"] for e in events)
    results = ArticleBodyResults(repo)
    assert results.acquire(job.body_result_key, "lease-only")
    results.release(job.body_result_key, "lease-only")
    with repo.transaction() as db:
        db.execute("UPDATE message_observations SET seen_count=seen_count+1")
    assert outbox.read(cursor) == []
    await ContentEnrichmentHub(repo, bus, extractor=Extractor()).run_once()
    store = ReadStore(tmp_path / "projected.db")
    store.migrate()
    mapper = DomainProjectors(store)
    for event in outbox.read(0, limit=500):
        if event["table_name"] not in {
            "article_body_results",
            "message_observations",
            "content_enrichment_jobs",
            "audit_log",
        }:
            continue
        records, metrics = mapper(event)
        store.ingest(
            "bus", str(event["seq"]), records, contributions=metrics, position=event["seq"]
        )
    with store.connect() as db:
        seq = store.highwater(db)
    cohort = body_cohort(store, "MU", seq, {}, None)
    assert cohort["total"] == cohort["full"] == cohort["network_attempts"] == 1
    with store.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM object_current WHERE kind='body_job'").fetchone()[0]
            == 1
        )


def test_read_only_operations_audit_deduplicates_poll_snapshots(tmp_path):
    from datetime import timedelta

    from scripts.ops.audit_news_timeliness import audit, audit_poll_window

    repo, bus, source, binding = _setup(tmp_path)
    now = datetime.now(UTC)
    bus.enqueue_enrichment(
        source=source, binding=binding, message=_message(9006), bootstrap=False, poll_run_id="audit"
    )
    body = audit(
        repo.path,
        (now - timedelta(seconds=5)).isoformat(),
        (now + timedelta(seconds=5)).isoformat(),
    )
    assert body["new_articles"]["total"] == 1 and body["new_articles"]["full"] == 0
    store = ReadStore(tmp_path / "audit-read.db")
    store.migrate()
    for revision in range(2):
        store.ingest(
            "bus",
            str(revision),
            [
                {
                    "kind": "native:poll_states",
                    "ticker": "MU",
                    "id": binding.binding_id,
                    "data": {
                        "status": "succeeded",
                        "last_dispatch_at": now.isoformat().replace("+00:00", "Z"),
                        "last_latency_ms": 1000,
                        "last_capture_at": now.isoformat(),
                        "last_first_capture_at": now.isoformat(),
                        "last_first_intake_at": (now + timedelta(milliseconds=200)).isoformat(),
                        "last_stage_timings": {"runtime_ms": 700, "accept_ms": 200},
                        "revision": revision,
                    },
                }
            ],
        )
    window = audit_poll_window(
        store.path,
        (now - timedelta(seconds=5)).isoformat(),
        (now + timedelta(seconds=5)).isoformat(),
    )
    result = window["bindings"][binding.binding_id]
    assert result["poll_ms"]["samples"] == 1
    assert result["capture_to_first_intake_ms"]["mean"] == 200
    assert not window["sample_limit_reached"]


def test_known_article_probe_has_bounded_cost_with_unrelated_history(tmp_path):
    repo,bus,source,binding = _setup(tmp_path)
    now=datetime.now(UTC).isoformat()
    with repo.transaction() as db:
        db.executemany("INSERT INTO raw_messages(raw_message_id,ticker,source_id,binding_id,identity_key,content_hash,raw_hash,revision,processing_status,published_at,collected_at,data_json) VALUES(?,?,?,?,?,?,?,1,'normalized',?,?,'{}')",
            [(f"history-{i}",binding.ticker,source.source_id,binding.binding_id,f"other-{i}","hash","raw",now,now) for i in range(3000)])
    steps=0
    def progress():
        nonlocal steps
        steps+=100
    with repo.connection_scope() as db:
        db.set_progress_handler(progress,100)
        assert repo.known_published_article(binding,_message(9999)) is None
        db.set_progress_handler(None,0)
    assert steps<1000, f"known article check scanned history: {steps} VM steps"
