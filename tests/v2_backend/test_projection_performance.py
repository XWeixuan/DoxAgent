import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from doxagent.v2_read.native_content import NativeContent
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.repository import ReadStore


def test_skipped_prefix_crash_replays_without_crossing_failure(tmp_path):
    store = ReadStore(tmp_path / 'read.db'); store.migrate()
    crash = [True]
    def mapper(event):
        if crash[0] and event['seq'] == 4:
            raise RuntimeError('crash')
        return [dict(kind='case_test',ticker='MU',id='c',data={'n':event['seq']})], []
    worker = ProjectionWorker(store, [], mapper)
    def load(n):
        return dict(seq=n,recorded_at=None,serving_skipped=n in (1,2,5))
    with pytest.raises(RuntimeError):
        worker._events(SimpleNamespace(source='runtime'), [1,2,3,4,5], load, budget=10)
    assert store.position('runtime') == 3
    assert store.get('case_test','MU','c') == {'n':3}
    crash[0] = False
    worker._events(SimpleNamespace(source='runtime'), [4,5,6], load, budget=10)
    assert store.position('runtime') == 6


def test_only_verified_skips_coalesce(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    worker=ProjectionWorker(store,[],lambda e: ([],[]))
    calls=[];original=store.ingest
    def ingest(*a,**kw):calls.append(kw['position']);return original(*a,**kw)
    store.ingest=ingest
    worker._events(SimpleNamespace(source='runtime'), list(range(1,101)),
                   lambda n:dict(seq=n,recorded_at=None,serving_skipped=True),budget=10)
    assert calls==[100] and store.position('runtime')==100


def test_repair_reader_reused_and_unattempted_counts_unchanged(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    for n in (1,2,3):store.ingest('runtime',str(n),[],position=n,gap='ValueError')
    loads=[];opens=[]
    @contextmanager
    def reader():
        opens.append(1)
        def load(n):loads.append(n);return dict(seq=n,recorded_at=None)
        yield load
    source=SimpleNamespace(source='runtime',event_reader=reader)
    worker=ProjectionWorker(store,[source],lambda e: ([],[]))
    assert worker.repair(limit=3,budget=0)==0
    with store.connect() as db:assert [r[0] for r in db.execute('SELECT attempts FROM gaps')]==[0,0,0]
    assert worker.repair(limit=3,budget=10)==3
    assert len(opens)==1 and len(loads)==3
    with store.connect() as db:assert db.execute('SELECT count(*) FROM gaps').fetchone()[0]==0


def test_thin_case_cache_preserves_direction_and_owns_mutable_result(tmp_path):
    codec=NativeContent(tmp_path/'source.db')
    frozen={'projection':{'policies':[{'policy_id':'p','activation_conditions':[{'condition_id':'c','decision':'SHORT','unused':'large'}]}]},
            'w3_execution_state':{'phase':'trade_decision'},'policy_watch_followup':{'active':True},'unneeded':'large '*10000}
    encoded=json.loads(codec.encode({'frozen_inputs':frozen,'source':{'snapshot':{'ticker':'MU'}},'status':'PENDING_W3'}))
    raw=json.dumps(encoded)
    first=codec.projection_case(raw)
    assert first['frozen_inputs']['projection']['policies'][0]['activation_conditions'][0]['decision']=='SHORT'
    assert 'unneeded' not in first['frozen_inputs']
    first['frozen_inputs']['w3_execution_state']['phase']='mutated'
    second=codec.projection_case(raw)
    assert second['frozen_inputs']['w3_execution_state']['phase']=='trade_decision'
    assert codec.decode(encoded)['frozen_inputs']==frozen
    assert codec._projection_frozen_bytes<=16*1024*1024


def test_fact_sync_pages_resume_after_process_restart(tmp_path):
    from tests.v2_backend.test_source_lifecycle import fixture_source
    from doxagent.v2_read.source_lifecycle import sync_business_facts
    from doxagent.v2_read.outbox import SourceOutbox
    source=fixture_source(tmp_path);source.source='runtime'
    store=ReadStore(tmp_path/'read.db');store.migrate();source.claim_serving_facts(store.path)
    for n in range(40):
        store.ingest('runtime',str(n),[dict(kind='attempt',ticker='MU',id=str(n),data={'status':'SUCCEEDED'})])
    assert sync_business_facts(source,store,budget=0) is False
    assert sync_business_facts(source,store) is False
    restarted=SourceOutbox(source.path,'runtime')
    assert sync_business_facts(restarted,store) is False
    assert sync_business_facts(restarted,store) is True
    with restarted.connection() as db:
        assert db.execute("SELECT count(*) FROM v2_serving_business_facts WHERE kind='attempt'").fetchone()[0]==40


def test_fact_restart_floor_is_prior_ack_receipt_not_head(tmp_path):
    from tests.v2_backend.test_source_lifecycle import fixture_source
    from doxagent.v2_read.source_lifecycle import sync_business_facts
    import hashlib
    source=fixture_source(tmp_path);source.source='runtime'
    store=ReadStore(tmp_path/'read.db');store.migrate();source.claim_serving_facts(store.path)
    for n in range(1,41):
        store.ingest('runtime',str(n),[dict(kind='attempt',ticker='MU',id=str(n),data={'status':'SUCCEEDED'})])
    consumer='read:'+hashlib.sha256(str(store.path).encode()).hexdigest()[:24]
    with source.connection(write=True) as db:
        db.execute("INSERT INTO v2_source_consumers VALUES(?,?,?)",(consumer,20,'2026-11-12T00:00:00Z'))
        db.execute("INSERT INTO v2_serving_business_facts VALUES('attempt','MU','existing','{}')")
    assert sync_business_facts(source,store) is False
    # One page starts after prior ACK=20, not current head=40; 16 rows copy.
    with source.connection() as db:
        ids=[r[0] for r in db.execute("SELECT id FROM v2_serving_business_facts WHERE id<>'existing'")]
    assert set(ids)=={str(n) for n in range(21,37)}
    assert sync_business_facts(source,store) is True
