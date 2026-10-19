from pathlib import Path
import gzip

from doxagent.v2_read.backup_retention import complete_migration_backup


def test_success_retires_only_previous_completed_matching_migration(tmp_path):
    originals = {'runtime': tmp_path/'business/runtime.sqlite3'}
    parent = tmp_path/'backups'
    first = parent/'first'
    first.mkdir(parents=True)
    (first/'runtime.sqlite3').write_bytes(b'first rollback')
    complete_migration_backup(first, originals)
    partial = parent/'failed-migration'
    partial.mkdir()
    (partial/'runtime.sqlite3').write_bytes(b'needed for resume')
    other = parent/'other-database-set'
    other.mkdir()
    complete_migration_backup(other, {'bus': tmp_path/'business/bus.sqlite3'})
    current = parent/'current'
    current.mkdir()
    (current/'runtime.sqlite3').write_bytes(b'latest rollback')
    report = complete_migration_backup(current, originals)
    assert report['retired'] == [str(first)]
    assert not first.exists()
    assert not (current/'runtime.sqlite3').exists()
    assert gzip.decompress((current/'runtime.sqlite3.gz').read_bytes()) == b'latest rollback'
    assert (partial/'runtime.sqlite3').read_bytes() == b'needed for resume'
    assert other.is_dir()
    assert complete_migration_backup(current, originals)['retired'] == []


def test_refuses_backup_parent_containing_originals(tmp_path):
    import pytest
    current = tmp_path/'backups/current'
    current.mkdir(parents=True)
    with pytest.raises(ValueError, match='business originals'):
        complete_migration_backup(current, {'runtime': current.parent/'live.sqlite3'})


def test_compression_failure_preserves_previous_rollback_and_current_source(tmp_path):
    from doxagent.v2_read.backup_retention import MARKER
    originals = {'runtime': tmp_path/'business/runtime.sqlite3'}
    first = tmp_path/'backups/first'
    first.mkdir(parents=True)
    (first/'runtime.sqlite3').write_bytes(b'previous')
    complete_migration_backup(first, originals)
    current = tmp_path/'backups/current'
    current.mkdir()
    (current/'runtime.sqlite3').write_bytes(b'current')
    (current/'runtime.sqlite3.gz.partial').write_bytes(b'incomplete')
    report = complete_migration_backup(current, originals)
    assert report['errors'] and not report['retired']
    assert first.exists() and not (current/MARKER).exists()
    assert (current/'runtime.sqlite3').read_bytes() == b'current'


def test_backup_estimate_includes_wal_pages_and_immutable_files(tmp_path):
    import sqlite3
    from contextlib import closing
    from doxagent.v2_read.backup_retention import estimate_migration_backup
    path = tmp_path/'business/runtime.sqlite3'
    path.parent.mkdir()
    content = path.parent/'native-files/digest/chunk.gz'
    content.parent.mkdir(parents=True)
    content.write_bytes(b'compressed fixture')
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE business(value BLOB)')
        db.execute('INSERT INTO business VALUES(?)',(b'x'*20000,))
        db.commit()
        logical = db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]
        assert logical > path.stat().st_size
        assert estimate_migration_backup({'runtime': path}) == logical+content.stat().st_size


def test_compression_resume_keeps_already_finished_database_in_coherent_manifest(tmp_path,monkeypatch):
    import json
    from doxagent.v2_read import backup_retention as retention
    originals={'a':tmp_path/'business/a.sqlite3','b':tmp_path/'business/b.sqlite3'}
    current=tmp_path/'backups/current';current.mkdir(parents=True)
    (current/'a.sqlite3').write_bytes(b'database a');(current/'b.sqlite3').write_bytes(b'database b')
    original=retention.gzip.GzipFile;calls=0
    def fail_second(*args,**kwargs):
        nonlocal calls
        if kwargs.get('mode')=='wb':
            calls+=1
            if calls==2:raise OSError('interrupted after the first database')
        return original(*args,**kwargs)
    monkeypatch.setattr(retention.gzip,'GzipFile',fail_second)
    assert complete_migration_backup(current,originals)['errors']
    assert not (current/'a.sqlite3').exists() and (current/'b.sqlite3').exists()
    monkeypatch.setattr(retention.gzip,'GzipFile',original)
    assert not complete_migration_backup(current,originals)['errors']
    manifest=json.loads((current/retention.MARKER).read_text())
    assert {x['file'] for x in manifest['databases']}=={'a.sqlite3.gz','b.sqlite3.gz'}
