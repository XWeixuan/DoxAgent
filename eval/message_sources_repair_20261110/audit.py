"""Read-only bounded source audit; no app initialization or network requests."""
import argparse
import collections
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--hours', type=float, default=2)
p.add_argument('--since')
args = p.parse_args()
now = datetime.now(timezone.utc)
since = (datetime.fromisoformat(args.since.replace('Z','+00:00')) if args.since else now - timedelta(hours=args.hours)).isoformat()[:19]
root = Path('/var/lib/docker/volumes')

def read(path):
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('pragma query_only=on')
    return db

def safe(s):
    return re.sub(r'https?://\S+', '[url]', str(s or ''))[:300]

b = read(root/'doxagent-v2_v2-data/_data/bus/bus.sqlite3')
a = read(root/'doxagent-v2_v2-data/_data/bus/acquisition.sqlite3')
r = read(root/'doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
out = {'at':now.isoformat(), 'since':since, 'window_end':now.isoformat(), 'disk_free':shutil.disk_usage('/').free}
out['containers'] = []
for role in ('v2-api','v2-message-collector','v2-message-bus','v2-content-enrichment','v2-site-access','v2-chrome-supervisor'):
    c = json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
    env = dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
    out['containers'].append({'role':role, 'id':c['Id'][:12], 'started':c['State']['StartedAt'],
        'commit':env.get('DOXAGENT_BUILD_COMMIT'), 'restarts':c['RestartCount'], 'oom':c['State']['OOMKilled'],
        'recheck':env.get('DOXAGENT_CONTENT_ENRICHMENT_RECHECK_ENABLED')})
specs = {v['source_id']:v for row in b.execute('select data_json from source_definitions where enabled=1') for v in [json.loads(row[0])]}
out['sources'] = [{k:v.get(k) for k in ('source_id','display_name','kind','acquisition_mode','adapter_ref','content_language','delivery_mode')} for v in specs.values()]
out['units'] = []
for row in a.execute('select binding_id,source_id,ticker,unit,required,state,head_json from units where required>0'):
    if row['source_id'] not in specs: continue
    v=json.loads(row['state']); h=json.loads(row['head_json'] or '{}')
    out['units'].append({**{k:row[k] for k in ('source_id','ticker','unit','binding_id')},
       **{k:v.get(k) for k in ('status','last_attempt_at','last_success_at','next_dispatch_at','consecutive_failures','target_due_at')},
       'last_error':safe(v.get('last_error_code')), 'head_status':h.get('status'), 'head_error':safe(h.get('last_error_code'))})
out['cycles'] = [dict(row) for row in a.execute("select source_id,status,reason,count(*) n,min(dispatch_at) first,max(dispatch_at) last from cycles indexed by cycles_time where dispatch_at>=? and dispatch_at<? and status<>'SCHEDULE' group by source_id,status,reason",(since,now.isoformat()))]
physical={}
for row in a.execute("select source_id,run_id,status,reason,data_json from cycles indexed by cycles_time where dispatch_at>=? and dispatch_at<? and status<>'SCHEDULE'",(since,now.isoformat())):
    v=json.loads(row['data_json']); key=(row['source_id'],v.get('physical_run_id') or row['run_id'].split(':',1)[0])
    physical[key]=(row['status'],row['reason'])
summary=collections.Counter((sid,status,safe(reason)) for (sid,pid),(status,reason) in physical.items())
out['physical_cycles']=[{'source_id':sid,'status':status,'reason':reason,'n':n} for (sid,status,reason),n in summary.items()]
out['access'] = []
counts=collections.Counter(); durations=collections.defaultdict(list)
for row in r.execute('select site_id,category,payload_json from access_events where occurred_at>=? and occurred_at<?',(since,now.isoformat())):
    if row['category']!='ACCESS_RESULT':continue
    v=json.loads(row['payload_json'])
    key=(row['site_id'],v.get('purpose'),v.get('disposition'),v.get('reason_code') or v.get('reason'),v.get('status_code'))
    counts[key]+=1
for key,n in counts.items():out['access'].append(dict(zip(('site','purpose','disposition','reason','status_code','n'),(*key,n))))
out['body']=[dict(row) for row in r.execute('select final_site_id,outcome,reason,count(*) n,max(completed_at) last from body_outcomes where completed_at>=? and completed_at<? group by 1,2,3',(since,now.isoformat()))]
out['schema']={name:[row[1] for row in r.execute('pragma table_info('+name+')')] for name in ('site_runtime','identity_runtime','browser_recoveries','challenge_tasks')}
out['recoveries_observed_at']=datetime.now(timezone.utc).isoformat()
out['recoveries']=[]
for row in r.execute('select data_json from browser_recoveries'):
    v=json.loads(row[0])
    if v.get('phase')=='RESOLVED':continue
    out['recoveries'].append({k:v.get(k) for k in ('recovery_id','site_id','identity_id','phase','handling_mode','manual_review_reason','automation_active','updated_at','next_probe_at','attempt_count','original_target_id','page_status','last_error')})
out['finished_at']=datetime.now(timezone.utc).isoformat()
print(json.dumps(out, ensure_ascii=False, indent=2))
