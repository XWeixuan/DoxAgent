import json
import sqlite3
from doxagent.v2_read.content_files import ContentFiles
from doxagent.v2_read.cli import backup


def test_backup_excludes_native_orphans_and_keeps_nested_receipt_refs(tmp_path):
    root=tmp_path/'source';root.mkdir();path=root/'source.db'
    files=ContentFiles(root/'native-files')
    used=files.put(json.dumps({'evidence':'unique'}).encode())
    orphan=files.put(b'{"discarded":"unused"}')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE business(id PRIMARY KEY,payload)')
        inner=json.dumps({'$doxagent_content_v1':used,'bytes':len(json.dumps({'evidence':'unique'}).encode())})
        db.execute('INSERT INTO business VALUES(1,?)',(json.dumps({'payload':inner}),))
    target=tmp_path/'backup/source.db';backup(path,target)
    assert (target.parent/'native-files'/used[:2]/used/'manifest.json').is_file()
    assert not (target.parent/'native-files'/orphan[:2]/orphan).exists()


def test_failed_file_copy_does_not_publish_a_resumable_database(tmp_path,monkeypatch):
    import pytest
    root=tmp_path/'source';root.mkdir();path=root/'source.db'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE business(id PRIMARY KEY,payload)')
    (root/'native-files').mkdir()
    def fail(*args):raise OSError('copy interrupted')
    monkeypatch.setattr(ContentFiles,'copy',fail)
    target=tmp_path/'backup/source.db'
    with pytest.raises(OSError,match='interrupted'):backup(path,target)
    assert not target.exists() and not list(target.parent.glob('*.pending'))
