from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import sqlite3
import pytest

from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.cli import backup
from doxagent.v2_read.source_lifecycle import expire,sync_business_facts
from doxagent.v2_read.source_snapshot import rebuild_current


def test_snapshot_batch_preserves_successes_and_rolls_back_failed_row(tmp_path):
    from doxagent.v2_read.source_snapshot import SnapshotStore
    store=SnapshotStore(tmp_path/'read.db');store.migrate()
    with store.importing():
        for i in range(501):
            store.ingest('test',str(i),[dict(kind='ticker',ticker='MU',id=str(i),data={'ticker':'MU'})])
        try:
            with store.connect(write=True) as db:
                db.execute("DELETE FROM object_current")
                raise ValueError('one malformed snapshot row')
        except ValueError:pass
        assert store.get('ticker','MU','0')
    with store.connect() as db:assert db.execute('SELECT count(*) FROM object_current').fetchone()[0]==501
from doxagent.v2_read.transport_archive import archive
from doxagent.v2_read.repository import ReadStore


def fixture_source(tmp_path):
    path=tmp_path/'bus'/'bus.db';path.parent.mkdir()
    with closing(sqlite3.connect(path)) as db,db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE poll_states(binding_id TEXT PRIMARY KEY,source_id TEXT,ticker TEXT,status TEXT,next_dispatch_at TEXT,data_json TEXT)')
    s=SourceOutbox(path,'bus');s.migrate(('poll_states',))
    with closing(s.connection(write=True)) as db,db:
        for n in range(6):
            v=dict(binding_id=str(n),source_id='source',ticker='MU',status='succeeded')
            db.execute("INSERT INTO poll_states VALUES(?,'source','MU','succeeded','1',?)",(str(n),json.dumps(v)))
        db.execute('UPDATE v2_source_outbox SET recorded_at=?',((datetime.now(UTC)-timedelta(days=9)).isoformat(),))
    return s


def test_expiry_crosses_one_repair_without_losing_its_packet_or_current(tmp_path):
    s=fixture_source(tmp_path);before=s.event(2)
    s.acknowledge('live',6,repair_pin=2)
    s.acknowledge_exceptions('live',6,[2])
    assert expire(s)['expired']==6
    assert s.event(2)==before and s.event(1) is None and s.event(6) is None
    with closing(s.connection()) as db:
        assert db.execute('SELECT count(*) FROM poll_states').fetchone()[0]==6
        assert db.execute('SELECT count(*) FROM v2_source_receipts').fetchone()[0]==0
        assert not db.execute('SELECT 1 FROM v2_source_pins').fetchone()
    with pytest.raises(ValueError,match='SOURCE_SNAPSHOT_REQUIRED'):s.coordinates(0)
    assert s.coordinates(6)==[]
    s.acknowledge_exceptions('live',6,[]);expire(s)
    assert s.event(2) is None


def test_old_packs_really_exit_and_retired_consumer_releases_ack(tmp_path):
    s=fixture_source(tmp_path);checkpoint=tmp_path/'backup'/'bus.db';backup(s.path,checkpoint)
    s.acknowledge('old-generation',1);s.acknowledge('live',6)
    assert archive(s,checkpoint)==1
    s.retire_consumer('old-generation')
    s.acknowledge_exceptions('live',6,[1])
    assert expire(s)['expired']==6
    assert s.event(1)['seq']==1
    with closing(s.connection(write=True)) as db,db:
        assert not db.execute('SELECT 1 FROM v2_receipt_packs').fetchone()
        db.execute('UPDATE v2_transport_files_exit SET not_before=0')
    assert expire(s)['files']==1


def test_current_snapshot_rebuild_after_all_transport_expired(tmp_path):
    s=fixture_source(tmp_path);s.acknowledge_exceptions('live',6,[]);expire(s)
    report=rebuild_current([s],tmp_path/'new-read'/'read.db')
    assert report['verified'] and report['rows']==6
    store=ReadStore(report['path'])
    assert store.position('bus')==6
    assert store.get('native:poll_states','MU','0')['binding_id']=='0'
    with closing(s.connection()) as db:
        assert {r[0] for r in db.execute('SELECT id FROM v2_source_consumers')}=={'live'}


def test_receipt_envelope_pins_nested_native_hash_and_delete_releases_owner(tmp_path):
    s=fixture_source(tmp_path)
    native=s.content.encode({'binding_id':'big','nested':{'content':'x'*20000}})
    with closing(s.connection(write=True)) as db,db:
        db.execute("INSERT INTO poll_states VALUES('big','source','MU','succeeded','1',?)",(native,))
        refs=db.execute("SELECT owner_type,artifact_id FROM artifact_ref WHERE owner_type IN ('poll_states','v2_source_receipts')").fetchall()
        assert {r[0] for r in refs}=={'poll_states','v2_source_receipts'}
        db.execute("UPDATE poll_states SET data_json='{}' WHERE binding_id='big'")
        assert not db.execute("SELECT 1 FROM artifact_ref WHERE owner_type='poll_states'").fetchone()
        assert db.execute("SELECT 1 FROM artifact_ref WHERE owner_type='v2_source_receipts'").fetchone()


def test_small_business_fact_sync_preserves_settlement_and_withdrawal(tmp_path):
    s=fixture_source(tmp_path);s.source='runtime'
    store=ReadStore(tmp_path/'read'/'read.db');store.migrate()
    s.claim_serving_facts(store.path)
    row=dict(kind='policy_hit_set',ticker='MU',id='case',data={'hits':{'P':{'ar':'r','occurred_at':'2026-10-10T12:00:00Z'}}})
    store.ingest('runtime','1',[row]);sync_business_facts(s,store)
    with closing(s.connection()) as db:
        value=json.loads(db.execute('SELECT payload FROM v2_serving_business_facts').fetchone()[0])
        assert value['data']==row['data']
    store.ingest('runtime','2',[{**row,'data':None}]);sync_business_facts(s,store)
    with closing(s.connection()) as db:assert not db.execute('SELECT 1 FROM v2_serving_business_facts').fetchone()


def test_source_native_exit_protects_exact_repair_and_resurrected_current(tmp_path):
    from doxagent.v2_read import source_native_gc as gc
    s=fixture_source(tmp_path)
    value={'binding_id':'big','nested':{'content':'x'*20000}}
    raw=s.content.encode(value)
    with closing(s.connection(write=True)) as db,db:
        db.execute("INSERT INTO poll_states VALUES('big','source','MU','succeeded','1',?)",(raw,))
        pin=db.execute('SELECT max(seq) FROM v2_source_outbox').fetchone()[0]
        db.execute("UPDATE poll_states SET data_json='{}' WHERE binding_id='big'")
        old=(datetime.now(UTC)-timedelta(days=9)).isoformat()
        db.execute('UPDATE v2_source_outbox SET recorded_at=?',(old,));db.execute('UPDATE artifact SET created_at=?',(old,))
    s.acknowledge_exceptions('live',s.head(),[pin]);expire(s)
    assert not gc.reconcile(s,seconds=0)['ready']
    assert gc.collect(s)['quarantined']==0
    while not gc.reconcile(s,seconds=2)['ready']:pass
    assert gc.collect(s)['quarantined']==0
    s.acknowledge_exceptions('live',s.head(),[]);expire(s)
    assert gc.collect(s)['quarantined']==1
    raw=s.content.encode(value)
    with closing(s.connection(write=True)) as db,db:
        db.execute("UPDATE poll_states SET data_json=? WHERE binding_id='big'",(raw,))
    assert gc.collect(s)['deleted']==0
    assert s.content.source_row({'data_json':raw})['data_json']==json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    with closing(s.connection()) as db:
        assert db.execute("SELECT state FROM artifact WHERE codec='json-chunks-v1'").fetchone()[0]=='LIVE'


def test_snapshot_restores_old_business_rows_with_null_search_metadata(tmp_path):
    s=fixture_source(tmp_path);s=SourceOutbox(s.path,'runtime');s.migrate(('poll_states',))
    store=ReadStore(tmp_path/'old-read'/'read.db');store.migrate()
    s.claim_serving_facts(store.path)
    record=dict(kind='policy_admission',ticker='MU',id='P:r',data={'first_activated_at':'2026-10-01T12:00:00Z','runtime_activation_id':'a'})
    store.ingest('r','1',[record])
    with store.connect(write=True) as db:db.execute("UPDATE object_current SET search_text=NULL WHERE kind='policy_admission'")
    sync_business_facts(s,store)
    report=rebuild_current([s],tmp_path/'new-read'/'read.db')
    assert report['verified']
    assert ReadStore(report['path']).get('policy_admission','MU','P:r')==record['data']


def test_shadow_generation_cannot_replace_canonical_business_facts(tmp_path):
    s=fixture_source(tmp_path);s=SourceOutbox(s.path,'runtime');s.migrate(('poll_states',))
    active=ReadStore(tmp_path/'active'/'read.db');active.migrate();s.claim_serving_facts(active.path)
    fact=dict(kind='policy_hit_set',ticker='MU',id='c',data={'hits':{'P':{'ar':'r','occurred_at':'2026-10-01T12:00:00Z'}}})
    active.ingest('r','1',[fact]);sync_business_facts(s,active)
    shadow=ReadStore(tmp_path/'shadow'/'read.db');shadow.migrate();sync_business_facts(s,shadow)
    with closing(s.connection()) as db:
        assert db.execute("SELECT count(*) FROM v2_serving_business_facts WHERE kind='policy_hit_set'").fetchone()[0]==1


def test_only_crashed_temporary_snapshot_consumers_expire(tmp_path):
    s=fixture_source(tmp_path);now=datetime.now(UTC)
    s.acknowledge_exceptions('snapshot:crashed',0,[])
    s.acknowledge_exceptions('read:still-owned',6,[])
    with closing(s.connection(write=True)) as db,db:
        db.execute("UPDATE v2_source_consumers SET updated_at=?",((now-timedelta(days=2)).isoformat(),))
    expire(s,now=now)
    with closing(s.connection()) as db:
        ids={r[0] for r in db.execute('SELECT id FROM v2_source_consumers')}
    assert 'snapshot:crashed' not in ids and 'read:still-owned' in ids
