import json,collections
d=json.load(open('eval/runtime_cost_audit_20261117/evidence.json',encoding='utf-8-sig'))
sel={}
for day in ['2026-11-13','2026-11-16']:
 for ticker in ['INTC','MU','NBIS']:
  ts=sorted([t for t in d['turns'] if t['day']==day and t['ticker']==ticker and t['lane']=='W1' and t['round_name']=='R1' and t['status']=='OK'],key=lambda t:t['started_at'])
  picks=set([0,len(ts)//4,len(ts)//2,3*len(ts)//4,len(ts)-1])
  tops=sorted(range(len(ts)),key=lambda i:ts[i].get('cache_write_input_tokens') or 0,reverse=True)[:3]
  for i in tops:picks.update(range(max(0,i-1),min(len(ts),i+2)))
  for i in sorted(picks):
   t=ts[i];sel[t['case_id']]=t
json.dump(list(sel.values()),open('eval/runtime_cost_audit_20261117/selected.json','w',encoding='utf8'),ensure_ascii=False)
print('selected',len(sel))
