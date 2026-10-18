import sqlite3

from doxagent.event_library.repository import EventLibraryRepository
from doxagent.persistent_runtime_v2.event_branch import branch_library
from tests.test_codex_event_library_incremental import _publish_v1


def test_branch_preserves_selected_native_and_birth_identities_without_transport(tmp_path):
    source=EventLibraryRepository(tmp_path/'source.db')
    _publish_v1(source,tmp_path/'fixture')
    with source._write() as db:
        db.execute('CREATE TABLE v2_source_receipts(id PRIMARY KEY,payload)')
        db.execute("INSERT INTO v2_source_receipts VALUES(1,?)",('x'*100000,))
        db.execute("INSERT INTO maintenance_runs VALUES('old','MU','COMPLETE',NULL,'view',1,NULL,NULL,NULL,'{}','2026-01-01')")
        identities=[tuple(r) for r in db.execute('SELECT * FROM v2_native_identities ORDER BY collection,native_key')]
    target=branch_library(source.path,tmp_path/'branch'/'events.db','MU',1)
    assert [e.model_dump() for e in target.published_events('MU',1)] == [e.model_dump() for e in source.published_events('MU',1)]
    with target._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_source_receipts'").fetchone()
        assert not db.execute('SELECT 1 FROM maintenance_runs').fetchone()
        assert not db.execute('SELECT 1 FROM delta_items').fetchone()
        assert identities == [tuple(r) for r in db.execute('SELECT * FROM v2_native_identities ORDER BY collection,native_key')]
        mapping=db.execute('SELECT runtime_scope,runtime_atomic_id,runtime_atomic_version,runtime_signature FROM runtime_atomic_mappings LIMIT 1').fetchone()
        assert mapping
    assert target.runtime_atomic_signature(mapping[0],mapping[1]) == (mapping[2],mapping[3])
    assert branch_library(source.path,target.path,'MU',1).path==target.path


def test_rewind_branch_keeps_original_revision_numbers_and_drops_future_state(tmp_path):
    source=EventLibraryRepository(tmp_path/'source.db')
    _publish_v1(source,tmp_path/'fixture')
    with source._write() as db:
        original=db.execute('SELECT * FROM canonical_event_revisions LIMIT 1').fetchone()
        db.execute('INSERT INTO canonical_event_revisions VALUES(?,?,?,?,?)',(original['ticker'],original['event_no'],2,2,original['payload_json']))
        db.execute("INSERT INTO library_versions VALUES('MU',2,1,'[]','PUBLISHED','2026-01-02','2026-01-02T00:00:00+00:00')")
        db.execute("UPDATE library_heads SET published_version=2 WHERE ticker='MU'")
    first=branch_library(source.path,tmp_path/'branch1'/'events.db','MU',1)
    second=branch_library(source.path,tmp_path/'branch2'/'events.db','MU',2)
    with first._read() as db:
        assert db.execute('SELECT max(library_version) FROM canonical_event_revisions').fetchone()[0]==1
    with second._read() as db:
        assert db.execute('SELECT count(*) FROM canonical_event_revisions WHERE event_no=?',(original['event_no'],)).fetchone()[0]==1
        assert db.execute('SELECT revision_no FROM canonical_event_revisions WHERE event_no=?',(original['event_no'],)).fetchone()[0]==2


def test_rewind_keeps_membership_at_its_inclusive_last_version(tmp_path):
    source=EventLibraryRepository(tmp_path/'source.db');_publish_v1(source,tmp_path/'fixture')
    with source._write() as db:
        db.execute('UPDATE event_fact_memberships SET valid_to_version=1')
        db.execute("INSERT INTO library_versions VALUES('MU',2,1,'[]','PUBLISHED','2026-01-02','2026-01-02T00:00:00+00:00')")
        db.execute("UPDATE library_heads SET published_version=2 WHERE ticker='MU'")
    target=branch_library(source.path,tmp_path/'branch'/'events.db','MU',1)
    assert target.published_events('MU',1)==source.published_events('MU',1)
