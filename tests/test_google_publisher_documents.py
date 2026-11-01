from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.publisher_documents import publisher_host, same_article
from doxagent.content_enrichment.quality import choose_candidate, inspect_html, inspect_reader
from doxagent.content_enrichment.transport import DEADLINE, Observation, remaining
from doxagent.monitoring.media_enrichment import DomainFetchController, MediaEnrichmentRecord
from tests.test_content_enrichment_pipeline import BODY, PARAGRAPHS, TITLE, article

ARC_URL = "https://www.chosun.com/english/industry-en/2026/10/19/STORY/"


def arc_page(**changes):
    payload = {
        "type": "story",
        "canonical_url": ARC_URL,
        "headlines": {"basic": TITLE},
        "content_elements": [{"type": "text", "content": p} for p in PARAGRAPHS]
        + [
            {
                "type": "table",
                "header": [{"content": "Investment"}],
                "rows": [[{"content": "$250 million"}, {"content": "18%"}]],
            }
        ],
        "related_content": {"content_elements": [{"type": "text", "content": "OTHER ARTICLE"}]},
    }
    payload.update(changes)
    return "<script>window.Fusion={};Fusion.globalContent=" + json.dumps(payload) + ";</script>"


def test_arc_public_story_is_bound_to_requested_article_and_keeps_tables():
    info = inspect_html(arc_page(), ARC_URL, TITLE)
    candidate, outcome, _ = choose_candidate(info, TITLE)
    assert outcome == "FULL" and candidate and candidate.method == "arc_public_story"
    assert all(p in candidate.text for p in PARAGRAPHS)
    assert "$250 million | 18%" in candidate.text
    assert "OTHER ARTICLE" not in candidate.text


@pytest.mark.parametrize(
    "changes",
    [
        {"canonical_url": "https://www.chosun.com/english/other/"},
        {"headlines": {"basic": "Unrelated weather headline"}},
        {"headlines": None},
        {"type": "collection"},
        {"isAccessibleForFree": False},
        {"content_restrictions": {"content_code": "premium"}},
    ],
)
def test_arc_does_not_accept_related_or_restricted_story(changes):
    candidate, _, _ = choose_candidate(inspect_html(arc_page(**changes), ARC_URL, TITLE), TITLE)
    assert candidate is None


def test_complete_embedded_form4_does_not_force_unnecessary_sec_hop():
    url = "https://www.stocktitan.net/sec-filings/MU/form-4-micron-example.html"
    body = (
        "FORM 4 UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        + BODY
        + "\nExercise price $1.09; shares sold 500,000.\nSignature: Reporting Person."
    )
    page = (
        f'<article><h1>{TITLE}</h1><div id="sec-filing-content">'
        f'<div class="filing-content-inner"><p>{body}</p></div></div>'
        '<a href="https://www.sec.gov/Archives/example.xml">View Original</a></article>'
    )
    info = inspect_html(page, url, TITLE)
    candidate, outcome, _ = choose_candidate(info, TITLE)
    assert outcome == "FULL" and candidate and candidate.method == "stocktitan_embedded_form4"
    assert "$1.09" in candidate.text and "500,000" in candidate.text
    assert info.publisher_links == []


def test_partial_form4_still_requires_original_document():
    page = (
        f'<article><h1>{TITLE}</h1><div id="sec-filing-content">'
        '<div class="filing-content-inner"><p>Only a Form 4 preview.</p></div></div>'
        '<a href="https://www.sec.gov/Archives/example.xml">View Original</a></article>'
    )
    url = "https://www.stocktitan.net/sec-filings/MU/form-4-micron-example.html"
    info = inspect_html(page, url, TITLE)
    assert choose_candidate(info, TITLE)[0] is None
    assert info.publisher_links == ["https://www.sec.gov/Archives/example.xml"]


def test_paid_barrons_preview_plus_recommendations_is_not_full():
    page = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "NewsArticle", "isAccessibleForFree": False})
        + "</script>"
        f'<article id="story"><h1>{TITLE}</h1><div class="crawler">'
        f'<section><p data-type="paragraph">{PARAGRAPHS[0]}</p></section></div>'
        "<section><h2>What to Read Next</h2>" + article(BODY) + "</section></article>"
    )
    info = inspect_html(page, "https://www.barrons.com/articles/story", TITLE)
    assert choose_candidate(info, TITLE) == (None, "UNAVAILABLE", "subscription_required")


def test_full_authenticated_barrons_dom_preserves_all_actual_paragraphs():
    page = (
        f'<article id="story"><h1>{TITLE}</h1><div class="crawler">'
        '<p data-type="paragraph">Crawler preview.</p></div><section>'
        + "".join(f'<p data-type="paragraph">{p}</p>' for p in PARAGRAPHS)
        + "</section><aside>Other recommended stock stories.</aside></article>"
    )
    candidate, outcome, _ = choose_candidate(
        inspect_html(page, "https://www.barrons.com/articles/story", TITLE), TITLE
    )
    assert outcome == "FULL" and candidate and candidate.text == BODY


def test_public_free_barrons_crawler_body_is_not_rejected_as_paid():
    page = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "NewsArticle", "isAccessibleForFree": True})
        + "</script>"
        f'<article id="story"><h1>{TITLE}</h1><div class="crawler">'
        + "".join(f'<p data-type="paragraph">{p}</p>' for p in PARAGRAPHS)
        + "</div></article>"
    )
    candidate, outcome, _ = choose_candidate(
        inspect_html(page, "https://www.barrons.com/articles/story", TITLE), TITLE
    )
    assert outcome == "FULL" and candidate and candidate.text == BODY


def test_ibd_video_playlist_cannot_be_accepted_as_transcript():
    url = "https://www.investors.com/ibd-videos/videos/news"
    page = f"<h1>{TITLE}</h1><div>Read Transcript</div><div>Featured Videos {BODY}</div>"
    assert choose_candidate(inspect_html(page, url, TITLE), TITLE)[2] == "unsupported_media"
    reader = (
        f"Title: {TITLE}\nURL Source: {url}\nMarkdown Content:\n# {TITLE}\nFeatured Videos\n{BODY}"
    )
    assert choose_candidate(inspect_reader(reader, url, TITLE), TITLE)[2] == "unsupported_media"


def test_ibd_truncated_preview_requires_subscription_but_normal_ellipsis_does_not():
    url = "https://www.investors.com/news/technology/story"
    preview = (
        f"<article><h1>{TITLE}</h1><p>{PARAGRAPHS[0]}…</p><p>Copyright ©2026 IBD.</p></article>"
    )
    assert choose_candidate(inspect_html(preview, url, TITLE), TITLE)[2] == "subscription_required"
    full = article(BODY.replace(" ", " … ", 1))
    assert choose_candidate(inspect_html(full, url, TITLE), TITLE)[1] == "FULL"


@pytest.mark.parametrize("heading", ["#", "##", "######", "setext"])
def test_reader_accepts_verified_headings_at_different_levels(heading):
    url = "https://www.tikr.com/blog/news"
    title_line = TITLE + "\n=======" if heading == "setext" else heading + " " + TITLE
    text = f"Title: {TITLE}\nURL Source: {url}\nMarkdown Content:\n{title_line}\n{BODY}"
    candidate, outcome, _ = choose_candidate(inspect_reader(text, url, TITLE), TITLE)
    assert outcome == "FULL" and candidate and candidate.text == BODY


def test_reader_identity_handles_publisher_url_encoding_but_not_different_articles():
    encoded = "https://www.streetinsider.com/Analyst/Company+%28MU%29/12.html"
    decoded = "https://www.streetinsider.com/Analyst/Company+(MU)/12.html"
    assert same_article(encoded, decoded)
    assert not same_article(encoded, decoded.replace("12.html", "13.html"))
    assert not same_article("https://example.com/article?id=1", "https://example.com/article?id=2")
    text = f"Title: {TITLE}\nURL Source: {decoded}\nMarkdown Content:\n# {TITLE}\n{BODY}"
    assert choose_candidate(inspect_reader(text, encoded, TITLE), TITLE)[1] == "FULL"
    assert (
        choose_candidate(inspect_reader(text, encoded.replace("12.html", "13.html"), TITLE), TITLE)[
            2
        ]
        == "publisher_identity_mismatch"
    )


@pytest.mark.parametrize("site", ["marketwatch.com", "thestreet.com", "seekingalpha.com"])
def test_known_reader_header_without_h1_requires_same_article_source(site):
    url = f"https://www.{site}/article/1"
    text = f"Title: {TITLE}\nURL Source: {url}\nMarkdown Content:\n{BODY}"
    assert choose_candidate(inspect_reader(text, url, TITLE), TITLE)[1] == "FULL"
    spoofed = text.replace("URL Source: " + url, "URL Source: https://other.test/article/1")
    assert (
        choose_candidate(inspect_reader(spoofed, url, TITLE), TITLE)[2]
        == "publisher_identity_mismatch"
    )


def test_javascript_original_link_is_not_a_publisher_hop():
    info = inspect_html(
        article() + '<a href="javascript:void(0)">View Original</a>',
        "https://www.moomoo.com/news/post/123/story",
        TITLE,
    )
    assert info.publisher_links == []
    assert choose_candidate(info, TITLE)[1] == "FULL"


class StaticTransport:
    controller = DomainFetchController()

    def __init__(self, bad_redirect=False):
        self.bad_redirect = bad_redirect
        self.urls = []

    async def fetch(self, url, attempts, *, phase="direct", publisher_url=None):
        self.urls.append(url)
        if self.bad_redirect and phase == "direct":
            return Observation("javascript:void(0)", "", 0, "invalid_url")
        if phase == "reader":
            original = url.removeprefix("https://r.jina.ai/")
            return Observation(
                url,
                f"Title: {TITLE}\nURL Source: {original}\nMarkdown Content:\n# {TITLE}\n{BODY}",
                200,
            )
        return Observation(url, article(), 200)


async def test_bad_transport_redirect_keeps_valid_publisher_for_reader():
    transport = StaticTransport(bad_redirect=True)
    url = "https://publisher.test/story"
    result = await ArticlePipeline(transport).extract(
        MediaEnrichmentRecord("s", "r", "google_news_search_rss", "MU", TITLE, None, url)
    )
    assert result.succeeded
    assert transport.urls == [url, "https://r.jina.ai/" + url]


async def test_document_parsing_does_not_block_the_hub_event_loop(monkeypatch):
    from doxagent.content_enrichment import pipeline

    original = pipeline.inspect_html

    def slow(*args, **kwargs):
        time.sleep(0.15)
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "inspect_html", slow)
    task = asyncio.create_task(
        ArticlePipeline(StaticTransport(), reader_enabled=False).extract(
            MediaEnrichmentRecord(
                "s",
                "r",
                "google_news_search_rss",
                "MU",
                TITLE,
                None,
                "https://publisher.test/story",
            )
        )
    )
    await asyncio.sleep(0.03)
    assert not task.done()
    assert (await task).succeeded


class FallbackTransport(StaticTransport):
    def __init__(self, reader_works=True):
        super().__init__()
        self.reader_works = reader_works
        self.reader_budget = None

    async def fetch(self, url, attempts, *, phase="direct", publisher_url=None):
        if phase == "direct":
            self.urls.append(url)
            return Observation(url, "Blocked by source", 403, "http_403")
        self.reader_budget = remaining(30)
        if not self.reader_works:
            self.urls.append(url)
            return Observation(url, "Temporarily unavailable", 503, "http_503")
        return await super().fetch(url, attempts, phase=phase, publisher_url=publisher_url)


class ExistingBrowser:
    def __init__(self):
        self.calls = []

    async def read(self, url, **kwargs):
        self.calls.append(url)
        return Observation(url, article(), 200), {}


async def test_benzinga_native_source_keeps_existing_http_then_browser_policy():
    url = "https://www.benzinga.com/news/story"
    record = MediaEnrichmentRecord("s", "r", "benzinga_news", "MU", TITLE, None, url)
    for works in (True, False):
        transport, browser = FallbackTransport(works), ExistingBrowser()
        result = await ArticlePipeline(transport, browser=browser).extract(record)
        assert result.succeeded
        assert transport.urls == [url]
        assert browser.calls == [url]


async def test_public_preference_does_not_override_required_authentication():
    url = "https://www.benzinga.com/news/story"

    async def resolve(target):
        return SimpleNamespace(
            body=SimpleNamespace(
                ref="builtin:benzinga@1",
                parameters={},
                access_order=["http_public", "browser", "reader"],
            ),
            auth=SimpleNamespace(body_requirement="inherit", requirement="required"),
            site_id="benzinga",
            strategy_revision=1,
            runtime_key="benzinga",
        )

    transport, browser = FallbackTransport(), ExistingBrowser()
    result = await ArticlePipeline(
        transport, browser=browser, site_client=SimpleNamespace(resolve=resolve)
    ).extract(MediaEnrichmentRecord("s", "r", "google_news_search_rss", "MU", TITLE, None, url))
    assert result.succeeded and browser.calls == [url] and transport.urls == []


@pytest.mark.parametrize(
    ("site", "seconds"),
    [("futunn.com", 12), ("news.futunn.com", 12), ("futunn.com.other.test", 8), ("example.com", 8)],
)
async def test_selective_reader_budget_is_clamped_by_original_article_deadline(site, seconds):
    for outer in (None, 3):
        token = DEADLINE.set(time.monotonic() + outer if outer else None)
        transport = FallbackTransport()
        try:
            result = await ArticlePipeline(transport).extract(
                MediaEnrichmentRecord(
                    "s",
                    "r",
                    "google_news_search_rss",
                    "MU",
                    TITLE,
                    None,
                    "https://" + site + "/news/story",
                )
            )
            assert result.succeeded
            assert transport.reader_budget is not None
            assert (outer or seconds) - 0.5 < transport.reader_budget <= (outer or seconds)
        finally:
            DEADLINE.reset(token)


def test_subdomain_routes_do_not_weaken_article_identity_boundaries():
    assert publisher_host("https://news.futunn.com/en/post/1") == "futunn.com"
    assert publisher_host("https://futunn.com.other.test/post/1") == "futunn.com.other.test"
    assert not same_article("https://news.futunn.com/en/post/1", "https://futunn.com/en/post/1")
    page = "<html><title>Document</title><script>opaque cookie and JS bootstrap</script></html>"
    info = inspect_html(page, "https://news.futunn.com/en/post/1", TITLE)
    assert info.page_kind == "app_shell" and info.access_reason == "render_required"
