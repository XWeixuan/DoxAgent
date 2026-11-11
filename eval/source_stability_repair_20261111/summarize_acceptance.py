"""Local-only summary of saved, bounded production evidence."""
import argparse,csv,json,collections
from pathlib import Path
from datetime import datetime
p=argparse.ArgumentParser();p.add_argument('snapshot');p.add_argument('--prefix',default='acceptance');args=p.parse_args()
root=Path('eval/source_stability_repair_20261111');before=json.loads((root/'prepublish.json').read_text(encoding='utf-8-sig'));after=json.loads((root/args.snapshot).read_text(encoding='utf-8-sig'))
def totals(v,test=False):
 counts=collections.Counter()
 for k,g in v['groups'].items():
  if k.endswith('_massive')!=test:continue
  counts.update(g['status'])
 n=counts['CAPTURED']+counts['FAILED']+counts['DEFERRED']
 return {'counts':dict(counts),'completed':n,'success_pct':round(100*counts['CAPTURED']/n,3) if n else None}
rows=[]
for k in sorted(set(before['groups'])|set(after['groups'])):
 b=before['groups'].get(k,{}).get('status',{});a=after['groups'].get(k,{}).get('status',{});bn=b.get('CAPTURED',0)+b.get('FAILED',0)+b.get('DEFERRED',0);an=a.get('CAPTURED',0)+a.get('FAILED',0)+a.get('DEFERRED',0)
 rows.append({'scope':k,'mode':'test' if k.endswith('_massive') else 'production','before_success':b.get('CAPTURED',0),'before_failed':b.get('FAILED',0),'before_success_pct':round(b.get('CAPTURED',0)/bn*100,3) if bn else '', 'after_success':a.get('CAPTURED',0),'after_failed':a.get('FAILED',0),'after_success_pct':round(a.get('CAPTURED',0)/an*100,3) if an else '', 'after_inflight':a.get('DISPATCHED',0),'after_reasons':json.dumps(after['groups'].get(k,{}).get('reasons',{}),ensure_ascii=False)})
with (root/(args.prefix+'-per-source.csv')).open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
periods={(u['source_id'],u['ticker'],u['unit']):u['interval_seconds'] for u in after['units']}
cadence=[]
for key,u in sorted(after.get('unit_windows',{}).items()):
 t=periods.get((u['source_id'],u['ticker'],u['unit']));g=u['success_intervals_seconds'];samples=u['success_samples']
 cadence.append({'source_id':u['source_id'],'ticker':u['ticker'],'unit':u['unit'],'target_seconds':t,'success_samples':samples,'interval_samples':g['n'],'p50_seconds':g.get('p50'),'p95_seconds':g.get('p95'),'max_seconds':g.get('max'),'edge_gap_seconds':u['edge_gap_seconds'],'observed_gap_gt_2_period':bool(t and max(g.get('max',0),u['edge_gap_seconds'])>2*t),'status_counts':json.dumps(u['status'])})
if cadence:
 with (root/(args.prefix+'-per-unit-cadence.csv')).open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(cadence[0]));w.writeheader();w.writerows(cadence)
old={(u['source_id'],u['ticker'],u['unit']):u['interval_seconds'] for u in before['units']};drift=[{'key':k,'before':old[k],'after':v} for k,v in periods.items() if k in old and old[k]!=v]
summary={'since':after['since'],'until':after['until'],'before_formal':totals(before),'after_formal':totals(after),'before_test':totals(before,True),'after_test':totals(after,True),'blocked_scopes':[r['scope'] for r in rows if r['after_failed'] and not r['after_success']],'current_bad_units':[{'source_id':u['source_id'],'ticker':u['ticker'],'unit':u['unit'],'status':u['status'],'error':u['error'],'last_success_at':u['last_success_at']} for u in after['units'] if u['status'] in ['failed','partial']],'interval_drift':drift,'supplement_proofs':after.get('supplement_proofs'), 'body':after['body'],'resources':after['resources']}
(root/(args.prefix+'-summary.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k in ['since','until','before_formal','after_formal','after_test','blocked_scopes','interval_drift','supplement_proofs']},ensure_ascii=False))
