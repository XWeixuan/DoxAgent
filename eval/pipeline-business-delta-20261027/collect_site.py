"""Metadata-only Site Access phase and live ownership collection."""
import gzip
import json
import sqlite3
import sys
import time
from datetime import UTC,datetime

since,until=sys.argv[1:3]
db=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True,timeout=2)
db.row_factory=sqlite3.Row
deadline=time.monotonic()+25
db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
out=dict(since=since,until=until,collected_at=datetime.now(UTC).isoformat())
out['errors']={}
def query(name,sql):
    global deadline
    deadline=time.monotonic()+15
    try:return [dict(r) for r in db.execute(sql)]
    except sqlite3.Error as exc:out['errors'][name]=str(exc);return []
out['events']=[]
for row in db.execute("SELECT operation_id,site_id,occurred_at,category,payload_json FROM access_events WHERE julianday(occurred_at)>=julianday(?) AND julianday(occurred_at)<=julianday(?) AND category IN ('ACCESS_RESULT','PROBE_FINISHED','PROBE_STARTED','CAPTURE_CONSUMED') ORDER BY julianday(occurred_at)",(since,until)):
    value=json.loads(row['payload_json'])
    out['events'].append({k:row[k] for k in ('operation_id','site_id','occurred_at','category')}|
        {k:value.get(k) for k in ('disposition','reason','reason_code','identity_id','purpose',
            'network_ms','queue_wait_ms','stage_timings','work_class','probe_id','stage','total_ms','queue_ms','control_ms','site_ms','error_type','request_sent','request_id') if k in value})
print('events',len(out['events']),file=sys.stderr,flush=True)
deadline=time.monotonic()+15
out['identities']=query('identities','SELECT identity_id,active_revision,enabled,updated_at FROM browser_identity_heads')
out['runtime']=[]
for row in query('runtime','SELECT identity_id,data_json,generation,updated_at FROM identity_runtime'):
    value=json.loads(row.pop('data_json'))
    row['state']={k:v for k,v in value.items() if k in ('state','status','phase','browser_instance_id','last_used_at','last_success_at','last_error','active_pages','live_pages','inflight','running','generation','worker_state')}
    out['runtime'].append(row)
out['targets']=query('targets',"SELECT identity_id,browser_instance_id,target_id,owner_kind,state,updated_at FROM owned_browser_targets WHERE state NOT IN ('CLOSED','LOST')")
out['scopes']=[]
for row in query('scopes','SELECT source_id,data_json FROM source_access_scopes'):
    value=json.loads(row['data_json'])
    out['scopes'].append(dict(source_id=row['source_id'],scope={k:v for k,v in value.items()
        if k in ('scope_id','members','source_id','mode','routing','revision','minimum_interval_seconds','allowed_sites')}))
out['strategies']=[]
for row in query('strategies',"SELECT r.* FROM site_strategy_revisions r JOIN site_strategy_heads h ON h.site_id=r.site_id AND h.active_revision=r.revision"):
    value=json.loads(row['spec_json'])
    out['strategies'].append({k:v for k,v in value.items() if k in ('site_id','revision','display_name','budgets','execution','timeouts','fetch','crawler','body','methods','access','browser','limits','request','capabilities')})
db.close()
sys.stdout.buffer.write(gzip.compress(json.dumps(out).encode()))
