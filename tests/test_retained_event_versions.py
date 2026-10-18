import json
import pytest
from doxagent.event_library.repository import EventLibraryRepository,UnknownLibraryObjectError
from doxagent.persistent_runtime_v2.retained_events import build,snapshot_digest
from doxagent.persistent_runtime_v2.event_branch import branch_library
from tests.test_codex_event_library_incremental import _publish_v1


def test_historical_branch_keeps_exact_versions_births_and_rewind_maps(tmp_path):
    source=EventLibraryRepository(tmp_path/'source.db');_publish_v1(source,tmp_path/'fixture')
    with source._write() as db:
        original=db.execute('SELECT * FROM canonical_event_revisions LIMIT 1').fetchone()
        for version in (2,3):
            payload=json.loads(original['payload_json']);payload['known_event_summary']='retained version '+str(version)
            db.execute('INSERT INTO canonical_event_revisions VALUES(?,?,?,?,?)',('MU',original['event_no'],version,version,json.dumps(payload)))
            db.execute("INSERT INTO library_versions VALUES('MU',?,?, '[]','PUBLISHED','2026-01-02','2026-01-02T00:00:00+00:00')",(version,version-1))
        db.execute("UPDATE library_heads SET published_version=3 WHERE ticker='MU'")
        db.execute('CREATE TABLE v2_source_receipts(id PRIMARY KEY,payload)');db.execute('INSERT INTO v2_source_receipts VALUES(1,?)',('x'*1000000,))
    report=build(source.path,tmp_path/'native.db','MU',[1])
    target=EventLibraryRepository(tmp_path/'native.db',read_only=True)
    assert report['verified'] and report['versions']==[1,3]
    assert report['target_bytes']<report['source_bytes']
    for version in (1,3):assert snapshot_digest(source,'MU',version)==snapshot_digest(target,'MU',version)
    with target._read() as db:
        assert not db.execute('SELECT 1 FROM delta_items').fetchone()
        assert db.execute('SELECT count(*) FROM retired_delta_receipts').fetchone()[0]==1
        mapping=db.execute('SELECT * FROM runtime_atomic_mappings LIMIT 1').fetchone()
    rewind=branch_library(target.path,tmp_path/'rewind.db','MU',1)
    assert rewind.runtime_atomic_signature(mapping['runtime_scope'],mapping['runtime_atomic_id'])==source.runtime_atomic_signature(mapping['runtime_scope'],mapping['runtime_atomic_id'])
    with pytest.raises(UnknownLibraryObjectError,match='not retained'):target.published_events('MU',2)
