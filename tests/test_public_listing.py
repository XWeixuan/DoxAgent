import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest

from doxagent.message_bus_v2.body_results import article_key
from doxagent.message_bus_v2.industry_sources import parse_shared_feed
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.poll_trace import REQUESTS, request_started
from doxagent.message_bus_v2.public_listing import (
    RECIPES,
    RSS_ONLY_SOURCES,
    ListingAdapter,
    cache_evidence,
    enabled,
    endpoint_units,
    parse_listing,
)
from doxagent.message_bus_v2.schema import (
    AcquisitionMode,
    ContentEnrichmentMode,
    PollResult,
    SharedPollContext,
    SourceDefinition,
    TickerSourceBinding,
    utc_now,
)
from doxagent.message_bus_v2.technology_sources import Capture, TechnologySource

FIXTURES = Path(__file__).parent / "fixtures/public_listing"
SAMPLES = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf8"))
NOW = datetime(2026, 11, 3, 21, tzinfo=UTC)


@asynccontextmanager
async def permit():
    yield


def definition(sid):
    found = next((s for s in initial_sources() if s.source_id == sid), None)
    if found is None:
        found = SourceDefinition(
            source_id=sid,
            display_name=sid,
            adapter_ref="builtin:newswire_rss",
            kind="api",
            scheduler_group="newswire_rss",
            acquisition_mode=AcquisitionMode.BY_TICKER,
            content_language="en",
        )
    return found.model_copy(
        update={
            "default_parameters": {**found.default_parameters, "discovery_mode": "http_listing"}
        }
    )


def spec_for(sid, recipe):
    s = definition(sid)
    return TechnologySource(
        sid,
        s.display_name,
        urlsplit(recipe.url).hostname.removeprefix("www."),
        (recipe.url,),
        (),
        language=s.content_language,
    )


def rows(sid, index=0):
    recipe = RECIPES[sid][index]
    return parse_listing(
        recipe,
        spec_for(sid, recipe),
        (FIXTURES / f"{sid}-{index}.html").read_text(encoding="utf8"),
        NOW,
    )


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: f"{s['source_id']}-{s['index']}")
def test_real_listing_contract_and_identity(sample):
    sid, index = sample["source_id"], sample["index"]
    messages = rows(sid, index)
    assert len(messages) >= 1
    assert len({m.url for m in messages}) == len(messages)
    assert all(m.body is None for m in messages)  # Original BODY path owns completion.
    assert all(m.metadata["identity_evidence"]["url_kind"] == "article" for m in messages)
    assert all(m.metadata["discovery_method"] == "http_listing" for m in messages)
    assert all(m.published_at.tzinfo is not None for m in messages)
    rss = FIXTURES / f"{sid}-{index}-rss.xml"
    if rss.exists():
        feed = RECIPES[sid][index].feed
        old = parse_shared_feed(
            rss.read_text(encoding="utf8"),
            feed_url=feed,
            publisher=definition(sid).display_name,
            language=definition(sid).content_language,
            now=NOW,
        )
        previous = {m.url: m for m in old}
        for message in messages:
            if message.url in previous:
                from doxagent.message_bus_v2.deduplication import article_identity

                old_message = article_identity(
                    previous[message.url], sid, default_language=definition(sid).content_language
                )
                assert article_key(message, sid) == article_key(old_message, sid)


def test_thelec_article_query_id_and_korean_time_survive():
    messages = rows("thelec_semiconductors_rss")
    assert len({m.url for m in messages}) == 2
    assert all("?idxno=" in m.url for m in messages)
    assert all(m.publication_time_basis == "EXACT" for m in messages)
    assert messages[0].published_at == datetime(2026, 11, 3, 8, 9, tzinfo=UTC)


def test_dates_are_not_fabricated_as_exact():
    assert all(m.publication_time_basis == "DATE" for m in rows("canary_media"))
    assert all(m.publication_time_basis == "UNKNOWN_FIRST_SEEN" for m in rows("utility_dive"))
    assert all(m.publication_time_basis == "EXACT" for m in rows("data_center_frontier"))
    assert rows("etnews_rss")[0].published_at.hour == 7  # 16:00 KST.


class Http:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def fetch(self, url, spec, **kwargs):
        self.calls.append((url, spec, kwargs))
        result = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(result, BaseException):
            raise result
        event = request_started(kwargs.get("method", "GET"), url, admitted=True)
        if event is not None:
            event.update(status_code=result.status, response_received_at=utc_now().isoformat())
        return replace(result, url=url)


def context(sid="quantum_computing_report", index=0, checkpoint=None):
    return SharedPollContext(
        run_id="fixture",
        source=definition(sid),
        acquisition_unit=list(endpoint_units(definition(sid)))[index],
        request_permit=permit,
        checkpoint=checkpoint or {},
        requested_at=NOW,
    )


def capture(sid="quantum_computing_report", index=0, status=200, headers=None):
    recipe = RECIPES[sid][index]
    return Capture(
        status,
        recipe.url,
        (FIXTURES / f"{sid}-{index}.html").read_text(encoding="utf8") if status == 200 else "",
        headers or {},
    )


async def test_delta_conditional_request_and_material_change_do_not_repeat_body_work():
    r = capture(headers={"etag": '"v1"'})
    h = Http(
        r,
        Capture(304, r.url, "", {"etag": '"v1"'}),
        r,
        Capture(
            200, r.url, r.text.replace("Iterative Redecode", "New Funding Iterative Redecode"), {}
        ),
    )
    delegate = SimpleNamespace(poll_shared=AsyncMock())
    adapter = ListingAdapter(delegate, h)
    ctx = context()
    events = []
    token = REQUESTS.set(events)
    try:
        first = await adapter.poll_shared(ctx)
        ctx.checkpoint = first.next_checkpoint
        assert len(first.messages) == 2 and not first.failures
        second = await adapter.poll_shared(ctx)
        ctx.checkpoint = second.next_checkpoint
        assert second.messages == [] and not second.failures
        assert h.calls[1][2]["headers"]["If-None-Match"] == '"v1"'
        third = await adapter.poll_shared(ctx)
        ctx.checkpoint = third.next_checkpoint
        assert third.messages == []
        fourth = await adapter.poll_shared(ctx)
        assert len(fourth.messages) == 1
        assert fourth.acquisition_metadata["validated_request_ids"]
        delegate.poll_shared.assert_not_called()
    finally:
        REQUESTS.reset(token)


@pytest.mark.parametrize(
    "response",
    [
        Capture(403, "https://quantumcomputingreport.com/news/", "denied", {}),
        Capture(
            200,
            "https://quantumcomputingreport.com/news/",
            "<html><title>Just a moment...</title></html>",
            {},
        ),
        Capture(304, "https://quantumcomputingreport.com/news/", "", {}),
    ],
)
async def test_rss_fallback_keeps_news_but_never_claims_healthy_listing(response):
    sample = rows("quantum_computing_report")[0]
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult(messages=[sample])))
    adapter = ListingAdapter(delegate, Http(response))
    events = []
    token = REQUESTS.set(events)
    try:
        result = await adapter.poll_shared(context())
    finally:
        REQUESTS.reset(token)
    assert result.messages and result.failures[0].error_code == "HTTP_LISTING_FAILED_RSS_FALLBACK"
    assert result.acquisition_metadata["discovery_method"] == "rss_fallback"
    assert result.messages[0].metadata["discovery_method"] == "rss_fallback"
    assert not result.window_done and not result.next_checkpoint["listings"]
    assert not result.acquisition_metadata["validated_request_ids"]
    assert not any(e.get("list_validated") for e in events)


async def test_old_cache_is_not_a_fresh_poll_and_uses_original_rss_delegate():
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult()))
    result = await ListingAdapter(
        delegate, Http(capture("tomshardware_rss", headers={"age": "5400"}))
    ).poll_shared(context("tomshardware_rss"))
    assert "stale_cache_age:5400" in result.failures[0].error_message
    delegate.poll_shared.assert_awaited_once()


async def test_nsf_uses_verified_uncached_readonly_post():
    h = Http(capture("nsf_news"))
    delegate = SimpleNamespace(poll_shared=AsyncMock())
    result = await ListingAdapter(delegate, h).poll_shared(context("nsf_news"))
    assert result.messages and not result.failures
    assert h.calls[0][2]["method"] == "POST"
    assert h.calls[0][2]["headers"]["X-Requested-With"] == "XMLHttpRequest"


async def test_thelec_uses_uncached_post_form_and_keeps_semiconductor_filter():
    h = Http(capture("thelec_semiconductors_rss"))
    result = await ListingAdapter(SimpleNamespace(), h).poll_shared(
        context("thelec_semiconductors_rss")
    )
    assert result.messages and not result.failures
    assert h.calls[0][2]["method"] == "POST"
    assert h.calls[0][2]["form_body"] == {"sc_section_code": "S1N2", "view_type": "sm"}


def test_thelec_wrong_uncached_all_news_is_not_a_successful_section():
    recipe = RECIPES["thelec_semiconductors_rss"][0]
    raw = (FIXTURES / "thelec_semiconductors_rss-0.html").read_text(encoding="utf8")
    with pytest.raises(ValueError, match="thelec_wrong_section"):
        parse_listing(
            recipe,
            spec_for("thelec_semiconductors_rss", recipe),
            "<title>전체 기사목록</title>" + raw,
            NOW,
        )


async def test_one_failed_internal_endpoint_cannot_block_its_sibling():
    class IsolatedHttp:
        async def fetch(self, url, spec, **kwargs):
            if "sheet=cloud" in url:
                await asyncio.Event().wait()
            return capture("crn_cloud_security_datacenter", 1)

    adapter = ListingAdapter(SimpleNamespace(), IsolatedHttp())
    slow = asyncio.create_task(adapter.poll_shared(context("crn_cloud_security_datacenter", 0)))
    try:
        good = await asyncio.wait_for(
            adapter.poll_shared(context("crn_cloud_security_datacenter", 1)), 1
        )
        assert good.messages and not good.failures
        assert not slow.done()
    finally:
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow


async def test_collection_revocation_is_not_treated_as_publisher_failure():
    from doxagent.message_bus_v2.collection_override import CollectionStopped

    delegate = SimpleNamespace(poll_shared=AsyncMock())
    with pytest.raises(CollectionStopped):
        await ListingAdapter(delegate, Http(CollectionStopped("revoked"))).poll_shared(context())
    delegate.poll_shared.assert_not_called()


async def test_closed_sweep_keeps_original_recovery_recipe():
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult()))
    h = Http(AssertionError("recovery must retain RSS recipe"))
    ctx = context()
    ctx.window_cutoff = NOW
    await ListingAdapter(delegate, h).poll_shared(ctx)
    delegate.poll_shared.assert_awaited_once()
    assert not h.calls


def test_configuration_preserves_body_binding_semantics_and_is_idempotent(tmp_path):
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from scripts.ops.configure_rss_listing_migration import configure

    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    for sid in RECIPES:
        source = definition(sid).model_copy(update={"default_parameters": {}})
        bus.register_source(source)
        repo.save_binding(
            TickerSourceBinding(
                binding_id="SNOW:" + sid,
                ticker="SNOW",
                source_id=sid,
                source_version=source.version,
                source_parameters={},
            )
        )
    before = {s.source_id: s for s in repo.list_sources()}
    report = configure(bus)
    assert len(report) == 20 and all(s["action"] == "updated" for s in report)
    for sid in RECIPES:
        current = repo.get_source(sid)
        old = before[sid]
        assert enabled(current) == (sid not in RSS_ONLY_SOURCES)
        assert current.kind == ("api" if sid in RSS_ONLY_SOURCES else "crawler")
        for field in [
            "adapter_ref",
            "entry_url",
            "site_id",
            "content_language",
            "acquisition_mode",
            "scheduler_group",
            "scheduler_constraints",
            "default_polling_config",
            "distribution_policy",
        ]:
            assert getattr(current, field) == getattr(old, field)
        binding = repo.get_binding("SNOW:" + sid)
        assert binding.source_parameters == {} and binding.source_version == current.version
    assert all(r["action"] == "unchanged" for r in configure(bus))
    repo.close()


def test_aviation_topics_have_distinct_units_and_article_identity_overlaps():
    recipes = endpoint_units(definition("aviationweek_rss"))
    assert len(recipes) == 5 and len({r.feed for r in recipes.values()}) == 1
    groups = [rows("aviationweek_rss", i) for i in range(5)]
    urls = [m.url for group in groups for m in group]
    assert len(set(urls)) < len(urls)  # Real cross-tag articles, not synthetic duplicates.
    assert all(m.publication_time_basis == "EXACT" for group in groups for m in group)


async def test_aviation_combined_poll_deduplicates_internal_topics():
    class TopicsHttp(Http):
        async def fetch(self, url, spec, **kwargs):
            index = next(i for i, r in enumerate(RECIPES["aviationweek_rss"]) if r.url == url)
            self.responses = [capture("aviationweek_rss", index)]
            return await super().fetch(url, spec, **kwargs)

    ctx = context("aviationweek_rss")
    ctx.acquisition_unit = "ALL"
    result = await ListingAdapter(SimpleNamespace(), TopicsHttp()).poll_shared(ctx)
    expected = {m.url for i in range(5) for m in rows("aviationweek_rss", i)}
    assert not result.failures
    assert len(result.messages) == len(expected)
    assert len(result.next_checkpoint["listings"]) == 5


async def test_aviation_separate_topic_captures_deduplicate_in_existing_bus(tmp_path, monkeypatch):
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service

    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    source = definition("aviationweek_rss").model_copy(
        update={"content_enrichment_mode": ContentEnrichmentMode.SKIP, "default_parameters": {}}
    )
    bus.register_source(source)
    binding = TickerSourceBinding(
        binding_id="RKLB:aviationweek_rss",
        ticker="RKLB",
        source_id=source.source_id,
        source_version=source.version,
    )
    repo.save_binding(binding)
    seen = {}
    for i in range(5):
        for message in rows("aviationweek_rss", i):
            if message.url in seen:
                first, second = seen[message.url], message
                break
            seen[message.url] = message
        else:
            continue
        break
    monkeypatch.setattr(
        "doxagent.message_bus_v2.service.utc_now",
        lambda: first.published_at + timedelta(seconds=20),
    )
    a = await bus.accept_message(source=source, binding=binding, message=first, bootstrap=False)
    b = await bus.accept_message(source=source, binding=binding, message=second, bootstrap=False)
    assert a.decision.value == "inserted" and b.decision.value == "duplicate"
    assert a.raw_message_id == b.raw_message_id
    repo.close()


@pytest.mark.parametrize("sid", sorted(RSS_ONLY_SOURCES))
async def test_deliberate_rss_choice_never_probes_http_or_marks_fallback_failure(sid):
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult()))
    http = Http(AssertionError("RSS choice must not probe HTML"))
    ctx = context(sid)
    ctx.source = ctx.source.model_copy(update={"default_parameters": {"discovery_mode": "rss"}})
    result = await ListingAdapter(delegate, http).poll_shared(ctx)
    assert not result.failures and not http.calls
    delegate.poll_shared.assert_awaited_once()


def test_cache_age_uses_date_even_when_age_is_hidden_but_not_article_last_modified():
    from dataclasses import replace

    recipe = RECIPES["quantum_computing_report"][0]
    base = capture(headers={"date": "Tue, 03 Nov 2026 20:00:00 GMT"})
    with pytest.raises(ValueError, match="stale_cache_age"):
        cache_evidence(base, recipe, now=NOW)
    fresh = replace(
        base,
        headers={
            "age": "0",
            "date": "Tue, 03 Nov 2026 21:00:00 GMT",
            "last-modified": "Wed, 01 Jan 2020 00:00:00 GMT",
            "cache-control": "max-age=31536000",
        },
    )
    assert cache_evidence(fresh, recipe, now=NOW)["observed_age_seconds"] == 0


@pytest.mark.parametrize("status", [200, 304])
async def test_old_http_success_and_conditional_response_are_failed_not_fresh(status):
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult()))
    ctx = context(
        checkpoint={
            "listings": {
                RECIPES["quantum_computing_report"][0].url: {"baseline": True, "etag": '"old"'}
            }
        }
    )
    response = capture(status=status, headers={"age": "900", "cache-control": "no-store,private"})
    result = await ListingAdapter(delegate, Http(response)).poll_shared(ctx)
    assert result.failures and "stale_cache_age" in result.failures[0].error_message
    assert not result.next_checkpoint["listings"]


async def test_quantum_second_page_cache_is_also_validated():
    first = capture("quantum_zeitgeist")
    second = capture("quantum_zeitgeist", headers={"age": "600"})
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult()))
    result = await ListingAdapter(delegate, Http(first, second)).poll_shared(
        context("quantum_zeitgeist")
    )
    assert result.failures and "stale_cache_age" in result.failures[0].error_message


async def test_cache_evidence_is_bound_to_physical_request_receipt():
    events = []
    token = REQUESTS.set(events)
    try:
        result = await ListingAdapter(
            SimpleNamespace(), Http(capture(headers={"age": "12"}))
        ).poll_shared(context())
    finally:
        REQUESTS.reset(token)
    assert not result.failures
    assert len(events) == 1 and events[0]["listing_cache"]["observed_age_seconds"] == 12


def test_explicit_stale_warning_is_not_accepted_as_fresh():
    with pytest.raises(ValueError, match="stale_cache_warning"):
        cache_evidence(
            capture(headers={"warning": '110 cache.example "Response is stale"'}),
            RECIPES["quantum_computing_report"][0],
            now=NOW,
        )


def test_proxy_explicit_stale_response_without_age_is_rejected():
    with pytest.raises(ValueError, match="stale_cache_status:x-proxy-cache=STALE"):
        cache_evidence(
            capture(headers={"x-proxy-cache": "STALE"}),
            RECIPES["quantum_computing_report"][0],
            now=NOW,
        )


@pytest.mark.parametrize("failure_code", ["HTTP_LISTING_FAILED_RSS_FALLBACK", "HTTP_LISTING_FAILED"])
async def test_fallback_capture_is_failed_in_cycles_unit_and_ui_even_with_old_success(
    tmp_path, monkeypatch, failure_code
):
    from doxagent.message_bus_v2.collection_plane import observability
    from doxagent.message_bus_v2.schema import AcquisitionFailure
    from tests.test_content_enrichment_hub import _message
    from tests.test_message_collection_plane import setup_plane

    repo, bus, worker, _, store = setup_plane(tmp_path)
    await worker.refresh(seeds=True)
    args = next(iter(worker.desired.values()))
    source, targets, unit, options = args
    worker.states[(targets[0].binding_id, unit)]["state"].update(
        last_capture_at=(utc_now() - timedelta(minutes=1)).isoformat(),
        last_success_at=utc_now().isoformat(),
    )

    class Fallback:
        async def poll(self, ctx):
            e = request_started("GET", "https://example.com/rss", admitted=True)
            e.update(status_code=200, response_received_at=utc_now().isoformat())
            return PollResult(
                messages=[_message(9181)],
                acquisition_metadata={"validated_request_ids": []},
                failures=[
                    AcquisitionFailure(
                        source_id=source.source_id,
                        binding_id=targets[0].binding_id,
                        ticker=targets[0].ticker,
                        error_code=failure_code,
                        error_message="HTTP403; RSS still available",
                        raw_hash="test",
                        original_payload={},
                    )
                ],
            )

    worker.adapters = SimpleNamespace(resolve=lambda *a, **k: Fallback())
    await worker.acquire(*args)
    monkeypatch.setattr(observability, "store_from_environment", lambda: store)
    projected = observability.binding_states()[targets[0].binding_id]
    assert projected["status"] == "failed"
    assert projected["last_error_message"] == "HTTP403; RSS still available"
    state = store.states()[(targets[0].binding_id, unit)]["state"]
    assert (
        state["status"] == "failed"
        and state["last_error_code"] == failure_code
    )
    with store.connect() as d:
        proof = json.loads(
            d.execute(
                "select data_json from cycles where status!='SCHEDULE' "
                "order by finished_at desc limit 1"
            ).fetchone()[0]
        )
        assert proof["terminal_error_message"] == "HTTP403; RSS still available"
        assert (
            d.execute(
                "select status from cycles where status!='SCHEDULE' "
                "order by finished_at desc limit 1"
            ).fetchone()[0]
            == "FAILED"
        )
    assert store.pending()  # Failure status never discards fallback news.


async def test_known_coverage_gap_keeps_rss_without_wasting_a_shell_request():
    http = Http(AssertionError("known incomplete page must not be refetched"))
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult()))
    result = await ListingAdapter(delegate, http).poll_shared(context("army_news_rss"))
    assert result.failures and "coverage_review" in result.failures[0].error_message
    assert not http.calls
    delegate.poll_shared.assert_awaited_once()


async def test_fallback_network_proof_is_separate_from_primary_cadence():
    from doxagent.message_bus_v2.poll_trace import validate_listing

    class Delegate:
        async def poll_shared(self, context):
            event = request_started("GET", "https://example.com/feed.xml", admitted=True)
            event.update(status_code=200, response_received_at=utc_now().isoformat())
            return PollResult(messages=rows("quantum_computing_report"))

    events = []
    token = REQUESTS.set(events)
    try:
        result = await ListingAdapter(Delegate(), Http(capture(status=403))).poll_shared(context())
        assert result.failures
        assert [e["role"] for e in events] == ["listing", "rss_fallback"]
        assert validate_listing(events) == []
    finally:
        REQUESTS.reset(token)


def test_legacy_shared_projection_also_preserves_failure_and_old_freshness(tmp_path):
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
    from doxagent.message_bus_v2.schema import AcquisitionFailure

    repo = MessageBusV2Repository(tmp_path / "legacy.db")
    source = definition("quantum_computing_report")
    binding = TickerSourceBinding(
        binding_id="IONQ:quantum_computing_report", ticker="IONQ", source_id=source.source_id
    )
    scheduler = GlobalPollScheduler.__new__(GlobalPollScheduler)
    scheduler.repository = repo
    repo.save_source(source)
    repo.save_binding(binding)
    old = utc_now() - timedelta(minutes=2)
    repo.save_poll_state(
        repo.get_poll_state(binding).model_copy(
            update={"last_success_at": old, "last_capture_at": old}
        )
    )
    failure = AcquisitionFailure(
        source_id=source.source_id,
        binding_id=binding.binding_id,
        ticker=binding.ticker,
        error_code="HTTP_LISTING_FAILED_RSS_FALLBACK",
        error_message="403",
        raw_hash="failure",
        original_payload={},
    )
    scheduler._record_distribution_poll(
        source,
        [(binding, 1, {})],
        {"created_at": utc_now().isoformat(), "mode": "REALTIME", "coverage": "PARTIAL"},
        observed_at=utc_now(),
        coverage="PARTIAL",
        discovery_failure=failure,
    )
    state = repo.get_poll_unit_state(binding, "SHARED")
    assert state.status.value == "failed"
    assert state.last_success_at == old and state.last_capture_at == old
    assert state.last_error_code == failure.error_code
    assert state.last_error_message == "403"
    repo.close()


async def test_transport_retry_recovers_on_same_route_without_rss_or_budget_amplification():
    http = Http(TimeoutError("pooled connection lost"), capture())
    delegate = SimpleNamespace(poll_shared=AsyncMock())
    result = await ListingAdapter(delegate, http).poll_shared(context())
    assert result.messages and not result.failures
    assert len(http.calls) == 2
    assert http.calls[0][0:2] == http.calls[1][0:2]
    delegate.poll_shared.assert_not_called()


async def test_transient_retries_share_one_short_deadline(monkeypatch):
    from doxagent.message_bus_v2 import public_listing

    monkeypatch.setattr(public_listing, "PRIMARY_TIMEOUT_SECONDS", 0.02)

    class Slow:
        calls = 0

        async def fetch(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(5)

    http = Slow()
    delegate = SimpleNamespace(poll_shared=AsyncMock(return_value=PollResult()))
    result = await asyncio.wait_for(ListingAdapter(delegate, http).poll_shared(context()), 0.5)
    assert result.failures and http.calls == 1
    delegate.poll_shared.assert_awaited_once()


def test_enterprise_internal_feed_opt_out_is_preserved():
    from doxagent.message_bus_v2.public_listing import endpoint_units

    source = definition("enterprise_apps_official_rss")
    salesforce = RECIPES[source.source_id][1]
    binding = TickerSourceBinding(
        binding_id="SNOW:" + source.source_id,
        ticker="SNOW",
        source_id=source.source_id,
        source_parameters={"rss_urls": [salesforce.feed]},
    )
    assert list(endpoint_units(source, binding).values()) == [salesforce]


async def test_quantum_two_pages_preserve_200_item_window_and_both_transport_proofs():
    first = capture("quantum_zeitgeist")
    data = json.loads(first.text)
    data["posts"] = [{**row, "URL": row["URL"].rstrip("/") + "-page2/"} for row in data["posts"]]
    second = Capture(200, first.url + "&offset=100", json.dumps(data), {})
    http = Http(first, second)
    events = []
    token = REQUESTS.set(events)
    try:
        result = await ListingAdapter(SimpleNamespace(), http).poll_shared(
            context("quantum_zeitgeist")
        )
        assert not result.failures and len(result.messages) == 4
        assert len(result.acquisition_metadata["validated_request_ids"]) == 2
        assert "offset=100" in http.calls[1][0]
        assert all(call[1].route == "us-standard-5" for call in http.calls)
    finally:
        REQUESTS.reset(token)


async def test_api_body_only_modification_is_a_material_update_without_repeating_unchanged_rows():
    from doxagent.message_bus_v2.body_results import update_hint

    first = capture("enterprise_apps_official_rss")
    data = json.loads(first.text)
    data[0]["modified_gmt"] = "2026-11-03T21:00:00"
    second = Capture(200, first.url, json.dumps(data), {})
    http = Http(first, second)
    adapter = ListingAdapter(SimpleNamespace(), http)
    ctx = context("enterprise_apps_official_rss")
    initial = await adapter.poll_shared(ctx)
    ctx.checkpoint = initial.next_checkpoint
    updated = await adapter.poll_shared(ctx)
    assert len(updated.messages) == 1
    assert updated.messages[0].title == initial.messages[0].title
    assert update_hint(updated.messages[0]) != update_hint(initial.messages[0])
