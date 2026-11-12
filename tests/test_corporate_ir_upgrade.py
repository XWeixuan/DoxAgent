from doxagent.message_bus_v2.corporate_ir import SOURCE_ID
from doxagent.message_bus_v2.live import LiveQueue
from doxagent.message_bus_v2.schema import RawMessageInput, UpdateActor, utc_now
from scripts.ops.upgrade_corporate_ir_20261113 import upgrade, seed_onds_aliases
from scripts.ops.configure_corporate_ir_sources import TICKERS
from tests.fixtures.message_bus_v2 import _bus


def test_upgrade_preserves_every_existing_cadence_and_is_idempotent(tmp_path):
    repo, bus = _bus(tmp_path/'bus.db')
    source = repo.get_source(SOURCE_ID)
    endpoints = [e for e in source.default_parameters['endpoints'] if e['endpoint_id'] != 'ionq.press']
    for e in endpoints:
        if e['endpoint_id'] == 'onds.press':
            e['parser_parameters'] = {}; e['revision'] = 2
        if e['endpoint_id'] == 'snow.press':
            e.update(entry_url='https://www.snowflake.com/en/news/press-releases/', method='html', parser_ref='snowflake', revision=2)
    bus.update_source(SOURCE_ID, {'default_parameters': {'endpoints': endpoints}}, actor=UpdateActor.USER)
    for ticker in TICKERS:
        bus.start_ticker(ticker)
        if ticker != 'IONQ':
            ids = [e['endpoint_id'] for e in endpoints if e['owner_ticker'] == ticker]
            bus.configure_binding(ticker=ticker, source_id=SOURCE_ID, source_parameters={'endpoint_ids': ids},
                                  polling={'target_interval_seconds': 30 if ticker == 'BE' else 20}, actor=UpdateActor.USER)
    live = LiveQueue(bus)
    live.repository.install()
    before = {b.binding_id: b.polling for b in repo.list_bindings(source_id=SOURCE_ID)}
    result = upgrade(bus)
    assert result['endpoint_count'] == 13 and len(result['bindings']) == 10
    assert repo.get_binding('IONQ:'+SOURCE_ID).polling.target_interval_seconds == 20
    assert all(repo.get_binding(k).polling == v for k,v in before.items())
    version = repo.get_source(SOURCE_ID).version
    upgrade(bus)
    assert repo.get_source(SOURCE_ID).version == version


def test_onds_old_owner_survives_rss_and_html_url_aliases(tmp_path):
    repo, bus = _bus(tmp_path/'bus.db')
    bus.start_ticker('ONDS')
    binding = bus.configure_binding(ticker='ONDS', source_id=SOURCE_ID,
        source_parameters={'endpoint_ids': ['onds.press']}, actor=UpdateActor.USER)
    queue = LiveQueue(bus); source = repo.get_source(SOURCE_ID)
    message = RawMessageInput(title='Ondas announces a defense supply agreement',
        url='https://ir.ondas.com/news/detail/341/old-long-slug',
        published_at=utc_now(), publication_time_basis='EXACT', raw_payload={})
    rows = queue.repository.prepare_input(source, [(binding.binding_id,'ONDS',0)], [message],
                                         observed_at=utc_now().isoformat(),unit='IR:onds.press')
    queue.repository.accept(rows)
    key = rows[0]['key']; old_key = 'legacy-onds-owner'
    with repo.transaction() as db:
        db.execute("UPDATE live_articles SET article_key=?,state='READY',outcome='FULL',body_digest='full' WHERE article_key=?", (old_key,key))
        db.execute('UPDATE live_deliveries SET article_key=? WHERE article_key=?',(old_key,key))
        db.execute('UPDATE live_origins SET article_key=? WHERE article_key=?',(old_key,key))
        db.execute('DELETE FROM live_origins WHERE origin_key=?',('canonical:'+key,))
    seed_onds_aliases(repo)
    html = message.model_copy(update={'url':'https://ir.ondas.com/press-releases/detail/341/short'})
    repeated = queue.repository.prepare_input(source,[(binding.binding_id,'ONDS',0)], [html],
                                              observed_at=utc_now().isoformat(),unit='IR:onds.press')
    assert repeated[0]['key'] == old_key
    queue.repository.accept(repeated)
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM live_articles WHERE source_id=?',(SOURCE_ID,)).fetchone()[0] == 1
