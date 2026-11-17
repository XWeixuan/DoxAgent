import json,sqlite3,collections,math,re,subprocess,shutil
from pathlib import Path
from datetime import datetime,timezone,timedelta
start='2026-11-10T19:54:30';end=datetime.now(timezone.utc);recent=end-timedelta(hours=1);detail=end-timedelta(hours=3)
root=Path('/var/lib/docker/volumes');base=root/'doxagent-v2_v2-data/_data/bus'
def db(p):
 c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c
a=db(base/'acquisition.sqlite3');b=db(base/'bus.sqlite3');r=db(root/'doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
def dt(x):return datetime.fromisoformat(x.replace('Z','+00:00')) if x else None
def q(v):
 v=sorted(v);n=len(v);return {'n':n,'mean':round(sum(v)/n,3) if n else None,'p50':v[(n-1)//2] if n else None,'p95':v[max(0,math.ceil(n*.95)-1)] if n else None,'max':v[-1] if n else None}
def scrub(t):
 t=re.sub(r'https?://[^\s\"\'<>]+',lambda m:m.group().split('?')[0],str(t));return re.sub(r'(?:sk-[\w-]+|Bearer\s+\S+|token[=:]\S+|password[=:]\S+)','[redacted]',t,flags=re.I)[:550]
specs={x['source_id']:json.loads(x['data_json']) for x in b.execute('select source_id,data_json from source_definitions')};bindings={x['binding_id']:json.loads(x['data_json']) for x in b.execute('select binding_id,data_json from ticker_source_bindings where tombstoned_at is null')};G={};seen=set();ids={}
for row in a.execute("select * from cycles indexed by cycles_time where dispatch_at>=? and dispatch_at<? and status<>'SCHEDULE' and source_id<>'reuters_site_search' order by dispatch_at",(start,end.isoformat()[:19])):
 sid=row['source_id'];sp=specs.get(sid)
 if not sp:continue
 v=json.loads(row['data_json']);pid=v.get('physical_run_id') or row['run_id'].split(':')[0]
 if pid in seen:continue
 seen.add(pid);at=dt(row['dispatch_at']);key=sid+'|'+row['unit'] if sid=='corporate_ir_press_releases' else sid
 g=G.setdefault(key,{'source_id':sid,'delivery':sp.get('delivery_mode'),'status':collections.Counter(),'reasons':collections.Counter(),'hourly':collections.defaultdict(collections.Counter),'recent':collections.Counter(),'recent_reasons':collections.Counter(),'last3h':collections.Counter(),'periods':collections.Counter(),'duration_recent':[],'failure_examples':[],'first_failure':None,'last_success':None,'last_failure':None,'max_failed_run':0,'failed_run':0,'last_status':None,'request':{'transport':collections.Counter(),'codes':collections.Counter(),'errors':collections.Counter(),'identity':collections.Counter(),'empty_trace_failed':0,'ep':{}}})
 st=row['status'];g['status'][st]+=1;g['hourly'][at.strftime('%Y-%m-%dT%H')][st]+=1;g['periods'][row['interval_seconds']]+=1;g['last_status']=st
 if row['reason']:g['reasons'][row['reason']]+=1
 if st=='CAPTURED':g['last_success']=row['dispatch_at'];g['failed_run']=0
 elif st in ('FAILED','DEFERRED'):
  g['failed_run']+=1;g['max_failed_run']=max(g['max_failed_run'],g['failed_run']);g['first_failure']=g['first_failure'] or row['dispatch_at'];g['last_failure']=row['dispatch_at']
 if at>=detail:g['last3h'][st]+=1
 if at>=recent:
  g['recent'][st]+=1
  if row['reason']:g['recent_reasons'][row['reason']]+=1
  done=dt(row['finished_at'])
  if done:g['duration_recent'].append((done-at).total_seconds())
 if at>=detail:
  ids[pid]=(key,st)
  if st in ('FAILED','DEFERRED') and len(g['failure_examples'])<8:g['failure_examples'].append({'at':row['dispatch_at'],'reason':row['reason'],'message':scrub(v.get('terminal_error_message')),'run_id':pid})
for row in a.execute('select * from physical_cycles indexed by physical_cycles_time where finished_at>=? and finished_at<?',(detail.isoformat()[:19],end.isoformat()[:19])):
 if row['run_id'] not in ids:continue
 key,st=ids[row['run_id']];g=G[key]['request'];events=json.loads(row['requests_json'])
 if not events and st in ('FAILED','DEFERRED'):g['empty_trace_failed']+=1
 for e in events:
  if e.get('role','listing')!='listing':continue
  tr=e.get('transport','?');g['transport'][tr]+=1;g['codes'][str(e.get('status_code'))]+=1;g['identity'][e.get('identity_id') or 'none']+=1
  if e.get('error_type'):g['errors'][e['error_type']]+=1
  ep=g['ep'].setdefault(e.get('endpoint','?'),{'codes':collections.Counter(),'errors':collections.Counter(),'valid':0});ep['codes'][str(e.get('status_code'))]+=1;ep['valid']+=bool(e.get('list_validated'))
  if e.get('error_type'):ep['errors'][e['error_type']]+=1
for g in G.values():g['duration_recent']=q(g['duration_recent'])
units=[]
for row in a.execute('select * from units where required>0 and source_id<>\'reuters_site_search\''):
 if row['source_id'] not in specs:continue
 v=json.loads(row['state']);h=json.loads(row['head_json']);units.append({'sid':row['source_id'],'ticker':row['ticker'],'unit':row['unit'],'period':v.get('schedule_interval_seconds'),'status':h.get('status',v.get('status')),'last_error':h.get('last_error_code',v.get('last_error_code')),'last_success':h.get('last_success_at',v.get('last_success_at'))})
out={'start':start,'end':end.isoformat(),'recent_since':recent.isoformat(),'detail_since':detail.isoformat(),'groups':G,'units':units,'sources':{sid:{k:s.get(k) for k in ['source_id','version','adapter_ref','kind','acquisition_mode','scheduler_constraints','default_polling_config','default_parameters']} for sid,s in specs.items() if sid!='reuters_site_search'},'containers':[]}
for name in ['v2-message-collector','v2-message-bus','v2-site-access','v2-chrome-supervisor','v2-content-enrichment']:
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+name+'-1']))[0];env=dict(x.split('=',1) for x in v['Config']['Env'] if '=' in x);out['containers'].append({'role':name,'started':v['State']['StartedAt'],'id':v['Id'][:12],'commit':env.get('DOXAGENT_BUILD_COMMIT'),'oom':v['State']['OOMKilled'],'restart':v['RestartCount']})
out['resources']={'free_disk':shutil.disk_usage('/').free,'acq_bytes':(base/'acquisition.sqlite3').stat().st_size,'psi':{k:Path('/proc/pressure/'+k).read_text() for k in ['cpu','io','memory']},'meminfo':{x.split(':')[0]:x.split(':')[1].strip() for x in Path('/proc/meminfo').read_text().splitlines() if x.split(':')[0] in ['MemAvailable','SwapFree']}}
out['registry_events']=[dict(x) for x in r.execute("select site_id,category,json_extract(payload_json,'$.disposition') disposition,json_extract(payload_json,'$.reason') reason,count(*) n from access_events where occurred_at>=? and site_id<>'reuters' and category in ('ACCESS_RESULT','RECOVERY_STARTED','RECOVERY_RESOLVED') group by site_id,category,disposition,reason",(detail.isoformat()[:19],))]
out['body']=[dict(x) for x in r.execute("select final_site_id,outcome,reason,count(*) n from body_outcomes where completed_at>=? and final_site_id<>'reuters' group by final_site_id,outcome,reason",(detail.isoformat()[:19],))]
Path('/tmp/source-stability-audit-20261111.json').write_text(json.dumps(out),encoding='utf-8');print(json.dumps({'end':out['end'],'physical_cycles':len(seen),'scopes':len(G)}));a.close();b.close();r.close()
