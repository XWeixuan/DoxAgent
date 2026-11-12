"""Deterministic cross-source regressions on the production live publication path."""
from dataclasses import replace

import pytest

from tests.test_content_enrichment_hub import _setup, _message, _GovernedExtractor
from tests.test_message_bus_v2_deduplication import BODY
from doxagent.content_enrichment.service import ContentEnrichmentHub
from doxagent.message_bus_v2.live import LiveQueue, LiveDeliveryWorker
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.message_bus_v2 import deduplication as dedup
from doxagent.monitoring.media_enrichment import MediaExtractionResult


class Extractor(_GovernedExtractor):
    def __init__(self):
        self.calls = []
        self.blocked = False
        self.content = BODY
        self.final_url = None

    async def extract(self, record):
        self.calls.append(record.url)
        if self.blocked:
            return MediaExtractionResult(record=record, final_url=self.final_url, reason='http_403', diagnostics={'outcome': 'UNAVAILABLE'})
        return replace(await super().extract(record), content=self.content, final_url=self.final_url)


@pytest.fixture
def pipeline(tmp_path):
    repo, bus, _, _ = _setup(tmp_path)
    bus.live_queue = LiveQueue(bus)
    extractor = Extractor()
    return repo, bus, extractor


def input_message(index, url, *, body=None, metadata=None, title=None, publisher='Reuters'):
    return _message(index, body=body).model_copy(update={
        'url': url, 'title': title or 'Micron announces Idaho manufacturing investment',
        'publisher_name': publisher, 'metadata': metadata or {},
        'raw_payload': {'id': index, **({'body': body} if body else {})}})


def capture(pipeline, source_id, message):
    repo, bus, _ = pipeline
    source = bus.require_source(source_id)
    binding = repo.get_binding('MU:' + source_id)
    if not binding:
        binding = bus.configure_binding(ticker='MU', source_id=source_id,
            source_parameters={'search_terms': ['Micron']}, actor=UpdateActor.SYSTEM)
    live = bus.live_queue.repository
    rows = live.prepare_input(source, [(binding.binding_id, 'MU', 0)], [message],
                              observed_at=utc_now().isoformat(), unit='HEAD')
    live.accept(rows)
    return rows


async def drain(pipeline):
    repo, bus, extractor = pipeline
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    try:
        await hub.run_once()
        await LiveDeliveryWorker(bus).run_available()
    finally:
        await hub.close()


def publications(pipeline):
    with pipeline[0]._connect() as db:
        return [tuple(r) for r in db.execute('SELECT raw_message_id,standard_message_id FROM standard_messages')]


async def test_raw_input_with_published_candidate_has_source_context(pipeline):
    first = input_message(901, 'https://publisher.test/article/source-context', body=BODY)
    capture(pipeline, 'finnhub_company_news', first)
    await drain(pipeline)
    repo, bus, _ = pipeline
    binding = bus.configure_binding(ticker='MU', source_id='benzinga_news',
        source_parameters={'search_terms': ['Micron']}, actor=UpdateActor.SYSTEM)
    incoming = input_message(902, first.url, body=BODY)
    assert not hasattr(incoming, 'source_id')
    match, checked = repo.publication_match(binding, incoming)
    assert match is not None and checked


async def test_massive_known_publication_still_checks_terms_revision(pipeline):
    from doxagent.message_bus_v2.schema import AcquisitionMode
    from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
    from tests.test_massive_distribution import terms
    repo,bus,_=pipeline
    revision=terms(repo)
    capture(pipeline,'finnhub_company_news',input_message(903,'https://publisher.test/revision-check',body=BODY))
    await drain(pipeline)
    source=repo.get_source('finnhub_company_news').model_copy(update={'acquisition_mode':AcquisitionMode.MASSIVE_DISTRIBUTION})
    repo.save_source(source)
    live=bus.live_queue.repository
    row=live.deliveries('PUBLISHED')[0]
    worker=LiveDeliveryWorker(bus)
    message,_,binding,_=worker._prepare(row)
    prepared=live.prepare_publication(bus,row,source,binding,message)
    assert prepared['known'] and prepared['massive_regex']
    svc=MonitoringTermsService(repo)
    _,current=svc.get('MU')
    updated=current.model_dump(mode='json')
    updated.update(expected_revision=revision,definition={'relevant':'Updated memory market','irrelevant':'Other topics'})
    svc.apply(TickerMonitoringTerms.model_validate(updated),actor='test')
    with pytest.raises(RuntimeError,match='live_publication_terms_changed'):
        live.publish(prepared)


async def test_api_permalink_and_language_share_body_before_publication(pipeline):
    url = 'https://www.benzinga.com/markets/options/26/10/62752031/company-story'
    capture(pipeline, 'benzinga_news', input_message(1, url))
    await drain(pipeline)
    capture(pipeline, 'google_news_search_rss', input_message(2, url, metadata={
        'provider': 'google_news', 'language': 'en', 'identity_evidence': {'url_kind': 'article'}}))
    await drain(pipeline)
    assert len(publications(pipeline)) == 1
    assert len(pipeline[2].calls) == 1
    with pipeline[0]._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0] == 1


@pytest.mark.parametrize('distinct', ['url', 'tracking'])
async def test_exact_full_body_has_one_publication_across_urls(pipeline, distinct):
    first = 'https://publisher.test/article/one'
    second = first + '?.tsrc=rss' if distinct == 'tracking' else 'https://republisher.test/article/two'
    for i, (sid, url) in enumerate([('finnhub_company_news', first), ('benzinga_news', second)]):
        capture(pipeline, sid, input_message(i, url))
        await drain(pipeline)
    assert len(publications(pipeline)) == 1
    with pipeline[0]._connect() as db:
        fingerprint = db.execute('SELECT body_fingerprint FROM logical_message_versions').fetchone()[0]
    assert fingerprint == dedup.full_fingerprint(BODY)


async def test_late_native_full_upgrades_failed_article_without_second_stream(pipeline):
    url = 'https://www.benzinga.com/markets/options/26/10/62752031/company-story'
    pipeline[2].blocked = True
    rows = capture(pipeline, 'finnhub_company_news', input_message(1, url))
    await drain(pipeline)
    assert pipeline[1].live_queue.repository.get(rows[0]['key'])['outcome'] == 'UNAVAILABLE'
    old_ids = publications(pipeline)
    capture(pipeline, 'benzinga_news', input_message(2, url, body=BODY, metadata={'provider': 'benzinga'}))
    await drain(pipeline)
    row = pipeline[1].live_queue.repository.get(rows[0]['key'])
    assert row['outcome'] == 'FULL' and row['initial_outcome'] == 'UNAVAILABLE'
    assert publications(pipeline) == old_ids
    assert len(pipeline[2].calls) == 1  # Native article, no second download.
    assert pipeline[0].get_raw(old_ids[0][0]).body == BODY.strip()


@pytest.mark.parametrize('boundary', ['different_body', 'independent', 'private_scope', 'language', 'short'])
async def test_distinct_articles_and_rights_are_not_suppressed(pipeline, boundary):
    for i, sid in enumerate(['finnhub_company_news', 'benzinga_news']):
        metadata = {'identity_evidence': {'url_kind': 'article'}}
        if boundary == 'independent':
            metadata['identity_evidence']['independent_publication'] = True
        if boundary == 'private_scope':
            metadata['body_access_scope'] = 'private:' + str(i)
        if boundary == 'language':
            metadata['language'] = 'en' if i == 0 else 'fr'
        if boundary == 'short':
            pipeline[2].content = 'A complete short company statement. ' * 5
        elif boundary == 'different_body' and i:
            pipeline[2].content = BODY.replace('15 billion', '25 billion')
        capture(pipeline, sid, input_message(i, 'https://publisher.test/article/' + str(i), metadata=metadata))
        await drain(pipeline)
    assert len(publications(pipeline)) == 2


async def test_google_publisher_suffix_is_not_a_numeric_conflict(pipeline):
    title = 'Micron investment rises to 15 billion dollars'
    capture(pipeline, 'finnhub_company_news', input_message(1, 'https://247wallst.com/investing/story-one', title=title, publisher='24/7 Wall St.'))
    await drain(pipeline)
    capture(pipeline, 'google_news_search_rss', input_message(2, 'https://republisher.test/article/two',
        title=title + ' - 24/7 Wall St.', publisher='24/7 Wall St.',
        metadata={'provider': 'google_news', 'language': 'en', 'identity_evidence': {'url_kind': 'article'}}))
    await drain(pipeline)
    assert len(publications(pipeline)) == 1


async def test_cached_google_wrapper_does_not_create_body_owner(pipeline):
    url = 'https://www.benzinga.com/markets/options/26/10/62752031/company-story'
    capture(pipeline, 'benzinga_news', input_message(1, url, body=BODY, metadata={'provider': 'benzinga'}))
    await drain(pipeline)
    wrapper = 'https://news.google.com/rss/articles/already-resolved?oc=5'
    pipeline[0].polling.google_url(wrapper, url)
    capture(pipeline, 'google_news_search_rss', input_message(2, wrapper, metadata={
        'provider': 'google_news', 'language': 'en', 'canonical_resolution': 'pending_body',
        'identity_evidence': {'url_kind': 'article'}}))
    await drain(pipeline)
    assert len(publications(pipeline)) == 1 and not pipeline[2].calls


@pytest.mark.parametrize('recovers', [False, True])
async def test_new_wrapper_can_improve_failed_owner_and_failure_still_joins(pipeline, recovers):
    url = 'https://publisher.test/article/one'
    pipeline[2].blocked = True
    first = capture(pipeline, 'finnhub_company_news', input_message(1, url))
    await drain(pipeline)
    wrapper = 'https://news.google.com/rss/articles/new-wrapper'
    capture(pipeline, 'google_news_search_rss', input_message(2, wrapper, metadata={
        'provider': 'google_news', 'language': 'en', 'canonical_resolution': 'pending_body',
        'identity_evidence': {'url_kind': 'article'}}))
    pipeline[2].blocked = not recovers
    hub = ContentEnrichmentHub(pipeline[0], pipeline[1], extractor=pipeline[2])
    async def resolve(_): return url
    hub._google_url = resolve
    try:
        await hub.run_once()
        await LiveDeliveryWorker(pipeline[1]).run_available()
    finally:
        await hub.close()
    assert len(publications(pipeline)) == 1
    assert pipeline[1].live_queue.repository.get(first[0]['key'])['outcome'] == ('FULL' if recovers else 'UNAVAILABLE')
    with pipeline[0]._connect() as db:
        assert db.execute("SELECT count(*) FROM live_articles WHERE state='MERGED'").fetchone()[0] == 1


async def test_native_full_upgrades_a_resolved_failed_proxy_locator(pipeline):
    target = 'https://www.benzinga.com/markets/options/26/10/62752031/company-story'
    pipeline[2].blocked = True
    pipeline[2].final_url = target
    rows = capture(pipeline, 'finnhub_company_news', input_message(1, 'https://finnhub.io/api/news?id=one'))
    await drain(pipeline)
    capture(pipeline, 'benzinga_news', input_message(2, target, body=BODY, metadata={'provider': 'benzinga'}))
    await drain(pipeline)
    assert pipeline[1].live_queue.repository.get(rows[0]['key'])['url'] == target
    assert pipeline[0].get_raw(publications(pipeline)[0][0]).body == BODY.strip()
    assert len(publications(pipeline)) == 1 and len(pipeline[2].calls) == 1


async def test_real_body_update_keeps_standard_and_refreshes_pure_fingerprint(pipeline):
    url = 'https://www.benzinga.com/markets/options/26/10/62752031/company-story'
    capture(pipeline, 'benzinga_news', input_message(1, url, body=BODY, metadata={'provider': 'benzinga'}))
    await drain(pipeline)
    old = publications(pipeline)
    changed = BODY.replace('15 billion', '25 billion')
    capture(pipeline, 'benzinga_news', input_message(1, url, body=changed, metadata={'provider': 'benzinga'}))
    await drain(pipeline)
    assert publications(pipeline) == old
    assert pipeline[0].get_raw(old[0][0]).body == changed.strip()
    with pipeline[0]._connect() as db:
        assert db.execute('SELECT body_fingerprint FROM logical_message_versions').fetchone()[0] == dedup.full_fingerprint(changed)


async def test_pre_fix_published_api_without_url_alias_is_reconnected(pipeline):
    url = 'https://www.benzinga.com/markets/options/26/10/62752031/company-story'
    capture(pipeline, 'benzinga_news', input_message(1, url))
    await drain(pipeline)
    old = publications(pipeline)
    raw = pipeline[0].get_raw(old[0][0])
    with pipeline[0].transaction() as db:
        db.execute("DELETE FROM message_identity_aliases WHERE alias LIKE 'url:%'")
        db.execute("UPDATE raw_messages SET data_json=json_remove(data_json,'$.metadata.language','$.metadata.identity_evidence.url_kind') WHERE raw_message_id=?", (raw.raw_message_id,))
    capture(pipeline, 'google_news_search_rss', input_message(2, url, metadata={
        'provider': 'google_news', 'language': 'en', 'identity_evidence': {'url_kind': 'article'}}))
    await drain(pipeline)
    assert publications(pipeline) == old and len(pipeline[2].calls) == 1


async def test_concurrent_preparation_retries_only_local_publication(pipeline, monkeypatch):
    from contextlib import contextmanager
    from contextvars import ContextVar
    repo, bus, _ = pipeline
    for i, sid in enumerate(['finnhub_company_news', 'benzinga_news']):
        capture(pipeline, sid, input_message(i, 'https://publisher.test/article/' + str(i)))
    hub = ContentEnrichmentHub(repo, bus, extractor=pipeline[2])
    try:
        await hub.run_once()
    finally:
        await hub.close()
    live = bus.live_queue.repository
    worker = LiveDeliveryWorker(bus)
    rows = live.deliveries('PENDING')
    prepared = []
    for row in rows:
        msg, source, binding, _ = worker._prepare(row)
        prepared.append((row, msg, source, binding,
                         live.prepare_publication(bus, row, source, binding, msg)))
    writing = ContextVar('dedup_writer', default=False)
    transaction = repo.transaction
    @contextmanager
    def measured():
        with transaction() as db:
            token = writing.set(True)
            try: yield db
            finally: writing.reset(token)
    monkeypatch.setattr(repo, 'transaction', measured)
    read = repo.content.files.read_all
    def checked(*args):
        assert not writing.get(), 'Native read inside publication transaction'
        return read(*args)
    monkeypatch.setattr(repo.content.files, 'read_all', checked)
    assert live.publish(prepared[0][4]) is not False
    assert live.publish(prepared[1][4]) is False
    row, msg, source, binding, _ = prepared[1]
    await worker.publish(row, source, binding, msg)
    assert len(publications(pipeline)) == 1 and len(pipeline[2].calls) == 2


def test_only_proven_tracking_parameters_are_removed():
    assert dedup.article_url('https://247wallst.com/investing/story/?.tsrc=rss&id=7', 'article').endswith('?id=7')
    assert dedup.article_url('https://www.barrons.com/articles/story?mod=RTA&siteid=yhoof2&id=8', 'article').endswith('?id=8')
    assert dedup.article_url('https://publisher.test/article/story?id=8&.tsrc=edition', 'article').endswith('?.tsrc=edition&id=8')
    assert dedup.article_url('https://finnhub.io/api/news?id=8', 'article') is None


async def test_cross_source_replay_and_restart_leave_one_runtime_case(pipeline, tmp_path):
    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal

    for i, sid in enumerate(['finnhub_company_news', 'benzinga_news']):
        capture(pipeline, sid, input_message(i, 'https://publisher.test/article/' + str(i)))
        await drain(pipeline)
    repo = pipeline[0]
    with repo._connect() as db:
        ids = [r[0] for r in db.execute('SELECT stream_item_id FROM stream_items')]
    assert len(ids) == 1
    journal_path = tmp_path / 'runtime.sqlite3'
    for _ in range(2):
        journal = RuntimeJournal(journal_path)
        coordinator = RuntimeCoordinator(None, journal)
        coordinator.mode = lambda *_: ('OPEN', None)
        try:
            for identity in ids:
                coordinator.accept_stream(repo.get_stream_item(identity))
            tasks = journal.tasks(kind='CASE')
            assert len(tasks) == 1
            assert tasks[0]['inputs']['source']['source_message_id'] == publications(pipeline)[0][1]
        finally:
            coordinator.close()


async def test_shared_article_body_preserves_each_tickers_publication(pipeline):
    repo, bus, _ = pipeline
    msg = input_message(1, 'https://publisher.test/article/one')
    capture(pipeline, 'benzinga_news', msg)
    binding = bus.configure_binding(ticker='INTC', source_id='finnhub_company_news',
                                   actor=UpdateActor.SYSTEM)
    live = bus.live_queue.repository
    rows = live.prepare_input(bus.require_source(binding.source_id),
        [(binding.binding_id, 'INTC', 0)], [msg], observed_at=utc_now().isoformat(), unit='HEAD')
    live.accept(rows)
    await drain(pipeline)
    with repo._connect() as db:
        assert {r[0] for r in db.execute('SELECT ticker FROM stream_items')} == {'MU', 'INTC'}
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0] == 1
    assert len(pipeline[2].calls) == 1
