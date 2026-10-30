from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from doxagent.message_bus_v2 import yahoo_sources
from doxagent.message_bus_v2.admission import AdmissionContext, evaluate_admission
from doxagent.message_bus_v2.deduplication import stable_fingerprint
from doxagent.message_bus_v2.news_adapters import YahooFinanceNewsAdapter
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.message_bus_v2.yahoo_graphql import acquire, parse_stream, variables
from doxagent.settings import DoxAgentSettings
from tests.test_message_bus_v2_news_sources import _context
from tests.test_yahoo_browser_transport import make_transport


def payload():
    return {"data": {"lightyearList": {"main": {"stream": [{"asset": {
        "__typename": "Story", "id": "uuid", "title": "Intel production update",
        "contentAttributes": {"pubDate": datetime.now(UTC).isoformat(),
                              "canonicalUrl": "https://finance.yahoo.com/news/intel.html",
                              "summary": "Provider summary", "provider": {"displayName": "Reuters"}},
    }}], "pageInfo": {"count": 1, "nextPage": False}}}}}


async def test_graphql_is_independent_of_rss_timeout_and_rss_rate_limit(tmp_path):
    rss_started = threading.Event()
    release_rss = threading.Event()

    async def handler(request):
        if request.url.host.startswith('feeds.'):
            rss_started.set()
            while not release_rss.is_set():
                await asyncio.sleep(.01)
            return httpx.Response(429)
        assert request.url.host == 'nexus-finance-prod.media.yahoo.com'
        return httpx.Response(200, json=payload())

    transport, factories = make_transport(handler)
    context, _ = _context(tmp_path, 'yahoo_finance_news', {})
    context = context.model_copy(update={'requested_at': datetime.now(UTC)})
    try:
        async with httpx.AsyncClient() as client:
            adapter = YahooFinanceNewsAdapter(DoxAgentSettings(_env_file=None), client,
                                              transport=transport, browser=object())
            rss = asyncio.create_task(transport.request('GET', 'https://feeds.finance.yahoo.com/test',
                                                        rate_scope='rss'))
            assert await asyncio.to_thread(rss_started.wait, 2)
            result = await asyncio.wait_for(adapter.poll(context.model_copy(
                update={'acquisition_unit': 'GRAPHQL_HEAD'})), 1)
            assert len(result.messages) == 1 and not rss.done()
            assert result.messages[0].publication_time_basis == 'EXACT'
            assert result.messages[0].metadata['query_mode'] == 'graphql_ticker_stream'
            release_rss.set()
            with pytest.raises(RuntimeError):
                await rss
            # The RSS circuit cannot disable the GraphQL endpoint.
            rows, _ = await acquire(transport, 'INTC')
            assert rows[0]['id'] == 'uuid'
        assert len(factories) == 2
    finally:
        release_rss.set()
        transport.close()


@pytest.mark.parametrize('value', [{}, {'errors': [{'message': 'denied'}]},
                                  {'data': {'lightyearList': {'main': {'stream': []}}}}])
def test_graphql_bad_contract_never_becomes_empty_success(value):
    with pytest.raises(ValueError):
        parse_stream(value)


def test_graphql_typed_story_video_outlink_and_proven_empty():
    value = payload()
    main = value['data']['lightyearList']['main']
    main['stream'].append({'asset': {'__typename': 'Outlink', 'uuid': 'out', 'headline': 'Partner',
                                    'url': 'https://partner.test/story', 'displayTime': '2026-10-30T20:00:00Z'}})
    main['stream'].append({'asset': {**main['stream'][0]['asset'], '__typename': 'Video', 'id': 'video'}})
    assert [r['id'] for r in parse_stream(value)[0]] == ['uuid', 'out', 'video']
    main['stream'] = []
    main['pageInfo']['count'] = 0
    assert parse_stream(value)[0] == []
    assert variables('intc')['listInput']['queryVariables'] == {'tickerSymbol': ['INTC']}


@pytest.mark.parametrize('label,age,resolution', [('Reuters • 2m ago', 180, 60),
                                                ('3 minutes ago', 240, 60),
                                                ('10s ago', 11, 1), ('just now', 60, 60),
                                                ('2h ago', 10800, 3600)])
def test_relative_time_is_a_conservative_explicit_range(label, age, resolution):
    now = datetime.now(UTC)
    stamp, evidence = yahoo_sources.relative_publication(label, now)
    assert (now - stamp).total_seconds() == age
    assert evidence['resolution_seconds'] == resolution
    assert datetime.fromisoformat(evidence['earliest']) <= datetime.fromisoformat(evidence['latest'])


async def test_fresh_relative_cards_emit_without_article_access_and_do_not_enter_tail(monkeypatch):
    monkeypatch.setattr(yahoo_sources, '_ARTICLE_DATES', {})
    url = 'https://finance.yahoo.com/m/00000000-0000-0000-0000-000000000001/partner'
    card = dict(id='uuid', link=url, title='New release', relative_time='Publisher • 2m ago')
    page = SimpleNamespace(evaluate=lambda *a: pytest.fail('HEAD downloaded an article'))
    rows, metadata = await yahoo_sources.capture_timestamp_rows(page, 'MU', [card], max_probes=0)
    assert rows[0]['publication_time_basis'] == 'RELATIVE'
    assert metadata['pending_timestamp_rows'] == []
    assert metadata['verified_article_dates'] == {} and yahoo_sources._ARTICLE_DATES == {}
    assert evaluate_admission(datetime.fromisoformat(rows[0]['pubDate']), None, datetime.now(UTC), 'RELATIVE') is None
    # The same approximate locator must not provide exact sweep membership.
    context = AdmissionContext(mode='CLOSED_SWEEP', sweep_id='s', source_task_id='t',
                               window_start=datetime.now(UTC)-timedelta(hours=1), cutoff=datetime.now(UTC))
    assert evaluate_admission(datetime.fromisoformat(rows[0]['pubDate']), context,
                              datetime.now(UTC), 'RELATIVE') == 'UNRESOLVED_PUBLICATION_TIME'


async def test_stale_cached_relative_html_does_not_reset_article_age(monkeypatch):
    monkeypatch.setattr(yahoo_sources, '_ARTICLE_DATES', {})
    card = dict(link='https://finance.yahoo.com/news/story.html', relative_time='2m ago',
                listing_observed_at=(datetime.now(UTC)-timedelta(hours=1)).isoformat())
    rows, metadata = await yahoo_sources.capture_timestamp_rows(object(), 'MU', [card], max_probes=0)
    assert rows == [] and metadata['pending_timestamp_rows'] == []
    assert metadata['expired_relative_cards'] == 1


async def test_html_parser_includes_wrapper_links_and_publishing_time(monkeypatch):
    from doxagent.site_strategy.origin_listing import yahoo_rows

    monkeypatch.setattr(yahoo_sources, '_ARTICLE_DATES', {})
    html = '''<section data-testid="storyitem"><a data-yga='{"yModuleName":"qsp-news","yDestinationContentUUID":"uuid"}'
    href="/m/00000000-0000-0000-0000-000000000001/story"><h3>New headline</h3></a>
    <div class="publishing yf-123">Reuters <i>•</i> 3m ago</div></section>'''
    rows, metadata = await yahoo_rows(object(), html, 'MU', {'max_probes': 0})
    assert rows[0]['id'] == 'uuid' and rows[0]['publication_time_basis'] == 'RELATIVE'
    assert metadata['relative_time_rows'] == 1


def test_relative_clock_drift_does_not_create_new_business_fingerprint():
    message = RawMessageInput(title='News', url='https://finance.yahoo.com/news/story.html',
                              external_id='uuid', published_at=datetime.now(UTC),
                              publication_time_basis='RELATIVE', raw_payload={})
    assert stable_fingerprint(message) == stable_fingerprint(message.model_copy(
        update={'published_at': message.published_at+timedelta(seconds=37)}))


async def test_collector_schedules_three_independent_lanes(tmp_path):
    from doxagent.message_bus_v2.collection_plane.store import required_unit
    from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
    from tests.test_polling_cadence_pipeline import _setup

    repo, bus, _, _ = _setup(tmp_path)
    source = repo.get_source('yahoo_finance_news')
    binding = repo.get_binding('MU:yahoo_finance_news')
    planner = GlobalPollScheduler(repo, bus, SimpleNamespace())
    units = list(planner.work_units(source, binding, repo.get_poll_state(binding), datetime.now(UTC), include_all=True))
    assert {o['acquisition_unit'] for _, o in units} >= {'HEAD', 'SUPPLEMENT_HEAD', 'GRAPHQL_HEAD'}
    assert planner._limiter(source, 'HEAD') is not planner._limiter(source, 'GRAPHQL_HEAD')
    assert planner._limiter(source, 'SUPPLEMENT_HEAD') is not planner._limiter(source, 'GRAPHQL_HEAD')
    assert not required_unit(source.source_id, 'TAIL:YAHOO_TIME')
    assert required_unit(source.source_id, 'GRAPHQL_HEAD')


async def test_parallel_path_install_inherits_running_ticker_bootstrap(tmp_path):
    from doxagent.message_bus_v2.collection_plane.config import Configuration
    from tests.test_message_collection_plane import setup_plane

    repo, bus, worker, adapter, store = setup_plane(tmp_path)
    source = repo.get_source('yahoo_finance_news')
    binding = repo.get_binding('MU:yahoo_finance_news')
    cfg = Configuration(sources={source.source_id: source}, bindings={binding.binding_id: binding},
                        seeds={}, realtime=True)
    worker.config_loader = lambda *a, **kw: cfg
    for unit in ('HEAD', 'SUPPLEMENT_HEAD'):
        store.save_unit(source, binding, unit, {'bootstrap_complete': True}, 'old')
    await worker.refresh(seeds=True)
    assert worker.states[(binding.binding_id, 'GRAPHQL_HEAD')]['state']['bootstrap_complete']


async def test_body_replaces_relative_time_with_exact_without_second_stream_message(tmp_path):
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.message_bus_v2.schema import PollResult
    from doxagent.monitoring.media_enrichment import MediaExtractionResult
    from tests.test_content_enrichment_hub import _setup

    repo, bus, _, _ = _setup(tmp_path)
    source = repo.get_source('yahoo_finance_news')
    binding = repo.get_binding('MU:yahoo_finance_news')
    repo.save_poll_state(repo.get_poll_state(binding).model_copy(update={'bootstrap_complete': True}))
    stamp = datetime.now(UTC)-timedelta(minutes=2)
    message = RawMessageInput(external_id='relative-body', title='Micron production expansion',
                               url='https://finance.yahoo.com/news/body-test.html',
                               published_at=stamp-timedelta(seconds=30), publication_time_basis='RELATIVE',
                               raw_payload={}, metadata={'publication_time_precision': 'relative'})

    class Extractor:
        calls = 0

        async def extract(self, record):
            self.calls += 1
            return MediaExtractionResult(record=record, content='Confirmed article body', final_url=record.url,
                                          diagnostics={'publisher_published_at': stamp.isoformat()})

    extractor = Extractor()
    hub = ContentEnrichmentHub(repo, bus, extractor=extractor)
    await bus.accept_poll_result(source=source, binding=binding,
                                  result=PollResult(messages=[message]))
    await hub.run_once()
    raw = repo.list_raw(ticker='MU')[0]
    assert raw.publication_time_basis == 'EXACT' and raw.published_at == stamp
    assert raw.metadata['publication_time_precision'] == 'second'
    await bus.accept_poll_result(source=source, binding=binding,
                                  result=PollResult(messages=[message.model_copy(
                                      update={'published_at': message.published_at+timedelta(seconds=20)})]))
    await hub.run_once()
    assert extractor.calls == 1
    assert repo.latest_stream_offset('MU') == 1


def test_native_uuid_unifies_parallel_rss_wrapper_and_graphql_before_body_without_hint_flapping(tmp_path):
    from doxagent.message_bus_v2.live import LiveQueue
    from tests.test_content_enrichment_hub import _setup

    repo, bus, _, _ = _setup(tmp_path)
    source = repo.get_source('yahoo_finance_news')
    binding = repo.get_binding('MU:yahoo_finance_news')
    live = LiveQueue(bus).repository
    uid = '00000000-0000-0000-0000-000000000001'
    direct = RawMessageInput(external_id=uid, title='Micron expansion', summary='GraphQL summary',
                              url='https://finance.yahoo.com/news/micron.html', raw_payload={},
                              published_at=datetime.now(UTC), metadata={'identity_evidence': {'id_kind': 'stable_article'}})
    wrapped = direct.model_copy(update={'url': f'https://finance.yahoo.com/m/{uid}/micron.html',
                                       'summary': 'Different RSS description'})

    def prepare(message, unit):
        return live.prepare_input(source, [(binding.binding_id, 'MU', 0)], [message],
                                  observed_at=datetime.now(UTC).isoformat(), unit=unit)

    # Both prepares happen before the first commit, as in parallel network paths.
    a, b = prepare(direct, 'GRAPHQL_HEAD'), prepare(wrapped, 'HEAD')
    assert live.accept(a) == live.accept(b)
    for _ in range(3):
        live.accept(prepare(direct, 'GRAPHQL_HEAD'))
        live.accept(prepare(wrapped, 'HEAD'))
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles').fetchone()[0] == 1
        assert db.execute('SELECT input_revision FROM live_articles').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM live_deliveries').fetchone()[0] == 1


def test_native_browser_graphql_filters_ticker_and_reads_current_edges():
    import json
    from doxagent.message_bus_v2.yahoo_sources import latest_news_response

    response = SimpleNamespace(url='https://nexus-finance-prod.media.yahoo.com/',
                               request=SimpleNamespace(post_data=json.dumps({
                                   'query': 'query FinancePolarisTickerListNews($x:Int){x}',
                                   'variables': {'listInput': {'queryVariables': {'tickerSymbol': ['MU']}}}})))
    response.request.url = response.url
    assert latest_news_response(response, 'MU')
    assert not latest_news_response(response, 'INTC')
    value = payload()
    main = value['data']['lightyearList']['main']
    main['edges'] = [{'node': node} for node in main.pop('stream')]
    main['pagination'] = {'nextPage': True, 'endCursor': 'native-cursor'}
    main.pop('pageInfo')
    assert parse_stream(value)[0][0]['id'] == 'uuid'


@pytest.mark.parametrize('name', ['legacy-stream.json', 'native-edges.json'])
def test_real_public_graphql_shapes(name):
    import json
    from pathlib import Path

    fixture = Path(__file__).parent / 'fixtures' / 'yahoo_realtime' / name
    rows, page = parse_stream(json.loads(fixture.read_text(encoding='utf-8')))
    assert len(rows) == 2 and page['nextPage']
    assert all(row['id'] and row['pubDate'] and row['link'].startswith('https://') for row in rows)
