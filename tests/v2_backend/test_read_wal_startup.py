import sqlite3
from contextlib import closing

import pytest
from doxagent.v2_read.repository import ReadStore


def test_existing_v4_migration_and_startup_restore_wal(tmp_path):
    store = ReadStore(tmp_path/'read.db')
    store.migrate()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
    store.migrate()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        db.execute('PRAGMA journal_mode=DELETE')
    store.ensure_wal()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_startup_guard_does_not_create_missing_database(tmp_path):
    store = ReadStore(tmp_path/'missing.db')
    with pytest.raises(sqlite3.OperationalError):
        store.ensure_wal()
    assert not store.path.exists()
