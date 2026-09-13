from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.quality import choose_candidate, inspect_html, inspect_reader
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.content_enrichment.transport import DEADLINE, PublicTransport, retry_after
from doxagent.monitoring.media_enrichment import (
    DomainFetchController,
    FetchAttempt,
    MediaExtractionResult,
)
from tests.test_content_enrichment_hub import _ConcurrentExtractor, _message, _setup

TITLE = "Micron memory production outlook"
PARAGRAPHS = [
    "Micron announced a new manufacturing agreement with its partners. "
    "The expansion will supply the next generation of memory products.",
    "The company expects equipment installation to begin next year. "
    "Management said deliveries will follow customer qualification.",
    "Demand continues to increase across data center customers. "
    "The company is maintaining its previously announced investment schedule.",
    "The new facility will complement the existing manufacturing network. "
    "Hiring will take place as construction reaches completion.",
    "Customers will qualify the products before commercial shipment. "
    "The agreement includes engineering support and long term supply commitments.",
    "Management reiterated that timing remains subject to regulatory approval. "
    "The company plans to provide another update with its next earnings release.",
    "The release includes no change to current fiscal year guidance. "
    "Further production details will be disclosed when the facility begins operation.",
]
BODY = "\n\n".join(PARAGRAPHS)


def article(body: str = BODY) -> str:
    return (
        "<article><h1>"
        + TITLE
        + "</h1>"
        + "".join("<p>" + p + "</p>" for p in body.split("\n\n"))
        + "</article>"
    )


def test_short_jsonld_does_not_preempt_complete_body():
    html = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "NewsArticle", "articleBody": "Tiny preview."})
        + "</script>"
        + article()
    )
    candidate, outcome, _ = choose_candidate(
        inspect_html(html, "https://example.com/article", TITLE), TITLE
    )
    assert candidate and candidate.text == BODY
    assert outcome == "FULL"


@pytest.mark.parametrize(
    "gate",
    [
        "Subscribe to continue",
        "Upgrade to read this article",
        "Sign in to read",
        "A Silver or Gold subscription plan is required to access premium news articles.",
    ],
)
def test_long_gated_content_never_becomes_full(gate):
    candidate, _, reason = choose_candidate(
        inspect_html(article(BODY + "\n\n" + gate), "https://example.com/article", TITLE), TITLE
    )
    assert candidate is None
    assert reason in {"login_required", "subscription_required"}


def test_quote_page_rejected_despite_length_and_ticker():
    candidate, _, reason = choose_candidate(
        inspect_html(article(), "https://www.benzinga.com/quote/SMTK", TITLE), TITLE
    )
    assert candidate is None and reason == "non_article_target"


def test_complete_short_article_is_accepted_and_media_description_is_not():
    short = PARAGRAPHS[0] + " " + PARAGRAPHS[1]
    assert (
        choose_candidate(inspect_html(article(short), "https://example.com/news/1", TITLE), TITLE)[
            1
        ]
        == "SHORT_FULL"
    )
    assert (
        choose_candidate(inspect_html(article(short), "https://example.com/video/1", TITLE), TITLE)[
            2
        ]
        == "unsupported_media"
    )
    assert choose_candidate(
        inspect_html(article("Transcript\n\n" + BODY), "https://example.com/video/1", TITLE), TITLE
    )[0]


def test_embedded_video_does_not_reject_text_and_related_content_is_excluded():
    html = article().replace("</article>", "<video></video><aside>" + BODY + "</aside></article>")
    chosen, _, _ = choose_candidate(inspect_html(html, "https://example.com/news/1", TITLE), TITLE)
    assert chosen and chosen.text == BODY


def test_publisher_link_is_article_evidence_not_first_asset():
    html = (
        '<a href="https://cdn.jwplayer.com/previews/1">Video</a>'
        + article(PARAGRAPHS[0])
        + '<a href="https://publisher.example/articles/1">Continue Reading</a>'
    )
    info = inspect_html(html, "https://finance.yahoo.com/m/1", TITLE)
    assert info.publisher_links == ["https://publisher.example/articles/1"]
    assert choose_candidate(info, TITLE)[0] is None


def test_reader_challenge_and_coinbase_content():
    info = inspect_reader(
        "Title: Access to this page has been denied\nMarkdown Content:\nPress & Hold",
        "https://example.com/article",
        TITLE,
    )
    assert info.access_reason == "challenge_required"
    text = "# " + TITLE + "\n\nCoinbase released a product.\n\nContinue Reading\n\n" + BODY
    info = inspect_reader(text, "https://finance.yahoo.com/articles/1", TITLE)
    assert "Coinbase" in info.candidates[0].text and PARAGRAPHS[-1] in info.candidates[0].text


def test_mismatched_article_and_app_shell():
    assert (
        choose_candidate(
            inspect_html(
                article().replace(TITLE, "Apple software update details"),
                "https://example.com/news/1",
                TITLE,
            ),
            TITLE,
        )[2]
        == "publisher_identity_mismatch"
    )
    assert (
        inspect_html(
            '<div id="root"></div><script src="flutter.js"></script>',
            "https://app.moby.co/home",
            TITLE,
        ).page_kind
        == "app_shell"
    )


class Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(url)
        return self.responses[url]


def response(url, text="", status=200, headers=None):
    return SimpleNamespace(url=url, text=text, status_code=status, headers=headers or {})


async def test_redirect_provenance_and_publisher_chain():
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

    start, yahoo, original = (
        "https://finnhub.io/api/news?id=1",
        "https://finance.yahoo.com/m/1",
        "https://publisher.example/article/1",
    )
    session = Session(
        {
            start: response(start, status=302, headers={"location": yahoo}),
            yahoo: response(
                yahoo, article(PARAGRAPHS[0]) + f'<a href="{original}">Continue Reading</a>'
            ),
            original: response(original, article()),
        }
    )
    pipeline = ArticlePipeline(
        PublicTransport(session, DomainFetchController(), validate_urls=False), reader_enabled=False
    )
    record = MediaEnrichmentRecord("id", "id", "source", "MU", TITLE, "", start)
    result = await pipeline.extract(record)
    assert result.succeeded and result.final_url == original
    assert [a.status_code for a in result.attempts] == [302, 200, 200]
    assert result.diagnostics["source_chain"][0]["to_url"] == original


async def test_expired_transport_never_calls_network_and_retry_after_date():
    session = Session({})
    token = DEADLINE.set(time.monotonic() - 1)
    try:
        result = await PublicTransport(session, DomainFetchController(), validate_urls=False).fetch(
            "https://example.com/a", []
        )
        assert result.reason == "deadline_exceeded" and not session.calls
    finally:
        DEADLINE.reset(token)
    future = (datetime.now(UTC) + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert 28 <= retry_after(future) <= 30


async def test_stale_claim_cannot_write_or_delete_replacement(tmp_path):
    repository, bus, source, binding = _setup(tmp_path)
    bus.enqueue_enrichment(
        source=source, binding=binding, message=_message(1), bootstrap=False, poll_run_id="p"
    )
    old = repository.claim_enrichment_jobs(limit=1)[0]
    with repository.transaction() as connection:
        connection.execute(
            "update content_enrichment_jobs set lease_expires_at=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
        )
    replacement = repository.claim_enrichment_jobs(limit=1)[0]
    with pytest.raises(RuntimeError, match="lease_lost"):
        repository.delete_enrichment_job(old.job_id, claim_token=old.claim_token)
    assert repository.list_enrichment_jobs()[0].claim_token == replacement.claim_token


async def test_original_url_identity_survives_enrichment_and_repeat_poll(tmp_path):
    repository, bus, source, binding = _setup(tmp_path)
    msg = _message(1).model_copy(update={"external_id": None})

    class RedirectExtractor(_ConcurrentExtractor):
        async def extract(self, record):
            return MediaExtractionResult(
                record=record, content=BODY, final_url="https://publisher.example/article/1"
            )

    bus.enqueue_enrichment(
        source=source, binding=binding, message=msg, bootstrap=False, poll_run_id="p"
    )
    await ContentEnrichmentHub(repository, bus, extractor=RedirectExtractor()).run_once()
    _, created = bus.enqueue_enrichment(
        source=source, binding=binding, message=msg, bootstrap=False, poll_run_id="again"
    )
    assert created is False
    assert len(repository.list_raw(ticker="MU")) == 1


def test_terminal_entitlement_does_not_retry_earlier_429():
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

    record = MediaEnrichmentRecord("id", "id", "source", "MU", TITLE, "", "https://example.com/a")
    result = MediaExtractionResult(
        record=record,
        reason="entitlement_missing",
        http_status=200,
        attempts=(FetchAttempt("direct", record.url, status_code=429, transient_hint=True),),
    )
    assert not ContentEnrichmentHub._retryable(result)


def test_long_body_behind_expand_button_is_still_partial():
    info = inspect_html(
        article() + "<button>Continue Reading</button>", "https://example.com/a", TITLE
    )
    assert choose_candidate(info, TITLE)[2] == "expand_required"


def test_finnhub_redirect_uses_explicit_target_not_external_asset():
    info = inspect_html(
        '<script src="https://cdn.example/player.js"></script>'
        '<meta http-equiv="refresh" content="0; url=https://example.com/news/1">',
        "https://finnhub.io/api/news?id=1",
        TITLE,
    )
    assert info.publisher_links == ["https://example.com/news/1"]


def test_seeking_alpha_biography_and_disclosures_cannot_complete_truncated_body():
    page = (
        '<script type="application/ld+json">'
        '{"@type":"NewsArticle","isAccessibleForFree":"False"}</script>'
        f"<article><h1>{TITLE}</h1><ul><li>{BODY}</li></ul>"
        '<div data-test-id="content-container"><p>The semiconductor outlook depends on</p></div>'
        f'<div data-test-id="content-container"><p>{BODY}</p></div>'
        '<div data-test-id="content-container" class="paywall-full-content">'
        f"<p>{BODY}</p></div></article>"
    )
    info = inspect_html(page, "https://seekingalpha.com/article/123-memory-production", TITLE)
    assert choose_candidate(info, TITLE)[2] == "subscription_required"
    assert len(info.candidates) == 1 and info.candidates[0].text.endswith("depends on")


async def test_claim_fairness_and_old_worker_raw_write_guard(tmp_path):
    repository, bus, source, binding = _setup(tmp_path)
    for index, host in enumerate(["slow.example"] * 9 + ["fast.example"]):
        bus.enqueue_enrichment(
            source=source,
            binding=binding,
            message=_message(index).model_copy(update={"url": f"https://{host}/news/{index}"}),
            bootstrap=False,
            poll_run_id="p",
        )
    jobs = repository.claim_enrichment_jobs(limit=2)
    assert {j.message.url.split("/")[2] for j in jobs} == {"slow.example", "fast.example"}
    old = jobs[0]
    with repository.transaction() as connection:
        connection.execute(
            "update content_enrichment_jobs set lease_expires_at=? where job_id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), old.job_id),
        )
    replacement = repository.claim_enrichment_jobs(limit=8)
    assert any(j.job_id == old.job_id and j.claim_token != old.claim_token for j in replacement)
    with pytest.raises(RuntimeError, match="lease_lost"):
        await bus.accept_message(
            source=source,
            binding=binding,
            message=old.message,
            bootstrap=False,
            trusted_enrichment=True,
            enrichment_input=old.message,
            enrichment_claim=(old.job_id, old.claim_token),
        )
    assert repository.list_raw(ticker="MU") == []


def test_reader_keeps_article_around_ad_but_excludes_privacy_footer():
    text = (
        "# " + TITLE + "\n\n" + PARAGRAPHS[0] + "\n\n"
        "### Go deeper with GlobalData\n\nA promotional report\n\n"
        "[Find out more](https://example.com/report)\n\n" + BODY + "\n\n"
        "### Sign up for our daily news round-up!\n\nPrivacy Preference Center\n\n" + BODY
    )
    info = inspect_reader(text, "https://www.just-auto.com/news/1", TITLE)
    assert PARAGRAPHS[-1] in info.candidates[0].text
    assert "Privacy" not in info.candidates[0].text and "promotional" not in info.candidates[0].text


def test_headline_only_promotion_is_not_a_short_full_article():
    info = inspect_html(
        article("This headline only article is a sample of real-time intelligence.\n\n" + BODY),
        "https://www.benzinga.com/news/1",
        TITLE,
    )
    assert choose_candidate(info, TITLE)[2] == "headline_only_source"


def test_chartmill_reader_header_and_newsletter_link_are_not_false_failures():
    info = inspect_reader(
        "Title: " + TITLE + "\n\nMarkdown Content:\n\n" + BODY,
        "https://www.chartmill.com/news/1",
        TITLE,
    )
    assert choose_candidate(info, TITLE)[0]
    info = inspect_html(
        article() + '<a href="https://example.com/newsletter">Read More</a>',
        "https://example.com/news/1",
        TITLE,
    )
    assert choose_candidate(info, TITLE)[0]


async def test_unknown_frozen_pipeline_version_does_not_start_network():
    from doxagent.content_enrichment.extractor import SharedContentExtractor
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

    extractor = SharedContentExtractor(session_factory=lambda: pytest.fail("unexpected network"))
    result = await extractor.extract_version(
        MediaEnrichmentRecord("id", "id", "s", "MU", TITLE, "", "https://example.com/a"),
        "future_version",
    )
    assert result.reason == "pipeline_version_unavailable"


async def test_public_429_can_use_reader_with_its_own_endpoint_limit():
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

    url = "https://www.fool.com/investing/2026/09/10/article/"
    reader = "https://r.jina.ai/" + url
    session = Session(
        {
            url: response(url, status=429, headers={"retry-after": "30"}),
            reader: response(reader, "Title: " + TITLE + "\n\nMarkdown Content:\n\n" + BODY),
        }
    )
    result = await ArticlePipeline(
        PublicTransport(session, DomainFetchController(), validate_urls=False)
    ).extract(MediaEnrichmentRecord("id", "id", "s", "MU", TITLE, "", url))
    assert result.succeeded and session.calls == [url, reader]


def test_article_body_excludes_normal_newsletter_and_promotion_tail():
    page = article().replace(
        "</article>",
        "<h2>Sign up for our weekly news round-up!</h2><p>Marketing copy</p></article>",
    )
    chosen, _, _ = choose_candidate(inspect_html(page, "https://example.com/news/1", TITLE), TITLE)
    assert chosen and chosen.text == BODY


@pytest.mark.parametrize("page,reason", [
    ('<title>JavaScript is disabled</title><script>window.awsWafCookieDomainList=[];'
     'window.gokuProps={};</script><noscript>JavaScript is disabled</noscript>',
     'render_required'),
    ('<title>Just a moment...</title><script>window._cf_chl_opt={};</script>'
     '<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>',
     'challenge_required'),
    ('<title>thestreet.com</title><p>Please enable JS and disable any ad blocker</p>'
     '<script src="https://geo.captcha-delivery.com/captcha.js"></script>',
     'challenge_required'),
])
def test_waf_bootstrap_is_not_wrong_article_or_paywall(page, reason):
    info = inspect_html(page, 'https://example.com/news/a', TITLE)
    assert choose_candidate(info, TITLE)[2] == reason
    assert ArticlePipeline._next(reason) not in {
        'authenticate_or_review_access', 'configure_entitled_account'
    }
    # A normal article may include security scripts; their presence alone is not a gate.
    normal = inspect_html(article() + page, 'https://example.com/a', TITLE)
    assert choose_candidate(normal, TITLE)[0]


async def test_aws_202_bootstrap_reaches_browser_and_validates_rendered_title():
    from unittest.mock import AsyncMock

    from doxagent.content_enrichment.transport import Observation
    from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord

    url = 'https://www.barchart.com/story/news/123/test'
    session = Session({url: response(url,
        '<title>JavaScript is disabled</title><script>awsWafCookieDomainList=[];gokuProps={};'
        '</script>', status=202)})
    browser = AsyncMock()
    browser.read.return_value = (Observation(url, article(), 200), {})
    result = await ArticlePipeline(
        PublicTransport(session, DomainFetchController(), validate_urls=False), browser=browser
    ).extract(MediaEnrichmentRecord('id', 'id', 's', 'MU', TITLE, '', url))
    assert result.succeeded and session.calls == [url]
    browser.read.assert_awaited_once()


@pytest.mark.parametrize("control", ["<button>Show more</button>", '<a href="#bio">Read more</a>'])
def test_sa_biography_expansion_does_not_block_full_article(control):
    url = "https://seekingalpha.com/article/123-test"
    page = (
        '<article><h1>' + TITLE + '</h1><div data-test-id="content-container">'
        + ''.join('<p>' + p + '</p>' for p in PARAGRAPHS)
        + '</div><div>Author biography' + control + '</div></article>'
    )
    info = inspect_html(page, url, TITLE)
    chosen, outcome, _ = choose_candidate(info, TITLE)
    assert chosen and chosen.text == BODY and outcome == "FULL"
    assert not info.expansion_required
    incomplete = page.replace(
        '</div><div>Author biography', control + '</div><div>Author biography'
    )
    assert choose_candidate(inspect_html(incomplete, url, TITLE), TITLE)[0] is None
    promotion = page.replace(
        '</div><div>Author biography',
        '<h2>More on my IG service</h2><p>Subscribe to my group.</p></div><div>Author biography',
    )
    chosen = choose_candidate(inspect_html(promotion, url, TITLE), TITLE)[0]
    assert chosen and chosen.text == BODY
