import json

from doxagent.api_v2.dto import validate
from doxagent.api_v2.projection_health import snapshot
from doxagent.v2_read.repository import ReadStore
from tests.v2_backend.test_source_lifecycle import fixture_source


def test_pinned_clocks_separate_current_backlog_from_historical_gaps(tmp_path):
    store = ReadStore(tmp_path / 'read.db'); store.migrate()
    clock = dict(checkpoint=10, head=20, source_event_at='2026-11-12T00:00:00Z', oldest_pending_at='2026-11-12T00:00:01Z')
    with store.connect(write=True) as db:
        db.execute("INSERT INTO source_health VALUES('runtime',20,10,'2026-11-12T00:01:00Z',NULL)")
        db.execute("INSERT INTO read_meta VALUES('projection_clock:runtime',?)", (json.dumps(clock),))
        db.execute("INSERT INTO gaps(source,event,reason) VALUES('runtime','old','PROVENANCE_UNVERIFIED')")
    with store.connect() as db:
        before = snapshot(db, 'EVENTS')
    runtime = next(row for row in before if row['source'] == 'runtime')
    assert runtime['checkpoint'] == 10 and runtime['oldest_pending_at'] == clock['oldest_pending_at']
    assert runtime['historical_gaps'] == 1
    assert before[0]['error'] == 'SOURCE_HEALTH_UNAVAILABLE'
    for row in before: validate('SourceProjection', row)
    with store.connect(write=True) as db:
        db.execute("UPDATE source_health SET checkpoint=20 WHERE source='runtime'")
    with store.connect() as db:
        after = next(row for row in snapshot(db, 'RUNTIME') if row['source'] == 'runtime')
    assert after['oldest_pending_at'] is None  # Old coordinate clock cannot describe a newer checkpoint.
    assert after['historical_gaps'] == 1
    assert runtime['checkpoint'] == 10  # Existing pinned snapshot remains unchanged.


def test_source_clock_only_reads_retained_headers(tmp_path):
    source = fixture_source(tmp_path)
    clock = source.projection_clock(2, 6)
    assert clock['source_event_at'] and clock['oldest_pending_at']
    assert source.projection_clock(6, 6)['oldest_pending_at'] is None


def test_read_context_clocks_are_opt_in_for_closed_legacy_clients(tmp_path):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_control.repository import ControlRepository
    from tests.v2_backend.test_api import OfflineAuth
    store=ReadStore(tmp_path/'read.db');store.migrate()
    store.ingest('fixture','ticker',[dict(kind='ticker',ticker='MU',id='MU',data={'removed':False})])
    control=ControlRepository(RuntimeJournal(tmp_path/'runtime.db'));control.migrate()
    with TestClient(create_app(store=store,control=control,auth=OfflineAuth())) as client:
        headers={'Authorization':'Bearer offline'}
        old=client.get(PREFIX+'/read-context?page=OVERVIEW',headers=headers)
        assert old.status_code==200,old.text
        assert 'projection' not in old.json()['data']
        new=client.get(PREFIX+'/read-context?page=OVERVIEW',headers={**headers,'X-Doxagent-Projection-Clock':'1'})
        assert new.status_code==200,new.text
        assert len(new.json()['data']['projection'])==4
