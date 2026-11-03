"""IBKR delivery channels share a story, not a copyright-template body."""
from types import SimpleNamespace

import pytest

from doxagent.content_enrichment.native import native_article
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2 import deduplication as dedup
from doxagent.message_bus_v2.body_results import article_key
from doxagent.message_bus_v2.ibkr_identity import DJ_CHANNELS, story_id
from doxagent.message_bus_v2.ibkr_news import IbkrNewsAdapter
from doxagent.message_bus_v2.live import LiveDeliveryWorker, LiveQueue
from doxagent.message_bus_v2.schema import utc_now, UpdateActor
from doxagent.settings import DoxAgentSettings
from tests.test_content_enrichment_hub import _setup
from tests.test_cross_source_live_dedup import Extractor, capture, drain, publications
from tests.test_message_bus_v2_deduplication import BODY

FOOTER = ' Dow Jones Newswires November 02, 2026 17:46 ET (22:46 GMT) Copyright (c) 2026 Dow Jones & Company, Inc. The statements in this document shall not be considered as an objective or independent explanation of the matters.'
TITLE = 'Intel CFO Says Demand for Data Center CPUs Remains Strong'


def sdk_message(code='DJ-N', core='1fb4b64d', *, body=BODY, title=TITLE, language='en'):
    row = {'providerCode': code, 'articleId': code + '$' + core,
           'time': utc_now().strftime('%Y-%m-%d %H:%M:%S'), 'articleType': 0,
           'headline': (f'{{A:800015:L:{language}}}' if language else '') + title,
           'articleText': '<pre>' + (body or '') + '</pre><p>(' + ('END' if body else 'MORE TO FOLLOW') + ')' + FOOTER + '</p>'}
    return adapter_result(row).messages[0]


def adapter_result(row):
    return IbkrNewsAdapter(DoxAgentSettings(_env_file=None), gateway=object())._result(
        SimpleNamespace(requested_at=utc_now(), checkpoint={}), [row], {})


@pytest.fixture
def pipeline(tmp_path):
    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    return repo, bus, Extractor()


@pytest.mark.parametrize('code', DJ_CHANNELS)
def test_known_family_identity_keeps_sdk_delivery_id(code):
    msg = sdk_message(code)
    assert story_id(msg) == 'ibkr:dowjones:1fb4b64d'
    assert msg.external_id == msg.source_item_key == f'{code}:{code}$1fb4b64d'
    assert msg.raw_payload['articleId'] == code + '$1fb4b64d'
    assert msg.metadata['language'] == 'en'
    assert msg.title == TITLE and msg.body == BODY.strip()
    assert native_article(msg).text == msg.body


@pytest.mark.parametrize('code,article', [('BRFUPDN', 'BRFUPDN$abc'), ('DJ-NEW', 'DJ-NEW$abc'), ('DJ-N', 'DJ-RTA$abc'), ('DJ-N', 'DJ-N$story')])
def test_unknown_or_mismatched_id_does_not_fold(code, article):
    msg = sdk_message(code)
    msg = msg.model_copy(update={'raw_payload': {**msg.raw_payload, 'articleId': article}})
    assert story_id(msg) is None
    assert story_id(sdk_message(), 'another_source') is None


@pytest.mark.parametrize('kind', ['long', 'flash', 'read_more'])
async def test_five_channels_have_one_body_owner_and_one_stream(pipeline, kind):
    body = None if kind == 'flash' else BODY + (' Read more about the election here.' if kind == 'read_more' else '')
    for code in DJ_CHANNELS[:5]:
        capture(pipeline, 'ibkr_news', sdk_message(code, body=body, title='!* ' + TITLE))
        await drain(pipeline)
    repo, _, extractor = pipeline
    assert len(publications(pipeline)) == 1 and not extractor.calls
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0] == 1
        assert db.execute('SELECT input_revision,revision FROM live_articles').fetchone()[:] == (1, 1)
    raw = repo.get_raw(publications(pipeline)[0][0])
    assert raw.body == (TITLE if kind == 'flash' else dedup.text(body))
    assert raw.metadata['media_enrichment']['outcome'] == ('SHORT_FULL' if kind == 'flash' else 'FULL')
    assert 'Copyright' not in raw.body


async def test_distinct_flash_facts_at_one_time_are_not_template_duplicates(pipeline):
    for core, title in [('abc', TITLE), ('def', 'Intel CFO Says Client PC Demand Remains Weak')]:
        capture(pipeline, 'ibkr_news', sdk_message(core=core, title=title, body=None))
        await drain(pipeline)
    assert len(publications(pipeline)) == 2


def test_empty_placeholder_filtered_but_empty_headline_real_article_kept():
    row = sdk_message().raw_payload
    empty = adapter_result({**row, 'headline': '{A:1:L:en}!', 'articleText': '(MORE TO FOLLOW)' + FOOTER})
    assert empty.messages == [] and empty.acquisition_metadata['ibkr_empty_filtered_count'] == 1
    assert empty.next_checkpoint['last_sdk_until']
    real = adapter_result({**row, 'headline': '!'}).messages[0]
    assert real.title is None and real.body == BODY.strip()
    assert native_article(real) is not None


async def test_updates_and_full_promotion_keep_original_publication(pipeline):
    capture(pipeline, 'ibkr_news', sdk_message(body=None))
    await drain(pipeline)
    ids = publications(pipeline)
    for code, body, title in [('DJ-RTA', BODY, TITLE),
                              ('DJ-RTE', BODY.replace('15 billion', '25 billion'), TITLE),
                              ('DJ-RTG', BODY.replace('15 billion', '25 billion'), 'Corrected Intel Factory Investment Details')]:
        capture(pipeline, 'ibkr_news', sdk_message(code, body=body, title=title))
        await drain(pipeline)
        assert publications(pipeline) == ids
        raw = pipeline[0].get_raw(ids[0][0])
        assert raw.body == body.strip() and raw.title == title
    old = pipeline[0].get_raw(ids[0][0])
    capture(pipeline, 'ibkr_news', sdk_message('DJ-RTPRO', body=None))
    await drain(pipeline)
    raw = pipeline[0].get_raw(ids[0][0])
    assert raw.body == old.body and raw.title == old.title
    assert raw.metadata['message_version']['content_revision'] == 4


async def test_bad_late_headline_cannot_remove_good_title(pipeline):
    capture(pipeline, 'ibkr_news', sdk_message())
    await drain(pipeline)
    capture(pipeline, 'ibkr_news', sdk_message('DJ-RTE', title='!'))
    await drain(pipeline)
    assert pipeline[0].get_raw(publications(pipeline)[0][0]).title == TITLE
    assert len(publications(pipeline)) == 1


@pytest.mark.parametrize('boundary', ['language', 'private_scope'])
async def test_explicit_scope_and_language_are_separate(pipeline, boundary):
    for i, code in enumerate(['DJ-N', 'DJ-RTE']):
        msg = sdk_message(code, body=None, language='fr' if i and boundary == 'language' else 'en')
        if boundary == 'private_scope':
            msg = msg.model_copy(update={'metadata': {**msg.metadata, 'body_access_scope': 'private:' + str(i)}})
        capture(pipeline, 'ibkr_news', msg)
        await drain(pipeline)
    assert len(publications(pipeline)) == 2


async def test_ticker_publications_remain_independent(pipeline):
    repo, bus, _ = pipeline
    msg = sdk_message(body=None)
    capture(pipeline, 'ibkr_news', msg)
    binding = bus.configure_binding(ticker='INTC', source_id='ibkr_news', actor=UpdateActor.SYSTEM)
    live = bus.live_queue.repository
    live.accept(live.prepare_input(bus.require_source('ibkr_news'), [(binding.binding_id, 'INTC', 0)],
                                  [msg], observed_at=utc_now().isoformat(), unit='HEAD'))
    await drain(pipeline)
    with repo._connect() as db:
        assert {r[0] for r in db.execute('SELECT ticker FROM stream_items')} == {'MU', 'INTC'}
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0] == 1


async def test_concurrent_capture_and_old_body_result_cannot_replace_new_input(pipeline):
    repo, bus, extractor = pipeline
    live = bus.live_queue.repository
    rows1 = capture(pipeline, 'ibkr_news', sdk_message(body=None))
    job = (await bus.live_queue.claim(1))[0]
    capture(pipeline, 'ibkr_news', sdk_message('DJ-RTE'))
    stale_message = job.message.model_copy(update={'metadata': {**job.message.metadata,
        'media_enrichment': {'succeeded': True, 'outcome': 'SHORT_FULL'}}})
    assert live.commit_result(live.prepare_result(job, stale_message)) is False
    await drain(pipeline)
    assert repo.get_raw(publications(pipeline)[0][0]).body == BODY.strip()
    assert live.get(rows1[0]['key'])['input_revision'] == 2


async def test_old_channel_key_is_reconnected_without_history_scan(pipeline):
    from unittest.mock import patch
    repo, bus, _ = pipeline
    msg = sdk_message()
    # Produce a pre-fix owner/alias shape using the actual current Live writer.
    with patch('doxagent.message_bus_v2.ibkr_identity.story_id', return_value=None):
        rows = capture(pipeline, 'ibkr_news', msg.model_copy(update={'metadata': {**msg.metadata,
            'identity_evidence': {'url_kind': 'generic'}, 'ibkr_content_kind': 'article'}}))
        await drain(pipeline)
    ids = publications(pipeline)
    with repo.transaction() as db:
        db.execute("DELETE FROM message_identity_aliases WHERE alias LIKE 'verified:ibkr:%'")
    capture(pipeline, 'ibkr_news', sdk_message('DJ-RTE'))
    await drain(pipeline)
    assert publications(pipeline) == ids
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0] == 1
        assert db.execute("SELECT article_key FROM live_origins WHERE origin_key=?", ('canonical:' + article_key(sdk_message(), 'ibkr_news'),)).fetchone()[0] == rows[0]['key']


async def test_prepared_channel_captures_share_one_slot_and_keep_good_title(pipeline):
    repo, bus, _ = pipeline
    live = bus.live_queue.repository
    binding = repo.get_binding('MU:ibkr_news')
    source = bus.require_source('ibkr_news')
    prepared = [live.prepare_input(source, [(binding.binding_id, 'MU', 0)], [sdk_message(code, title=title)],
                                   observed_at=utc_now().isoformat(), unit='HEAD')
                for code, title in [('DJ-N', TITLE), ('DJ-RTE', '!'), ('DJ-RTG', TITLE)]]
    for rows in prepared:
        live.accept(rows)
    await drain(pipeline)
    assert len(publications(pipeline)) == 1
    assert repo.get_raw(publications(pipeline)[0][0]).title == TITLE
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0] == 1


async def test_late_flash_does_not_cancel_full_promotion_in_flight(pipeline):
    repo, bus, extractor = pipeline
    capture(pipeline, 'ibkr_news', sdk_message(body=None))
    await drain(pipeline)
    rows = capture(pipeline, 'ibkr_news', sdk_message('DJ-RTA'))
    job = (await bus.live_queue.claim(1))[0]
    revision = bus.live_queue.repository.get(rows[0]['key'])['input_revision']
    capture(pipeline, 'ibkr_news', sdk_message('DJ-RTE', body=None))
    assert bus.live_queue.repository.get(rows[0]['key'])['input_revision'] == revision
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    try:
        await hub._process(job, utc_now())
        await LiveDeliveryWorker(bus).run_available()
    finally:
        await hub.close()
    assert repo.get_raw(publications(pipeline)[0][0]).body == BODY.strip()


async def test_multiple_legacy_publications_choose_earliest_and_refresh_template(pipeline):
    from unittest.mock import patch
    from doxagent.content_enrichment.quality import Candidate
    repo, bus, _ = pipeline
    with patch('doxagent.message_bus_v2.ibkr_identity.story_id', return_value=None), \
         patch('doxagent.message_bus_v2.ibkr_identity.normalize_message', side_effect=lambda msg: msg), \
         patch('doxagent.content_enrichment.service.native_article',
               side_effect=lambda msg: Candidate(msg.body, 'native_provider_article', True, msg.title or '', 30)):
        for code in ['DJ-N', 'DJ-RTE']:
            msg = sdk_message(code, body=None)
            # Old SDK adapter used the entire footer as the native body.
            msg = msg.model_copy(update={'body': '(MORE TO FOLLOW)' + FOOTER})
            capture(pipeline, 'ibkr_news', msg)
            await drain(pipeline)
    old = publications(pipeline)
    assert len(old) == 2
    capture(pipeline, 'ibkr_news', sdk_message('DJ-RTG', body=None))
    await drain(pipeline)
    assert publications(pipeline) == old
    assert repo.get_raw(old[0][0]).body == TITLE
    with repo._connect() as db:
        assert db.execute('SELECT article_key FROM live_origins WHERE origin_key=?',
            ('canonical:' + article_key(sdk_message(body=None), 'ibkr_news'),)).fetchone()[0] == repo.get_raw(old[0][0]).metadata['live_article_key']


async def test_short_complete_sdk_article_uses_real_text_and_preserves_provenance(pipeline):
    body = 'Intel says its quarterly results will be released after the close.'
    msg = sdk_message(body=body, language='')
    assert 'language' not in msg.metadata
    assert native_article(msg).text == body
    assert native_article(msg.model_copy(update={'body': 'Unrelated content'})) is None
    assert native_article(msg.model_copy(update={'raw_payload': {}})) is None
    capture(pipeline, 'ibkr_news', msg)
    await drain(pipeline)
    raw = pipeline[0].get_raw(publications(pipeline)[0][0])
    assert raw.body == body and raw.metadata['media_enrichment']['outcome'] == 'SHORT_FULL'
    assert not pipeline[2].calls


async def test_replayed_channels_and_runtime_restart_only_admit_one_case(pipeline, tmp_path):
    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    repo, _, _ = pipeline
    for code in DJ_CHANNELS:
        capture(pipeline, 'ibkr_news', sdk_message(code, body=None))
        await drain(pipeline)
    stream = repo.read_stream('MU', after_offset=0, limit=10)
    assert len(stream) == 1
    for _ in range(2):
        journal = RuntimeJournal(tmp_path / 'runtime.sqlite3')
        coordinator = RuntimeCoordinator(None, journal)
        coordinator.mode = lambda *_: ('OPEN', None)
        try:
            for item in stream:
                coordinator.accept_stream(item)
            assert len(journal.tasks(kind='CASE')) == 1
        finally:
            coordinator.close()


def test_sdk_plain_comparisons_are_not_parsed_as_html_twice():
    row = sdk_message().raw_payload
    msg = adapter_result({**row, 'headline': '{L:fr}' + TITLE,
        'articleText': '<p>Intel says revenue &lt; 15 billion and margin &gt; 20 percent.</p><p>(END)' + FOOTER + '</p>'}).messages[0]
    assert msg.metadata['language'] == 'fr'
    assert msg.body == 'Intel says revenue < 15 billion and margin > 20 percent.'
    assert native_article(msg).text == msg.body


def test_numeric_flash_is_not_a_punctuation_only_placeholder():
    msg = sdk_message(body=None, title='!* 0.25')
    assert msg.title == msg.body == '0.25'
    assert native_article(msg).method == 'native_provider_flash'
