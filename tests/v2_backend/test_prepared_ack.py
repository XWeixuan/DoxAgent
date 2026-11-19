from contextlib import closing
from tests.v2_backend.test_source_lifecycle import fixture_source


def test_prepared_ack_uses_delta_without_ddl_or_global_pin_scan(tmp_path, monkeypatch):
    source = fixture_source(tmp_path)
    source.acknowledge_exceptions('live', 2, [1])
    state = source.acknowledge_prepared('live', 3, [1])
    sql = []
    original = source.connection

    def connection(**kw):
        db = original(**kw)
        db.set_trace_callback(sql.append)
        return db

    monkeypatch.setattr(source, 'connection', connection)
    state = source.acknowledge_prepared('live', 6, [2], state)
    assert not any('CREATE ' in q.upper() for q in sql)
    assert not any('SELECT seq FROM v2_source_exception_pins' in q for q in sql)
    with closing(original()) as db:
        assert [r[0] for r in db.execute('SELECT seq FROM v2_source_exception_pins')] == [2]
        assert db.execute('SELECT checkpoint FROM v2_source_consumers').fetchone()[0] == 6


def test_changed_ack_metadata_reloads_pins_before_committing(tmp_path):
    source = fixture_source(tmp_path)
    state = source.acknowledge_prepared('live', 2, [1])
    source.acknowledge_exceptions('live', 5, [2, 3])
    source.acknowledge_prepared('live', 6, [4], state)
    with closing(source.connection()) as db:
        assert [r[0] for r in db.execute('SELECT seq FROM v2_source_exception_pins')] == [4]


def test_borrowed_projection_connection_releases_snapshot_and_reloads(tmp_path):
    source = fixture_source(tmp_path)
    with source.projection_session():
        state = source.acknowledge_prepared('live', 2, [1])
        assert not source.connection().in_transaction
        source.acknowledge_exceptions('live', 5, [2, 3])
        source.acknowledge_prepared('live', 6, [4], state)
        assert not source.connection().in_transaction
        with closing(source.connection()) as db:
            assert [r[0] for r in db.execute('SELECT seq FROM v2_source_exception_pins')] == [4]
