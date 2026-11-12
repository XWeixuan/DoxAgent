import json

import pytest

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.v2_read.identities import migrate
from doxagent.v2_read.libraries import LibraryIndexer
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.reference import project
from doxagent.v2_read.repository import ReadStore
from tests.test_codex_event_library_incremental import _publish_v1


def seeded(tmp_path):
    root=tmp_path/'events'
    native=EventLibraryRepository(root/'US/MU/event_library.sqlite3')
    with native._write() as db:migrate(db)
    _publish_v1(native,root)
    compiler=EventLibraryViewCompiler(native)
    events=[e.model_dump(mode='json') for e in compiler.reference_events('MU',1)]
    captured=dict(version=1,events=events,rendered={e['event_id']:e['title'] for e in events})
    value=dict(ticker='MU',delta_id='d',semantic_day='2026-11-12',prepared_at='2026-11-12T00:00:00Z',root=str(root),status='PREPARED',before=dict(version=0,events=[],rendered={}),after=captured)
    store=ReadStore(tmp_path/'read.db');store.migrate()
    return root,store,value


def test_reference_state_only_reuses_atomic_members_and_keeps_day_delta(tmp_path,monkeypatch):
    root,store,value=seeded(tmp_path)
    records=project(store,value)
    assert any(r['kind']=='fact' for r in records)
    # Simulated failure before COMMIT must not publish a completion proof.
    with pytest.raises(ValueError):
        store.ingest('runtime','failed',records+[dict(kind='bad',ticker='MU',id='x',data={'a':1}),dict(kind='bad',ticker='MU',id='x',data={'a':2})])
    with store.connect() as db:assert db.execute("SELECT count(*) FROM object_current WHERE kind='projection_materialization'").fetchone()[0]==0
    store.ingest('runtime','1',records,position=1)
    monkeypatch.setattr(LibraryIndexer,'index',lambda *a:pytest.fail('reindexed committed immutable library'))
    with monkeypatch.context() as state_only:
        state_only.setattr(LibraryIndexer,'reference',lambda *a:pytest.fail('state-only update resolved full captured members'))
        settled=project(store,{**value,'status':'SUCCEEDED'})
    assert not any(r['kind'] in {'event','event_detail','fact','reference_event'} for r in settled)
    store.ingest('runtime','2',settled,position=2)
    day=store.get('delta_day','MU',value['semantic_day'])
    assert day['state']=='AVAILABLE'
    # A later task has equal own sides but must retain today's original V0 base.
    next_value={**value,'delta_id':'next','prepared_at':'2026-11-12T00:01:00Z','before':value['after'],'status':'SUCCEEDED'}
    store.ingest('runtime','3',project(store,next_value),position=3)
    assert store.get('reference_delta','MU','next')['from_library_version']==0
    assert store.get('delta_day','MU',value['semantic_day'])['state']=='AVAILABLE'


def test_old_library_head_with_missing_members_does_not_certify_completion(tmp_path):
    root,store,_=seeded(tmp_path)
    ref,records=LibraryIndexer(root).index('MU',1)
    store.ingest('legacy','head-only',[r for r in records if r['kind']=='library'])
    _,repair=LibraryIndexer(root).ensure(store,'MU',1)
    assert any(r['kind']=='fact' for r in repair)
    store.ingest('runtime','repaired',repair)
    assert LibraryIndexer(root).ensure(store,'MU',1)[1]==[]


def test_policy_head_dedup_and_late_delete_fenced_by_native_watermark(tmp_path,monkeypatch):
    from tests.v2_backend.test_policy_consumption_coverage import seeded as policies
    store,summary=policies(tmp_path)
    modern={**summary,'directions':['LONG'],'executable_directions':['LONG'],'consumed_directions':[]}
    store.ingest('fixture','modern',[dict(kind='policy_catalog',ticker='MU',id='P1:ar',data=modern)])
    mapper=DomainProjectors(store)
    def event(seq,operation='UPDATE'):
        return dict(source='runtime',seq=seq,table_name='runtime_values',entity_id='x',operation=operation,recorded_at='2026-11-12T00:00:00Z',row=dict(namespace='policy_authorization_head',key='MU',payload=json.dumps('rev')))
    rows,counts=mapper(event(10));store.ingest('runtime','10',rows,contributions=counts,position=10)
    import doxagent.v2_read.lifecycle as lifecycle
    original=lifecycle.rows
    monkeypatch.setattr(lifecycle,'rows',lambda *a,**k:pytest.fail('same-presence head scanned historical catalog'))
    rows,counts=mapper(event(11));store.ingest('runtime','11',rows,contributions=counts,position=11)
    assert not any(r['kind']=='policy_catalog' for r in rows)
    rows,_=mapper(event(9,'DELETE'))
    assert not any(r['kind']=='policy_catalog' for r in rows)
    monkeypatch.setattr(lifecycle,'rows',original)
    rows,_=mapper(event(12,'DELETE'))
    assert any(r['kind']=='policy_catalog' for r in rows)


def test_affected_catalog_lookup_uses_full_identity_probe(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    # SQLite otherwise reverses this join and scans the whole ticker catalog
    # once for every affected key, despite INDEXED BY current_identity.
    with store.connect() as db:
        plans=db.execute("EXPLAIN QUERY PLAN SELECT c.id,c.payload FROM json_each(?) q CROSS JOIN object_current c INDEXED BY current_identity ON c.kind='policy_catalog' AND c.ticker=? AND c.id=q.value",('["P:ar"]','MU')).fetchall()
    assert any('kind=? AND ticker=? AND id=?' in row[3] for row in plans)


def test_legacy_reference_presence_does_not_certify_another_branch_birth(tmp_path):
    _,store,value=seeded(tmp_path)
    value['after']['events'][0]['facts']=[]
    records=[r for r in project(store,value) if r['kind']!='projection_materialization']
    for r in records:
        if r['kind']=='reference_event':r['data']['event_key']='another-branch-birth'
    store.ingest('legacy','branch',records)
    repaired=project(store,value)
    events=[r for r in repaired if r['kind']=='reference_event']
    assert events and all(r['data']['event_key']!='another-branch-birth' for r in events)


def test_unverified_legacy_library_rebuilds_lineage_proof(tmp_path,monkeypatch):
    root,store,_=seeded(tmp_path)
    _,records=LibraryIndexer(root).index('MU',1)
    store.ingest('legacy','library',records)
    import sqlite3
    with sqlite3.connect(root/'US/MU/event_library.sqlite3') as db:
        db.execute("UPDATE v2_native_identities SET provenance='INFERRED' WHERE collection='library_versions'")
    indexer=LibraryIndexer(root);original=indexer.index;calls=[]
    def index(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(indexer,'index',index)
    _,records=indexer.ensure(store,'MU',1)
    assert calls and any(r['kind']=='lineage_gap' for r in records)
