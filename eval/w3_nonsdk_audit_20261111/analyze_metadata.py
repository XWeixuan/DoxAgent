"""Timing ledger lower bounds. Local backoff is INTERNAL, never excluded."""
from datetime import datetime
import json
import math
from pathlib import Path

FOLDER=Path(__file__).parent

def instant(v):return datetime.fromisoformat(v.replace('Z','+00:00')).timestamp() if v else None
def union(xs):
    out=[]
    for a,b in sorted((a,b) for a,b in xs if b>=a):
        if out and a<=out[-1][1]:out[-1][1]=max(out[-1][1],b)
        else:out.append([a,b])
    return out
def seconds(xs):return sum(b-a for a,b in union(xs))
def overlap(xs,ys):return seconds([(max(a,c),min(b,d)) for a,b in union(xs) for c,d in union(ys) if min(b,d)>=max(a,c)])
def stats(values):
    x=sorted(values)
    if not x:return {'n':0}
    q=lambda p:x[max(0,math.ceil(len(x)*p)-1)]
    return dict(n=len(x),p50=q(.5),p95=q(.95),maximum=x[-1],over5=sum(v>5 for v in x))

def analyze(metadata='metadata.json', output='ledger_analysis.json'):
    data=json.loads((FOLDER/metadata).read_text());versions=json.loads((FOLDER/'production-version-verification.json').read_text(encoding='utf-8-sig'))
    restart=instant(versions['roles']['v2-scheduler']['started_at']);hist=set(data['historical_case_ids']);rows=[]
    for case in data['cases']:
        for stage in case['stages']:
            phase=stage['phase'];etype='route_to_w3' if phase=='trade_decision' else 'w3_post_decision'
            effect=next((e for e in case['effects'] if e['effect_type']==etype),None)
            if not effect:continue
            group=('historical_recovery' if case['case_id'] in hist else
                   'new_after_convergence' if instant(case['created_at'])>=restart else
                   'same_w3_before_convergence' if instant(case['created_at'])>=instant('2026-11-10T16:00:22.603+00:00')
                   else 'old_or_cross_incident')
            row=dict(case_id=case['case_id'],ticker=case['ticker'],phase=phase,group=group,
                created_at=case['created_at'],effect_id=effect['effect_id'],effect=effect,stage=stage,
                ready=case.get('ready'),complete=False,missing=[])
            jobs=[j for j in case['jobs'] if j['attempt_id'].startswith(case['case_id'][-24:]+'-'+phase)]
            row['job_ids']=[j['id'] for j in jobs]
            if stage['status']!='COMMITTED' or effect['status']!='COMPLETED':row['missing'].append('phase_or_effect_not_complete')
            if not jobs:row['missing'].append('worker_job')
            current=next((j for j in jobs if j['id']==stage.get('worker_job_id')),None)
            if not current:row['missing'].append('committed_worker_job')
            sdk=[]
            for j in jobs:
                timing=j.get('execution_timing') or {};a,b=instant(timing.get('sdk_dispatched_at')),instant(timing.get('sdk_completed_at'))
                if a is not None and b is not None and b>=a:sdk.append((a,b))
                elif j['error_code'] not in {'CODEX_START_TIMEOUT','WORKER_MCP_UNAVAILABLE'}:
                    row['missing'].append('sdk_timing:'+j['id'])
            if row['missing']:
                rows.append(row);continue
            start=instant(effect['created_at'])
            # Operations cannot run before the Trade commit. Effect objects are
            # constructed slightly earlier, before encoding/that transaction.
            # Keep this predecessor work in Trade and in the whole-Case envelope.
            if phase=='post_decision_operations':
                predecessor=next((s for s in case['stages'] if s['phase']=='trade_decision'),None)
                if predecessor and predecessor.get('committed_at'):
                    start=max(start,instant(predecessor['committed_at']))
            row['predecessor_creation_overlap_s']=start-instant(effect['created_at'])
            row['measured_start_epoch']=start
            end=max(instant(effect['updated_at']),instant(stage['committed_at']))
            sdk=[(max(a,start),min(b,end)) for a,b in sdk if b>start and a<end]
            internal=lambda xs:seconds(xs)-overlap(xs,sdk)
            first=min(instant(j['created_at']) for j in jobs)
            q=[];startup=[];handoff=[]
            for j in jobs:
                t=j.get('execution_timing') or {};created,started=instant(j['created_at']),instant(j['started_at'])
                a,b=instant(t.get('sdk_dispatched_at')),instant(t.get('sdk_completed_at'));finish=instant(j['finished_at'])
                if started:q.append((created,started))
                if started and a:startup.append((started,a))
                if b and finish:handoff.append((b,finish))
            commit=instant(stage['committed_at']);finish=instant(current['finished_at'])
            pieces={'effect_entry_and_prepare':[(start,first)],'worker_queue':q,'worker_startup':startup,
                    'sdk_end_to_worker_terminal':handoff,'terminal_to_business_commit':[(finish,commit)],
                    'postcommit_effect_finish':[(commit,end)]}
            parts={name:internal(xs) for name,xs in pieces.items()}
            covered=[x for xs in pieces.values() for x in xs]
            total=end-start;sdk_s=seconds(sdk);non=total-sdk_s
            parts['interattempt_or_unassigned']=max(0,non-internal(covered))
            row.update(complete=True,total_s=total,sdk_s=sdk_s,non_sdk_s=non,parts=parts,
                       sdk_intervals=sdk,job_count=len(jobs),rate_failures=sum('429' in (j['error_message'] or '') for j in jobs))
            rows.append(row)
    summary={}
    for group in ['same_w3_before_convergence','new_after_convergence','historical_recovery','old_or_cross_incident']:
        for phase in ['trade_decision','post_decision_operations']:
            selected=[r for r in rows if r['group']==group and r['phase']==phase];complete=[r for r in selected if r['complete']]
            summary[group+'/'+phase]=dict(total=len(selected),incomplete=len(selected)-len(complete),
                non_sdk_s=stats([r['non_sdk_s'] for r in complete]),sdk_s=stats([r['sdk_s'] for r in complete]),
                parts_s={k:stats([r['parts'][k] for r in complete]) for k in (complete[0]['parts'] if complete else [])})
    case_rows=[]
    for cid in {r['case_id'] for r in rows}:
        phases=[r for r in rows if r['case_id']==cid]
        if len(phases)!=2 or not all(r['complete'] for r in phases):continue
        begin=min(instant(r['effect']['created_at']) for r in phases)
        end=max(instant(r['effect']['updated_at']) for r in phases)
        sdk=seconds([interval for r in phases for interval in r['sdk_intervals']])
        case_rows.append(dict(case_id=cid,group=phases[0]['group'],non_sdk_s=end-begin-sdk,
                              sdk_s=sdk,total_s=end-begin))
    case_summary={g:stats([r['non_sdk_s'] for r in case_rows if r['group']==g]) for g in {r['group'] for r in case_rows}}
    result=dict(snapshot_at=data['at'],restart_at=versions['roles']['v2-scheduler']['started_at'],
                note='SDK Turn wall envelope; application backoff remains internal; business timestamps give lower bounds',
                summary=summary,rows=rows,case_rows=case_rows,case_summary=case_summary)
    (FOLDER/output).write_text(json.dumps(result,indent=2))
    print(json.dumps(summary,indent=2))
    print('TOP_FRESH',json.dumps([{k:r[k] for k in ['case_id','ticker','phase','group','non_sdk_s','parts','job_count','rate_failures']} for r in sorted(
        [r for r in rows if r['group']=='new_after_convergence' and r['complete']],key=lambda r:r['non_sdk_s'],reverse=True)[:12]],indent=2))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--metadata',default='metadata.json');parser.add_argument('--output',default='ledger_analysis.json')
    args=parser.parse_args();analyze(args.metadata,args.output)
