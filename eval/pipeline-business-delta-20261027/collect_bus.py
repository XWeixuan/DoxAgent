"""Read-only fixed-cutoff execution metadata; no article bodies, no schema changes."""
import gzip
import json
import sqlite3
import sys
import time
from datetime import UTC,datetime
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent

since,until=sys.argv[1:3]
path=DoxAgentSettings().message_bus_v2_sqlite_path
db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2)
db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
native=NativeContent(path)
out=dict(since=since,until=until,collected_at=datetime.now(UTC).isoformat(),errors={})
deadline=0
db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
def query(name,sql,args=()):
    global deadline
    start=time.monotonic();deadline=start+20
    try:
        value=[dict(r) for r in db.execute(sql,args)]
        print(name,len(value),round(time.monotonic()-start,2),file=sys.stderr,flush=True)
        return value
    except sqlite3.Error as exc:
        out['errors'][name]=str(exc);return []
def decoded(raw):return native.decode(json.loads(raw))
def pick(value,keys):return {k:value.get(k) for k in keys.split() if k in value}

out['schedules']=query('schedules',"SELECT * FROM poll_runs WHERE status='SCHEDULE' AND dispatch_at<=? ORDER BY dispatch_at",(until,))
for row in out['schedules']:row.update(json.loads(row.pop('data_json')))
out['capture_backlog']=query('capture_backlog',"SELECT source_id,status,count(*) captures,sum(message_count-intake_position) inputs,min(observed_at) oldest FROM poll_captures WHERE status IN ('PENDING','RUNNING','FAILED') GROUP BY source_id,status")
out['input_reuse']=query('input_reuse',"SELECT p.source_id,count(*) captures,sum(p.message_count) inputs,sum(k.reused_from IS NOT NULL) reused FROM poll_capture_inputs k JOIN poll_captures p USING(capture_id) WHERE p.observed_at>=? AND p.observed_at<=? GROUP BY p.source_id",(since,until))
out['body_records']=query('body_records',"SELECT fetch_key,first_observed_at,status,next_check_at,data_json AS encoded FROM article_body_results WHERE first_observed_at>=? AND first_observed_at<=? ORDER BY first_observed_at",(since,until))
for row in out['body_records']:
    value=json.loads(row.pop('encoded'))
    row.update(pick(value,'canonical_article_key publisher access_scope language source_id url final_canonical_url eligible legacy_result prior_publication_raw_id acquired_at last_error outcome first_fetch_at check_seq acquired_scope updated_at'))
out['jobs']=[]
for row in query('jobs',"SELECT job_id,status,created_at,data_json AS encoded FROM content_enrichment_jobs WHERE created_at>=? AND created_at<=? ORDER BY created_at",(since,until)):
    value=decoded(row.pop('encoded'));message=value.get('message') or {};metadata=message.get('metadata') or {}
    row.update(pick(value,'work_kind owner_kind bootstrap execution_id created_at updated_at deadline_at budget_started_at first_fetch_at prior_attempts body_result_key known_article_raw_id poll_run_id article_id not_before claimed_at lease_until claim_owner'))
    row.update(source_id=(value.get('source') or {}).get('source_id'),ticker=(value.get('binding') or {}).get('ticker'),
        result=pick(value.get('result') or {},'outcome reason completed_at error message'),
        pipeline_trace=metadata.get('pipeline_trace'),url=message.get('url'),body_state=metadata.get('body_update_state'))
    out['jobs'].append(row)
out['delivery']=[];out['body_executions']=[]
for kind,key in [('message_delivery','delivery'),('body_completion','body_executions')]:
    for row in query(key,"SELECT data_json AS encoded FROM audit_log WHERE entity_type=? AND created_at>=? AND created_at<=? ORDER BY created_at",(kind,since,until)):
        value=decoded(row['encoded']);payload=value.get('payload') or {}
        if kind=='message_delivery':out[key].append(value)
        else:out[key].append(payload)
out['job_queue']=query('job_queue',"SELECT status,count(*) n,min(created_at) oldest FROM content_enrichment_jobs GROUP BY status")
out['distribution_backlog']=query('distribution_backlog',"SELECT state,count(*) n FROM distribution_jev_continuations GROUP BY state")
for table,key in [('source_definitions','sources'),('ticker_source_bindings','bindings'),('operational_alerts','alerts')]:
    out[key]=[]
    for row in query(key,'SELECT data_json AS encoded FROM '+table):
        value=decoded(row['encoded'])
        if key=='poll_states':
            cp=value.get('checkpoint') or {};units=value.get('units') or {}
            value={**pick(value,'binding_id ticker source_id status target_due_at next_dispatch_at last_dispatch_at last_success_at last_failure_at consecutive_failures last_error_code last_latency_ms last_stage_timings last_capture_at last_first_intake_at collected_count filtered_count published_count source_definition_version binding_version'),
                'unit_summary':{unit:pick(v,'next_dispatch_at target_due_at last_success_at last_error_code status') for unit,v in units.items()},
                'checkpoint_keys':list(cp),'ir_endpoints':cp.get('ir_endpoints'),
                'pending_timestamps':len(cp.get('pending_timestamp_rows',[]))}
        out[key].append(value)
out['control']=query('control','SELECT data_json FROM bus_collection_control ORDER BY revision DESC LIMIT 1')
out['table_columns']={t:[r[1] for r in db.execute('PRAGMA table_info('+t+')')] for t in ['distribution_runs','distribution_deliveries','distribution_articles','message_observations','logical_message_versions','stream_items']}
out['source_head']=query('source_head',"SELECT head FROM v2_source_state WHERE source='bus'")
out['stream_publications']=query('stream_publications','SELECT ticker,count(*) n,max(published_at) latest FROM stream_items WHERE published_at>=? AND published_at<=? GROUP BY ticker',(since,until))
db.close()
sys.stdout.buffer.write(gzip.compress(json.dumps(out,ensure_ascii=False).encode()))
