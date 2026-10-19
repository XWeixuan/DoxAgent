import json
import pytest
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.graph import project
from doxagent.v2_read.source_snapshot import settle_cases
from doxagent.v2_read.runtime import hot_path_contributions,w3_contributions


@pytest.mark.parametrize('failed',[False,True])
def test_final_snapshot_graph_matches_incremental_turn_history(tmp_path,failed):
    summary=dict(case_id='case',results=['FAILURE'] if failed else ['ARCHIVE'],first_round_shape='PARALLEL',w3_status=None,status='FAILED' if failed else 'COMPLETED',semantic_day='2026-10-01',received_at='2026-10-01T10:00:00Z')
    native=dict(case_id='case',runtime_mode='REALTIME',status='FAILED' if failed else 'COMPLETED',trading_date='2026-10-01',source=dict(snapshot=dict(ticker='MU')),w1_final=dict(confidence='low'),w2_final=None if failed else dict(confidence='high'))
    attempts=[]
    for node in ['W1','W2']:
        data=dict(node_id=node,round='R1',status='FAILED' if failed and node=='W2' else 'SUCCEEDED',timing=dict(first_started_at=dict(value='2026-10-01T10:00:00Z'),completed_at=dict(value='2026-10-01T10:00:10Z')))
        attempts.append(dict(kind='attempt',ticker='MU',id=node,parent='case',route=node,data=data))
    outcomes=[]
    for mode in ['incremental','once']:
        store=ReadStore(tmp_path/(mode+'.db'));store.migrate()
        store.ingest('test','seed',[dict(kind='case',ticker='MU',id='case',data=summary),dict(kind='native:runtime_v2_cases',ticker='MU',id='case',data=native)])
        summary=store.get('case','MU','case')
        for index,attempt in enumerate(attempts):
            records,counts=project(store,'MU',summary,[attempt]) if mode=='incremental' else ([],[])
            store.ingest('test',str(index),[attempt,*records],contributions=counts)
        if mode=='once':
            settle_cases(store)
        else:
            store.ingest('test','timing',[],contributions=hot_path_contributions(store,native)+w3_contributions(store,native))
        with store.connect() as db:
            objects=list(db.execute("SELECT kind,id,payload FROM object_current WHERE kind IN ('graph_case','graph_member','graph_observation') ORDER BY kind,id"))
            counts=list(db.execute("SELECT metric,ticker,entity,day,dimensions,value FROM contributions_current WHERE metric LIKE 'graph_%' OR metric IN ('hot_path_seconds','hot_path_samples','w3_seconds','w3_samples') ORDER BY metric,ticker,entity,day,dimensions"))
        outcomes.append(([tuple(r) for r in objects],[tuple(r) for r in counts]))
        samples=[r[-1] for r in counts if r[0]=='hot_path_samples']
        assert samples==([] if failed else ['1'])
    assert outcomes[0]==outcomes[1]


def test_runtime_snapshot_settles_timing_after_native_turn_import(tmp_path,monkeypatch):
    from datetime import UTC,datetime
    from doxagent.v2_read.formal import FormalProjectors
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.source_snapshot import rebuild_current
    from tests.test_runtime_orchestration_execution import runtime_at
    from tests.test_persistent_runtime_v2 import _source
    runtime,journal=runtime_at(tmp_path,[datetime(2026,9,8,12,tzinfo=UTC)])
    try:
        case=runtime.execute_message(_source())
        source=SourceOutbox(journal.path,'runtime')
        source.migrate(('runtime_v2_cases','runtime_v2_turns'))
        original=FormalProjectors.__init__
        def initialize(self,store,*args,**kwargs):
            original(self,store,*args,**kwargs)
            # Other source owners normally provide these independent facts.
            store.ingest('fixture','admission',[
                dict(kind='business_provenance',ticker='MU',id=case.case_id,data={'basis':'fixture'}),
                dict(kind='native:source_definitions',ticker='',id=case.source.source_id,data={'display_name':'Fixture API','kind':'api'}),
            ])
        monkeypatch.setattr(FormalProjectors,'__init__',initialize)
        report=rebuild_current([source],tmp_path/'rebuilt'/'read.db')
        assert report['verified'] and report['settled_cases']==1
        store=ReadStore(report['path'])
        assert store.get('native:runtime_v2_cases','MU',case.case_id)['runtime_mode']=='REALTIME'
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM object_current WHERE kind='attempt'").fetchone()[0]>0
            sample=db.execute("SELECT value FROM contributions_current WHERE metric='hot_path_samples' AND entity=?",(case.case_id,)).fetchone()
        assert sample[0]=='1'
    finally:runtime.close()


@pytest.mark.parametrize('status',['COMPLETED','FAILED'])
def test_final_snapshot_w3_timing_uses_terminal_attempts(tmp_path,status):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    summary=dict(case_id='w3',results=['ARCHIVE'] if status=='COMPLETED' else ['FAILURE'],first_round_shape='PARALLEL',initial_route='W3',w3_status='RESOLVED' if status=='COMPLETED' else 'FAILED',status=status,semantic_day='2026-10-01',received_at='2026-10-01T10:00:00Z')
    native=dict(case_id='w3',runtime_mode='REALTIME',status=status,trading_date='2026-10-01',source=dict(snapshot=dict(ticker='MU')))
    store.ingest('snapshot','case-first',[dict(kind='case',ticker='MU',id='w3',data=summary),dict(kind='native:runtime_v2_cases',ticker='MU',id='w3',data=native)],contributions=w3_contributions(store,native))
    attempt=dict(node_id='W3',round='AGENT',status='SUCCEEDED' if status=='COMPLETED' else 'FAILED',timing=dict(first_started_at=dict(value='2026-10-01T10:00:10Z'),completed_at=dict(value='2026-10-01T10:00:25Z')))
    store.ingest('snapshot','attempt-later',[dict(kind='attempt',ticker='MU',id='w3-attempt',parent='w3',route='W3',data=attempt)])
    settle_cases(store)
    with store.connect() as db:
        values=dict(db.execute("SELECT metric,value FROM contributions_current WHERE metric IN ('w3_seconds','w3_samples')"))
    assert values=={'w3_seconds':'15.0','w3_samples':'1'}
