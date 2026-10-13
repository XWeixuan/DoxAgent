"""Verify compressed rollback/retirement on disposable Linux SQLite fixtures."""
import gzip
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from doxagent.v2_read.backup_retention import complete_migration_backup

with tempfile.TemporaryDirectory(prefix='disk-repair-p4-') as folder:
    root=Path(folder)
    originals={'runtime':root/'business/runtime.sqlite3'}
    first=root/'backups/first'
    latest=root/'backups/latest'
    unknown=root/'backups/unfinished'
    for path in [first,latest,unknown]:
        path.mkdir(parents=True)
    for path,value in [(first,1),(latest,42)]:
        with closing(sqlite3.connect(path/'runtime.sqlite3')) as db:
            db.execute('CREATE TABLE business(value INTEGER)')
            db.execute('INSERT INTO business VALUES(?)',(value,))
            db.commit()
    complete_migration_backup(first,originals)
    report=complete_migration_backup(latest,originals)
    assert not first.exists() and unknown.exists() and not report['errors']
    saved=root/'restored.sqlite3'
    saved.write_bytes(gzip.decompress((latest/'runtime.sqlite3.gz').read_bytes()))
    with closing(sqlite3.connect(saved)) as db:
        assert db.execute('PRAGMA quick_check').fetchall()==[('ok',)]
        assert db.execute('SELECT value FROM business').fetchone()[0]==42
    print(json.dumps(dict(verified=True,compressed_restore='ok',superseded_removed=True,
                         unfinished_retained=True)),flush=True)
