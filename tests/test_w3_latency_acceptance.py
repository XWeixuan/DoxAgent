"""Acceptance must retain internal retries, nested tools and real commit tails."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(path):
    spec=importlib.util.spec_from_file_location('acceptance_fixture',ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('missing',[False,True])
def test_real_completion_retry_and_nested_tools_are_all_counted(monkeypatch,missing):
    target=load('scripts/ops/w3_internal_latency_acceptance.py')
    math=load('eval/w3_nonsdk_audit_20261111/analyze_metadata.py')
    stamp=lambda n:f'2026-11-11T00:00:{n:06.3f}+00:00'
    cid='case_123456789012345678901234'
    phases=['trade_decision','post_decision_operations']
    effects=[dict(effect_id='t',effect_type='route_to_w3',created_at=stamp(0),status='COMPLETED'),
             dict(effect_id='o',effect_type='w3_post_decision',created_at=stamp(4.5),status='COMPLETED')]
    stages=[dict(phase=p,status='COMMITTED',worker_job_id=j,committed_at=stamp(n))
            for p,j,n in zip(phases,['t2','o1'],[4.5,7.6])]
    jobs=[]
    for phase,j,created,started,sdk_start,sdk_end,end in [
        (phases[0],'t1',.1,.2,1,2,2.1),
        (phases[0],'t2',2.5,2.6,3,4,4.1),
        (phases[1],'o1',5,5.1,6,7,7.1),
    ]:
        jobs.append(dict(id=j,attempt_id=cid[-24:]+'-'+phase+'-1',created_at=stamp(created),
                         started_at=stamp(started),finished_at=stamp(end),error_code=None,
                         execution_timing=dict(sdk_dispatched_at=stamp(sdk_start),sdk_completed_at=stamp(sdk_end))))
    case=dict(case_id=cid,ticker='MU',effects=effects,stages=stages,jobs=jobs)
    traces=[]
    for eid,commit,end in [('t',4.6,5),('o',7.7,8)]:
        traces.append(dict(segment='effect_owner',case_id=cid,effect_id=eid,segment_complete=True,
                           finished_at=stamp(end),milestones={'effect.body_return':{'result':1},
                                                           'phase.business_committed':{'at':stamp(commit)}}))
    for phase,start,end in [(phases[0],1.5,1.7),(phases[1],6.2,6.3)]:
        traces.append(dict(segment='research_tool',case_id=cid,task_phase=phase,
                           started_at=stamp(start),finished_at=stamp(end)))
    if missing:traces[1]['milestones'].pop('phase.business_committed')
    payload=b''.join(b'w3_boundary_timing '+json.dumps(t).encode()+b'\n' for t in traces)
    class Logs:
        def __init__(self,*args,**kwargs):self.stdout=io.BytesIO(payload)
        def wait(self,**kwargs):return 0
        def poll(self):return 0
    collector=SimpleNamespace(collect=lambda **kw:dict(cases=[case]))
    monkeypatch.setattr(target,'module',lambda name,path:collector if name=='metadata' else math)
    monkeypatch.setattr(target.subprocess,'Popen',Logs)
    value=target.collect(stamp(0),stamp(10),[cid])
    # Both fake role logs contain the same tool intervals: union must deduplicate.
    trade,operations=value['rows']
    assert trade['non_sdk_s']==pytest.approx(3.2)
    if missing:
        assert not operations['complete']
        assert 'business_commit_return_boundary' in operations['missing']
        assert operations['known_result_to_stage_commit_lower_bound_s']==pytest.approx(.5)
        assert operations['known_boundary_internal_lower_bound_s']==pytest.approx(2)
        assert value['full_case']['n']==0
    else:
        assert operations['non_sdk_s']==pytest.approx(2.5)
        assert value['full_case']['p95']==pytest.approx(5.3)
        assert value['full_case']['over5']==1
