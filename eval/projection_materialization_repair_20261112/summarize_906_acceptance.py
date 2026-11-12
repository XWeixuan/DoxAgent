import json,math,statistics
from pathlib import Path
from datetime import datetime
b=Path(__file__).resolve().parent

def read(name):
 out=[]
 for l in (b/name).read_text().splitlines():
  try:out.append(json.loads(l))
  except ValueError:pass
 return out

def t(row):return datetime.fromisoformat(row['at']).timestamp()
def pct(v,p):return sorted(v)[max(0,math.ceil(len(v)*p)-1)] if v else None
rows=[r for r in read('samples-90647e2.jsonl') if 'sources' in r]
first,last=rows[0],rows[-1];elapsed=t(last)-t(first)
out={'start':first['at'],'end':last['at'],'seconds':elapsed,'samples':len(rows),'sources':{},'gaps_start':first.get('gaps'),'gaps_end':last.get('gaps'),'catchup':{}}
for source in first['sources']:
 a,z=first['sources'][source],last['sources'][source]
 ages=[]
 for r in rows:
  v=r['sources'].get(source,{})
  ages.append(max(0,t(r)-datetime.fromisoformat(v['next_hot']['recorded_at'].replace('Z','+00:00')).timestamp()) if v.get('next_hot') else 0)
 out['sources'][source]={'checkpoint_start':a['checkpoint'],'checkpoint_end':z['checkpoint'],'head_start':a['head'],'head_end':z['head'],'distance_start':a['coordinate_distance'],'distance_end':z['coordinate_distance'],'coordinate_lambda':(z['head']-a['head'])/elapsed if elapsed else None,'coordinate_mu':(z['checkpoint']-a['checkpoint'])/elapsed if elapsed else None,'pending_age_p95':pct(ages,.95),'pending_age_max':max(ages)}
 caught=next((r for r in rows if r['sources'][source]['coordinate_distance']==0),None)
 if caught:out['catchup'][source]=caught['at']
steady=[r for r in rows if r['at']>='2026-11-12T13:18:00']
out['steady_pending_age']={}
for source in first['sources']:
 ages=[]
 for r in steady:
  v=r['sources'][source]
  ages.append(max(0,t(r)-datetime.fromisoformat(v['next_hot']['recorded_at'].replace('Z','+00:00')).timestamp()) if v.get('next_hot') else 0)
 out['steady_pending_age'][source]={'samples':len(ages),'p95':pct(ages,.95),'max':max(ages) if ages else None}
res=[r for r in read('resources-90647e2.jsonl') if 'processes' in r]
def cpu(d):return int(dict(l.split() for l in d['cpu.stat'].splitlines() if len(l.split())==2)['usage_usec'])
def writes(d):return sum(int(k.split('=')[1]) for l in d.get('io.stat','').splitlines() for k in l.split()[1:] if k.startswith('wbytes='))
roles=['doxagent-v2-v2-projector-1','doxagent-v2-v2-maintenance-1'];out['resources']={}
for role in roles:
 cores=[];w=[];rss=[]
 for a,z in zip(res,res[1:]):
  x,y=a['processes'][role],z['processes'][role];dt=t(z)-t(a)
  if x.get('pid')==y.get('pid') and dt>0 and 'cpu.stat' in x and 'cpu.stat' in y:
   cores.append((cpu(y)-cpu(x))/1e6/dt);w.append((writes(y)-writes(x))/dt/2**20)
  rss.append(y.get('rss_bytes',0)/2**20)
 out['resources'][role]={'cpu_cores_mean':statistics.mean(cores) if cores else None,'cpu_cores_max_interval':max(cores) if cores else None,'write_mib_s_mean':statistics.mean(w) if w else None,'rss_mib_max':max(rss) if rss else None}
busy=[];cpu_psi=[];io_psi=[]
for r in res:
 for key,target in [('pressure/cpu',cpu_psi),('pressure/io',io_psi)]:
  target.append(float(next(x for x in r[key].splitlines() if x.startswith('some ')).split('avg60=')[1].split()[0]))
for a,z in zip(res,res[1:]):
 x=list(map(int,a['stat'].splitlines()[0].split()[1:9]));y=list(map(int,z['stat'].splitlines()[0].split()[1:9]));d=[b-a for a,b in zip(x,y)];busy.append(100*(1-(d[3]+d[4])/sum(d)))
out['host']={'cpu_busy_mean':statistics.mean(busy) if busy else None,'cpu_psi_some_avg60_mean':statistics.mean(cpu_psi) if cpu_psi else None,'io_psi_some_avg60_mean':statistics.mean(io_psi) if io_psi else None}
reports=[];slow=[]
for line in (b/'performance-90647e2.log').read_text().splitlines():
 try:stamp,payload=line.split(' ',1);j=json.loads(payload)
 except ValueError:continue
 if 'projection_slow' in j:slow.append({'at':stamp,**j['projection_slow']})
 if 'projection_performance' in j:reports.append({'at':stamp,**j['projection_performance']})
work={}
for r in reports:
 for key,v in r.get('workloads',{}).items():
  acc=work.setdefault(key,{'events':0,'records':0,'map_seconds':0,'ingest_seconds':0,'max_seconds':0,'latency_buckets':[0]*7})
  for field in ['events','records','map_seconds','ingest_seconds']:acc[field]+=v[field]
  acc['max_seconds']=max(acc['max_seconds'],v['max_seconds']);acc['latency_buckets']=[a+b for a,b in zip(acc['latency_buckets'],v['latency_buckets'])]
out['performance']={'reports':len(reports),'mapped':sum(sum(x['events'] for x in r.get('sources',{}).values()) for r in reports),'verified_skips':sum(r.get('verified_skips',0) for r in reports),'workloads':work,'slow_events':slow}
(b/'acceptance-90647e2-summary.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='performance'},indent=2))
print('performance reports',len(reports),'mapped',out['performance']['mapped'],'verified_skips',out['performance']['verified_skips'])
for key,v in work.items():
 if any(s in key for s in ['reference_deliveries','authorization_head','activation_revisions','ticker_active_revision','v2_ticker_control','stream_members']):print(key,v)
