from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from doxagent.content_enrichment.browser import PublisherBrowser
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.transport import Observation, PublicTransport
from doxagent.monitoring.media_enrichment import DomainFetchController, MediaEnrichmentRecord
from tests.test_content_enrichment_pipeline import BODY, TITLE, Session, article, response


class Page:
    url = "https://seekingalpha.com/article/123-memory-production"

    def __init__(self, text):
        self.text = text
        self.closed = False
        self.navigations = 0

    async def route(self, *args):
        pass

    async def goto(self, *args, **kwargs):
        self.navigations += 1
        return type("Response", (), {"status": 200})()

    def locator(self, *args):
        return self

    async def wait_for(self, **kwargs):
        pass

    async def inner_text(self):
        return self.text

    async def content(self):
        return article(self.text)

    async def close(self):
        self.closed = True


def configured(monkeypatch, tmp_path, text):
    monkeypatch.setattr("doxagent.content_enrichment.browser.public_url", AsyncMock())
    browser = PublisherBrowser(identity_dir=tmp_path, authenticated_hosts={"seekingalpha.com"})
    page = Page(text)
    context = AsyncMock()
    context.new_page.return_value = page
    resp = AsyncMock()
    resp.status = 200
    resp.text.return_value = "<html><body>Loading article...</body></html>"
    context.request.get.return_value = resp
    monkeypatch.setattr(browser, "_context", AsyncMock(return_value=context))
    return browser, page, context


async def test_incomplete_cookie_http_falls_through_to_render(monkeypatch, tmp_path):
    browser, page, context = configured(monkeypatch, tmp_path, BODY)
    result, state = await browser.read(page.url)
    assert not result.reason and BODY.splitlines()[0] in result.text
    assert page.navigations == 1 and page.closed
    assert state["auth_state"] == "UNVERIFIED"
    context.request.get.assert_awaited_once()


async def test_session_recovery_rechecks_article_in_same_job(monkeypatch, tmp_path):
    browser, page, _ = configured(monkeypatch, tmp_path, "Sign in to read")

    async def recover(*args):
        page.text = BODY
        return "unverified"

    recovery = AsyncMock(side_effect=recover)
    monkeypatch.setattr("doxagent.content_enrichment.browser.recover_seeking_alpha", recovery)
    result, _ = await browser.read(page.url)
    assert not result.reason and page.navigations == 2
    recovery.assert_awaited_once()


async def test_challenge_stops_recovery_and_later_jobs(monkeypatch, tmp_path):
    browser, page, context = configured(monkeypatch, tmp_path, "Please prove you are not a robot")
    recovery = AsyncMock()
    monkeypatch.setattr("doxagent.content_enrichment.browser.recover_seeking_alpha", recovery)
    result, state = await browser.read(page.url)
    assert result.reason == "challenge_required" and state["auth_state"] == "REAUTH_REQUIRED"
    result, _ = await browser.read(page.url)
    assert result.reason == "reauth_required"
    recovery.assert_not_awaited()
    context.new_page.assert_awaited_once()


async def test_subscription_is_not_assumed_to_be_expired_login(monkeypatch, tmp_path):
    browser, page, _ = configured(monkeypatch, tmp_path, "Subscribe to read this article")
    result, state = await browser.read(page.url)
    assert result.reason == "subscription_required"
    assert state["auth_state"] == "UNVERIFIED"


async def test_browser_ignores_sidebar_subscription_ad(monkeypatch, tmp_path):
    browser, page, _ = configured(monkeypatch, tmp_path, BODY + '\nSubscribe to read premium news')
    page.content = AsyncMock(
        return_value=article() + '<aside>Subscribe to read premium news</aside>'
    )
    result, state = await browser.read(page.url)
    assert not result.reason and state['auth_state'] == 'UNVERIFIED'


async def test_browser_waits_for_article_after_long_js_bootstrap(monkeypatch, tmp_path):
    browser, page, _ = configured(monkeypatch, tmp_path, 'Loading navigation. ' * 30)
    page.content = AsyncMock(side_effect=[
        '<title>JavaScript is disabled</title><script>awsWafCookieDomainList=[];gokuProps={};'
        '</script>', article(), article(), article(),
    ])
    page.wait_for_function = AsyncMock()
    result, _ = await browser.read(page.url)
    assert not result.reason
    page.wait_for_function.assert_awaited_once()


async def test_cdp_disconnect_leaves_operator_browser_and_context_open():
    browser = PublisherBrowser(cdp_url="http://operator-browser:9223")
    context = AsyncMock()
    browser._contexts = {"www.reuters.com": context}
    chrome = AsyncMock()
    browser._browser = chrome
    playwright = AsyncMock()
    browser._playwright = playwright
    await browser.close()
    context.close.assert_not_awaited()
    chrome.close.assert_not_awaited()
    playwright.stop.assert_awaited_once()


async def test_cdp_reconnect_discards_contexts_from_disconnected_operator_browser():
    browser = PublisherBrowser(cdp_url="http://browser:9223")
    old = Mock()
    old.is_connected.return_value = False
    browser._browser = old
    browser._contexts = {"example.com": Mock()}
    context = Mock()
    connected = Mock(contexts=[context])
    browser._playwright = AsyncMock()
    browser._playwright.chromium.connect_over_cdp.return_value = connected
    assert await browser._context("example.com") is context
    browser._playwright.chromium.connect_over_cdp.assert_awaited_once()
    old.close.assert_not_called()


@pytest.mark.parametrize("failure", [
    "browser_unavailable", "browser_runtime_missing", "render_timeout",
])
async def test_public_reader_still_runs_when_optional_browser_runtime_fails(failure):
    url = "https://www.benzinga.com/news/free-article"
    reader = "https://r.jina.ai/" + url
    session = Session({
        url: response(url, '<title>Access denied</title>', status=403),
        reader: response(reader, f'Title: {TITLE}\nURL Source: {url}\nMarkdown Content:\n\n{BODY}'),
    })
    browser = AsyncMock()
    browser.read.return_value = (Observation(url, "", 0, failure), {})
    result = await ArticlePipeline(
        PublicTransport(session, DomainFetchController(), validate_urls=False), browser=browser
    ).extract(MediaEnrichmentRecord("id", "id", "source", "MU", TITLE, "", url))
    assert result.succeeded and session.calls == [url, reader]
    assert result.diagnostics["browser_failure_reason"] == failure


@pytest.mark.parametrize("status", [200, 401])
async def test_protected_content_never_uses_public_reader_and_validates_identity(status):
    url = Page.url
    session = Session({url: response(
        url, article("Sign in to read") if status == 200 else '<title>Unauthorized</title>',
        status=status,
    )})
    browser = AsyncMock()
    browser.read.return_value = (
        Observation(
            url,
            article()
            .replace("</h1>", '</h1><div data-test-id="content-container">')
            .replace("</article>", "</div></article>"),
            200,
        ),
        {"credential_ref": "seekingalpha.com"},
    )
    # Synchronous state persistence is part of the publisher profile contract.
    from unittest.mock import Mock

    browser.verified = Mock(return_value={"auth_state": "VALID", "session_revision": 1})
    result = await ArticlePipeline(
        PublicTransport(session, DomainFetchController(), validate_urls=False), browser=browser
    ).extract(MediaEnrichmentRecord("id", "id", "source", "MU", TITLE, "", url))
    assert result.succeeded and session.calls == [url]
    assert result.diagnostics["session_revision"] == 1


async def test_cdp_identity_restores_revision_and_requires_article_verification(tmp_path):
    import json
    host = "www.reuters.com"
    directory = tmp_path / host
    directory.mkdir()
    (directory / "status.json").write_text(json.dumps({
        "auth_state": "VALID", "session_revision": 7,
    }))
    browser = PublisherBrowser(cdp_url="http://browser:9223", identity_dir=tmp_path,
                               authenticated_hosts={host})
    browser._playwright = AsyncMock()
    browser._playwright.chromium.connect_over_cdp.return_value = Mock(contexts=[Mock()])
    await browser._context(host)
    assert browser._states[host]["auth_state"] == "UNVERIFIED"
    assert browser.verified(host)["session_revision"] == 8


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a",
        "http://169.254.169.254/latest/meta-data",
        "http://198.18.0.1/a",
        "file:///etc/passwd",
    ],
)
async def test_private_urls_rejected_even_with_proxy_dns_opt_in(url):
    from doxagent.content_enrichment.transport import public_url

    with pytest.raises(ValueError):
        await public_url(url, trusted_proxy_dns=True)
