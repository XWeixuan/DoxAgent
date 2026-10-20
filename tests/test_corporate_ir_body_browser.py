import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.corporate_ir.body import IrBodyReader, exact_publication
from doxagent.message_bus_v2.corporate_ir.browser_pool import PublicBrowserPool
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.http import Capture
from doxagent.message_bus_v2.schema import RawMessageInput, utc_now
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord


def article():
    return "".join(
        f"<p>Announcement section {i} explains a completed company investment. "
        "The project expands production capability for customers across the region. "
        "The company provided its implementation schedule and expected commercial impact.</p>"
        for i in range(8)
    )


def job_record(index=11, raw=None):
    spec = initial_endpoints()[index]
    url = "https://" + spec.allowed_article_hosts[0] + "/press/announcement"
    message = RawMessageInput(
        title="Company expands production capability",
        url=url,
        published_at=utc_now(),
        raw_payload={"article_url": url, "feed_html": raw or ""},
        metadata={
            "ir_endpoint_spec": spec.model_dump(mode="json"),
            "ir_body_field": "content:encoded",
        },
    )
    job = SimpleNamespace(message=message, binding=SimpleNamespace(ticker=spec.owner_ticker))
    record = MediaEnrichmentRecord(
        standard_message_id="test",
        raw_message_id="test",
        source_id="corporate_ir_press_releases",
        ticker=spec.owner_ticker,
        title=message.title,
        body="",
        url=url,
    )
    return job, record


@pytest.mark.asyncio
async def test_full_native_feed_is_validated_without_site_or_detail_fetch():
    http = SimpleNamespace(
        fetch=AsyncMock(side_effect=AssertionError("native full must not refetch"))
    )
    reader = IrBodyReader(http)
    job, record = job_record(raw=article())
    result = await reader.extract(job, record)
    assert result.succeeded and result.diagnostics["outcome"] == "FULL"
    http.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_teaser_goes_to_public_detail_and_denial_is_not_full():
    job, record = job_record(raw="<p>Short announcement teaser... <a>Read more</a></p>")
    http = SimpleNamespace(
        fetch=AsyncMock(return_value=Capture(403, record.url, "Access Denied", {}, 13))
    )
    result = await IrBodyReader(http).extract(job, record)
    assert not result.succeeded and result.reason == "ir_http_403"
    http.fetch.assert_awaited_once()


def test_bad_dates_and_template_midnight_never_gain_exact_precision():
    raw = (
        '<script type="application/ld+json">'
        '{"@type":"NewsArticle","datePublished":"2026-2-30TAD::Z"}</script>'
    )
    assert exact_publication(raw, initial_endpoints()[9]) is None
    midnight = (
        '<script type="application/ld+json">'
        '{"@type":"NewsArticle","datePublished":"2026-10-01T00:00:00Z"}</script>'
    )
    assert exact_publication(midnight, initial_endpoints()[6]) is None
    valid = (
        '<script type="application/ld+json">'
        '{"@type":"NewsArticle","datePublished":"2026-10-14T16:00:00-04:00"}</script>'
    )
    assert exact_publication(valid, initial_endpoints()[7]).endswith("-04:00")


def test_ceg_release_scope_excludes_alert_form():
    from doxagent.message_bus_v2.corporate_ir.body import inspect_article

    raw = (
        '<html><div class="ce-container aem-GridColumn--default--10">'
        "<h1>Company expands production capability</h1>"
        + article()
        + "</div><p>Press Release Alerts subscribe here.</p></html>"
    )
    candidate, quality, _ = inspect_article(
        raw,
        "https://www.constellationenergy.com/news/release.html",
        "Company expands production capability",
    )
    assert quality == "FULL" and "Press Release Alerts" not in candidate.text


@pytest.mark.asyncio
async def test_browser_context_failure_keeps_sibling_and_http_independent():
    contexts = []

    async def new_context():
        index = len(contexts)
        page = SimpleNamespace(
            url="https://www.intc.com/news", content=AsyncMock(return_value="<h1>ready</h1>")
        )

        async def goto(*args, **kwargs):
            await asyncio.sleep(0.01)
            if index == 0:
                raise TimeoutError("one page stalled")
            return SimpleNamespace(status=200)

        page.goto = goto
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        contexts.append(context)
        return context

    browser = SimpleNamespace(
        is_connected=lambda: True,
        new_context=new_context,
        close=AsyncMock(),
        new_browser_cdp_session=AsyncMock(
            return_value=SimpleNamespace(send=AsyncMock(), detach=AsyncMock())
        ),
    )
    pool = PublicBrowserPool()
    assert pool.browser is None and pool.playwright is None  # zero eager processes
    pool.browser = browser
    try:
        spec = initial_endpoints()[0].model_copy(update={"method": "browser"})
        results = await asyncio.gather(pool.fetch(spec), pool.fetch(spec), return_exceptions=True)
        assert isinstance(results[0], TimeoutError) and results[1][0] == 200
        assert len(contexts) == 2 and contexts[0] is not contexts[1]
        assert all(c.close.await_count == 1 for c in contexts)
        browser.close.assert_not_awaited()
        assert pool.active == 0 and pool.slots._value == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_driver_failure_retires_only_owned_browser():
    page = SimpleNamespace(goto=AsyncMock(side_effect=ConnectionError("driver closed pipe")))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    browser = SimpleNamespace(
        is_connected=lambda: True,
        new_context=AsyncMock(return_value=context),
        new_browser_cdp_session=AsyncMock(side_effect=ConnectionError("driver dead")),
        close=AsyncMock(),
    )
    pool = PublicBrowserPool()
    pool.browser = browser
    try:
        with pytest.raises(ConnectionError):
            await pool.fetch(initial_endpoints()[0])
        browser.close.assert_awaited_once()
        assert pool.browser is None and pool.active == 0
    finally:
        await pool.close()
