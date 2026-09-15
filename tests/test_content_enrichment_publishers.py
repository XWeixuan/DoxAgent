from __future__ import annotations

import json

import pytest

from doxagent.content_enrichment.captions import caption_text, cnbc_caption_source
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.publishers import public_api_html, public_article_api
from doxagent.content_enrichment.transport import PublicTransport
from doxagent.monitoring.media_enrichment import DomainFetchController, MediaEnrichmentRecord
from tests.test_content_enrichment_pipeline import BODY, TITLE, Session, response

URL = 'https://247wallst.com/investing/2026/09/01/micron-memory-production/'


def wp_post(**changes):
    return dict({'link': URL, 'status': 'publish', 'title': {'rendered': TITLE},
                 'content': {'protected': False, 'rendered': '<p>' + BODY + '</p>'}}, **changes)


@pytest.mark.parametrize('changes', [
    {'link': 'https://247wallst.com/investing/different-article/'},
    {'link': 'https://attacker.test/investing/2026/09/01/micron-memory-production/'},
    {'content': {'protected': True, 'rendered': '<p>' + BODY + '</p>'}},
    {'status': 'draft'},
])
def test_public_api_requires_published_free_exact_article(changes):
    assert public_api_html(json.dumps([wp_post(**changes)]), URL) is None


async def test_public_wp_body_preserves_article_identity_and_records_actual_api_source():
    api = public_article_api(URL)
    session = Session({
        URL: response(URL, '<title>Access denied</title>', status=403),
        api: response(api, json.dumps([wp_post()])),
    })
    result = await ArticlePipeline(
        PublicTransport(session, DomainFetchController(), validate_urls=False)
    ).extract(MediaEnrichmentRecord('id', 'id', 's', 'MU', TITLE, '', URL))
    assert result.succeeded and result.final_url == URL
    assert result.diagnostics['body_source_url'] == api
    assert session.calls == [URL, api]


VIDEO = 'https://www.cnbc.com/video/2026/09/01/memory.html'
CAPTION = 'https://pdl-iphone-cnbc-com.akamaized.net/test.vtt'


def video_page(**changes):
    data = dict({'url': VIDEO, 'premium': False, 'title': TITLE,
                 'duration': len(BODY.split('\n\n')),
                 'encodings': [{'url': CAPTION, 'formatName': 'WebVTT_0_Download'}]}, **changes)
    return '<h1>' + TITLE + '</h1><script>window.__s_data=' + json.dumps(data) + ';</script>'


@pytest.mark.parametrize('changes', [
    {'url': VIDEO + '?different=1'}, {'premium': True}, {'title': 'Completely unrelated sports'},
    {'encodings': [{'url': 'https://attacker.test/test.vtt', 'formatName': 'WebVTT_0_Download'}]},
])
def test_caption_source_rejects_unrelated_video_or_nonpublic_entitlement(changes):
    assert cnbc_caption_source(video_page(**changes), VIDEO, TITLE) is None


async def test_public_caption_pipeline_labels_transcript_and_keeps_video_url():
    paragraphs = BODY.split('\n\n')
    vtt = 'WEBVTT\n\n' + '\n\n'.join(
        f'{i}\n00:00:{i:02d}.000 --> 00:00:{i+1:02d}.000\n{paragraph}'
        for i, paragraph in enumerate(paragraphs)
    )
    session = Session({VIDEO: response(VIDEO, video_page()), CAPTION: response(CAPTION, vtt)})
    result = await ArticlePipeline(
        PublicTransport(session, DomainFetchController(), validate_urls=False)
    ).extract(MediaEnrichmentRecord('id', 'id', 's', 'MU', TITLE, '', VIDEO))
    assert result.succeeded and result.final_url == VIDEO
    assert result.diagnostics['content_role'] == 'video_transcript'
    assert result.diagnostics['body_source_url'] == CAPTION
    assert result.content == 'Transcript\n\n' + '\n'.join(paragraphs)


def test_empty_or_html_caption_file_does_not_fabricate_transcript():
    assert caption_text('WEBVTT\n\n') == ''
    assert caption_text('<html><body>' + BODY + '</body></html>') == ''
    short = 'WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nThis is only the introduction.'
    assert caption_text(short, duration=180) == ''


def test_rolling_broadcast_captions_extend_windows_without_repeating_speech():
    cues = ['I', 'I THINK', 'I THINK MEMORY', 'I THINK MEMORY IS',
            'MEMORY IS', 'MEMORY IS GROWING.', 'IS GROWING.',
            'ANOTHER SPEAKER THINKS SO.', 'I THINK MEMORY IS GROWING.']
    vtt = 'WEBVTT\n\n' + '\n\n'.join(
        f'00:00:{i:02d}.000 --> 00:00:{i+1:02d}.000\n{cue}'
        for i, cue in enumerate(cues)
    )
    assert caption_text(vtt) == (
        'Transcript\n\nI THINK MEMORY IS GROWING.\n'
        'ANOTHER SPEAKER THINKS SO.\nI THINK MEMORY IS GROWING.'
    )


def test_reuters_div_paragraphs_exclude_recommendations_and_newsletter():
    from doxagent.content_enrichment.quality import choose_candidate, inspect_html
    paras = BODY.split('\n\n')
    body = ''.join(f'<div data-testid="paragraph-{i}">{p}</div>' for i, p in enumerate(paras))
    page = f'<h1>{TITLE}</h1><div data-testid="ArticleBody">{body}<p>Newsletter</p></div>'
    page += '<article><p>Unrelated recommendation</p></article>'
    c, _, _ = choose_candidate(
        inspect_html(page, 'https://www.reuters.com/world/news/', TITLE), TITLE)
    assert c and c.text == BODY and c.method == 'reuters_article_body'


def test_wsj_author_biography_cannot_outrank_the_actual_body():
    from doxagent.content_enrichment.quality import choose_candidate, inspect_html
    body = ''.join(f'<p data-type="paragraph">{p}</p>' for p in BODY.split('\n\n'))
    page = f'<h1>{TITLE}</h1><article><div class="paywall">{body}</div>'
    page += '<article><p>' + 'This is an author biography. ' * 50 + '</p></article></article>'
    c, _, _ = choose_candidate(inspect_html(page, 'https://www.wsj.com/finance/news', TITLE), TITLE)
    assert c and c.text == BODY and c.method == 'wsj_article_body'


def test_barrons_live_card_accepts_short_complete_body_without_timestamp_or_byline():
    from doxagent.content_enrichment.quality import choose_candidate, inspect_html
    brief = BODY.split('\n\n')[0]
    page = f'<div data-id="LiveCoverageCard_index_CardWrapper"><h1>{TITLE}</h1><p>By Author</p>'
    page += f'<div data-id="LiveCoverageCard_index_CardBlock"><p class="FormattedText">{brief}'
    page += '</p></div></div>'
    c, outcome, _ = choose_candidate(inspect_html(
        page, 'https://www.barrons.com/livecoverage/day/card/news', TITLE), TITLE)
    assert c and c.text == brief and outcome == 'SHORT_FULL'


def test_localized_sa_outside_article_matches_original_title_only_for_exact_article_id():
    from doxagent.content_enrichment.quality import choose_candidate, inspect_html
    attrs = {'originalTitle': TITLE}
    data = {'article': {'response': {'data': {
        'id': '123', 'type': 'fullArticle', 'attributes': attrs,
    }}}}
    body = '公司扩建工厂以满足需求增长。管理层预计明年开始安装设备。' * 35
    page = '<h1>本地语言标题</h1><div data-test-id="content-container"><p>' + body + '</p></div>'
    page += '<script>window.SSR_DATA=' + json.dumps(data) + ';</script>'
    url = 'https://seekingalpha.com/article/123-memory'
    c, _, _ = choose_candidate(inspect_html(page, url, TITLE), TITLE)
    assert c and c.text == body and c.method == 'sa_article_body'
    assert choose_candidate(inspect_html(page, url.replace('123', '999'), TITLE), TITLE)[0] is None
