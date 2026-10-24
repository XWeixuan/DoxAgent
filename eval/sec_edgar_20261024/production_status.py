import json,sqlite3,subprocess,statistics
from pathlib import Path
from datetime import datetime,UTC
ROOT=Path('/var/lib/doxagent/sec-edgar-20261024');data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');out={'at':datetime.now(UTC).isoformat()}
def run(args):return subprocess.check_output(args,text=True).strip()
ids=run(['docker','ps','-aq']).splitlines();containers=json.loads(run(['docker','inspect',*ids]));selected={'v2-message-bus','v2-content-enrichment','v2-api','v2-control','v2-initialization','v2-scheduler','v2-chrome-supervisor','v2-site-access'};out['containers']=[{'service':c['Config']['Labels'].get('com.docker.compose.service'),'container':c['Id'],'image':c['Image'],'started_at':c['State']['StartedAt'],'running':c['State']['Running'],'health':c['State'].get('Health',{}).get('Status'),'restarts':c['RestartCount']} for c in containers if c['Config'].get('Labels',{}).get('com.docker.compose.service') in selected]
start=next(c['started_at'] for c in out['containers'] if c['service']=='v2-message-bus');out['bus_started_at']=start
with sqlite3.connect((data/'bus/bus.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
 out['bindings']=[]
 for bid,ticker,raw in db.execute("SELECT binding_id,ticker,data_json FROM ticker_source_bindings WHERE source_id='sec_edgar'"):
  b=json.loads(raw);p=db.execute('SELECT data_json FROM poll_states WHERE binding_id=?',(bid,)).fetchone();p=json.loads(p[0]) if p else {};out['bindings'].append({'id':bid,'ticker':ticker,'enabled':b['enabled'],'interval':b['polling']['target_interval_seconds'],'status':p.get('status'),'last_attempt_at':p.get('last_attempt_at'),'last_success_at':p.get('last_success_at'),'error':p.get('last_error_message'),'latency_ms':p.get('last_latency_ms'),'cik':p.get('checkpoint',{}).get('cik')})
 rows=db.execute("SELECT ticker,dispatch_at,finished_at,status,data_json FROM poll_runs WHERE source_id='sec_edgar' AND unit='HEAD' AND dispatch_at>=? AND status<>'SCHEDULE' ORDER BY dispatch_at",(start,)).fetchall();out['poll_runs']=[];gaps=[];durations=[];last={}
 for ticker,dispatch,finished,status,raw in rows:
  if ticker in last:gaps.append((datetime.fromisoformat(dispatch.replace('Z','+00:00'))-last[ticker]).total_seconds())
  last[ticker]=datetime.fromisoformat(dispatch.replace('Z','+00:00'));duration=(datetime.fromisoformat(finished.replace('Z','+00:00'))-last[ticker]).total_seconds() if finished else None
  if duration is not None:durations.append(duration)
  d=json.loads(raw);out['poll_runs'].append({'ticker':ticker,'dispatch':dispatch,'finished':finished,'status':status,'seconds':duration,'stages_ms':d.get('stages_ms'),'requests':d.get('requests')})
 def stats(a):
  a=sorted(a);return {'count':len(a),'mean':statistics.mean(a),'p50':statistics.median(a),'p95':a[min(len(a)-1,int(.95*len(a)))],'max':max(a)} if a else {}
 out['interval_seconds']=stats(gaps);out['completion_seconds']=stats(durations)
 out['raw_count']=db.execute("SELECT count(*) FROM raw_messages WHERE source_id='sec_edgar'").fetchone()[0]
 out['body_jobs']=db.execute("SELECT status,count(*) FROM content_enrichment_jobs WHERE json_extract(data_json,'$.source.source_id')='sec_edgar' GROUP BY status").fetchall()
 out['source_definition_count']=db.execute("SELECT count(*) FROM source_definitions WHERE source_id='sec_edgar'").fetchone()[0]
before=json.loads((ROOT/'runtime-before.json').read_text());protected=['v2-chrome-supervisor','v2-site-access'];out['protected_unchanged']={name:any(c['container']==b.get('id',c['container']) and c['image']==b['image'] and c['started_at']==b['started_at'] for b in before if b['name']=='/doxagent-v2-'+name+'-1') for name in protected for c in out['containers'] if c['service']==name}
out['timer_active']=subprocess.run(['systemctl','is-active','--quiet','doxagent-release-resources.timer']).returncode==0
(ROOT/'status.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
