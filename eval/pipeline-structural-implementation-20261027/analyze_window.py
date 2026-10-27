"""Offline fixed-window proof and business cohort summary; retains all raw inputs."""
import json,sys,collections
from datetime import datetime
from doxagent.message_bus_v2.cadence import cadence

def dt(x):return datetime.fromisoformat(x.replace('Z','+00:00'))
def stats(v):
 v=sorted(v)
 return dict(n=len(v),mean=sum(v)/len(v),p50=v[len(v)//2],p95=v[int((len(v)-1)*.95)],max=v[-1],within10=sum(t<=10 for t in v)) if v else {'n':0}
def analyze(acq,business,since,until):
 s,u=dt(since),dt(until);runs=acq['raw_runs'];captures=[]
 for r in runs:
  q=next((q for q in r.get('requests',[]) if q.get('physical_request_id')==r.get('winning_request_id')),None)
  if r.get('list_validated') and q and q.get('request_started_at'):
   captures.append({**r,'capture_id':'p:'+r['run_id'],'observed_at':r['listing_received_at'],'request_started_at':q['request_started_at']})
 out={'since':since,'until':until,'sources':{}}
 for source in ['ALL']+sorted({r['source_id'] for r in runs}):
  rr=[r for r in runs if source=='ALL' or r['source_id']==source];cc=[r for r in captures if source=='ALL' or r['source_id']==source]
  c=cadence(rr,cc,s,u);units=c.pop('units');c.pop('fresh_gap_seconds',None)
  points=collections.defaultdict(set);cost=[];seen=set();dur=[]
  for r in rr:
   if r['status']=='SCHEDULE' or not s<=dt(r['dispatch_at'])<=u:continue
   if r.get('finished_at') and dt(r['finished_at'])<=u:dur.append((dt(r['finished_at'])-dt(r['dispatch_at'])).total_seconds())
  for r in cc:
   if s<=dt(r['request_started_at'])<=dt(r['observed_at'])<=u:
    points[(r['binding_id'],r['unit'])].add(r['observed_at'])
    if r['winning_request_id'] not in seen:
     cost.append((dt(r['observed_at'])-dt(r['request_started_at'])).total_seconds());seen.add(r['winning_request_id'])
  gaps=[]
  for pp in points.values():
   pp=sorted(dt(v) for v in pp);gaps.extend((b-a).total_seconds() for a,b in zip(pp,pp[1:]))
  c.update(actual_in_window_fresh_gaps=stats(gaps),winning_transport_seconds=stats(cost),finished_poll_seconds=stats(dur),
   statuses=dict(collections.Counter(r['status'] for r in rr if r['status']!='SCHEDULE' and s<=dt(r['dispatch_at'])<=u)))
  out['sources'][source]=c
 body=[r for r in business['body_records'] if s<=dt(r['first_observed_at'])<=u and r.get('eligible') and not r.get('legacy_result') and not r.get('prior_publication_raw_id')]
 full=[r for r in body if r.get('outcome') in ['FULL','SHORT_FULL'] and r.get('acquired_at') and dt(r['acquired_at'])<=u]
 out['body']={'cohort':len(body),'full_by_cutoff':len(full),'ratio':len(full)/len(body) if body else None,'outcomes':dict(collections.Counter(str((r['status'],r.get('outcome'),r.get('last_error'))) for r in body))}
 delivery=[];fresh_delivery=[];stages=collections.defaultdict(list);bykey={r['fetch_key']:r for r in body};delivered={}
 for r in business['delivery']:
  p=r.get('payload',{});t=p.get('pipeline_trace',{});a=t.get('listing_received_at');z=p.get('readable_at')
  if a and z and s<=dt(a)<=dt(z)<=u:
   seconds=(dt(z)-dt(a)).total_seconds();delivery.append(seconds)
   key=business.get('delivery_body_keys',{}).get(p.get('raw_message_id'))
   if key in bykey:
    first=(dt(z)-dt(bykey[key]['first_observed_at'])).total_seconds()
    if first>=0:delivered[key]=min(delivered.get(key,float('inf')),first)
   for akey,zkey,label in [('listing_received_at','capture_committed_at','listing_to_bus'),('capture_committed_at','candidate_prepared_at','bus_to_prepared')]:
    if t.get(akey) and t.get(zkey):stages[label].append((dt(t[zkey])-dt(t[akey])).total_seconds())
 out['delivery']={'all_published_samples':stats(delivery),'unique_first_seen_article_samples':stats(list(delivered.values())),'eligible_not_seen_consumable':len(body)-len(delivered),'stages':{k:stats(v) for k,v in stages.items()},'note':'Published subset only; exact raw-message body key join. Eligible unpublished may include irrelevant/deduplicated/failed/pending articles; not automatically a delivery fault.'}
 return out
if __name__=='__main__':
 acq=json.load(open(sys.argv[1],encoding='utf-8'));business=json.load(open(sys.argv[2],encoding='utf-8'))
 result=analyze(acq,business,sys.argv[3],sys.argv[4] if len(sys.argv)>4 else acq['until']);print(json.dumps(result,ensure_ascii=False,indent=2))
