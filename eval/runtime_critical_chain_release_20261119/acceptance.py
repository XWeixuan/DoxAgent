import json,gzip,math,statistics,argparse
from pathlib import Path
from datetime import datetime
p=Path(__file__).parent
parser=argparse.ArgumentParser();parser.add_argument('--since',default='2026-11-17T09:54:56Z');parser.add_argument('--until');parser.add_argument('--output',default='acceptance.json');parser.add_argument('--open-load-verified',action='store_true',help='Operator has verified actual opening-load coverage, not only sample count');args=parser.parse_args()
def ts(v):
 value=datetime.fromisoformat(v.replace('Z','+00:00'))
 if value.tzinfo is None:raise ValueError('Audit clocks require an explicit timezone')
 return value.timestamp()
def merged(xs):
 out=[]
 for a,b in sorted(xs):
  if b<a:raise ValueError('reversed clock')
  if out and a<=out[-1][1]:out[-1][1]=max(b,out[-1][1])
  else:out.append([a,b])
 return out
def duration(xs,a,b):return sum(y-x for x,y in merged([(max(a,x),min(b,y)) for x,y in xs if y>a and x<b]))
def minus(interval,local):
 out=[interval]
 for x,y in merged(local):
  nxt=[]
  for a,b in out:
   if y<=a or x>=b:nxt.append((a,b));continue
   if x>a:nxt.append((a,min(x,b)))
   if y<b:nxt.append((max(y,a),b))
  out=nxt
 return out
def stats(xs):
 xs=sorted(xs)
 return dict(n=len(xs),p50=statistics.median(xs),p95=xs[math.ceil(.95*len(xs))-1],max=max(xs)) if xs else dict(n=0)
data=json.load(gzip.open(p/'cohort-final.json.gz'));logs=json.load(gzip.open(p/'logs-final.json.gz'));traces=logs['traces'];rows=[]
for c in data['cases']:
 if ts(c['created_at'])<ts(args.since):continue
 if args.until and ts(c['created_at'])>ts(args.until):continue
 row=dict(case_id=c['case_id'],ticker=c['ticker'],created=c['created_at'],status=c['status'],missing=[])
 ef={v['effect_type']:v for v in c['effects']};st={v['phase']:v for v in c['stages']}
 phase_by_job={a['attempt_id']:a.get('phase') for a in c['attempts'] if a.get('node_id')=='W3'}
 # Worker requests can be archived before the Read projection catches up. The
 # committed source stage is the authoritative Job-to-business-phase identity.
 phase_by_job.update({s['worker_job_id']:('trade_decision' if s['phase'].startswith('trade_decision')
                       else s['phase']) for s in c['stages'] if s.get('worker_job_id')})
 model={'trade_decision':[],'post_decision_operations':[]};sdk_raw={k:[] for k in model};local=[]
 for j in c['jobs']:
  phase=phase_by_job.get(j['id']) or j.get('phase');t=j['timing'];a=t.get('sdk_dispatched_at');b=t.get('sdk_completed_at')
  if phase not in model or not a or not b:row['missing'].append('job_clock_or_phase:'+j['id']);continue
  interval=(ts(a),ts(b));tools=[(ts(t['started_at']),ts(t['finished_at'])) for t in traces if t.get('segment')=='research_tool' and t.get('capsule_id')==j.get('capsule_id') and t.get('finished_at')]
  local+=tools;sdk_raw[phase].append(interval);model[phase]+=minus(interval,tools)
 ends={}
 for et,phase in [('route_to_w3','trade_decision'),('w3_post_decision','post_decision_operations')]:
  e=ef.get(et);s=st.get(phase)
  if not e or not s or e['status']!='COMPLETED' or s['status']!='COMMITTED':row['missing'].append('unfinished:'+phase);continue
  terminal=max(ts(e['updated_at']),ts(s['committed_at']))
  done=[ts(t['finished_at']) for t in traces if t.get('effect_id')==e['effect_id'] and t.get('segment')=='effect_owner' and ts(t['finished_at'])>=terminal]
  if not done:row['missing'].append('final_owner_trace:'+phase);continue
  ends[phase]=max(done)
 if 'trade_decision' in ends and ef['route_to_w3'].get('effective_at') and model['trade_decision']:
  a=ts(ef['route_to_w3']['created_at']);b=ends['trade_decision'];row['trade_non_sdk']=b-a-duration(model['trade_decision'],a,b)
  row['trade_ledger_lower_bound']=ts(ef['route_to_w3']['effective_at'])-a-duration(sdk_raw['trade_decision'],a,ts(ef['route_to_w3']['effective_at']))
 if all(k in ends for k in model) and all(model.values()):
  a=ts(ef['route_to_w3']['created_at']);b=ends['post_decision_operations'];oa=max(ts(ef['w3_post_decision']['created_at']),ts(ef['route_to_w3']['effective_at']));row['operations_non_sdk']=b-oa-duration(model['post_decision_operations'],oa,b);row['whole_w3_non_sdk']=b-a-duration(sum(model.values(),[]),a,b)
  source_rounds=[dict(r,model_interval=(json.loads(r['model_interval'])
                 if isinstance(r.get('model_interval'),str) else r.get('model_interval') or {}))
                 for r in c['rounds']]
  hot=[(ts(r['model_interval']['start']),ts(r['model_interval']['end'])) for r in source_rounds
       if r['model_interval'].get('start') and r['model_interval'].get('end')]
  created=ts(c['created_at']);w3_start=ts(ef['route_to_w3']['created_at'])
  critical_rounds=[r for r in source_rounds if not (r['lane']=='W1' and r['round_name']=='R3')]
  for r in critical_rounds:
   if not r['model_interval'].get('start') or not r['model_interval'].get('end'):
    row['missing'].append('critical_round_clock:'+r['turn_id'])
  critical_hot=[(ts(r['model_interval']['start']),ts(r['model_interval']['end']))
                for r in critical_rounds if r['model_interval'].get('start') and r['model_interval'].get('end')]
  row['runtime_union_with_aux_model']=b-created-duration(hot+sum(model.values(),[]),created,b)
  # R3 fact extraction runs beside W3. Its model time must not conceal Ready,
  # SQL or other critical W3 work. Partition the Case at actual W3 route creation.
  row['pre_w3_non_model']=w3_start-created-duration(critical_hot,created,w3_start)
  row['whole_runtime_non_model']=row['pre_w3_non_model']+row['whole_w3_non_sdk']
  row['local_tools_union']=duration(local,created,b)
  assert row['whole_runtime_non_model']>=row['whole_w3_non_sdk']-0.001
 rows.append(row)
metrics={k:stats([r[k] for r in rows if k in r]) for k in ('trade_non_sdk','operations_non_sdk','whole_w3_non_sdk','whole_runtime_non_model')}
truncated=bool(data.get('candidate_count',0)>=512 and ts(data['oldest_candidate'])>ts(args.since))
result=dict(since=args.since,until=args.until,at=data['at'],window_truncated=truncated,enrolled=len(rows),target_seconds=10,metrics=metrics,acceptance_ready=not truncated and len(rows)>=100 and all(not r['missing'] for r in rows) and all(v.get('p95',1e9)<=10 for v in metrics.values()),rows=rows,warnings=logs['warnings'])
result['open_load_verified']=args.open_load_verified
result['formal_acceptance_ready']=result['acceptance_ready'] and args.open_load_verified
(p/args.output).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
