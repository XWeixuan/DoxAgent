"""Reproducible fixed-window source, unit, article and stage analysis."""
import csv
import gzip
import json
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
from doxagent.message_bus_v2.cadence import cadence,distribution

ROOT=Path(__file__).parent
def read(name):
    with gzip.open(ROOT/name,'rt',encoding='utf-8') as stream:return json.load(stream)
def rows(name):
    with gzip.open(ROOT/name,'rt',encoding='utf-8') as stream:
        return [x for line in stream if (x:=json.loads(line)).get('kind') not in {'window','complete'}]
def at(value):return datetime.fromisoformat(value.replace('Z','+00:00')) if value else None
def diff(a,b):return (at(b)-at(a)).total_seconds() if a and b else None
def stats(values):return distribution([v for v in values if v is not None])
import sys
prefix=sys.argv[1]
output_prefix=sys.argv[4] if len(sys.argv)>4 else prefix
bus=read(prefix+'-bus.json.gz');site={'events':[]} if not (ROOT/(prefix+'-site.json.gz')).exists() else read(prefix+'-site.json.gz')
runs={r['run_id']:r for r in rows(prefix+'-runs.jsonl.gz')}
captures=rows(prefix+'-captures.jsonl.gz')
S=at(sys.argv[2] if len(sys.argv)>2 else bus['since']);E=at(sys.argv[3] if len(sys.argv)>3 else bus['until'])
config={s['source_id']:s for s in bus['sources']}
summary={'since':S.isoformat(),'until':E.isoformat(),'windows':{},'rows':dict(runs=len(runs),captures=len(captures)),
         'config':dict(sources=len(config),enabled_sources=sum(s['enabled'] for s in config.values()),
         bindings=len(bus['bindings']),active_bindings=sum(b['enabled'] and not b.get('tombstoned_at') for b in bus['bindings']))}
detail=[]
for label,start,end in [('window',S,E)]:
    rr=[r for r in runs.values() if start<=at(r['dispatch_at'])<=end]
    cc=[c for c in captures if start<=at(c['observed_at'])<=end]
    # Keep one minute of predecessor requests/list captures for gap boundaries.
    result=cadence([*bus['schedules'],*rr],cc,start,end)
    sources=defaultdict(list)
    for unit in result['units']:
        if not unit['expected_cycles']:continue
        source=unit['binding_id'].partition(':')[2]
        sources[source].append(unit)
        detail.append({'window':label,'source_id':source,**{k:v for k,v in unit.items() if not isinstance(v,dict)},
                       **{'request_late_'+k:v for k,v in unit['request_late_seconds'].items()},
                       **{'fresh_gap_'+k:v for k,v in unit['fresh_gap_seconds'].items()}})
    out=[]
    for source,units in sources.items():
        row={'source_id':source,'name':config.get(source,{}).get('display_name'),
            **{key:sum(u[key] for u in units) for key in ['expected_cycles','fresh_cycles','request_cycles','dispatched_cycles','missing_cycles','pending_cycles','settled_cycles','late_recovered_cycles']},
            'units':len(units),'unknown':sum(not u['eligibility_recorded'] for u in units)}
        row['coverage']=row['fresh_cycles']/row['expected_cycles'] if row['expected_cycles'] else None
        source_runs=[r for r in rr if r['source_id']==source]
        source_caps=[c for c in cc if c['source_id']==source]
        row['run_status']=dict(Counter(r['status'] for r in source_runs))
        row['reasons']=Counter(r.get('reason') for r in source_runs if r.get('reason')).most_common(5)
        row['run_seconds']=stats(diff(r['dispatch_at'],r.get('finished_at')) for r in source_runs)
        row['due_to_dispatch']=stats(diff(r['due_at'],r['dispatch_at']) for r in source_runs)
        row['capture_commit']=stats(diff(c['observed_at'],c['committed_at']) for c in source_caps)
        row['capture_first_intake']=stats(diff(c['observed_at'],c.get('first_intake_at')) for c in source_caps)
        row['capture_complete']=stats(diff(c['observed_at'],c.get('completed_at')) for c in source_caps)
        row['inputs']=sum(c['message_count'] for c in source_caps)
        row['unconsumed_inputs']=sum(c['message_count']-c['intake_position'] for c in source_caps)
        row['unconsumed_captures']=sum(c['status'] in {'PENDING','RUNNING'} for c in source_caps)
        row['run_stages']={key:stats(r.get('stages_ms',{}).get(key,0)/1000 for r in source_runs)
                           for key in sorted({k for r in source_runs for k in r.get('stages_ms',{})})}
        out.append(row)
    summary['windows'][label]={k:v for k,v in result.items() if k not in {'units'}}|{'sources':sorted(out,key=lambda x:x['coverage'])}
    print(label,result['expected_cycles'],result['fresh_cycles'],result['coverage'],flush=True)

body=[b for b in bus['body_records'] if S<=at(b['first_observed_at'])<E and b.get('eligible',True) and not b.get('legacy_result')]
summary['body']={'total':len(body),'outcomes':dict(Counter(str((b['status'],b.get('outcome'))) for b in body)),
    'sources':[],'failures':[b for b in body if b['status']!='READY']}
for source in sorted({b.get('source_id') for b in body}):
    group=[b for b in body if b.get('source_id')==source]
    full=sum(b.get('outcome') in {'FULL','SHORT_FULL'} for b in group)
    summary['body']['sources'].append(dict(source_id=source,total=len(group),full=full,ratio=full/len(group),
        reasons=Counter(str(b.get('last_error')) for b in group if b.get('outcome') not in {'FULL','SHORT_FULL'}).most_common(8)))
jobs={j['job_id']:j for j in bus['jobs']}
newkeys={b['fetch_key'] for b in body}
executions=[r for r in bus['body_executions'] if S<=at(r['started_at'])<E]
summary['executions']={'n':len(executions),'work_kinds':dict(Counter(r.get('work_kind') for r in executions)),
    'network':sum(bool(r.get('network_attempted')) for r in executions),
    'network_succeeded':sum(bool(r.get('network_attempted')) and bool(r.get('succeeded')) for r in executions),
    'cache':sum(bool(r.get('cache_reused')) for r in executions),
    'body_elapsed':stats(diff(r['started_at'],r.get('completed_at')) for r in executions),
    'reason':Counter(r.get('reason') for r in executions if r.get('reason')).most_common(12)}
summary['deliveries']=[]
for row in bus['delivery']:
    value=row['payload'];trace=value.get('pipeline_trace') or {}
    first=trace.get('listing_received_at')
    if not first or not S<=at(first)<E:continue
    summary['deliveries'].append(dict(id=value['standard_message_id'],raw_id=value['raw_message_id'],
        trace=trace,readable_at=value['readable_at'],seconds=diff(first,value['readable_at']),
        capture_commit=diff(first,trace.get('capture_committed_at')),
        candidate=diff(trace.get('capture_committed_at'),trace.get('candidate_prepared_at')),
        after_candidate=diff(trace.get('candidate_prepared_at'),value['readable_at']),streams=value.get('streams')))
summary['delivery_stats']=stats(d['seconds'] for d in summary['deliveries'])
summary['delivery_stats']['under_10']=sum(d['seconds']<=10 for d in summary['deliveries'])
summary['delivery_stage_stats']={name:stats(d[name] for d in summary['deliveries']) for name in ['capture_commit','candidate','after_candidate']}
events=[r for r in site['events'] if r['category']=='ACCESS_RESULT' and S<=at(r['occurred_at'])<=E]
summary['site']=[]
for name,purpose in sorted({(r['site_id'],r.get('purpose','UNKNOWN')) for r in events}):
    group=[r for r in events if r['site_id']==name and r.get('purpose','UNKNOWN')==purpose]
    summary['site'].append(dict(site_id=name,purpose=purpose,n=len(group),
        dispositions=dict(Counter(r['disposition'] for r in group)),
        reasons=Counter(r.get('reason_code') or r.get('reason') for r in group if r['disposition']!='SUCCESS').most_common(6),
        network=stats(r.get('network_ms',0)/1000 for r in group),queue=stats(r.get('queue_wait_ms',0)/1000 for r in group),
        stages={key:stats(r.get('stage_timings',{}).get(key,0)/1000 for r in group)
                for key in sorted({k for r in group for k in r.get('stage_timings',{})}) if 'epoch' not in key}))
(ROOT/(output_prefix+'-analysis.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
with (ROOT/(output_prefix+'-unit-coverage.csv')).open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(detail[0]));writer.writeheader();writer.writerows(detail)
print('body',len(body),'deliveries',len(summary['deliveries']),summary['delivery_stats'],flush=True)
