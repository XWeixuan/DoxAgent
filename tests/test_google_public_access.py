import base64
from types import SimpleNamespace

import httpx
import pytest

from doxagent.content_enrichment.google_access import GooglePublicAccess, plan
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.quality import choose_candidate, inspect_html, inspect_reader
from doxagent.content_enrichment.transport import Observation
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from tests.test_content_enrichment_pipeline import BODY, TITLE, article
from tests.test_google_publisher_documents import ExistingBrowser, StaticTransport


def test_actual_article_scopes_exclude_comments_topic_cards_and_keep_intro():
    tech = f'<h1>{TITLE}</h1><article class="newspost"><div class="text p">{BODY}</div></article>'
    tech += "<article><p>Unrelated reader comment. " + ("Noise. " * 200) + "</p></article>"
    moomoo = (
        f'<h1>{TITLE}</h1><div class="feed-detail__content-wrapper">'
        f'<div>Unrelated hot topic</div><div class="feed-detail-preset">{BODY}</div></div>'
    )
    guru = (
        f'<h1>{TITLE}</h1><div class="article-container">'
        f"<p>Five insiders sold $19 million.</p><p>{BODY}</p></div>"
    )
    for site, text in [("techpowerup.com", tech), ("moomoo.com", moomoo), ("gurufocus.com", guru)]:
        c, outcome, _ = choose_candidate(
            inspect_html(text, "https://" + site + "/news/story", TITLE), TITLE
        )
        assert c and outcome == "FULL" and " ".join(BODY.split()) in " ".join(c.text.split())
        assert "Noise" not in c.text and "Unrelated hot topic" not in c.text
        if site == "gurufocus.com":
            assert "Five insiders sold $19 million." in c.text


class Routes:
    def __init__(self):
        self.calls = []

    async def read(self, url, stage, attempts):
        self.calls.append(stage)
        if stage in {"zyte", "zyte_http"}:
            return Observation(url, article(), 200)
        return Observation(url, "", 503, "provider_http_503")

    async def http(self, url, attempts, transport):
        self.calls.append("http_public")
        return Observation(url, article(), 200)


async def test_google_reader_first_has_one_fallback_and_never_uses_native_browser():
    routes, browser = Routes(), ExistingBrowser()
    result = await ArticlePipeline(
        StaticTransport(), google_access=routes, browser=browser
    ).extract(
        MediaEnrichmentRecord(
            "s",
            "r",
            "google_news_search_rss",
            "MU",
            TITLE,
            None,
            "https://www.barchart.com/news/story",
        )
    )
    assert result.succeeded and routes.calls == ["reader", "zyte"] and not browser.calls
    assert len(result.diagnostics["stage_timings"]) == 2


async def test_native_source_uses_its_original_paths_instead_of_google_overrides():
    routes = Routes()
    result = await ArticlePipeline(StaticTransport(), google_access=routes).extract(
        MediaEnrichmentRecord(
            "s", "r", "benzinga_news", "MU", TITLE, None, "https://www.benzinga.com/news/story"
        )
    )
    assert result.succeeded and routes.calls == []


async def test_required_identity_is_not_sent_to_any_public_provider():
    routes, browser = Routes(), ExistingBrowser()

    async def resolve(url):
        return SimpleNamespace(
            body=SimpleNamespace(ref="builtin:generic@1", parameters={}, access_order=["browser"]),
            auth=SimpleNamespace(body_requirement="required", requirement="required"),
            site_id="paid",
            strategy_revision=1,
            runtime_key="paid",
        )

    result = await ArticlePipeline(
        StaticTransport(),
        google_access=routes,
        browser=browser,
        site_client=SimpleNamespace(resolve=resolve),
    ).extract(
        MediaEnrichmentRecord(
            "s",
            "r",
            "google_news_search_rss",
            "MU",
            TITLE,
            None,
            "https://paid.test/articles/story",
        )
    )
    assert result.succeeded and routes.calls == [] and len(browser.calls) == 1


async def test_provider_auth_stays_in_headers_and_zyte_returns_real_html(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "test-private-key")
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "url": "https://stocktitan.net/news/story",
                "statusCode": 200,
                "httpResponseBody": base64.b64encode(article().encode()).decode(),
            },
        )

    reader = GooglePublicAccess()
    reader._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    attempts = []
    try:
        result = await reader.read("https://stocktitan.net/news/story", "zyte", attempts)
        assert result.text == article() and result.reason is None
        assert "authorization" in seen[0].headers and "test-private-key" not in str(seen[0].url)
        assert "test-private-key" not in str([a.to_payload() for a in attempts])
    finally:
        await reader.close()


async def test_futunn_selects_reader_browser_engine(monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, text="Actual reader text")

    reader = GooglePublicAccess()
    reader._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        await reader.read("https://news.futunn.com/post/1", "reader", [])
        assert seen[0].headers["x-engine"] == "browser"
    finally:
        await reader.close()


async def test_shared_profile_busy_yields_instead_of_serializing_http_jobs():
    reader = GooglePublicAccess()
    async with reader._browser_lock:
        result = await reader._browser("https://publisher.test/story")
    assert result.reason == "site_budget_deferred"


def test_all_google_plans_have_at_most_one_fallback():
    for site in [
        "futunn.com",
        "moomoo.com",
        "gurufocus.com",
        "techpowerup.com",
        "thestreet.com",
        "stocktitan.net",
        "benzinga.com",
        "ordinary.test",
    ]:
        assert len(plan("https://" + site + "/story")) <= 2


async def test_rotating_discussion_page_is_not_downloaded_as_a_news_article():
    routes = Routes()
    result = await ArticlePipeline(StaticTransport(), google_access=routes).extract(
        MediaEnrichmentRecord(
            "s",
            "r",
            "google_news_search_rss",
            "MU",
            TITLE,
            None,
            "https://q.futunn.com/discussion/123",
        )
    )
    assert result.reason == "non_article_target" and routes.calls == []


def test_tiny_community_post_cannot_use_promos_to_become_full():
    page = (
        '<h1>Intel will go up.</h1><div class="feed-detail-preset">Intel will go up.</div>'
        f"<article>{BODY}</article>"
    )
    info = inspect_html(page, "https://www.moomoo.com/community/feed/post-123", "Intel will go up.")
    assert choose_candidate(info, "Intel will go up.")[2] == "short_social_post"


def test_live_guru_score_is_not_misread_as_insider_transaction_count():
    page = (
        f'<h1>{TITLE}</h1><div class="article-container"><p>Nebius Group '
        '<a class="el-popover__reference">NBIS<span class="span-red">-10.38%</span>'
        '<span class="gf-score-badge-wrapper">55</span></a> insiders sold $19 million.</p>'
        f"<p>{BODY}</p></div>"
    )
    c, outcome, _ = choose_candidate(
        inspect_html(page, "https://gurufocus.com/news/story", TITLE), TITLE
    )
    assert c and outcome == "FULL" and "$19 million" in c.text
    assert "55" not in c.text and "-10.38%" not in c.text


def test_stock_reader_keeps_intro_and_body_after_inline_recommendation():
    url = "https://stocktwits.com/news-articles/markets/equity/example/id123"
    text = (
        f"Title: {TITLE}\nURL Source: {url}\nMarkdown Content:\n{BODY}\n"
        "## Micron memory earnings\nRead Next[Unrelated recommendation](https://stocktwits.com/other)\n"
        "The financing agreement commits $250 million and 18% additional capacity.\n"
        "## Latest News\nUNRELATED FOOTER STORY\n" + BODY
    )
    c, outcome, _ = choose_candidate(inspect_reader(text, url, TITLE), TITLE)
    assert c and outcome == "FULL" and BODY.split("\n")[0] in c.text
    assert "$250 million" in c.text and "18%" in c.text
    assert "UNRELATED FOOTER STORY" not in c.text and "Unrelated recommendation" not in c.text


def test_scoped_street_reader_uses_document_header_instead_of_company_bio_heading():
    url = "https://www.streetinsider.com/Corporate+News/example/123.html"
    text = (
        f"Title: {TITLE}\nURL Source: {url}\nMarkdown Content:\n{BODY}\n"
        "## About Micron Memory\nCompany biography text.\n"
        "You May Also Be Interested In\nUNRELATED FOOTER"
    )
    c, outcome, _ = choose_candidate(inspect_reader(text, url, TITLE), TITLE)
    assert c and outcome == "FULL" and BODY.split("\n")[0] in c.text
    assert "UNRELATED FOOTER" not in c.text


def test_remote_provider_validation_does_not_depend_on_local_publisher_dns(monkeypatch):
    import socket

    from doxagent.content_enrichment.google_access import provider_target

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("DNS unavailable")),
    )
    provider_target("https://www.stocktitan.net/news/filing.html")
    for url in [
        "http://127.0.0.1/article",
        "http://169.254.169.254/latest",
        "http://localhost/article",
        "https://private.internal/article",
        "https://user:password@example.com/story",
        "file:///etc/passwd",
    ]:
        with pytest.raises(ValueError):
            provider_target(url)


@pytest.mark.parametrize("inside", [False, True])
def test_barchart_expansion_is_limited_to_actual_article(inside):
    from doxagent.content_enrichment.quality import choose_candidate, inspect_html

    title = "Rocket Lab announces a new launch contract"
    body = "The company secured a $250 million contract. Deliveries begin in 2027. " * 20
    button = "<button>Continue Reading</button>"
    page = (
        f"<html><h1>{title}</h1><div class='article-content'><p>{body}</p>"
        + (button if inside else "")
        + "<h2>Where Should You Invest $1,000 Right Now?</h2><p>Advertising offer.</p></div>"
        + ("" if inside else button)
        + "</html>"
    )
    candidate, outcome, reason = choose_candidate(
        inspect_html(page, "https://www.barchart.com/story/news/12345/new-contract", title), title
    )
    if inside:
        assert outcome == "PARTIAL" and reason == "expand_required"
    else:
        assert outcome == "FULL"
        assert "Advertising offer" not in candidate.text
