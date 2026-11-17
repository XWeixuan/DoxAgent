import sqlite3,json,collections
from datetime import datetime,timedelta,timezone
c=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
since=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat();groups={};samples=[]
q="""with runs as (select json_extract(data_json,'$.physical_run_id') run_id,min(source_id) source_id from cycles indexed by cycles_time where dispatch_at>=? and source_id<>'reuters_site_search' group by 1) select r.source_id,p.requests_json from runs r join physical_cycles p on p.run_id=r.run_id"""
for r in c.execute(q,(since,)):
 g=groups.setdefault(r['source_id'],collections.Counter())
 for e in json.loads(r['requests_json'] or '[]'):
  g['events']+=1;g[e.get('transport','?')]+=1
  if e.get('request_started_at') and e.get('response_received_at'):
   d=(datetime.fromisoformat(e['response_received_at'])-datetime.fromisoformat(e['request_started_at'])).total_seconds();g['received']+=1;g['service_seconds']+=d
  if len(samples)<3:samples.append({k:v for k,v in e.items() if k not in ['url','request_key','validators']})
print(json.dumps({'since':since,'groups':groups,'samples':samples},default=str))
