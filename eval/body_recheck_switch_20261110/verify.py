"""Read-only owner/receipt acceptance; no website requests or provider calls."""
import argparse,json,sqlite3,subprocess
from datetime import datetime,timezone
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--since',required=True);a=p.parse_args()
now=datetime.now(timezone.utc).isoformat();out={'since':a.since,'at':now}
roles=('v2-api','v2-message-bus','v2-content-enrichment','v2-message-collector','v2-site-access','v2-chrome-supervisor')
out['roles']=[]
for role in roles:
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
 env=dict(x.split('=',1) for x in v['Config']['Env'] if '=' in x)
 out['roles'].append({'role':role,'id':v['Id'][:12],'commit':env.get('DOXAGENT_BUILD_COMMIT'),'started_at':v['State']['StartedAt'],'status':v['State']['Status'],'recheck_switch':env.get('DOXAGENT_CONTENT_ENRICHMENT_RECHECK_ENABLED')})
def connect(path):
 db=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('pragma query_only=on');return db
with connect('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/bus.sqlite3') as db:
 out['ordinary_active']=db.execute("select count(*) from live_articles where work_kind='recheck' and state='FETCHING' and julianday(lease_until)>julianday(?)",(now,)).fetchone()[0]
 out['ordinary_new_start']=db.execute("select count(*) from live_articles where work_kind='recheck' and julianday(first_fetch_at)>=julianday(?)",(a.since,)).fetchone()[0]
 out['parked_ordinary']=db.execute("select count(*) from live_articles where work_kind='recheck' and state in ('FETCH_DUE','FETCHING')").fetchone()[0]
 out['legacy_active']=db.execute("select count(*) from content_enrichment_jobs where json_extract(data_json,'$.work_kind')='recheck' and status='running' and julianday(lease_expires_at)>julianday(?)",(now,)).fetchone()[0]
 out['initial_results']=[dict(r) for r in db.execute("select initial_outcome,count(*) n from live_articles where julianday(initial_result_at)>=julianday(?) group by initial_outcome",(a.since,))]
 out['changed_results']=[dict(r) for r in db.execute("select work_kind,outcome,count(*) n from live_articles where julianday(checked_at)>=julianday(?) group by work_kind,outcome",(a.since,))]
 out['recent_deliveries']=[dict(r) for r in db.execute("select state,count(*) n from live_deliveries where julianday(initial_decision_at)>=julianday(?) group by state",(a.since,))]
with connect('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3') as db:
 out['body_receipts']=[dict(r) for r in db.execute("select json_extract(payload_json,'$.work_class') work_class,json_extract(payload_json,'$.disposition') disposition,count(*) n from access_events where category='ACCESS_RESULT' and occurred_at>=? and julianday(occurred_at)>=julianday(?) and json_extract(payload_json,'$.purpose')='BODY' group by work_class,disposition",(a.since[:19],a.since))]
print(json.dumps(out,indent=2))
