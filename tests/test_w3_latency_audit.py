import json
import sqlite3
from datetime import datetime,UTC,timedelta

from scripts.ops.w3_latency_audit import breakdown,audit


def test_internal_time_is_union_based_and_charges_all_scheduler_delay():
    result=breakdown(0,20,[(1,5),(3,7)],[(6,10)])
    assert result['sdk_ms']==6000
    assert result['provider_wait_ms']==3000
    assert result['non_sdk_ms']==11000


def test_five_second_gate_is_whole_case_not_individual_phases(tmp_path):
    runtime,jobs=tmp_path/'runtime.db',tmp_path/'jobs.db'
    origin=datetime(2026,11,10,12,tzinfo=UTC)
    stamp=lambda n:(origin+timedelta(seconds=n)).isoformat()
    cid='case_0123456789abcdef0123456789abcdef'
    with sqlite3.connect(runtime) as db:
        db.executescript('''CREATE TABLE runtime_v2_cases(case_id TEXT,created_at TEXT,payload_json TEXT);
            CREATE TABLE runtime_v2_effects(effect_id TEXT,case_id TEXT,effect_type TEXT,status TEXT,created_at TEXT);
            CREATE TABLE runtime_v2_w3_stages(case_id TEXT,phase TEXT,status TEXT,worker_job_id TEXT);
            CREATE TABLE runtime_values(namespace TEXT,key TEXT,payload TEXT,PRIMARY KEY(namespace,key));''')
        db.execute('INSERT INTO runtime_v2_cases VALUES(?,?,?)',(cid,stamp(0),'{"runtime_mode":"REALTIME"}'))
        db.execute('INSERT INTO runtime_v2_cases VALUES(?,?,?)',('future',stamp(100),'{"runtime_mode":"REALTIME"}'))
        for phase,eid,start,end in [('trade_decision','trade',0,11),('post_decision_operations','ops',11,22)]:
            db.execute('INSERT INTO runtime_v2_effects VALUES(?,?,?,?,?)',
                       (eid,cid,'route_to_w3' if eid=='trade' else 'w3_post_decision','COMPLETED',stamp(start)))
            db.execute('INSERT INTO runtime_v2_w3_stages VALUES(?,?,?,?)',(cid,phase,'COMMITTED',eid))
            db.execute('INSERT INTO runtime_values VALUES(?,?,?)',('w3_boundary_timing',eid+':1',
                       json.dumps({'started_at':stamp(start),'finished_at':stamp(end),
                                   'effect_available_at':stamp(start)})))
            if eid=='trade':
                # A provider wait before this cohort begins must not replace
                # the caller's upper time bound while iterating later Cases.
                db.execute('INSERT INTO runtime_values VALUES(?,?,?)',('w3_boundary_timing',eid+':0',
                           json.dumps({'started_at':stamp(start-1),'finished_at':stamp(start),
                                       'effect_available_at':stamp(start-1),
                                       'provider_wait':{'observed_at':stamp(start-1),'retry_at':stamp(start)}})))
    with sqlite3.connect(jobs) as db:
        db.executescript('''CREATE TABLE jobs(id TEXT,status TEXT,payload TEXT);
            CREATE INDEX jobs_run_owner ON jobs(json_extract(payload,'$.run_id'));''')
        for eid,phase,start,end in [('trade','trade_decision',2,10),('ops','post_decision_operations',12,20)]:
            db.execute('INSERT INTO jobs VALUES(?,?,?)',(eid,'succeeded',json.dumps(dict(run_id='run',
                attempt_id=cid[-24:]+'-'+phase,resource_receipt={'execution_timing':{
                    'sdk_dispatched_at':stamp(start),'sdk_completed_at':stamp(end)}}))))
    result=audit(runtime,jobs,since=stamp(-1),until=stamp(50))
    assert result['measured_count']==1 and result['incomplete_count']==0
    assert result['non_sdk_ms']['p95']==6000
    assert not result['p95_within_5s']  # 3s + 3s must not pass as two separate gates.
