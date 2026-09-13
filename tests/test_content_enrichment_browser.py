from __future__ import annotations

from unittest.mock import AsyncMock

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


async def test_protected_content_never_uses_public_reader_and_validates_identity():
    url = Page.url
    session = Session({url: response(url, article("Sign in to read"))})
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
