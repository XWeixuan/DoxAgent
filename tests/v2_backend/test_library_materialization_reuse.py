import json
import pytest
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.repository import ReadStore
from tests.test_codex_event_library_incremental import _publish_v1


def test_reuse_requires_committed_same_file_version_and_unchanged_content(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    root=tmp_path/'events';repo=EventLibraryRepository(root/'US/MU/event_library.sqlite3');_publish_v1(repo,tmp_path/'fixture')
    mapper=FormalProjectors(store)
    reference,records=mapper._library(root,'MU',1)
    assert records
    # Failed/uncommitted first materialization cannot turn into an empty retry.
    assert mapper._library(root,'MU',1)[1]
    store.ingest('fixture','first',records)
    assert mapper._library(root,'MU',1)==(reference,[])
    with repo._write() as db:
        row=db.execute('SELECT event_no,payload_json FROM canonical_event_revisions LIMIT 1').fetchone()
        value=json.loads(row['payload_json']);value['title']='changed after publication'
        db.execute('UPDATE canonical_event_revisions SET payload_json=? WHERE event_no=?',(json.dumps(value),row['event_no']))
    fresh,changed=mapper._library(root,'MU',1)
    assert changed
    assert any(r['kind']=='event_detail' and r['data']['event']['title']=='changed after publication' for r in changed)
    other=tmp_path/'other'
    _publish_v1(EventLibraryRepository(other/'US/MU/event_library.sqlite3'),tmp_path/'fixture-other')
    assert mapper._library(other,'MU',1)[1]
