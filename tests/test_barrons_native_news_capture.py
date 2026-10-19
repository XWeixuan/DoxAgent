from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.barrons_sources import (
    BarronsNewsFeedError,
    capture_barrons_ticker_news,
    render_news_feeds,
)
from doxagent.message_bus_v2.industry_sources import parse_barrons_ticker_listing
from doxagent.site_strategy.schema import FailureCategory
from doxagent.site_strategy.service import classify_exception


def story(url="https://www.barrons.com/articles/micron-chip-report-123", **changes):
    return {
        "$type": "News.StoryCard",
        "url": url,
        "headline": "Micron & Intel discuss <advanced> memory chips",
        "summary": "Memory demand remains strong.",
        "timestampUtc": {"value": "2026-10-19T18:20:00Z"},
        **changes,
    }


def feeds(main=None, other=None):
    return [
        {
            "status": 200,
            "text": json.dumps(
                {
                    "blocks": [
                        {
                            "$type": "News.MoreHeadlinesCard",
                            "contentFeed": channel,
                            "blocks": items,
                        }
                    ]
                }
            ),
        }
        for channel, items in zip(
            ("Barrons", "BarronsDowJonesNetwork"),
            ([story()] if main is None else main, [] if other is None else other),
            strict=True,
        )
    ]


def parse(html):
    return parse_barrons_ticker_listing(html, ticker="MU", now=datetime(2026, 10, 20, tzinfo=UTC))


def test_both_tabs_use_exact_native_dates_without_hydrated_ui_labels():
    rows = parse(
        render_news_feeds(
            feeds(
                other=[
                    story("https://www.wsj.com/articles/semiconductor-report-456"),
                    story("https://www.marketwatch.com/story/memory-demand-789"),
                    story("https://www.investors.com/news/ibd-excluded-123"),
                    story("https://unrelated.example/articles/not-dow-jones-123"),
                ]
            )
        )
    )
    assert {r.publisher_name for r in rows} == {
        "Barron's",
        "The Wall Street Journal",
        "MarketWatch",
    }
    assert {r.publication_time_basis for r in rows} == {"EXACT"}
    assert {r.published_at.isoformat() for r in rows} == {"2026-10-19T18:20:00+00:00"}
    assert rows[0].title == "Micron & Intel discuss <advanced> memory chips"
    assert {r.raw_payload["section"] for r in rows} == {"barrons", "other_dow_jones"}


def test_only_explicitly_complete_empty_feeds_are_valid_empty():
    assert parse(render_news_feeds(feeds(main=[]))) == []
    with pytest.raises(RuntimeError, match="empty_or_changed"):
        parse('<main data-barrons-feed="complete"><section data-dj-section="barrons"/></main>')


@pytest.mark.parametrize("stamp", [None, {"value": "yesterday"}, {"value": "2026-10-19T18:20:00"}])
def test_unknown_dates_are_never_replaced_with_first_seen(stamp):
    with pytest.raises(BarronsNewsFeedError):
        render_news_feeds(feeds(main=[story(timestampUtc=stamp)]))


@pytest.mark.parametrize(
    "status,category",
    [
        (401, FailureCategory.AUTH_OR_ACCESS_UNKNOWN),
        (403, FailureCategory.AUTH_OR_ACCESS_UNKNOWN),
        (429, FailureCategory.ACCESS_RATE_LIMIT),
        (503, FailureCategory.TRANSIENT_TRANSPORT),
    ],
)
def test_second_tab_failure_is_not_silently_certified_as_complete(status, category):
    values = feeds()
    values[1] = {"status": status, "text": "Access denied"}
    with pytest.raises(BarronsNewsFeedError) as caught:
        render_news_feeds(values)
    assert classify_exception(caught.value) == (category, f"barrons_news_feed_http_{status}")


@pytest.mark.parametrize("text", ["<html>not JSON</html>", '{"blocks":[]}', '{"blocks":[{}]}'])
def test_malformed_api_is_not_treated_as_empty_news(text):
    values = feeds()
    values[1] = {"status": 200, "text": text}
    with pytest.raises(BarronsNewsFeedError):
        render_news_feeds(values)


@pytest.mark.asyncio
async def test_capture_uses_same_origin_browser_fetch_no_locator_or_interception():
    page = SimpleNamespace(evaluate=AsyncMock(return_value=feeds()))
    html = await capture_barrons_ticker_news(page, "https://www.barrons.com/market-data/stocks/MU")
    assert len(parse(html)) == 1
    script, paths = page.evaluate.call_args.args
    assert "credentials:'include'" in script and "AbortController" in script
    assert all(p.startswith("/market-data/api/news?") for p in paths)
    assert "channel=Barrons&" in paths[0]
    assert "channel=BarronsDowJonesNetwork&" in paths[1]
    assert all("chartingSymbol=stock%2F%2F%2Fmu&" in p for p in paths)


@pytest.mark.asyncio
async def test_hung_page_script_is_bounded_without_another_request_path():
    async def hang(*args):
        await asyncio.Event().wait()

    page = SimpleNamespace(evaluate=AsyncMock(side_effect=hang))
    with pytest.raises(TimeoutError):
        await capture_barrons_ticker_news(
            page, "https://www.barrons.com/market-data/stocks/mu", timeout_seconds=0.01
        )
    page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/market-data/stocks/mu",
        "https://www.barrons.com/articles/anything",
        "https://www.barrons.com/market-data/stocks/mu/../../evil",
    ],
)
async def test_feed_paths_cannot_be_supplied_by_callers(url):
    page = SimpleNamespace(evaluate=AsyncMock())
    with pytest.raises(BarronsNewsFeedError, match="invalid_ticker_url"):
        await capture_barrons_ticker_news(page, url)
    page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["CRAWLER", "BODY"])
async def test_runtime_recipe_checks_document_denial_before_native_feed(
    tmp_path, monkeypatch, purpose
):
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.schema import AccessRequest, BrowserRuntimeKind
    from doxagent.site_strategy.seeds import bootstrap_seed
    from doxagent.site_strategy.service import SiteStrategyService

    repository = SiteStrategyRepository(tmp_path / "site.db")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    url = (
        "https://www.barrons.com/market-data/stocks/mu"
        if purpose == "CRAWLER"
        else "https://www.barrons.com/articles/micron-chip-report-123"
    )
    article = (
        "<article><h1>Micron memory production results</h1>"
        + "".join(
            "<p>"
            + ("Micron reported memory demand and semiconductor capacity growth. " * 12)
            + "</p>"
            for _ in range(5)
        )
        + "</article>"
    )
    page = SimpleNamespace(
        url=url,
        goto=AsyncMock(),
        all_headers=AsyncMock(return_value={}),
        status=200,
        content=AsyncMock(
            return_value=(
                "<html><title>Micron Stock Overview</title></html>"
                if purpose == "CRAWLER"
                else article
            )
        ),
        evaluate=AsyncMock(return_value=feeds()),
    )
    page.goto.return_value = page
    provenance = SimpleNamespace(instance_id="same-native-chrome", generation=1)
    lease = SimpleNamespace(browser_cdp=object(), provenance=provenance)

    class Lease:
        browser_cdp = lease.browser_cdp
        provenance = lease.provenance

        async def __aenter__(self):
            return page

        async def __aexit__(self, *args):
            pass

    gate = SimpleNamespace(
        start=AsyncMock(), close=AsyncMock(), blocked=[], root_target_id="our-target"
    )
    monkeypatch.setattr("doxagent.site_strategy.runtime._DocumentNavigationGate", lambda *a: gate)
    monkeypatch.setattr("doxagent.site_strategy.runtime.public_url", AsyncMock())
    monkeypatch.setattr(service.runtime.browser_runtimes, "page", AsyncMock(return_value=Lease()))
    retained = AsyncMock(return_value="preserved-challenge")
    monkeypatch.setattr(service.runtime, "_retain_challenge", retained)
    identity = repository.get_identity("barrons-1").model_copy(
        update={"runtime_kind": BrowserRuntimeKind.EXTERNAL_CHROME}
    )
    request = AccessRequest(
        operation_id="test",
        url=url,
        purpose=purpose,
        mode="BROWSER",
        recipe_ref="builtin:barrons_ticker@1" if purpose == "CRAWLER" else None,
    )
    try:
        result = await service.runtime._browser(
            request, service.resolve(url), identity, repository.get_egress(identity.egress_id)
        )
        if purpose == "CRAWLER":
            assert len(parse(result.body)) == 1
            page.evaluate.assert_awaited_once()
        else:
            from doxagent.site_strategy.verification import validate_capture

            assert validate_capture(result, service.resolve(url)).body in {"FULL", "SHORT_FULL"}
            page.evaluate.assert_not_awaited()
        assert page.goto.call_args.kwargs["wait_until"] == "commit"
        retained.assert_not_awaited()
        page.evaluate.reset_mock()
        page.content.return_value = "<title>Access is temporarily restricted</title>"
        result = await service.runtime._browser(
            request, service.resolve(url), identity, repository.get_egress(identity.egress_id)
        )
        assert result.challenge_token == "preserved-challenge"
        page.evaluate.assert_not_awaited()
        retained.assert_awaited_once()
    finally:
        repository.close()


def test_login_home_and_crawler_urls_cannot_be_subscription_body_proof():
    from doxagent.site_strategy.verification import body_verification_url

    scope = SimpleNamespace(
        verification_plan={
            "crawler": {
                "url": "https://www.barrons.com/market-data/stocks/MU",
            }
        }
    )
    article = "https://www.barrons.com/articles/real-subscription-proof"
    assert (
        body_verification_url(
            scope,
            "https://www.barrons.com/login",
            "https://www.barrons.com/",
            "https://www.barrons.com/market-data/stocks/MU?mod=search",
            article,
            excluded_urls=("https://www.barrons.com/login",),
        )
        == article
    )


@pytest.mark.asyncio
async def test_recovery_never_attaches_to_closed_native_orphan_cached_by_playwright():
    from doxagent.site_strategy.external_runtime import ExternalChromeRuntime

    # Native Target.getTargetInfo is authoritative even before Playwright gets
    # PageClosed. Calling new_cdp_session(stale) would never finish in the bad case.
    stale = SimpleNamespace(
        url="https://www.barrons.com/market-data/stocks/mu", is_closed=lambda: False
    )
    human = SimpleNamespace(url="https://www.barrons.com/", is_closed=lambda: False)
    session = SimpleNamespace(
        send=AsyncMock(return_value={"targetInfo": {"targetId": "human"}}), detach=AsyncMock()
    )

    async def attach(page):
        assert page is human, "must not RPC an orphan pending PageClosed delivery"
        return session

    identity = SimpleNamespace(identity_id="dowjones-main", revision=1)
    entry = SimpleNamespace(
        context=SimpleNamespace(
            pages=[stale, human], new_cdp_session=AsyncMock(side_effect=attach)
        ),
        cdp=SimpleNamespace(
            send=AsyncMock(
                return_value={
                    "targetInfo": {"targetId": "human", "url": human.url},
                }
            )
        ),
        identity=identity,
        instance=SimpleNamespace(instance_id="chrome", generation=1),
        active_pages=0,
        last_used=0,
    )
    runtime = ExternalChromeRuntime(SimpleNamespace())
    runtime._entry = AsyncMock(return_value=entry)
    lease = await runtime.existing_page(identity, SimpleNamespace(), target_id="human")
    assert lease.page is human
    entry.context.new_cdp_session.assert_awaited_once_with(human)
    await lease.abandon()


@pytest.mark.asyncio
async def test_native_login_presence_does_not_start_or_replace_chrome():
    from doxagent.site_strategy.maintenance_document import original_target_exists

    identity = SimpleNamespace(identity_id="dowjones-main")
    session = SimpleNamespace(target_id="original", browser_instance_id="old")
    target = SimpleNamespace(
        identity_id="dowjones-main",
        target_id="original",
        browser_instance_id="old",
        owner_kind="LOGIN",
        state="LIVE",
    )
    client = SimpleNamespace(
        status=AsyncMock(
            return_value={
                "running": True,
                "instance_id": "new",
            }
        ),
        ensure=AsyncMock(),
        stop=AsyncMock(),
    )
    assert await original_target_exists(client, identity, session, target) is False
    client.ensure.assert_not_awaited()
    client.stop.assert_not_awaited()
    client.status.return_value = {"running": False, "known_missing": False}
    with pytest.raises(RuntimeError, match="unverified"):
        await original_target_exists(client, identity, session, target)
