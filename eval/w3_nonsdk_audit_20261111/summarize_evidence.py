"""Compact, content-free review evidence derived from the frozen exports."""
from datetime import datetime
import json
from pathlib import Path
import re

folder=Path(__file__).parent
read=lambda name:json.loads((folder/name).read_text(encoding='utf-8-sig'))
ledger=read('frozen-cohort-ledger.json');old=read('ledger-latest.json');metadata=read('metadata-latest.json')
traces=read('traces.json');live=read('live-observation.json')
selected=['case_f438f9c5998f4e3a98a5bbb46fa6dddd','case_dc48755e0334446eb95733ac52dfdb03',
          'case_1221cbd4d4074a0e96fa091d5d94be37','case_42271f13bfde42d6bff4703ec1d677c5',
          'case_03c8e943b36e4880805e6c1aee9f6a00']
summary={'snapshot_at':ledger['snapshot_at'],'phase_summary':{k:v for k,v in ledger['summary'].items() if k.startswith('new_after')},
 'case_summary':ledger['case_summary'],'all_55_cases_have_ledger':all(r['complete'] for r in ledger['rows']),
 'boundary_coverage_is_not_complete':True,'provider_wait_excluded_s':0,
 'examples':[],'slow_warning_aggregates':{},'observer':{'started_at':live['started_at'],'finished_at':live['finished_at'],
 'files':len(live['traces']),'bytes':live['bytes_read'],'dropped':live['dropped']},
 'pre_convergence_summary':{k:v['non_sdk_s'] for k,v in old['summary'].items() if k.startswith('same_w3')}}
for cid in selected:
    c=next(c for c in metadata['cases'] if c['case_id']==cid)
    rows=[r for r in (ledger['rows'] if cid in {r['case_id'] for r in ledger['rows']} else old['rows']) if r['case_id']==cid]
    summary['examples'].append(dict(case_id=cid,ticker=c['ticker'],ready=c.get('ready'),rows=rows,jobs=c['jobs'],
        final_traces=[t for t in traces['db']+traces['files'] if t.get('case_id')==cid and t.get('finished_at')]))
for warning in traces['transactions']:
    m=re.search(r'operation=(.*?) write=(\w+) elapsed_ms=(\d+) wait_ms=(-?\d+).*hold_ms=(-?\d+) commit_ms=(-?\d+)',warning['message'])
    if not m:continue
    v=summary['slow_warning_aggregates'].setdefault(m[1],dict(count=0,elapsed_ms=0,wait_ms=0,hold_ms=0,commit_ms=0))
    v['count']+=1
    for k,a in [('elapsed_ms',3),('wait_ms',4),('hold_ms',5),('commit_ms',6)]:v[k]+=int(m[a])
summary['warning_window']=[min(r['at'] for r in traces['transactions']),max(r['at'] for r in traces['transactions'])]
samples=live['samples'];seconds=(datetime.fromisoformat(samples[-1]['at'])-datetime.fromisoformat(samples[0]['at'])).total_seconds()
summary['observer']['minimum_available_gib']=min(s['mem_available_kib'] for s in samples)/1024**2
summary['observer']['swapout_mib']=(samples[-1]['vmstat']['pswpout']-samples[0]['vmstat']['pswpout'])*4096/1024**2
summary['observer']['kernel_oom_delta']=samples[-1]['vmstat']['oom_kill']-samples[0]['vmstat']['oom_kill']
summary['observer']['role_resources']={}
for role in samples[0]['roles']:
    usage=lambda s:dict(line.split() for line in s['roles'][role]['cpu.stat'].splitlines())
    summary['observer']['role_resources'][role]=dict(
        peak_memory_gib=max(int(s['roles'][role]['memory.current']) for s in samples)/1024**3,
        average_cpu_cores=(int(usage(samples[-1])['usage_usec'])-int(usage(samples[0])['usage_usec']))/1e6/seconds)
for name in ['import-probe.json','payload-probe.json','final-production-verification.json']:
    summary[name]=read(name)
for r in ledger['rows']:
    assert not r['complete'] or abs(sum(r['parts'].values())-r['non_sdk_s'])<.001
assert len(ledger['case_rows'])==55 and len(ledger['rows'])==110
(folder/'audit-evidence-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
print('55 Cases / 110 phases balanced; compact evidence saved')
