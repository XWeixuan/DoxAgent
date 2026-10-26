"""Fixed-window production metadata only, with bounded read work and no schema writes."""
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime

from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent

start = sys.argv[1]
settings = DoxAgentSettings()
path = settings.message_bus_v2_sqlite_path
db = sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2)
db.row_factory = sqlite3.Row
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
deadline = time.monotonic()+20
db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
native = NativeContent(path)


def query(sql,args=()):
    global deadline
    began = time.monotonic()
    deadline = began+12
    result = [dict(r) for r in db.execute(sql,args)]
    print(json.dumps({'query':sql.split('FROM')[0][:70],'seconds':round(time.monotonic()-began,3),'rows':len(result)}),file=sys.stderr,flush=True)
    return result


out = {'since':start,'at':datetime.now(UTC).isoformat()}
out['runs'] = query("SELECT * FROM poll_runs WHERE dispatch_at>=? AND status!='SCHEDULE' ORDER BY dispatch_at LIMIT 100000",(start,))
out['schedules'] = query("SELECT * FROM poll_runs WHERE status='SCHEDULE' ORDER BY dispatch_at DESC LIMIT 12000")
for row in [*out['runs'],*out['schedules']]:
    row.update(json.loads(row.pop('data_json')))
out['captures'] = query("SELECT capture_id,run_id,binding_id,source_id,ticker,unit,observed_at,committed_at,status,intake_position,message_count,first_intake_at,completed_at,last_error,request_started_at,source_version,binding_version FROM poll_captures WHERE observed_at>=? ORDER BY observed_at LIMIT 100000",(start,))
out['backlog'] = query("SELECT source_id,status,count(*) captures,sum(message_count-intake_position) inputs,min(observed_at) oldest FROM poll_captures WHERE status IN ('PENDING','RUNNING') GROUP BY source_id,status")
out['reuse'] = query("SELECT p.source_id,count(*) captures,sum(p.message_count) inputs FROM poll_capture_inputs k JOIN poll_captures p USING(capture_id) WHERE p.observed_at>=? AND k.reused_from IS NOT NULL GROUP BY p.source_id",(start,))
out['new_body_records'] = query("SELECT fetch_key,first_observed_at,status,next_check_at,json_extract(data_json,'$.outcome') outcome,json_extract(data_json,'$.source_id') source_id,json_extract(data_json,'$.url') url,json_extract(data_json,'$.final_canonical_url') final_url,json_extract(data_json,'$.eligible') eligible,json_extract(data_json,'$.legacy_result') legacy_result,json_extract(data_json,'$.prior_publication_raw_id') prior_publication_raw_id,json_extract(data_json,'$.acquired_at') acquired_at,json_extract(data_json,'$.last_error') error FROM article_body_results WHERE first_observed_at>=? ORDER BY first_observed_at LIMIT 5000",(start,))
out['delivery_receipts'] = []
for row in query("SELECT data_json AS encoded FROM audit_log WHERE entity_type='message_delivery' AND created_at>=? ORDER BY created_at LIMIT 5000",(start,)):
    out['delivery_receipts'].append(native.decode(json.loads(row['encoded'])))
out['jobs'] = []
for row in query("SELECT job_id,status,created_at,data_json AS encoded FROM content_enrichment_jobs WHERE created_at>=? ORDER BY created_at LIMIT 3000",(start,)):
    value = native.decode(json.loads(row.pop('encoded')))
    message = value.get('message',{})
    row.update(work_kind=value.get('work_kind'),owner_kind=value.get('owner_kind'),bootstrap=value.get('bootstrap'),
        execution_id=value.get('execution_id'),source_id=value.get('source',{}).get('source_id'),
        ticker=(value.get('binding') or {}).get('ticker'),created_at=value.get('created_at'),
        deadline_at=value.get('deadline_at'),budget_started_at=value.get('budget_started_at'),
        prior_attempts=value.get('prior_attempts'),body_result_key=value.get('body_result_key'),
        known_article_raw_id=value.get('known_article_raw_id'),poll_run_id=value.get('poll_run_id'),
        result={k:(value.get('result') or {}).get(k) for k in ('outcome','reason','completed_at')},article_id=value.get('article_id'),
        pipeline_trace=message.get('metadata',{}).get('pipeline_trace'),url=message.get('url'))
    out['jobs'].append(row)
out['body_executions'] = []
for row in query("SELECT data_json AS encoded FROM audit_log WHERE entity_type='body_completion' AND created_at>=? ORDER BY created_at LIMIT 10000",(start,)):
    out['body_executions'].append(native.decode(json.loads(row['encoded']))['payload'])
out['job_queue'] = query("SELECT status,count(*) count,min(created_at) oldest FROM content_enrichment_jobs WHERE status IN ('queued','running','retry_wait') GROUP BY status")
out['continuations'] = query("SELECT state,count(*) count FROM distribution_jev_continuations GROUP BY state")
out['control'] = query("SELECT data_json FROM bus_collection_control ORDER BY revision DESC LIMIT 1")
out['source_head'] = query("SELECT head FROM v2_source_state WHERE source='bus'")
out['tail_debt'] = [{'binding_id':r['binding_id'],'unit':r['unit'],'status':r['status'],'due_at':r['due_at'],'dispatch_at':r['dispatch_at'],'finished_at':r.get('finished_at'),'reason':r.get('reason')} for r in out['runs'] if r['unit'].startswith('TAIL:')]
out['censored'] = {'runs':len(out['runs'])==100000,'captures':len(out['captures'])==100000,'jobs':len(out['jobs'])==3000,'body_records':len(out['new_body_records'])==5000}
db.close()
print(json.dumps(out,ensure_ascii=False))
