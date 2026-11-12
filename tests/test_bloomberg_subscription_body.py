from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.quality import choose_candidate, inspect_html
from doxagent.content_enrichment.transport import Observation
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.site_strategy.bloomberg_document import capture_document, snapshot

URL = 'https://www.bloomberg.com/news/articles/2026-11-12/company-financing'
TITLE = 'Company Announces Financing Agreement'
PARAGRAPH = ('The company will raise $750 million under a five year secured agreement. '
             'Interest is 6.25% and repayment begins in 2028. ')


def document(*, complete=True, body=None):
    return snapshot(dict(storyId='TODY4FKJH6V800', canonical=URL, headline=TITLE,
                         schema={'@type': 'NewsArticle', 'isAccessibleForFree': False,
                                 'datePublished': '2026-11-12T17:21:58.025Z'},
                         complete=complete,
                         body='<div class="body-content">' + (body or '<p>' + PARAGRAPH*8 + '</p>') + '</div>'))


def test_visible_subscription_preserves_terms_and_exact_published_time():
    info = inspect_html(document(), URL, TITLE)
    candidate, outcome, _ = choose_candidate(info, TITLE)
    assert outcome == 'FULL' and candidate.complete
    assert '$750 million' in candidate.text and '6.25%' in candidate.text and '2028' in candidate.text
    assert info.publisher_published_at == '2026-11-12T17:21:58.025000+00:00'


@pytest.mark.parametrize('change', ['raw_html', 'incomplete', 'wrong_article'])
def test_hidden_html_and_previews_never_become_full(change):
    html = document(complete=change != 'incomplete')
    if change == 'raw_html':
        html = html.replace('data-dox-bloomberg-snapshot="visible-v1"', '')
    if change == 'wrong_article':
        html = html.replace('href="' + URL, 'href="' + URL + '-other')
    assert choose_candidate(inspect_html(html, URL, TITLE), TITLE)[0] is None


def test_short_paid_story_requires_visible_complete_evidence():
    assert choose_candidate(inspect_html(document(body='<p>' + PARAGRAPH + '</p>'), URL, TITLE), TITLE)[1] == 'SHORT_FULL'
    assert choose_candidate(inspect_html(document(complete=False, body='<p>' + PARAGRAPH + '</p>'), URL, TITLE), TITLE)[0] is None


def test_challenge_precedes_article_quality():
    html = '<title>Verify you are human</title><div id="px-captcha"></div>'
    assert choose_candidate(inspect_html(html, URL, TITLE), TITLE)[2] == 'challenge_required'


@pytest.mark.asyncio
async def test_capture_waits_for_entitlement_hydration_and_stable_body():
    base = dict(storyId='TODY4FKJH6V800', text=PARAGRAPH, canonical=URL,
                headline=TITLE, schema={}, body='<div class="body-content"><p>' + PARAGRAPH + '</p></div>')
    page = SimpleNamespace(url=URL, evaluate=AsyncMock(side_effect=[
        {**base, 'complete': False}, {**base, 'complete': True}, {**base, 'complete': True}]))
    html, url = await capture_document(page, timeout=2)
    assert url == URL and 'data-dox-bloomberg-complete="true"' in html
    assert page.evaluate.await_count == 3


@pytest.mark.parametrize('source,expected', [('bloomberg_massive', 'managed'), ('businesswire_massive', 'public')])
@pytest.mark.asyncio
async def test_massive_routing_uses_paid_identity_only_for_bloomberg(source, expected):
    extractor = SharedContentExtractor()
    extractor.start = AsyncMock()
    extractor._session = object()
    extractor._pipeline = SimpleNamespace(extract=AsyncMock(return_value='public'))
    extractor._managed_pipeline = SimpleNamespace(extract=AsyncMock(return_value='managed'))
    record = MediaEnrichmentRecord('id', 'id', source, 'MU', TITLE, '', URL)
    assert await extractor.extract_version(record, 'body_v2.2') == expected


@pytest.mark.asyncio
async def test_bloomberg_missing_owner_does_not_fall_back_to_public_text():
    extractor = SharedContentExtractor()
    extractor.start = AsyncMock()
    extractor._session = object()
    extractor._pipeline = SimpleNamespace(extract=AsyncMock())
    record = MediaEnrichmentRecord('id', 'id', 'bloomberg_massive', 'MU', TITLE, '', URL)
    result = await extractor.extract_version(record, 'body_v2.2')
    assert result.reason == 'site_access_unavailable'
    extractor._pipeline.extract.assert_not_called()


@pytest.mark.asyncio
async def test_massive_pipeline_keeps_required_browser_access_order():
    resolved = SimpleNamespace(
        site_id='bloomberg_latest_lab', strategy_revision=3, runtime_key='bloomberg_latest_lab',
        body=SimpleNamespace(ref='builtin:bloomberg@1', parameters={}, access_order=['browser']),
        auth=SimpleNamespace(body_requirement='required'))
    transport = SimpleNamespace(fetch=AsyncMock(side_effect=AssertionError('no public fallback')))
    browser = SimpleNamespace(site_managed=True, read=AsyncMock(return_value=(Observation(URL, document(), 200, None), {})))
    pipeline = ArticlePipeline(transport, browser=browser, site_client=SimpleNamespace(resolve=AsyncMock(return_value=resolved)))
    result = await pipeline.extract(MediaEnrichmentRecord('id', 'id', 'bloomberg_massive', 'MU', TITLE, '', URL))
    assert result.reason is None and result.diagnostics['outcome'] == 'FULL'
    browser.read.assert_awaited_once()
    transport.fetch.assert_not_called()
