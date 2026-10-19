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


def test_bus_snapshot_rebuilds_message_search_from_thin_body_reference(tmp_path):
    source_path=tmp_path/'bus.db'
    at='2026-10-01T12:00:00Z'
    body='证据'*30000+'needle-after-chunk-boundary'
    rows={
        'source_definitions':dict(source_id='news',display_name='News',kind='api'),
        'standard_messages':dict(ticker='MU',standard_message_id='message',raw_message_id='raw',revision=1,
            url='https://example.com/article',title='Article',source_id='news',binding_id='MU:news',
            published_at=at,collected_at=at,normalized_at=at,body=body,metadata={'v2_control_origin':{'epoch':1}}),
        'stream_items':dict(ticker='MU',stream_item_id='stream',published_at=at,stream_offset=1),
        'stream_members':dict(stream_item_id='stream',standard_message_id='message',member_index=0),
    }
    with closing(sqlite3.connect(source_path)) as db,db:
        for table,value in rows.items():
            db.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,data_json TEXT)')
            db.execute('INSERT INTO '+table+' VALUES(?,?)',('1',json.dumps(value)))
    source=SourceOutbox(source_path,'bus');source.migrate(tuple(rows))
    report=rebuild_current([source],tmp_path/'rebuilt'/'read.db')
    assert report['verified'],report['gaps']
    store=ReadStore(report['path'])
    native=store.get('native:standard_messages','MU','message')
    assert 'body' not in native and native['body_ref']['size_bytes']==len(body.encode())
    message=store.get('message','MU','message')
    assert message['body']==native['body_ref']
    from doxagent.v2_read.projectors import DomainProjectors
    assert DomainProjectors(store).message_search('MU',native)=='Article\n'+body
    with store.connect() as db:
        assert db.execute("SELECT search_text FROM object_current WHERE kind='message'").fetchone()[0]==('Article\n'+body).casefold()


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


def test_snapshot_preserves_retired_catalog_and_usage_after_process_exit(tmp_path):
    s=fixture_source(tmp_path);s=SourceOutbox(s.path,'runtime');s.migrate(('poll_states',))
    live=ReadStore(tmp_path/'live'/'read.db');live.migrate();s.claim_serving_facts(live.path)
    catalog=dict(kind='policy_catalog',ticker='MU',id='old-policy',data={'lifecycle':'RETIRED','policy_id':'P'})
    usage=dict(kind='usage',ticker='MU',id='call',day='2026-10-01',parent='completed-run',data={
        'scope':'API','node':'D1','model':'qwen3.8-flash','provider':'bailian',
        'timing':{'first_started_at':{'value':'2026-10-01T12:00:00Z'}},
        'usage':{'input_tokens':100,'cached_input_tokens':0,'output_tokens':20},'coverage':'COMPLETE'})
    live.ingest('fixture','facts',[catalog,usage]);sync_business_facts(s,live)
    report=rebuild_current([s],tmp_path/'rebuilt'/'read.db')
    target=ReadStore(report['path'])
    assert target.get('policy_catalog','MU','old-policy')==catalog['data']
    assert target.get('usage','MU','call')==usage['data']
    with target.connect() as db:
        assert db.execute("SELECT value FROM contributions_current WHERE metric='input_tokens' AND entity='call'").fetchone()[0]=='100'


def test_snapshot_seeds_membership_before_current_activation(tmp_path,monkeypatch):
    from tests.v2_backend.test_policy_consumption_coverage import seeded
    from doxagent.v2_read.formal import FormalProjectors
    from doxagent.v2_read.lifecycle import activate
    s=fixture_source(tmp_path);s=SourceOutbox(s.path,'runtime');s.migrate(('poll_states',))
    live,summary=seeded(tmp_path);s.claim_serving_facts(live.path)
    attempt=dict(kind='attempt',ticker='MU',id='retired-job',parent='case',route='W3',data={'status':'SUCCEEDED'})
    live.ingest('fixture','terminal-audit',[attempt],contributions=[dict(metric='new_fact_candidates',ticker='MU',entity='old-candidate',day='2026-10-01',value='1')])
    sync_business_facts(s,live)
    original=FormalProjectors.__call__
    def apply_current(self,event):
        records,counts=original(self,event)
        if event['table_name']=='poll_states':
            self.store.ingest('fixture','detail',[dict(kind='policy_detail',ticker='MU',id='rev',parent='d3',data={'summary':summary,'policy':{}})])
            changes,_=activate(self.store,'MU',dict(runtime_activation_id='a',policy_set=dict(artifact_id='d3',policy_set_version=1,published_at='2026-09-03T00:00:00Z')),None)
            records.extend(changes)
        return records,counts
    monkeypatch.setattr(FormalProjectors,'__call__',apply_current)
    report=rebuild_current([s],tmp_path/'rebuilt'/'read.db');assert report['verified']
    target=ReadStore(report['path'])
    assert target.get('attempt','MU','retired-job')==attempt['data']
    with target.connect() as db:
        assert not db.execute("SELECT 1 FROM object_current WHERE kind='policy_change'").fetchone()
        assert db.execute("SELECT value FROM contributions_current WHERE metric='new_fact_candidates'").fetchone()[0]=='1'


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


def test_source_snapshot_keeps_old_native_bytes_after_current_row_changes(tmp_path):
    from doxagent.v2_read import source_native_gc as gc
    s=fixture_source(tmp_path)
    value={'binding_id':'big','content':'snapshot still reads this'*1000}
    raw=s.content.encode(value)
    with closing(s.connection(write=True)) as db,db:
        db.execute("INSERT INTO poll_states VALUES('big','source','MU','succeeded','1',?)",(raw,))
        db.execute("UPDATE poll_states SET data_json='{}' WHERE binding_id='big'")
        old=(datetime.now(UTC)-timedelta(days=9)).isoformat()
        db.execute('UPDATE v2_source_outbox SET recorded_at=?',(old,));db.execute('UPDATE artifact SET created_at=?',(old,))
    s.acknowledge_exceptions('live',s.head(),[]);expire(s)
    while not gc.reconcile(s,seconds=2)['ready']:pass
    s.acknowledge_exceptions('snapshot:reader',s.head(),[])
    assert gc.collect(s)['deferred']=='current-source-snapshot'
    assert json.loads(s.content.text(raw))==value
    # The regular live reader does not freeze native exit; a crashed snapshot
    # also cannot keep it pinned after the existing 24h lease.
    with closing(s.connection(write=True)) as db,db:
        db.execute("UPDATE v2_source_consumers SET updated_at=? WHERE id='snapshot:reader'",((datetime.now(UTC)-timedelta(hours=25)).isoformat(),))
    assert gc.collect(s)['quarantined']==1


def test_failed_source_snapshot_removes_only_its_new_staging_and_consumer(tmp_path):
    s=fixture_source(tmp_path)
    # Capture still lists the table: a broken source fails inside the actual
    # import, after target initialization and temporary consumer registration.
    with closing(s.connection(write=True)) as db,db:db.execute('DROP TABLE poll_states')
    s.acknowledge_exceptions('live',s.head(),[])
    target=tmp_path/'failed-generation'/'read.db'
    with pytest.raises(sqlite3.OperationalError,match='no such table'):
        rebuild_current([s],target)
    assert not target.parent.exists() and s.path.is_file()
    with closing(s.connection()) as db:
        assert {r[0] for r in db.execute('SELECT id FROM v2_source_consumers')}=={'live'}


def test_source_snapshot_cannot_claim_a_shared_existing_directory(tmp_path):
    s=fixture_source(tmp_path);parent=tmp_path/'shared';parent.mkdir()
    original=parent/'business.txt';original.write_text('unique original')
    with pytest.raises(ValueError,match='fresh dedicated directory'):
        rebuild_current([s],parent/'new.db')
    assert original.read_text()=='unique original'


def test_cli_sigterm_unwinds_new_snapshot_and_restores_signal_handler(tmp_path,monkeypatch):
    import signal,sys
    from doxagent.v2_read.cli import main
    from doxagent.v2_read.formal import FormalProjectors
    s=fixture_source(tmp_path);s.acknowledge_exceptions('live',s.head(),[])
    target=tmp_path/'terminated-generation'/'read.db';previous=signal.getsignal(signal.SIGTERM)
    def terminate(*_):signal.raise_signal(signal.SIGTERM)
    monkeypatch.setattr(FormalProjectors,'__call__',terminate)
    monkeypatch.setattr(sys,'argv',['read','current-snapshot','--source','bus='+str(s.path),'--read-db',str(target)])
    with pytest.raises(SystemExit) as stopped:main()
    assert stopped.value.code==128+signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM)==previous
    assert not target.parent.exists()
    with closing(s.connection()) as db:
        assert {r[0] for r in db.execute('SELECT id FROM v2_source_consumers')}=={'live'}


def test_old_native_owner_refs_exit_in_bounded_pages_and_keep_external_and_current(tmp_path):
    from doxagent.v2_read import source_native_gc as gc
    from doxagent.v2_read.artifact_registry import retain
    from doxagent.v2_read.native_content import MARKER
    s=fixture_source(tmp_path)
    orphan=json.loads(s.content.encode({'body':'x'*20000}))['body'][MARKER]
    current={'binding_id':'0','body':'y'*20000};raw=s.content.encode(current)
    with closing(s.connection(write=True)) as db,db:
        db.execute("UPDATE poll_states SET data_json=? WHERE binding_id='0'",(raw,))
        for n in range(10):
            retain(db,orphan,orphan,20000,codec='json-chunks-v1',location='native-files/'+orphan[:2]+'/'+orphan,owner_type='poll_states',owner_id=json.dumps(['gone-'+str(n)]))
        retain(db,orphan,orphan,20000,codec='json-chunks-v1',location='native-files/'+orphan[:2]+'/'+orphan,owner_type='external-recovery',owner_id='hold')
        db.execute('UPDATE artifact SET created_at=?',((datetime.now(UTC)-timedelta(days=9)).isoformat(),))
    steps=[]
    for _ in range(100):
        report=gc.reconcile(s,limit=2,seconds=2)
        with closing(s.connection()) as db:
            steps.append(db.execute("SELECT count(*) FROM artifact_ref WHERE owner_type='poll_states' AND owner_id LIKE '%gone-%'").fetchone()[0])
        if report['ready']:break
        assert gc.collect(s)['quarantined']==0
    assert report['ready'] and steps[-1]==0
    assert all(a-b<=2 for a,b in zip([10,*steps],steps))
    with closing(s.connection()) as db:
        assert db.execute("SELECT count(*) FROM artifact_ref WHERE owner_type='external-recovery'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM artifact_ref WHERE owner_type='poll_states' AND owner_id=?",(json.dumps(['0'],separators=(',',':')),)).fetchone()[0]==1
    assert gc.collect(s)['quarantined']==0
    with closing(s.connection(write=True)) as db,db:db.execute("DELETE FROM artifact_ref WHERE owner_type='external-recovery'")
    assert gc.collect(s)['quarantined']==1
    assert json.loads(s.content.text(raw))==current
