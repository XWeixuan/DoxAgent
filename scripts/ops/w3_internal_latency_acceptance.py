"""Bounded read-only W3 acceptance. No model calls, body export or business writes."""
from contextlib import closing
from datetime import UTC,datetime
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess

REPO=Path(__file__).resolve().parents[2]
VOLUME=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value);return value


def collect(since,until=None,ids=None):
    helpers=REPO/'eval/w3_nonsdk_audit_20261111'
    collector=module('metadata',helpers/'collect_metadata.py')
    math=module('ledger',helpers/'analyze_metadata.py')
    instant,union,seconds,overlap,stats=math.instant,math.union,math.seconds,math.overlap,math.stats
    lower,upper=instant(since),instant(until) if until else datetime.now(UTC).timestamp()
    selector_truncated=False
    if ids is None:
        with closing(collector.connect(VOLUME/'runtime/runtime.sqlite3')) as db:
            maximum=db.execute('SELECT max(rowid) FROM runtime_v2_effects').fetchone()[0] or 0
            records=db.execute("SELECT case_id,effect_type,json_extract(payload_json,'$.created_at') created "
                               "FROM runtime_v2_effects WHERE rowid>? ORDER BY rowid",(max(0,maximum-5000),)).fetchall()
        selector_truncated=bool(maximum>5000 and records and min(instant(r['created']) for r in records)>lower)
        ids=list(dict.fromkeys(r['case_id'] for r in records if r['effect_type']=='route_to_w3' and lower<=instant(r['created'])<=upper))
    data=collector.collect(case_ids=ids) if ids else {'cases':[],'budget_exhausted':False}
    traces=[];log_budgets={};parse_errors=0;unattributed_tools=0;id_set=set(ids)
    for role in ['v2-scheduler','codex-worker']:
        proc=subprocess.Popen(['docker','logs','--since',since,'--tail','6000','doxagent-v2-'+role+'-1'],
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        total=0;lines=0;truncated=False
        try:
            while raw:=proc.stdout.readline(256*1024):
                total+=len(raw)
                lines+=1
                if total>16*1024**2:
                    truncated=True;proc.terminate();break
                if b'w3_boundary_timing ' not in raw:continue
                try:
                    trace=json.loads(raw.split(b'w3_boundary_timing ',1)[1])
                    if trace.get('case_id') in id_set:traces.append(trace)
                    elif trace.get('segment')=='research_tool' and not trace.get('case_id'):
                        unattributed_tools+=1
                except ValueError:parse_errors+=1
            proc.wait(timeout=5)
        finally:
            proc.stdout.close()
            if proc.poll() is None:proc.kill();proc.wait()
        log_budgets[role]={'bytes':total,'byte_cap_reached':truncated,'tail_cap_reached':lines>=6000}
    owners={}
    for trace in traces:
        if trace.get('segment')!='effect_owner' or not trace.get('segment_complete'):continue
        result=trace.get('milestones',{}).get('effect.body_return',{}).get('result')
        if result!=1:continue
        key=trace['effect_id']
        if key not in owners or trace['finished_at']>owners[key]['finished_at']:owners[key]=trace
    rows=[];case_rows=[]
    for case in data['cases']:
        phases=[]
        for phase,kind in [('trade_decision','route_to_w3'),('post_decision_operations','w3_post_decision')]:
            effect=next((e for e in case['effects'] if e['effect_type']==kind),None)
            stage=next((s for s in case['stages'] if s['phase']==phase),None)
            row=dict(case_id=case['case_id'],ticker=case['ticker'],phase=phase,complete=False,missing=[])
            if not effect or not stage:
                row['missing'].append('phase_not_started');rows.append(row);continue
            owner=owners.get(effect['effect_id']);start=instant(effect['created_at'])
            if phase=='post_decision_operations':
                trade=next((e for e in case['effects'] if e['effect_type']=='route_to_w3'),None)
                predecessor=owners.get(trade['effect_id']) if trade else None
                committed=(predecessor or {}).get('milestones',{}).get('phase.business_committed',{}).get('at')
                if committed:start=max(start,instant(committed))
                else:row['missing'].append('trade_commit_return_boundary')
            jobs=[j for j in case['jobs'] if j['attempt_id'].startswith(case['case_id'][-24:]+'-'+phase)]
            sdk=[]
            for job in jobs:
                timing=job.get('execution_timing') or {};a,b=instant(timing.get('sdk_dispatched_at')),instant(timing.get('sdk_completed_at'))
                if a and b:sdk.append((a,b))
                elif job['error_code'] not in {'CODEX_START_TIMEOUT','WORKER_MCP_UNAVAILABLE'}:
                    row['missing'].append('sdk_timing:'+job['id'])
            if not jobs:row['missing'].append('worker_job')
            if effect['status']!='COMPLETED' or stage['status']!='COMMITTED':row['missing'].append('not_committed')
            if not owner:row['missing'].append('owner_return_boundary')
            else:
                commit=owner.get('milestones',{}).get('phase.business_committed',{}).get('at')
                if not commit:row['missing'].append('business_commit_return_boundary')
            if row['missing']:
                row['known_job_queue_s']=sum(max(0,(instant(j['started_at']) or upper)-instant(j['created_at'])) for j in jobs)
                rows.append(row);continue
            end=instant(owner['finished_at']);sdk=[(max(a,start),min(b,end)) for a,b in sdk if b>start and a<end]
            tools=[t for t in traces if t.get('segment')=='research_tool' and t.get('case_id')==case['case_id'] and t.get('task_phase')==phase]
            tool_intervals=[(instant(t['started_at']),instant(t['finished_at'])) for t in tools]
            nested=overlap(tool_intervals,sdk)
            sdk_seconds=seconds(sdk)
            first=min(instant(j['created_at']) for j in jobs)
            current=next((j for j in jobs if j['id']==stage['worker_job_id']),None)
            if not current:
                row['missing'].append('committed_worker_job');rows.append(row);continue
            parts={
                'eligible_to_job':first-start,
                'job_queue':sum(instant(j['started_at'])-instant(j['created_at']) for j in jobs if j['started_at']),
                'worker_startup':sum(max(0,instant((j.get('execution_timing') or {}).get('sdk_dispatched_at'))-instant(j['started_at'])) for j in jobs if j['started_at'] and (j.get('execution_timing') or {}).get('sdk_dispatched_at')),
                'finished_to_commit':instant(commit)-instant(current['finished_at']),
                'commit_to_owner_end':end-instant(commit),
            }
            row.update(complete=True,start_epoch=start,end_epoch=end,non_sdk_s=end-start-sdk_seconds+nested,
                       boundary_non_sdk_s=end-start-sdk_seconds,sdk_envelope_s=sdk_seconds,
                       nested_dox_tool_s=nested,tool_calls=len(tools),parts=parts,sdk_intervals=sdk,
                       tool_intervals=tool_intervals,owner=owner,job_ids=[j['id'] for j in jobs])
            phases.append(row);rows.append(row)
        if len(phases)==2:
            start=min(p['start_epoch'] for p in phases);end=max(p['end_epoch'] for p in phases)
            sdk=[i for p in phases for i in p['sdk_intervals']];tools=[i for p in phases for i in p['tool_intervals']]
            case_rows.append(dict(case_id=case['case_id'],non_sdk_s=end-start-seconds(sdk)+overlap(tools,sdk),
                                  boundary_non_sdk_s=end-start-seconds(sdk),nested_dox_tool_s=overlap(tools,sdk)))
    summary={}
    for phase in ['trade_decision','post_decision_operations']:
        selected=[r for r in rows if r['phase']==phase];complete=[r for r in selected if r['complete']]
        summary[phase]=dict(total=len(ids),complete=len(complete),incomplete=len(ids)-len(complete),
                            internal_s=stats([r['non_sdk_s'] for r in complete]),
                            boundary_internal_s=stats([r['boundary_non_sdk_s'] for r in complete]),
                            sdk_envelope_s=stats([r['sdk_envelope_s'] for r in complete]),
                            nested_dox_tool_s=stats([r['nested_dox_tool_s'] for r in complete]),
                            parts={k:stats([r['parts'][k] for r in complete]) for k in (complete[0]['parts'] if complete else [])})
    return dict(at=datetime.now(UTC).isoformat(),since=since,until=until,case_ids=ids,
                selector_truncated=selector_truncated,metadata_budget_exhausted=data.get('budget_exhausted',False),
                log_budgets=log_budgets,external_provider_wait_excluded_s=0,summary=summary,
                trace_parse_errors=parse_errors,unattributed_tool_receipts=unattributed_tools,
                full_case=stats([c['non_sdk_s'] for c in case_rows]),case_rows=case_rows,rows=rows,
                trace_count=len(traces),metadata=data,traces=traces)


if __name__=='__main__':
    import argparse
    os.nice(15)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--since',required=True);p.add_argument('--until')
    p.add_argument('--case-ids',type=Path);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();value=collect(args.since,args.until,json.loads(args.case_ids.read_text()) if args.case_ids else None)
    args.out.write_text(json.dumps(value,separators=(',',':')))
    print(json.dumps({k:value[k] for k in ['at','summary','full_case','trace_count','selector_truncated','metadata_budget_exhausted']}))
