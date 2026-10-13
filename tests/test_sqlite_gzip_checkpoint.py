import gzip
import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('gzip_checkpoint', Path(__file__).parents[1] / 'scripts/ops/sqlite_gzip_checkpoint.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_checkpoint_is_extracted_from_verified_backup_and_schema_root(tmp_path):
    source = tmp_path / 'source.db'
    with sqlite3.connect(source) as db:
        for n in range(150):
            db.execute(f'CREATE TABLE example_{n}(id INTEGER PRIMARY KEY, payload TEXT)')
        db.execute('CREATE TABLE v2_source_state(source TEXT PRIMARY KEY, epoch TEXT NOT NULL, head INTEGER NOT NULL, retained_floor INTEGER NOT NULL)')
        db.execute("INSERT INTO v2_source_state VALUES('bus','abcdef0123456789',2271999,0)")
    raw = source.read_bytes()
    archive = tmp_path / 'source.gz'
    archive.write_bytes(gzip.compress(raw))
    expected = dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    target = tmp_path / 'checkpoint.db'
    result = module.extract(archive, expected, source='bus', epoch='abcdef0123456789', destination=target)
    assert result['head'] == 2271999
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT * FROM v2_source_state').fetchone() == ('bus', 'abcdef0123456789', 2271999, 0)
    expected['sha256'] = 'wrong'
    with pytest.raises(ValueError, match='uncompressed'):
        module.extract(archive, expected, source='bus', epoch='abcdef0123456789', destination=tmp_path / 'bad.db')
