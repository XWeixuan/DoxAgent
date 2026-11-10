"""Production natural-traffic observation, bounded read-only SQLite and resource snapshots."""
import argparse,collections,json,math,sqlite3,shutil,subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=argparse.ArgumentParser();p.add_argument('--since',required=True);p.add_argument('--output',required=True);p.add_argument('--until');args=p.parse_args();start=datetime.fromisoformat(args.since.replace('Z','+00:00'));end=datetime.fromisoformat(args.until.replace('Z','+00:00')) if args.until else datetime.now(timezone.utc)
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus')
def db(path):
 c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c
b=db(root/'bus.sqlite3');a=db(root/'acquisition.sqlite3');r=db(Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3'))
def dt(x):return datetime.fromisoformat(x.replace('Z','+00:00')) if x else None
def q(v):
 v=sorted(v);n=len(v);return {'n':n,'mean':round(sum(v)/n,3) if n else None,'p50':round(v[(n-1)//2],3) if n else None,'p95':round(v[max(0,math.ceil(n*.95)-1)],3) if n else None,'max':round(v[-1],3) if n else None}
retired={'tikhub_x_search','tikhub_x_user_posts','globenewswire_semiconductors_rss'};specs={x['source_id']:json.loads(x['data_json']) for x in b.execute('select source_id,data_json from source_definitions')};bindings={x['binding_id']:json.loads(x['data_json']) for x in b.execute('select binding_id,data_json from ticker_source_bindings where tombstoned_at is null')};run={x[0] for x in b.execute("select ticker from ticker_monitoring_states where status='running'")}
G={};seen=set();prior={};ids={}
for row in a.execute("select * from cycles indexed by cycles_time where dispatch_at>=? and dispatch_at<? and status<>'SCHEDULE' order by dispatch_at",(start.isoformat()[:19],end.isoformat()[:19])):
 sid=row['source_id'];sp=specs.get(sid);binding=bindings.get(row['binding_id'])
 if not sp or not binding or binding['ticker'] not in run:continue
 proof=json.loads(row['data_json']);pid=proof.get('physical_run_id') or row['run_id'].split(':')[0]
 if pid in seen:continue
 seen.add(pid);key=sid+'|'+row['unit'] if sid=='corporate_ir_press_releases' else sid
 g=G.setdefault(key,{'status':collections.Counter(),'reasons':collections.Counter(),'periods':collections.Counter(),'full':[],'success_gaps':[],'minute':collections.defaultdict(collections.Counter),'request_counts':collections.Counter(),'http_codes':collections.Counter(),'scope_ids':collections.Counter(),'units':{},'requests_duration':[]})
 effective_status='DISPATCHED' if dt(row['finished_at']) and dt(row['finished_at'])>end else row['status']
 g['status'][effective_status]+=1;g['periods'][row['interval_seconds']]+=1
 if row['reason'] and effective_status!='DISPATCHED':g['reasons'][row['reason']]+=1
 at=dt(row['dispatch_at']);done=dt(row['finished_at']);received=dt(proof.get('listing_received_at'));g['minute'][at.strftime('%H:%M')][effective_status]+=1
 if done and done>=at and done<=end:g['full'].append((done-at).total_seconds())
 unit=row['unit'];shared=sp.get('acquisition_mode') in ('by_distribution','massive_distribution');uk=(sid,unit if shared else row['ticker']+':'+unit)
 if effective_status=='CAPTURED' and received and received<=end:
  if uk in prior and received>=prior[uk]:g['success_gaps'].append((received-prior[uk]).total_seconds())
  prior[uk]=received
 g['units'][uk[1]]=effective_status;ids[pid]=key
reqseen=set()
for row in a.execute('select * from physical_cycles indexed by physical_cycles_time where finished_at>=? and finished_at<?',(start.isoformat()[:19],end.isoformat()[:19])):
 if row['run_id'] not in ids:continue
 g=G[ids[row['run_id']]]
 for e in json.loads(row['requests_json']):
  if e.get('role','listing')!='listing':continue
  rid=e.get('physical_request_id')
  if rid and rid in reqseen:continue
  if rid:reqseen.add(rid)
  g['request_counts'][e.get('transport','?')]+=1;g['http_codes'][str(e.get('status_code'))]+=1
  if e.get('identity_id'):g['scope_ids'][e['identity_id']]+=1
  if dt(e.get('request_started_at')) and dt(e.get('response_received_at')):g['requests_duration'].append((dt(e['response_received_at'])-dt(e['request_started_at'])).total_seconds())
for g in G.values():
 for k in ['full','success_gaps','requests_duration']:g[k]=q(g[k])
out={'start':start.isoformat(),'end':end.isoformat(),'elapsed_minutes':(end-start).total_seconds()/60,'groups':G,'units':[],'legacy':{},'resources':{},'body':[]}
for row in a.execute('select * from units where required>0'):
 binding=bindings.get(row['binding_id'])
 if not binding or binding['ticker'] not in run:continue
 state=json.loads(row['state']);head=json.loads(row['head_json']);out['units'].append({'source_id':row['source_id'],'ticker':row['ticker'],'unit':row['unit'],'interval':state.get('schedule_interval_seconds'),'status':head.get('status',state.get('status')),'last_error_code':head.get('last_error_code',state.get('last_error_code')),'last_success_at':head.get('last_success_at',state.get('last_success_at'))})
for sid in retired:
 scope=r.execute('select data_json from source_access_scopes where source_id=?',(sid,)).fetchone()
 out['legacy'][sid]={'registered':sid in specs,'bindings':sum(x['source_id']==sid for x in bindings.values()),'scope_enabled':json.loads(scope[0]).get('enabled') if scope else None,'default_profile_references':sum(sum(e['source_id']==sid for e in json.loads(row[0]).get('entries',[])) for row in b.execute('select data_json from default_profiles'))}
out['body']=[dict(x) for x in r.execute('select final_site_id,outcome,reason,count(*) n from body_outcomes where completed_at>=? and completed_at<? group by final_site_id,outcome,reason',(start.isoformat()[:19],end.isoformat()[:19]))]
out['recovery_events']=[dict(x) for x in r.execute("select site_id,category,count(*) n from access_events where occurred_at>=? and occurred_at<? and category in ('RECOVERY_STARTED','RECOVERY_RESOLVED') group by site_id,category",(start.isoformat()[:19],end.isoformat()[:19]))]
out['new_human_tasks']=[dict(x) for x in r.execute("select identity_id,state,count(*) n from challenge_tasks where json_extract(data_json,'$.opened_at')>=? and json_extract(data_json,'$.opened_at')<? group by identity_id,state",(start.isoformat()[:19],end.isoformat()[:19]))]
out['new_solver_jobs']=[dict(x) for x in r.execute("select identity_id,json_extract(data_json,'$.status') status,count(*) n from datadome_solver_jobs where created_at>=? and created_at<? group by identity_id,status",(start.isoformat()[:19],end.isoformat()[:19]))]
out['resources']={'sampled_at':datetime.now(timezone.utc).isoformat(),'disk_free':shutil.disk_usage('/').free,'acquisition_bytes':(root/'acquisition.sqlite3').stat().st_size,'acquisition_wal':(root/'acquisition.sqlite3-wal').stat().st_size if (root/'acquisition.sqlite3-wal').exists() else 0,'psi':{n:Path('/proc/pressure/'+n).read_text().strip() for n in ['cpu','memory','io']},'meminfo':{x.split(':')[0]:x.split(':')[1].strip() for x in Path('/proc/meminfo').read_text().splitlines() if x.split(':')[0] in ['MemAvailable','SwapFree']}}
for name in ['doxagent-v2-v2-message-collector-1','doxagent-v2-v2-site-access-1','doxagent-v2-v2-chrome-supervisor-1']:
 v=json.loads(subprocess.check_output(['docker','inspect',name]))[0];out.setdefault('containers',[]).append({'name':name,'id':v['Id'][:12],'started_at':v['State']['StartedAt'],'running':v['State']['Running'],'restarts':v['RestartCount'],'oom_killed':v['State']['OOMKilled']})
Path(args.output).write_text(json.dumps(out),encoding='utf-8');print(json.dumps({'output':args.output,'elapsed_minutes':out['elapsed_minutes'],'sources':len(G),'physical_cycles':len(seen)}));b.close();a.close();r.close()
