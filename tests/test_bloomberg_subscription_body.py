from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.bloomberg import verified_subscription_result
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.quality import choose_candidate, inspect_html
from doxagent.content_enrichment.transport import Observation
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.message_bus_v2.body_results import ArticleBodyResults, access_scope
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.site_strategy.bloomberg_document import capture_document, snapshot

URL = 'https://www.bloomberg.com/news/articles/2026-11-12/company-financing'
TITLE = 'Company Announces Financing Agreement'
PARAGRAPH = ('The company will raise $750 million under a five year secured agreement. '
             'Interest is 6.25% and repayment begins in 2028. ')


def test_subscription_cache_does_not_reuse_historical_public_preview():
    message = RawMessageInput(title=TITLE, body='', url=URL, raw_payload={})
    assert access_scope(message, 'bloomberg_massive') == 'scope:publisher:bloomberg.com'
    assert not ArticleBodyResults.compatible({'access_scope': 'public'}, message, 'bloomberg_massive')
    assert ArticleBodyResults.compatible({'access_scope': 'scope:publisher:bloomberg.com'}, message, 'bloomberg_massive')
    assert not verified_subscription_result({'diagnostics': {'outcome': 'FULL'}})
    assert verified_subscription_result({'diagnostics': {'candidate_summary': [
        {'method': 'bloomberg_visible_dom', 'structured': True}]}})


@pytest.mark.parametrize('verified', [False, True])
def test_live_resolved_cache_checks_visible_body_proof(tmp_path, verified):
    from tests.test_content_enrichment_hub import _setup, _message
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.message_bus_v2.newswire_sources import initial_sources
    from doxagent.message_bus_v2.schema import utc_now
    from doxagent.message_bus_v2.test_delivery import bindings

    repo, bus, _, _ = _setup(tmp_path)
    queue = LiveQueue(bus)
    source = next(s for s in initial_sources() if s.source_id == 'bloomberg_massive')
    repo.save_source(source)
    binding = bindings(source)[0]
    message = _message(990).model_copy(update={'url': URL, 'metadata': {'_delivery_mode': 'test'}})
    rows = queue.repository.prepare_input(source, [(binding.binding_id, binding.ticker, 0)],
                                          [message], observed_at=utc_now().isoformat(), unit='TEST:WIRE:latest')
    queue.repository.accept(rows)
    stored = queue.repository.get(rows[0]['key'])
    message = RawMessageInput.model_validate(queue.repository.load(stored['input_ref']))
    meta = {'outcome': 'FULL', 'candidate_summary': [
        {'method': 'bloomberg_visible_dom' if verified else 'article_dom', 'structured': True}]}
    result = message.model_copy(update={'body': PARAGRAPH*8, 'metadata': {**message.metadata, 'media_enrichment': meta}})
    ref, _ = queue.repository.put(result.model_dump(mode='json'))
    with repo.transaction() as db:
        db.execute("UPDATE live_articles SET result_ref=?,outcome='FULL',acquired_at=? WHERE article_key=?",
                   (ref, utc_now().isoformat(), rows[0]['key']))
    reused = queue.resolved_reuse(message, source.source_id, URL)
    assert bool(reused) == verified


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


def test_unknown_template_does_not_invalidate_paid_entitlement():
    html = document().replace('data-dox-bloomberg-snapshot="visible-v1"', '')
    assert inspect_html(html, URL, TITLE).access_reason == 'render_required'
    gated = document(complete=False).replace('data-dox-bloomberg-access-reason="render_required"',
                                          'data-dox-bloomberg-access-reason="subscription_required"')
    assert inspect_html(gated, URL, TITLE).access_reason == 'subscription_required'


@pytest.mark.parametrize('title', ['Verify you are human', 'Bloomberg - Are you a robot?'])
def test_challenge_precedes_article_quality(title):
    html = '<title>' + title + '</title><div id="px-captcha"></div>'
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
