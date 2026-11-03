"""Late gap recovery must settle counts from the current message image."""
from decimal import Decimal

from doxagent.api_v2.bus_metrics import counts, source_counts_today
from doxagent.v2_read.repository import ReadStore


def message():
    return dict(kind='message', ticker='INTC', id='m', day='2026-10-25',
                source_id='google_news_search_rss', route='ARCHIVE',
                data={'source': {'kind': 'NEWS'},
                      'stream_published_at': '2026-10-25T13:39:00Z',
                      'pipeline': {'discovered_at': '2026-10-25T13:38:50Z'}})


def orphan_link():
    return dict(kind='message_raw_link', ticker='INTC', id='raw', parent='m', data={})


def test_older_gap_recovery_counts_message_after_newer_raw_link(tmp_path):
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    before = store.ingest('bus', 'link:20', [orphan_link()], position=20)
    with store.connect(write=True) as db:
        # Retain a watermark written by the old projector before this fix.
        db.execute("INSERT OR REPLACE INTO contribution_watermarks "
                   "VALUES('bus','bus_messages','INTC','m',20)")
    after = store.ingest('bus', 'message:10:repair:1', [message()], position=10)

    assert counts(store, 'INTC', after, {}, ['2026-10-25'])[0] == Decimal(1)
    assert source_counts_today(store, 'INTC', after, '2026-10-25')[
        'google_news_search_rss']['value'] == 1
    assert counts(store, 'INTC', before, {}, ['2026-10-25'])[0] == Decimal(0)
    assert store.position('bus') == 20

    # Older source data still cannot overwrite the current message.
    store.ingest('bus', 'stale-message:9', [{**message(), 'day': '2026-10-24'}], position=9)
    assert counts(store, 'INTC', after, {}, ['2026-10-25'])[0] == Decimal(1)


def test_older_message_change_and_delete_settle_newer_body_counts(tmp_path):
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    original = store.ingest('bus', 'message:10', [message()], position=10)
    body = dict(kind='body_attempt', ticker='INTC', id='attempt', parent='raw',
                day='2026-10-25', data={'succeeded': True})
    store.ingest('bus', 'body:30', [orphan_link(), body], position=30)
    corrected = store.ingest('bus', 'message:20:repair:1',
                             [{**message(), 'route': 'TRADE'}], position=20)
    assert counts(store, 'INTC', corrected, {'route': 'ARCHIVE'}, None)[0] == 0
    assert counts(store, 'INTC', corrected, {'route': 'TRADE'}, None) == (Decimal(1), Decimal(1))
    assert counts(store, 'INTC', original, {'route': 'ARCHIVE'}, None)[0] == 1
    deleted = store.ingest('bus', 'message:25:repair:1',
                           [dict(kind='message', ticker='INTC', id='m', data=None)], position=25)
    assert counts(store, 'INTC', deleted, {}, None) == (Decimal(0), None)
    assert store.position('bus') == 30


def test_targeted_refresh_preserves_message_and_is_idempotent(tmp_path, monkeypatch):
    from doxagent.v2_read import bus_aggregates

    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    with monkeypatch.context() as patch:
        patch.setattr(bus_aggregates, 'contributions', lambda db, members: [])
        original = store.ingest('bus', 'legacy-missing-count', [message()], position=10)
    with store.connect() as db:
        before_message = tuple(db.execute(
            "SELECT * FROM object_current WHERE kind='message'").fetchone())
    assert counts(store, 'INTC', original, {}, None)[0] == 0

    repaired = store.ingest('bus-count-repair', 'one', [], refresh_bus_members=[('INTC', 'm')])
    assert repaired > original
    assert counts(store, 'INTC', repaired, {}, None)[0] == 1
    assert counts(store, 'INTC', original, {}, None)[0] == 0
    assert store.ingest('bus-count-repair', 'two', [],
                        refresh_bus_members=[('INTC', 'm')]) == repaired
    assert store.position('bus') == 10
    with store.connect() as db:
        assert tuple(db.execute(
            "SELECT * FROM object_current WHERE kind='message'").fetchone()) == before_message
        assert db.execute("SELECT count(*) FROM contributions_current "
                          "WHERE metric='bus_messages'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM changes WHERE seq=?",
                          (repaired,)).fetchone()[0] == 0
