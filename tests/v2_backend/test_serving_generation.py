import json
import sqlite3
from datetime import UTC, datetime, timedelta

from doxagent.v2_read.artifact_registry import collect_diagnostics
from doxagent.v2_read.maintenance import collect
from doxagent.v2_read.policy_hits import project
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.serving_generation import build


def test_current_is_single_physical_copy_and_definitions_have_owner_exit(tmp_path):
    store = ReadStore(tmp_path/'read.db'); store.migrate()
    fact = {'fact_id':'f', 'proposition':'exact published fact'}
    a = store.ingest('s','1',[dict(kind='fact',ticker='MU',id='snapshot-a:f',data={'fact':fact})])
    store.ingest('s','2',[dict(kind='fact',ticker='MU',id='snapshot-b:f',data={'fact':fact})])
    with store.connect() as db:
        assert db.execute("SELECT type FROM sqlite_master WHERE name='objects'").fetchone()[0] == 'view'
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='business_revisions'").fetchone()
        assert db.execute('SELECT count(*) FROM object_history').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM definitions').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM definition_ref').fetchone()[0] == 2
    for n, identity in enumerate(('snapshot-a:f','snapshot-b:f'),3):
        store.ingest('s',str(n),[dict(kind='fact',ticker='MU',id=identity,data=None)])
    assert store.get('fact','MU','snapshot-a:f',a)['fact'] == fact
    collect(store,now=datetime.now(UTC)+timedelta(hours=1),budget=2)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM definitions').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM definition_ref').fetchone()[0] == 0


def test_sparse_set_retraction_revision_day_replay_and_unknown(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    case={'case_id':'c','source':{'snapshot':{'ticker':'MU'}},
          'frozen_inputs':{'projection':{'policies':[{'policy_id':'P','activation_revision':'r1'}]}},
          'w2_final':{'policy_ids':['P']}}
    def settle(identity,final,at,position):
        rows,metrics=project(store,case,{'final_policy_hit':{'value':final}},
                             {'operation':'UPDATE','recorded_at':at})
        return store.ingest('runtime',identity,rows,position=position,contributions=metrics)
    seq=settle('1',True,'2026-10-10T16:00:00Z',1)
    settle('1',True,'2026-10-12T16:00:00Z',1)
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM contributions_current').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM contributions_history').fetchone()[0] == 0
    case['frozen_inputs']['projection']['policies'][0]['activation_revision']='r2'
    settle('2',True,'2026-10-12T16:00:00Z',2)
    with store.connect() as db:
        row=db.execute('SELECT dimensions,day,value FROM contributions_current').fetchone()
        assert json.loads(row[0])['ar']=='r2' and row[2]=='1'
    settle('3',False,'2026-10-13T16:00:00Z',3)
    assert store.get('policy_hit_set','MU','c') is None
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM contributions_current').fetchone()[0] == 0
        assert db.execute("SELECT value FROM metric_buckets_current WHERE metric='policy_hits' AND day='*'").fetchone()[0] == '0'
    rows,metrics=project(store,case,{'final_policy_hit':{'value':None}},
                         {'operation':'BACKFILL','recorded_at':'2026-10-14T00:00:00Z'})
    assert rows == [] and metrics == []
    assert store.get('policy_hit_set','MU','c',seq)['hits']['P']['ar']=='r1'


def test_generation_keeps_exact_business_evidence_without_old_protocol(tmp_path):
    old=ReadStore(tmp_path/'old'/'read.db');old.migrate()
    old.ingest('runtime','1',[
        dict(kind='case',ticker='MU',id='c',data={'final_policy_hit':{'value':False}}),
        dict(kind='fact',ticker='MU',id='s:f',data={'fact':{'fact_id':'f','proposition':'v1'}}),
        dict(kind='native:runtime_v2_cases',ticker='MU',id='c',data={
            'case_id':'c','w1_final':{'reasoning':'x'*20000},
            'frozen_inputs':{'unneeded_history':'q'*100000}}),
    ],position=1,contributions=[dict(metric='cost',ticker='MU',entity='c',day='2026-10-10',value='1.5'),
        dict(metric='policy_ar_hits',ticker='MU',entity='c:P',day='2026-10-10',value='1',
             dimensions={'business_key':'P:r','policy_id':'P','ar':'r'})])
    token=old.save_token('owner','scope',{'seq':1},view=True)
    report=build(old.path,tmp_path/'new'/'read.db',reserve_bytes=0)
    new=ReadStore(tmp_path/'new'/'read.db')
    assert report['verified'] and report['generation'] != report['prior_generation']
    assert new.get('fact','MU','s:f') == old.get('fact','MU','s:f')
    assert new.get('native:runtime_v2_cases','MU','c')['w1_final']['reasoning']=='x'*20000
    with new.connect() as db:
        assert db.execute('SELECT count(*) FROM object_history').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM views WHERE id=?',(token,)).fetchone()[0]==0
        assert db.execute("SELECT value FROM contributions_current WHERE metric='cost'").fetchone()[0]=='1.5'
        aliases=db.execute("SELECT dimensions,value FROM metric_buckets_current WHERE metric='policy_hits'").fetchall()
        assert len(aliases)==2
        assert all(json.loads(row[0])=={'business_key':'P'} and row[1]=='1' for row in aliases)
    old.ingest('runtime','2',[dict(kind='case',ticker='MU',id='c',data={'final_policy_hit':{'value':True}})],position=2)
    assert new.get('case','MU','c')['final_policy_hit']['value'] is False


def test_serving_file_reclaimed_only_after_last_current_and_history_owner(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    blob={'output':'x'*20000}
    for n in (1,2):
        store.ingest('s',str(n),[dict(kind='native:runtime_v2_turns',ticker='MU',id=str(n),data=blob)])
    with store.connect() as db:
        digest=db.execute("SELECT id FROM artifact WHERE class='SERVING'").fetchone()[0]
        assert db.execute('SELECT count(*) FROM artifact_ref').fetchone()[0]==2
    store.ingest('s','3',[dict(kind='native:runtime_v2_turns',ticker='MU',id='1',data=None)])
    now=datetime.now(UTC)+timedelta(hours=1)
    collect(store,now=now,budget=2)
    assert collect_diagnostics(store,now=now)==[]
    store.ingest('s','4',[dict(kind='native:runtime_v2_turns',ticker='MU',id='2',data=None)])
    collect(store,now=now,budget=2)
    assert len(collect_diagnostics(store,now=now))==1
    collect_diagnostics(store,now=now)
    with store.connect() as db:
        assert db.execute('SELECT state FROM artifact WHERE id=?',(digest,)).fetchone()[0]=='DELETED'
    store.ingest('s','5',[dict(kind='native:runtime_v2_turns',ticker='MU',id='3',data=blob)])
    assert store.get('native:runtime_v2_turns','MU','3')==blob
    with store.connect() as db:
        assert db.execute('SELECT state FROM artifact WHERE id=?',(digest,)).fetchone()[0]=='LIVE'


def test_regeneration_cancels_pending_quarantine_without_leaking_owners(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    blob={'output':'x'*20000}
    def change(n,data):
        store.ingest('s',str(n),[dict(kind='native:runtime_v2_turns',ticker='MU',id='c',data=data)])
    now=datetime.now(UTC)+timedelta(hours=1)
    change(1,blob);change(2,None);collect(store,now=now,budget=2)
    assert collect_diagnostics(store,now=now)
    change(3,blob)
    assert store.get('native:runtime_v2_turns','MU','c')==blob
    change(4,None);collect(store,now=now,budget=2)
    assert collect_diagnostics(store,now=now)
    collect_diagnostics(store,now=now)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM artifact WHERE state='DELETED'").fetchone()[0]==1


def test_definition_noop_compares_references_without_reloading_large_body(tmp_path,monkeypatch):
    from doxagent.v2_read.native_content import NativeContent
    from doxagent.v2_read.serving_schema import DEFINITION_MARKER
    store=ReadStore(tmp_path/'read.db');store.migrate()
    record=dict(kind='fact',ticker='MU',id='fact',data=dict(fact=dict(fact_id='F1',title='kept',body='x'*200000),observations=[{'note':'y'*200}]*300))
    first=store.ingest('fixture','first',[record])
    decode=NativeContent.decode
    def guard(self,value,*,expand_definitions=True):
        if isinstance(value,dict) and DEFINITION_MARKER in value and expand_definitions:
            raise AssertionError('unchanged definition body should not be read by the writer')
        return decode(self,value,expand_definitions=expand_definitions)
    monkeypatch.setattr(NativeContent,'decode',guard)
    assert store.ingest('fixture','same',[record])==first
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM object_history').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM definitions').fetchone()[0]==1
