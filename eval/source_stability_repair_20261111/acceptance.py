"""Bounded read-only natural-traffic acceptance. No publisher or provider calls."""
import argparse,collections,json,sqlite3,subprocess,shutil,math
from datetime import datetime,timezone,timedelta
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--since');p.add_argument('--output',required=True);args=p.parse_args()
now=datetime.now(timezone.utc);since=args.since or (now-timedelta(minutes=15)).isoformat();end=now.isoformat()
base=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus')
def db(path):
 c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');return c
c=db(base/'acquisition.sqlite3');groups={}
# One indexed recent window, one row per physical run; never fanout-weight rates.
sql="""SELECT binding_id,ticker,source_id,unit,status,reason,dispatch_at,data_json FROM cycles INDEXED BY cycles_time
 WHERE dispatch_at>=? AND dispatch_at<? AND status<>'SCHEDULE' AND source_id<>'reuters_site_search'"""
seen=set();timings=collections.Counter();unit_windows={};supplement_runs={}
def stats(xs):
 xs=sorted(xs);return {"n":len(xs),"p50":round(xs[(len(xs)-1)//2],3),"p95":round(xs[math.ceil(len(xs)*.95)-1],3),"max":round(xs[-1],3)} if xs else {"n":0}
def stamp(t):return datetime.fromisoformat(t).timestamp()
for r in c.execute(sql,(since,end)):
 data=json.loads(r['data_json']);key=data.get('physical_run_id') or data.get('run_id')
 u=unit_windows.setdefault(r['binding_id']+'|'+r['unit'],{'source_id':r['source_id'],'ticker':r['ticker'],'unit':r['unit'],'status':collections.Counter(),'received':set()});u['status'][r['status']]+=1
 if r['status']=='CAPTURED' and data.get('list_validated') and data.get('listing_received_at'):
  u['received'].add(data['listing_received_at'])
 if key in seen:continue
 seen.add(key)
 if r['source_id'] in ['technews_hardware','quantum_zeitgeist']:supplement_runs[key]=r['source_id']
 scope=r['source_id']+(':'+r['unit'] if r['source_id']=='corporate_ir_press_releases' else '')
 g=groups.setdefault(scope,{'status':collections.Counter(),'reasons':collections.Counter(),'phases':collections.Counter()});g['status'][r['status']]+=1
 if r['reason']:g['reasons'][r['reason']]+=1
 if data.get('http_timing'):g['phases'][data['http_timing'].get('http_last_phase','unknown')]+=1
units=[dict(r) for r in c.execute("""SELECT source_id,ticker,unit,json_extract(state,'$.status') status,
 json_extract(state,'$.last_success_at') last_success_at,json_extract(state,'$.last_error_code') error,
 json_extract(state,'$.http_timing') timing,json_extract(state,'$.window_coverage') coverage,
 json_extract(state,'$.schedule_interval_seconds') interval_seconds FROM units
 WHERE required>0 AND source_id<>'reuters_site_search'""")]
for key,u in unit_windows.items():
 received=sorted({stamp(t) for t in u.pop('received') if stamp(t)>=stamp(since) and stamp(t)<=stamp(end)})
 u['success_intervals_seconds']=stats([b-a for a,b in zip(received,received[1:])])
 u['success_samples']=len(received);u['edge_gap_seconds']=round(max([received[0]-stamp(since),stamp(end)-received[-1]]) if received else stamp(end)-stamp(since),3)
supplement=collections.defaultdict(collections.Counter)
if supplement_runs:
 for row in c.execute('select run_id,requests_json from physical_cycles where run_id in (select value from json_each(?))',(json.dumps(list(supplement_runs)),)):
  es=[e for e in json.loads(row['requests_json']) if e.get('role','listing')=='listing' and e.get('list_validated') and e.get('proof_basis')=='TRANSPORT_SEND']
  supplement[supplement_runs[row['run_id']]]['both_pages' if len(es)>=2 else 'head_only' if es else 'none']+=1
containers=[]
for role in ['v2-message-collector','v2-site-access','v2-chrome-supervisor','v2-content-enrichment','v2-message-bus']:
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0];env=dict(s.split('=',1) for s in v['Config']['Env'] if '=' in s)
 containers.append({'role':role,'id':v['Id'][:12],'started':v['State']['StartedAt'],'oom':v['State']['OOMKilled'],'restarts':v['RestartCount'],'commit':env.get('DOXAGENT_BUILD_COMMIT')})
r=db(Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3'))
body=[dict(x) for x in r.execute("select final_site_id,outcome,reason,count(*) n from body_outcomes indexed by body_outcomes_utc_time where julianday(completed_at)>=julianday(?) and julianday(completed_at)<julianday(?) and final_site_id<>'reuters' group by final_site_id,outcome,reason",(since,end))]
access=[dict(x) for x in r.execute("select site_id,json_extract(payload_json,'$.disposition') disposition,json_extract(payload_json,'$.reason') reason,count(*) n from access_events indexed by access_events_utc_time where julianday(occurred_at)>=julianday(?) and julianday(occurred_at)<julianday(?) and site_id<>'reuters' and category='ACCESS_RESULT' group by site_id,disposition,reason",(since,end))]
b=db(base/'bus.sqlite3')
delivery=[dict(row) for row in b.execute("""SELECT r.source_id,r.processing_status,count(*) raw_entries,
 sum(s.standard_message_id is not null) standardized,sum(m.stream_item_id is not null) published_entries
 FROM raw_messages r INDEXED BY idx_mbv2_raw_processing
 LEFT JOIN standard_messages s ON s.raw_message_id=r.raw_message_id
 LEFT JOIN stream_members m ON m.standard_message_id=s.standard_message_id
 WHERE r.processing_status IN ('pending','processing','completed','failed') AND r.collected_at>=? AND r.collected_at<? AND r.source_id<>'reuters_site_search'
 GROUP BY r.source_id,r.processing_status""",(since,end))]
intake=[dict(row) for row in b.execute("SELECT source_id,status,count(*) n from poll_captures indexed by poll_captures_time where observed_at>=? and observed_at<? and source_id<>'reuters_site_search' group by source_id,status",(since,end))]
b.close()
out={'since':since,'until':end,'groups':groups,'units':units,'unit_windows':unit_windows,'supplement_proofs':supplement,'containers':containers,'delivery':delivery,'intake':intake,'body':body,'access':access,'resources':{'disk_free':shutil.disk_usage('/').free,'psi':{k:Path('/proc/pressure/'+k).read_text() for k in ['cpu','io','memory']},'meminfo':{s.split(':')[0]:s.split(':')[1].strip() for s in Path('/proc/meminfo').read_text().splitlines() if s.split(':')[0] in ['MemAvailable','SwapFree']},'acquisition_bytes':(base/'acquisition.sqlite3').stat().st_size,'wal_bytes':(base/'acquisition.sqlite3-wal').stat().st_size if (base/'acquisition.sqlite3-wal').exists() else 0}}
Path(args.output).write_text(json.dumps(out),encoding='utf-8');c.close();r.close();print(json.dumps({'since':since,'until':end,'scopes':len(groups),'cycles':len(seen)}))
