import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.message_bus_v2.newswire_listing import RECIPES, cache_evidence, parse_listing, poll_listing
from doxagent.message_bus_v2.newswire_sources import initial_sources, endpoint_units, parse_feed
from doxagent.message_bus_v2.schema import PollResult, SharedPollContext

NOW = datetime(2026, 11, 6, 19, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "newswire_listing"


def source(sid="prnewswire_massive"):
    return next(s for s in initial_sources() if s.source_id == sid)


@asynccontextmanager
async def permit():
    yield


def context(sid="prnewswire_massive", **kwargs):
    return SharedPollContext(run_id="test", source=source(sid), acquisition_unit="TEST:WIRE:listing",
                             requested_at=NOW, request_permit=permit, **kwargs)


def response(text="", status=200, headers=None):
    return httpx.Response(status, text=text, headers=headers or {}, request=httpx.Request("GET", "https://wire.test/"))


def fixture(name):
    return (FIXTURES / (name + ".html")).read_text(encoding="utf8")


@pytest.mark.parametrize("sid,name", [("prnewswire_massive", "prnewswire"), ("globenewswire_massive", "gn"), ("access_newswire_massive", "access")])
def test_real_cards_keep_full_titles_links_eastern_time_and_minute_precision(sid, name):
    rows = parse_listing(fixture(name), source(sid), NOW)
    assert len(rows) == 3
    assert all(r.body is None and r.metadata["publication_time_precision"] == "minute" for r in rows)
    assert rows[0].published_at == datetime(2026, 11, 6, 18, 40 if name == "access" else 55, tzinfo=UTC)
    assert not rows[0].title.startswith(("13:55", "13:40"))


def test_prn_midnight_does_not_turn_previous_day_releases_into_fresh_news():
    text = fixture("prnewswire").replace("13:55 ET", "Nov 05, 2026, 23:55 ET")
    row = parse_listing(text, source(), NOW + timedelta(days=1))[0]
    assert row.published_at == datetime(2026, 11, 6, 4, 55, tzinfo=UTC)


@pytest.mark.parametrize("sid,name", [("prnewswire_massive", "prnewswire"), ("globenewswire_massive", "gn")])
def test_rss_and_html_have_same_body_identity_even_when_guid_and_description_differ(sid, name):
    from doxagent.message_bus_v2.body_results import article_key

    listing = parse_listing(fixture(name), source(sid), NOW)[0]
    xml = f'<rss><channel><item><title>{listing.title.replace("&", "&amp;")}</title><link>{listing.url}</link><guid>provider-123</guid><pubDate>Fri, 6 Nov 2026 18:55:00 +0000</pubDate><description>RSS teaser</description></item></channel></rss>'
    rss = parse_feed(xml, source(sid), source(sid).default_parameters["endpoints"][0], NOW)[0]
    assert article_key(listing, sid) == article_key(rss, sid)
    if sid == "globenewswire_massive":
        from doxagent.message_bus_v2.body_results import update_hint
        assert listing.summary is None and rss.summary is None
        assert update_hint(listing) == update_hint(rss)


@pytest.mark.parametrize("sid", ["prnewswire_massive", "globenewswire_massive", "access_newswire_massive"])
def test_challenge_or_shell_is_not_an_empty_healthy_listing(sid):
    with pytest.raises(ValueError):
        parse_listing("<html><title>Just a moment</title><a href='/news'>News</a></html>", source(sid), NOW)


@pytest.mark.parametrize("headers", [{"Age": "121"}, {"Age": "invalid"}, {"Warning": "110 - stale"}])
def test_stale_cache_is_failure(headers):
    assert cache_evidence(response(headers=headers), "https://wire.test", "GET").get("error")


async def test_second_poll_unchanged_listing_does_not_repeat_body_inputs():
    adapter = SimpleNamespace(listing_request=AsyncMock(return_value=(response(fixture("prnewswire")), "direct")), poll_feed=AsyncMock())
    first = await poll_listing(adapter, context())
    assert len(first.messages) == 3 and not first.failures
    second = await poll_listing(adapter, context(checkpoint=first.next_checkpoint))
    assert not second.messages and not second.failures
    adapter.poll_feed.assert_not_called()
    assert adapter.listing_request.call_args.args[2]["method"] == "GET"


async def test_304_requires_prior_validated_baseline_and_preserves_seen():
    adapter = SimpleNamespace(listing_request=AsyncMock(return_value=(response(status=304), "direct")), poll_feed=AsyncMock(return_value=PollResult()))
    missing = await poll_listing(adapter, context("globenewswire_massive"))
    assert missing.failures and not missing.window_done
    cp = {"head": {"baseline": True, "etag": "v1"}, "seen": {"url": "digest"}}
    result = await poll_listing(adapter, context("globenewswire_massive", checkpoint=cp))
    assert not result.failures and result.next_checkpoint == cp
    assert adapter.listing_request.call_args.args[-1] == {"If-None-Match": "v1"}


async def test_http_failure_retains_rss_messages_but_never_renews_listing_health():
    from doxagent.message_bus_v2.poll_trace import REQUESTS, REQUEST_ROLE

    rows = parse_listing(fixture("prnewswire"), source(), NOW)
    roles = []
    async def rss(ctx):
        roles.append(REQUEST_ROLE.get())
        assert ctx.checkpoint == {}
        return PollResult(messages=rows, next_checkpoint={"seen": {"rss": "not_primary"}})

    adapter = SimpleNamespace(listing_request=AsyncMock(return_value=(response(status=403), "direct")), poll_feed=rss)
    events = []
    token = REQUESTS.set(events)
    try:
        result = await poll_listing(adapter, context())
    finally:
        REQUESTS.reset(token)
    assert result.failures[0].error_code == "HTTP_LISTING_FAILED_RSS_FALLBACK"
    assert len(result.messages) == 3 and not result.window_done
    assert result.next_checkpoint == {} and result.acquisition_metadata["validated_request_ids"] == []
    assert roles == ["rss_fallback", "rss_fallback"]
    assert all(r.metadata["discovery_method"] == "rss_fallback" for r in result.messages)


async def test_partial_http_failure_does_not_admit_cards_older_than_normal_window(monkeypatch):
    recipe={**RECIPES['prnewswire_massive'],'size':3}
    monkeypatch.setitem(RECIPES,'prnewswire_massive',recipe)
    text=fixture('prnewswire').replace('13:55 ET','12:15 ET',1)
    adapter=SimpleNamespace(listing_request=AsyncMock(side_effect=[
        (response(text),'direct'),(response(status=500),'direct')]),
        poll_feed=AsyncMock(return_value=PollResult()))
    result=await poll_listing(adapter,context())
    assert result.failures and not result.window_done
    assert len(result.messages)==2
    assert all(row.published_at>=NOW-timedelta(minutes=30) for row in result.messages)


async def test_access_does_not_invent_blog_rss_as_fallback():
    adapter = SimpleNamespace(listing_request=AsyncMock(return_value=(response(status=403), "us-standard-5")), poll_feed=AsyncMock())
    result = await poll_listing(adapter, context("access_newswire_massive"))
    assert result.failures[0].error_code == "HTTP_LISTING_FAILED"
    adapter.poll_feed.assert_not_called()


async def test_paging_covers_burst_and_failed_second_page_keeps_first_page_news(monkeypatch):
    monkeypatch.setitem(RECIPES, "prnewswire_massive", {**RECIPES["prnewswire_massive"], "size": 3})
    second = fixture("prnewswire").replace("13:55 ET", "13:20 ET").replace("13:48 ET", "13:20 ET").replace("13:41 ET", "13:20 ET").replace("302932308", "302932999")
    adapter = SimpleNamespace(listing_request=AsyncMock(side_effect=[(response(fixture("prnewswire")), "direct"), (response(second), "direct")]), poll_feed=AsyncMock(return_value=PollResult()))
    result = await poll_listing(adapter, context())
    assert not result.failures and adapter.listing_request.await_count == 2
    assert "page=2" in adapter.listing_request.call_args.args[1]
    assert len(result.messages) == 3  # The new second-page identity is outside the admission window.
    adapter.listing_request.side_effect = [(response(fixture("prnewswire")), "direct"), (response(status=403), "direct")]
    result = await poll_listing(adapter, context())
    assert result.failures and len(result.messages) == 3
    assert all(r.metadata["discovery_method"] == "http_listing_partial" for r in result.messages)


async def test_cache_age_failure_is_auditable_and_falls_back_instead_of_claiming_freshness():
    adapter = SimpleNamespace(listing_request=AsyncMock(return_value=(response(fixture("prnewswire"), headers={"age": "500", "cache-control": "max-age=60"}), "direct")), poll_feed=AsyncMock(return_value=PollResult()))
    result = await poll_listing(adapter, context())
    assert result.failures and result.acquisition_metadata["cache"][0]["age"] == "500"
    assert "cache_age" in result.failures[0].error_message


async def test_one_old_outlier_cannot_hide_fresh_articles_on_the_next_page(monkeypatch):
    monkeypatch.setitem(RECIPES, "prnewswire_massive", {**RECIPES["prnewswire_massive"], "size": 3})
    first = fixture("prnewswire").replace("13:55 ET", "13:20 ET")
    second = fixture("prnewswire").replace("302932308", "302932999")
    adapter = SimpleNamespace(listing_request=AsyncMock(side_effect=[(response(first), "direct"), (response(second), "direct"), (response(first), "direct"), (response(first), "direct")]), poll_feed=AsyncMock(return_value=PollResult()))
    # Prior known articles only live on the second page in this burst.
    old_url = parse_listing(second, source(), NOW)[0].url
    result = await poll_listing(adapter, context(checkpoint={"seen": {old_url: "old"}}))
    assert not result.failures and adapter.listing_request.await_count == 2
    assert any(r.url == old_url for r in result.messages)


@pytest.mark.parametrize("sid", ["tmx_newsfile_massive"])
async def test_reviewed_challenges_do_not_hammer_http_or_start_browser(sid):
    adapter = SimpleNamespace(listing_request=AsyncMock(), poll_feed=AsyncMock(return_value=PollResult()))
    result = await poll_listing(adapter, context(sid))
    assert result.failures and "review required" in result.failures[0].error_message
    adapter.listing_request.assert_not_called()
    adapter.poll_feed.assert_awaited_once()


async def test_revocation_cancellation_never_trigger_fallback():
    from doxagent.message_bus_v2.collection_override import CollectionStopped

    adapter = SimpleNamespace(listing_request=AsyncMock(side_effect=CollectionStopped("revoked")), poll_feed=AsyncMock())
    with pytest.raises(CollectionStopped):
        await poll_listing(adapter, context())
    adapter.poll_feed.assert_not_called()
    adapter.listing_request.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await poll_listing(adapter, context())
    adapter.poll_feed.assert_not_called()


async def test_slow_publisher_does_not_block_another_platform():
    async def fetch(ctx, *args):
        if ctx.source.source_id == "prnewswire_massive":
            await asyncio.Event().wait()
        return response(fixture("gn")), "direct"

    adapter = SimpleNamespace(listing_request=fetch, poll_feed=AsyncMock())
    slow = asyncio.create_task(poll_listing(adapter, context()))
    try:
        good = await asyncio.wait_for(poll_listing(adapter, context("globenewswire_massive")), 1)
        assert good.messages and not good.failures and not slow.done()
    finally:
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow


async def test_globe_rss_is_independently_scheduled_and_never_waits_for_http():
    from doxagent.message_bus_v2.newswire_sources import NewswireAdapter

    adapter = NewswireAdapter()
    rows = parse_listing(fixture("gn"), source("globenewswire_massive"), NOW)
    adapter.poll_feed = AsyncMock(return_value=PollResult(messages=rows))
    adapter.listing_request = AsyncMock(side_effect=AssertionError("parallel RSS must not call HTTP listing"))
    ctx = context("globenewswire_massive").model_copy(update={"acquisition_unit": "TEST:WIRE:all"})
    result = await adapter.poll_shared(ctx)
    assert result.messages and result.acquisition_metadata["method"] == "rss_parallel"
    assert all(r.metadata["discovery_method"] == "rss_parallel" for r in result.messages)
    adapter.listing_request.assert_not_called()
    await adapter.close()


def test_globe_language_and_body_identity_match_across_parallel_endpoints():
    from doxagent.message_bus_v2.body_results import article_key, update_hint

    sid = "globenewswire_massive"
    listing = parse_listing(fixture("gn").replace("/en/", "/fr/"), source(sid), NOW)[0]
    xml = f'<rss><channel><item><title>{listing.title.replace("&", "&amp;")}</title><link>{listing.url}</link><pubDate>Fri, 6 Nov 2026 18:55:00 +0000</pubDate><description>Different teaser</description></item></channel></rss>'
    rss = parse_feed(xml, source(sid), source(sid).default_parameters["endpoints"][0], NOW)[0]
    assert listing.metadata["language"] == rss.metadata["language"] == "fr"
    assert article_key(listing, sid) == article_key(rss, sid)
    assert update_hint(listing) == update_hint(rss)


def test_real_hebrew_percent_escape_case_is_one_body_owner_across_both_paths(tmp_path):
    from doxagent.message_bus_v2.body_results import article_key, update_hint
    from doxagent.message_bus_v2.newswire_listing import canonical_article_url
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.test_delivery import bindings
    from tests.test_content_enrichment_hub import _setup

    sid = "globenewswire_massive"
    text = fixture("gn")
    from lxml import html
    tree = html.fromstring(text)
    link = tree.xpath('//div[contains(@class,"mainLink")]/a')[0]
    link.set("href", "/news-release/2026/11/06/3397854/673/he/%d7%a8%d7%95.html")
    listing = parse_listing(html.tostring(tree, encoding="unicode"), source(sid), NOW)[0]
    upper = canonical_article_url(listing.url)
    xml = f'<rss><channel><item><title>{listing.title.replace("&", "&amp;")}</title><link>{upper}</link><pubDate>Fri, 6 Nov 2026 18:55:00 +0000</pubDate><description>Different teaser</description></item></channel></rss>'
    rss = parse_feed(xml, source(sid), source(sid).default_parameters["endpoints"][0], NOW)[0]
    assert listing.url == rss.url == upper
    assert "%D7%A8" in upper
    assert article_key(listing, sid) == article_key(rss, sid)
    assert update_hint(listing) == update_hint(rss)
    assert canonical_article_url("https://www.prnewswire.com/a%2fb.html") == "https://www.prnewswire.com/a%2fb.html"
    repo, bus, _, _ = _setup(tmp_path)
    s = source(sid);repo.save_source(s);bus.live_queue = LiveQueue(bus)
    live = bus.live_queue.repository
    from doxagent.message_bus_v2.schema import utc_now
    for m in [listing, rss, listing]:
        m = m.model_copy(update={"published_at": utc_now()})
        prepared = live.prepare_input(s, [(b.binding_id, b.ticker, 0) for b in bindings(s)], [m], observed_at=utc_now().isoformat(), unit="WIRE:listing")
        live.accept(prepared)
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM live_articles WHERE source_id=?", (sid,)).fetchone()[0] == 1
        assert db.execute("SELECT input_revision FROM live_articles WHERE source_id=?", (sid,)).fetchone()[0] == 1


def test_configuration_changes_only_discovery_and_is_idempotent(tmp_path):
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from doxagent.message_bus_v2.schema import SourceKind
    from scripts.ops.configure_newswire_http import configure

    repo = MessageBusV2Repository(tmp_path / "bus.db")
    bus = MessageBusV2Service(repo)
    for s in initial_sources():
        s.default_parameters.pop("discovery_mode", None)
        bus.register_source(s.model_copy(update={"kind": SourceKind.API}))
    before = {s.source_id: s for s in repo.list_sources()}
    assert all(r["action"] == "updated" for r in configure(bus))
    for sid in RECIPES:
        old, current = before[sid], repo.get_source(sid)
        rss = sid == "tmx_newsfile_massive"
        assert endpoint_units(current) == (["TEST:WIRE:all"] if rss else ["TEST:WIRE:listing"] + (["TEST:WIRE:all"] if sid == "globenewswire_massive" else []))
        for field in ("adapter_ref", "site_id", "content_language", "entry_url", "acquisition_mode", "scheduler_constraints", "distribution_policy", "delivery_mode", "default_polling_config"):
            assert getattr(old, field) == getattr(current, field)
        assert current.default_parameters == {**old.default_parameters, "discovery_mode": "rss" if rss else "http_listing"}
        assert current.kind == ("api" if rss else "crawler")
    assert repo.get_source("bloomberg_massive") == before["bloomberg_massive"]
    assert all(r["action"] == "unchanged" for r in configure(bus))
    repo.close()


def bw_packet():
    return json.loads((FIXTURES / "businesswire.json").read_text(encoding="utf-8-sig"))


def test_businesswire_real_rsc_data_and_original_rss_share_identity_and_body_hint():
    from doxagent.message_bus_v2.body_results import article_key, update_hint
    from doxagent.message_bus_v2.newswire_listing import canonical_article_url

    sid = "businesswire_massive"
    packet = bw_packet()
    rows = parse_listing('9:["$",null,'+json.dumps(packet)+']', source(sid), NOW)
    assert len(rows) == 3 and {r.metadata["language"] for r in rows} == {"en", "es"}
    assert rows[0].published_at.tzinfo is not None
    for row in rows:
        assert row.body is None and row.summary is None
        assert row.raw_payload["listing_summary"]
        assert row.url.endswith("/")
        xml = f'<rss><channel><item><title>{row.title.replace("&", "&amp;")}</title><link>{row.url}?feedref=tracking</link><pubDate>Mon, 9 Nov 2026 21:55:00 UT</pubDate><description>Different RSS teaser</description></item></channel></rss>'
        rss = parse_feed(xml, source(sid), source(sid).default_parameters["endpoints"][0], NOW)[0]
        assert rss.url == row.url and article_key(row, sid) == article_key(rss, sid)
        assert update_hint(row) == update_hint(rss)
        assert canonical_article_url(row.url.rstrip("/")) == row.url
    packet['news']['items'][0]['headline']='Acme &amp; Partners'
    assert parse_listing(json.dumps(packet), source(sid), NOW)[0].title=='Acme & Partners'


async def test_businesswire_uses_readonly_rsc_post_and_rejects_ignored_pagination(monkeypatch):
    sid = "businesswire_massive"
    data = bw_packet()
    # A repeated first page must not silently masquerade as page two.
    monkeypatch.setitem(RECIPES, sid, {**RECIPES[sid], "size": 3})
    adapter = SimpleNamespace(listing_request=AsyncMock(return_value=(response(json.dumps(data)), "direct")), poll_feed=AsyncMock(return_value=PollResult()))
    now = datetime.fromisoformat(data["news"]["items"][0]["releaseDate"].replace("Z", "+00:00")) + timedelta(minutes=1)
    result = await poll_listing(adapter, context(sid).model_copy(update={"requested_at": now}))
    assert result.failures and "requested_page_ignored" in result.failures[0].error_message
    assert adapter.listing_request.call_args.args[2]["method"] == "POST"
    assert adapter.listing_request.call_args.args[-1] == {"RSC": "1"}
    assert result.messages  # Keep the successfully parsed first page.


async def test_newsfile_is_intentional_rss_without_http_failure_or_browser():
    from doxagent.message_bus_v2.newswire_sources import NewswireAdapter

    s = source("tmx_newsfile_massive")
    assert s.kind == "api" and s.default_parameters["discovery_mode"] == "rss"
    assert endpoint_units(s) == ["TEST:WIRE:all"]
    adapter = NewswireAdapter()
    adapter.poll_feed = AsyncMock(return_value=PollResult())
    adapter.listing_request = AsyncMock(side_effect=AssertionError("Newsfile must remain RSS"))
    result = await adapter.poll_shared(context(s.source_id).model_copy(update={"acquisition_unit":"TEST:WIRE:all"}))
    assert not result.failures
    adapter.poll_feed.assert_awaited_once()
    adapter.listing_request.assert_not_called()
    await adapter.close()


def test_businesswire_id_ignores_actual_rss_and_frontend_slug_differences(tmp_path):
    from doxagent.message_bus_v2.body_results import article_key, update_hint
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.schema import RawMessageInput, utc_now
    from doxagent.message_bus_v2 import deduplication as dedup
    from scripts.ops.configure_newswire_http import preserve_businesswire_owners
    from tests.test_content_enrichment_hub import _setup

    sid = 'businesswire_massive';title='Royal Australasian College of Surgeons Targets AU$2.1 Million in Savings With HPE Private Cloud'
    rss_url='https://www.businesswire.com/news/home/20261109710510/en/Royal-Australasian-College-of-Surgeons-Targets-AU2.1-Million-in-Savings-With-HPE-Private-Cloud/'
    web_url=rss_url.replace('AU2.1','AU%242.1')
    message=RawMessageInput(url=rss_url,title=title,published_at=utc_now(),raw_payload={},metadata={'language':'en','_delivery_mode':'test','identity_evidence':{'url_kind':'article'}})
    web=message.model_copy(update={'url':web_url})
    assert article_key(message,sid)==article_key(web,sid)
    assert update_hint(message)==update_hint(web)
    assert article_key(message,'other')!=article_key(web,'other')  # Narrow source scope.
    repo,bus,_,_=_setup(tmp_path);s=source(sid);repo.save_source(s);bus.live_queue=LiveQueue(bus);live=bus.live_queue.repository
    # Seed retained pre-migration URL identities exactly as the old code did.
    from doxagent.message_bus_v2.schema import sha256_text,canonical_json
    from unittest.mock import patch
    def old_key(m,source_id):return 'test_'+sha256_text(canonical_json([dedup.article_url(m.url,'article'),'public','en']))
    with patch('doxagent.message_bus_v2.live_repository.article_key',old_key), patch('doxagent.message_bus_v2.newswire_listing.businesswire_item_key',return_value=None):
        for m in [message,web]:
            live.accept(live.prepare_input(s,[],[m],observed_at=utc_now().isoformat(),unit='WIRE:all'))
    retained=old_key(message,sid)
    with repo._connect() as db:
        db.execute("UPDATE live_articles SET state='READY',outcome='FULL',body_digest='retained-full-body' WHERE article_key=?",(retained,))
    assert preserve_businesswire_owners(repo)==1
    for m in [web,message,web]:
        live.accept(live.prepare_input(s,[],[m],observed_at=utc_now().isoformat(),unit='WIRE:listing'))
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles WHERE source_id=?',(sid,)).fetchone()[0]==2  # History retained; no new owner.
        old=db.execute('SELECT state,body_digest,input_revision FROM live_articles WHERE article_key=?',(retained,)).fetchone()
        assert tuple(old)==('READY','retained-full-body',1)
        assert db.execute('SELECT article_key FROM live_origins WHERE origin_key=?',('canonical:'+article_key(web,sid),)).fetchone()[0]==retained
    assert preserve_businesswire_owners(repo)==1
    changed=web.model_copy(update={'title':title+' UPDATED','url':web.url.replace('Private-Cloud','Private-Cloud-Updated')})
    live.accept(live.prepare_input(s,[],[changed],observed_at=utc_now().isoformat(),unit='WIRE:listing'))
    with repo._connect() as db:
        assert tuple(db.execute('SELECT state,input_revision FROM live_articles WHERE article_key=?',(retained,)).fetchone())==('FETCH_DUE',2)
