from contextlib import closing
from doxagent.v2_read.source_lifecycle import expire
from tests.v2_backend.test_source_lifecycle import fixture_source


def test_expiry_queries_only_page_pins_without_hot_migration(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    source.acknowledge_exceptions('live', 6, [2])
    sql = []
    original = source.connection

    def connection(**kw):
        db = original(**kw)
        db.set_trace_callback(sql.append)
        return db

    monkeypatch.setattr(source, 'connection', connection)
    report = expire(source, limit=2, hot_only=True)
    assert report['scanned'] == 2 and report['expired'] == 2
    pin_reads = [q for q in sql if q.startswith('SELECT DISTINCT seq FROM v2_source_exception_pins')]
    assert len(pin_reads) == 2 and all('json_each' in q for q in pin_reads)
    assert not any('CREATE ' in q.upper() for q in sql)
    assert source.event(2) is not None


def test_new_pin_after_planning_defers_without_skipping_or_losing_receipt(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    source.acknowledge_exceptions('live', 6, [])
    original = source.connection
    injected = []

    def connection(**kw):
        if kw.get('write') and not injected:
            injected.append(True)
            with closing(original(write=True)) as db, db:
                db.execute("INSERT INTO v2_source_exception_pins VALUES('other',1)")
        return original(**kw)

    monkeypatch.setattr(source, 'connection', connection)
    assert expire(source, hot_only=True)['expired'] == 0
    assert source.event(1) is not None
    assert expire(source, hot_only=True)['expired'] == 6
    assert source.event(1) is not None
