import asyncio
from contextlib import asynccontextmanager

import pytest

from doxagent.message_bus_v2.poll_trace import REQUESTS, request_started
from doxagent.message_bus_v2.rss_http_race import execute
from doxagent.message_bus_v2.schema import (
    PollResult,
    RawMessageInput,
    SharedPollContext,
    SourceDefinition,
    utc_now,
)


@asynccontextmanager
async def permit():
    yield


def context(**extra):
    return SharedPollContext(
        run_id="race",
        source=SourceDefinition(
            source_id="test_race",
            display_name="test",
            kind="api",
            adapter_ref="builtin:newswire_rss",
            scheduler_group="test",
            acquisition_mode="by_distribution",
        ),
        request_permit=permit,
        **extra,
    )


def response(name, *, checkpoint=None):
    event = request_started("GET", "https://example.com/" + name, admitted=True)
    event.update(response_received_at=utc_now().isoformat(), status_code=200)
    return PollResult(
        messages=[
            RawMessageInput(
                title=name,
                url="https://example.com/news/" + name,
                published_at=utc_now(),
                raw_payload={},
            )
        ],
        next_checkpoint=checkpoint or {"name": name},
    )


async def test_fast_lane_is_committed_before_slow_lane_and_late_unique_articles_survive():
    committed = asyncio.Event()
    release = asyncio.Event()
    pages = []

    async def on_page(messages, **evidence):
        pages.extend(m.title for m in messages)
        committed.set()
        assert evidence["validated_request_ids"]

    async def fast(checkpoint):
        return response("http")

    async def slow(checkpoint):
        await release.wait()
        return response("rss")

    events = []
    token = REQUESTS.set(events)
    try:
        task = asyncio.create_task(execute(context(on_page=on_page), {"http": fast, "rss": slow}))
        await asyncio.wait_for(committed.wait(), 1)
        assert pages == ["http"] and not task.done()
        release.set()
        result = await task
    finally:
        REQUESTS.reset(token)
    assert pages == ["http", "rss"]
    assert not result.failures and len(result.messages) == 2
    assert result.acquisition_metadata["race"]["winner"] == "http"
    assert {r["race_lane"] for r in events} == {"http", "rss"}


@pytest.mark.parametrize("failures", [{"http"}, {"rss"}, {"http", "rss"}, set()])
async def test_only_both_failures_mark_the_logical_feed_failed(failures):
    def runner(name):
        async def run(cp):
            if name in failures:
                raise TimeoutError(name)
            return response(name)

        return run

    result = await execute(context(), {name: runner(name) for name in ("http", "rss")})
    assert bool(result.failures) == (len(failures) == 2)
    if result.failures:
        assert result.failures[0].error_code == "HTTP_RSS_BOTH_FAILED"


async def test_empty_unproven_result_cannot_manufacture_success_and_checkpoints_are_isolated():
    async def unproven(cp):
        assert cp == {"etag": "http-old"}
        return PollResult()

    async def rss(cp):
        assert cp == {"etag": "rss-old"}
        return response("rss", checkpoint={"etag": "rss-new"})

    result = await execute(
        context(
            checkpoint={"race_lanes": {"http": {"etag": "http-old"}, "rss": {"etag": "rss-old"}}}
        ),
        {"http": unproven, "rss": rss},
    )
    assert not result.failures
    assert not result.acquisition_metadata["race"]["channels"]["http"]["success"]
    assert result.next_checkpoint["race_lanes"] == {
        "http": {"etag": "http-old"},
        "rss": {"etag": "rss-new"},
    }


async def test_cancelled_race_stops_sibling_requests():
    stopped = asyncio.Event()

    async def pending(cp):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(execute(context(), {"http": pending, "rss": pending}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


# Real publisher snapshots retain the exact subscribed column and time contract.
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from doxagent.message_bus_v2.rss_http_race import IR_RECIPES, RECIPES, endpoint_units, poll_ir
from doxagent.message_bus_v2.public_listing import parse_listing
from doxagent.message_bus_v2.technology_sources import Capture, TechnologySource

FIXTURES = Path(__file__).parent / "fixtures/rss_http_race"
SAMPLES = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf8"))


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda x: x["source"] + "-" + str(x["index"]))
def test_verified_publisher_columns_and_timestamps(sample):
    recipe = (
        IR_RECIPES[sample["source"]]
        if sample["source"] in IR_RECIPES
        else RECIPES[sample["source"]][sample["index"]]
    )
    spec = TechnologySource(
        sample["source"],
        sample["source"],
        urlsplit(recipe.url).hostname.removeprefix("www."),
        (recipe.url,),
        (),
    )
    rows = parse_listing(
        recipe, spec, (FIXTURES / sample["file"]).read_text(encoding="utf8"), utc_now()
    )
    assert rows[0].url == sample["first_url"]
    assert rows[0].published_at.isoformat() == sample["first_time"]
    assert rows[0].publication_time_basis == sample["basis"]
    assert len({m.url for m in rows}) == len(rows)


@pytest.mark.parametrize("endpoint", list(IR_RECIPES))
async def test_ir_lanes_keep_original_download_identity_frozen_body_and_owner(endpoint):
    from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints, initial_source
    from doxagent.message_bus_v2.corporate_ir.parsers import parse_listing as native_parse
    from doxagent.message_bus_v2.schema import PollContext, TickerSourceBinding
    from doxagent.message_bus_v2.body_results import article_key

    spec = next(s for s in initial_endpoints() if s.endpoint_id == endpoint)
    source = initial_source()
    binding = TickerSourceBinding(
        binding_id=spec.owner_ticker + ":" + source.source_id,
        ticker=spec.owner_ticker,
        source_id=source.source_id,
    )
    sample = next(s for s in SAMPLES if s["source"] == endpoint)
    native, _ = native_parse(spec, (FIXTURES / (endpoint + ".xml")).read_text(encoding="utf8"))
    raw = (FIXTURES / sample["file"]).read_text(encoding="utf8")

    class Http:
        async def fetch(self, url, recipe, **kwargs):
            e = request_started("GET", url, admitted=True)
            e.update(status_code=200, response_received_at=utc_now().isoformat())
            return Capture(200, url, raw, {})

    async def rss(*args):
        e = request_started("GET", spec.entry_url, admitted=True)
        e.update(status_code=200, response_received_at=utc_now().isoformat())
        return PollResult(
            messages=native,
            next_checkpoint={
                "ir_state": {"binding_id": binding.binding_id, "endpoint_id": endpoint}
            },
        )

    token = REQUESTS.set([])
    try:
        result = await poll_ir(
            SimpleNamespace(ir=rss),
            SimpleNamespace(listing_http=Http()),
            spec,
            PollContext(
                source=source,
                binding=binding,
                ticker=binding.ticker,
                request_permit=permit,
                requested_at=datetime.fromisoformat(sample["first_time"]) + timedelta(minutes=10),
            ),
        )
    finally:
        REQUESTS.reset(token)
    assert not result.failures
    http = next(m for m in result.messages if m.metadata["discovery_lane"] == "http")
    expected = next(m for m in native if m.title == http.title)
    assert article_key(http, source.source_id) == article_key(expected, source.source_id)
    assert http.metadata["ir_endpoint_spec"] == spec.model_dump(mode="json")
    assert http.publisher_name == spec.owner_ticker and "language" not in http.metadata
    assert result.next_checkpoint["ir_state"]["status"] == "succeeded"


def test_unknown_subscribed_feed_is_preserved_as_rss_not_silently_dropped():
    from doxagent.message_bus_v2.schema import TickerSourceBinding

    source = context().source.model_copy(update={"source_id": "nbis_official_actor_rss"})
    feeds = ["https://clickhouse.com/rss.xml", "https://new-actor.example/feed/"]
    units = endpoint_units(
        source,
        TickerSourceBinding(
            binding_id="NBIS:" + source.source_id,
            ticker="NBIS",
            source_id=source.source_id,
            source_parameters={"rss_urls": feeds},
        ),
    )
    assert {r.feed for r in units.values()} == set(feeds)
    assert {r.parser for r in units.values()} == {"race_clickhouse", "race_rss_only"}


def test_configuration_is_idempotent_and_preserves_subscriptions_and_body_settings(tmp_path):
    from tests.fixtures.message_bus_v2 import _bus
    from scripts.ops.configure_rss_http_race import configure

    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("INTC")
    bus.start_ticker("CEG")
    bus.start_ticker("BE")
    before = {s.source_id: s for s in repo.list_sources()}
    bindings = {b.binding_id: b for b in repo.list_bindings()}
    configure(bus)
    for sid in [*RECIPES, "corporate_ir_press_releases"]:
        old = before.get(sid)
        current = repo.get_source(sid)
        if old is None:
            continue
        for field in [
            "adapter_ref",
            "content_enrichment_mode",
            "content_language",
            "acquisition_mode",
            "default_polling_config",
            "distribution_policy",
        ]:
            assert getattr(current, field) == getattr(old, field)
        assert current.scheduler_constraints.max_concurrency >= 2
    for bid, b in bindings.items():
        current = repo.get_binding(bid)
        assert (
            current.source_parameters == b.source_parameters
            and current.polling == b.polling
            and current.enabled == b.enabled
        )
    assert all(r["action"] in {"unchanged", "absent"} for r in configure(bus))
    repo.close()


async def test_adaptive_capacity_does_not_serialize_the_two_racers(tmp_path):
    from tests.test_message_collection_plane import setup_plane

    _, _, worker, _, _ = setup_plane(tmp_path)
    source = next(iter(worker.config_loader().sources.values()))
    source = source.model_copy(
        update={
            "default_parameters": {"discovery_mode": "http_rss_race"},
            "scheduler_constraints": source.scheduler_constraints.model_copy(
                update={"capacity_mode": "adaptive", "max_concurrency": 4}
            ),
        }
    )
    assert worker.planner._limiter(source).constraints.max_concurrency == 2


async def test_late_invalid_http200_does_not_renew_freshness_after_good_lane(tmp_path):
    from tests.test_message_collection_plane import setup_plane

    _, _, worker, _, store = setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    args = next(iter(worker.desired.values()))
    source, targets, unit, _ = args
    valid_at = utc_now() - timedelta(seconds=4)

    class Adapter:
        async def poll(self, ctx):
            valid = request_started("GET", "https://example.com/rss", admitted=True)
            valid.update(
                status_code=200, response_received_at=valid_at.isoformat(), list_validated=True
            )
            invalid = request_started("GET", "https://example.com/challenge", admitted=True)
            invalid.update(status_code=200, response_received_at=utc_now().isoformat())
            return PollResult(
                acquisition_metadata={"validated_request_ids": [valid["physical_request_id"]]}
            )

    worker.adapters = SimpleNamespace(resolve=lambda *a, **k: Adapter())
    await worker.acquire(*args)
    assert (
        worker.states[(targets[0].binding_id, unit)]["state"]["last_capture_at"]
        == valid_at.isoformat()
    )
    assert (
        store.states()[(targets[0].binding_id, unit)]["state"]["last_capture_at"]
        == valid_at.isoformat()
    )


def test_race_summaries_share_one_body_job_and_real_revisions_still_schedule(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    source = source.model_copy(
        update={
            "default_parameters": {**source.default_parameters, "discovery_mode": "http_rss_race"}
        }
    )

    def ingest(lane, summary, title="New Micron manufacturing contract", updated=None):
        message = _message(8811).model_copy(
            update={
                "title": title,
                "summary": summary,
                "metadata": {
                    "discovery_lane": lane,
                    "identity_evidence": {
                        "url_kind": "article",
                        **({"updated_at": updated} if updated else {}),
                    },
                },
            }
        )
        rows = live.prepare_input(
            source,
            [(binding.binding_id, binding.ticker, 0)],
            [message],
            observed_at=utc_now().isoformat(),
            unit="FEED:race",
        )
        live.accept(rows)
        return rows[0]["key"]

    key = ingest("http", None)
    with repo.transaction() as db:
        db.execute(
            "update live_articles set state='FETCHING',claim_token='owned' where article_key=?",
            (key,),
        )
    assert ingest("rss", "A publisher RSS description") == key
    with repo._connect() as db:
        row = db.execute("select input_revision,claim_token from live_articles").fetchone()
        assert tuple(row) == (1, "owned")
    ingest("http", None, title="Micron expands manufacturing contract to 25 billion dollars")
    with repo._connect() as db:
        assert db.execute("select input_revision from live_articles").fetchone()[0] == 2
    ingest("rss", "A publisher RSS description")
    with repo._connect() as db:
        assert db.execute("select input_revision from live_articles").fetchone()[0] == 2
    repo.close()


def test_new_http_lane_acquires_proven_recent_update_to_existing_article(tmp_path):
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue

    repo, bus, source, binding = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    source = source.model_copy(
        update={
            "default_parameters": {**source.default_parameters, "discovery_mode": "http_rss_race"}
        }
    )
    m = _message(9911).model_copy(
        update={
            "published_at": utc_now() - timedelta(hours=1),
            "metadata": {"discovery_lane": "rss", "identity_evidence": {"url_kind": "article"}},
        }
    )
    first = live.prepare_input(
        source,
        [(binding.binding_id, binding.ticker, 0)],
        [m],
        observed_at=(utc_now() - timedelta(hours=1)).isoformat(),
        unit="FEED:race",
    )
    live.accept(first)
    updated = m.model_copy(
        update={
            "metadata": {
                "discovery_lane": "http",
                "identity_evidence": {"url_kind": "article", "updated_at": utc_now().isoformat()},
            }
        }
    )
    second = live.prepare_input(
        source,
        [(binding.binding_id, binding.ticker, 0)],
        [updated],
        observed_at=utc_now().isoformat(),
        unit="FEED:race",
    )
    live.accept(second)
    with repo._connect() as db:
        assert db.execute("select input_revision from live_articles").fetchone()[0] == 2
    repo.close()


def test_migration_accepts_legacy_binding_only_required_rss_urls(tmp_path):
    from tests.fixtures.message_bus_v2 import _bus
    from doxagent.message_bus_v2.schema import TickerSourceBinding
    from scripts.ops.configure_rss_http_race import configure

    repo, bus = _bus(tmp_path / "bus.db")
    sid = "nbis_official_actor_rss"
    source = SourceDefinition(
        source_id=sid,
        display_name="Official actors",
        kind="api",
        adapter_ref="builtin:newswire_rss",
        acquisition_mode="by_ticker",
        scheduler_group="actor",
        parameter_schema={
            "type": "object",
            "properties": {"rss_urls": {"type": "array", "items": {"type": "string"}}},
            "required": ["rss_urls"],
            "additionalProperties": False,
        },
    )
    bus.register_source(source)
    feeds = [RECIPES[sid][0].feed]
    repo.save_binding(
        TickerSourceBinding(
            binding_id="NBIS:" + sid,
            ticker="NBIS",
            source_id=sid,
            source_parameters={"rss_urls": feeds},
        )
    )
    configure(bus)
    assert repo.get_binding("NBIS:" + sid).source_parameters["rss_urls"] == feeds
    assert repo.get_source(sid).default_parameters["rss_urls"] == [r.feed for r in RECIPES[sid]]
    repo.close()
