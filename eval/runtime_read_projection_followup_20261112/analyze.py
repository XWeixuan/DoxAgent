"""Summarize saved evidence; makes no network or database calls."""
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

b=Path(__file__).resolve().parent
def read(name):return json.loads((b/name).read_text())
def percentile(items,q):
    a=sorted(items)
    return a[max(0,math.ceil(len(a)*q)-1)] if a else None
out={}
x=read('rates.json')['queries'];out['rate_queries']={k:{a:v for a,v in z.items() if a!='rows'} for k,z in x.items()}
for source in ('runtime','bus'):
    rows=x[source+'_minute_production'].get('rows',[])
    start='2026-11-12T00:00';finish='2026-11-12T11:00'
    a=[r for r in rows if start<=r['minute']<finish]
    minute={r['minute']:r['n'] for r in a}
    first=datetime.fromisoformat(start);last=datetime.fromisoformat(finish)
    values=[minute.get((first+timedelta(minutes=i)).isoformat(timespec='minutes'),0)/60 for i in range(int((last-first).total_seconds()/60))]
    five=[sum(values[i:i+5])/5 for i in range(0,len(values)-4,5)]
    out[source+'_production']={'complete_window_utc':[start,finish],'events':sum(r['n'] for r in a),'mean_per_s':statistics.mean(values),'minute_p95_per_s':percentile(values,.95),'minute_p99_per_s':percentile(values,.99),'minute_max_per_s':max(values),'five_minute_max_per_s':max(five),'top_minutes':sorted(a,key=lambda r:r['n'],reverse=True)[:8]}
    consumed=[r for k,v in x.items() if k.startswith('consumed_') for r in v.get('rows',[]) if r['source']==source and r['max_position']]
    groups=defaultdict(list)
    for r in consumed:
        stamp=datetime.fromisoformat(r['minute']);groups[stamp.replace(minute=stamp.minute//30*30).isoformat(timespec='minutes')].append(r)
    out[source+'_consumption_30m']=[];previous=None
    for key,rows in sorted(groups.items()):
        maximum=max(r['max_position'] for r in rows)
        if previous is not None:
            complete=key<'2026-11-12T11:30'
            out[source+'_consumption_30m'].append({'bin_start_utc':key,'complete':complete,'checkpoint_max':maximum,'coordinate_rate_per_s':(maximum-previous)/1800 if complete else None,'max_delay_s':max(r['max_event_delay'] or 0 for r in rows)})
        previous=maximum
for profile in ('live-profile.raw',):
    if not (b/profile).exists():continue
    frames=Counter();leaves=Counter();phases=Counter();total=0
    for line in (b/profile).read_text().splitlines():
        if '_run_module_as_main (' not in line:continue
        try:stack,n=line.rsplit(' ',1);n=int(n)
        except ValueError:continue
        total+=n
        for frame in set(stack.split(';')):frames[frame]+=n
        leaves[stack.split(';')[-1]]+=n
        if 'main (doxagent/v2_read/cli.py:319)' in stack:phase='idle_sleep'
        elif 'ProjectionWorker.repair ' in stack:phase='repair'
        elif 'ReadStore.ingest ' in stack:phase='ingest'
        elif 'FormalProjectors.__call__ ' in stack:phase='mapping'
        elif 'ProjectionWorker._events ' in stack:phase='event_loading_other'
        else:phase='source_setup_ack_coverage'
        phases[phase]+=n
    out[profile]={'samples':total,'phases':{k:{'samples':n,'percent':100*n/total} for k,n in phases.items()},'frames':[{'frame':k,'samples':n,'percent':100*n/total} for k,n in frames.most_common(35)],'leaves':[{'frame':k,'samples':n,'percent':100*n/total} for k,n in leaves.most_common(20)]}
if (b/'samples.jsonl').exists():
    samples=[json.loads(s) for s in (b/'samples.jsonl').read_text().splitlines() if s.startswith('{')]
    samples=[s for s in samples if 'error' not in s]
    out['live_window']={'start':samples[0]['at'],'end':samples[-1]['at'],'samples':len(samples),'probe_ms_max':max(s['probe_ms'] for s in samples),'sources':{}}
    for source in ('runtime','bus','research','initialization'):
        start=samples[0]['sources'][source];end=samples[-1]['sources'][source]
        seconds=(datetime.fromisoformat(samples[-1]['at'])-datetime.fromisoformat(samples[0]['at'])).total_seconds()
        lags=[];lags_records=[]
        for s in samples:
            src=s['sources'][source];row=src.get('next_hot')
            if row:
                age=(datetime.fromisoformat(s['at'])-datetime.fromisoformat(row['recorded_at'].replace('Z','+00:00'))).total_seconds()
                lags_records.append(age)
        seen=set()
        for s in samples:
            for r in s['recent_commits']:
                if r['seq'] not in seen and r['source']==source and r.get('projected_at') and r.get('at'):
                    seen.add(r['seq'])
                    lags.append((datetime.fromisoformat(r['projected_at'].replace('Z','+00:00'))-datetime.fromisoformat(r['at'].replace('Z','+00:00'))).total_seconds())
        out['live_window']['sources'][source]={'start':start,'end':end,'arrival_per_s':(end['head']-start['head'])/seconds,'coordinate_consumption_per_s':(end['checkpoint']-start['checkpoint'])/seconds,'net_backlog_change':end['coordinate_distance']-start['coordinate_distance'],'max_oldest_hot_age_s':max(lags_records) if lags_records else 0,'sampled_mutation_delay_p50':percentile(lags,.5),'sampled_mutation_delay_p95':percentile(lags,.95),'sampled_mutation_delay_p99':percentile(lags,.99),'sampled_mutation_n':len(lags)}
(b/'summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k!='rate_queries'},ensure_ascii=False,indent=2))
