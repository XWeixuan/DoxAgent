"""Bounded production metadata sampler; no article text or credentials exported."""
import json
import sqlite3
import time
from datetime import UTC, datetime

from doxagent.settings import DoxAgentSettings

START = '2026-10-25T18:11:12.546526+00:00'
s = DoxAgentSettings()
db = sqlite3.connect('file:'+s.message_bus_v2_sqlite_path+'?mode=ro', uri=True, timeout=2)
db.row_factory = sqlite3.Row
db.execute('PRAGMA query_only=ON')
deadline = time.monotonic()+20
db.set_progress_handler(lambda: int(time.monotonic()>deadline), 10000)

def query(sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]

out = {'since': START, 'at': datetime.now(UTC).isoformat()}
out['runs'] = query("SELECT * FROM poll_runs WHERE dispatch_at>=? AND status!='SCHEDULE' ORDER BY dispatch_at LIMIT 30000", (START,))
for row in out['runs']:
    row.update(json.loads(row.pop('data_json')))
out['schedules'] = query("SELECT * FROM poll_runs WHERE status='SCHEDULE' ORDER BY dispatch_at DESC LIMIT 10000")
for row in out['schedules']:
    row.update(json.loads(row.pop('data_json')))
out['captures'] = query("SELECT capture_id,run_id,binding_id,source_id,ticker,unit,observed_at,committed_at,status,intake_position,message_count,first_intake_at,completed_at,last_error,targets_json,request_started_at,source_version,binding_version FROM poll_captures WHERE observed_at>=? ORDER BY observed_at LIMIT 30000", (START,))
out['backlog'] = query("SELECT status,count(*) AS count,sum(message_count-intake_position) AS remaining,min(observed_at) AS oldest FROM poll_captures WHERE status NOT IN ('COMPLETED','CANCELLED') GROUP BY status")
out['poll_states'] = query("SELECT binding_id,status,data_json FROM poll_states")
for row in out['poll_states']:
    p = json.loads(row.pop('data_json'))
    row.update({k:p.get(k) for k in ('last_attempt_at','last_success_at','last_latency_ms','last_stage_timings','next_dispatch_at','last_error_code','consecutive_failures')})
out['new_articles'] = query("SELECT fetch_key,first_observed_at,status,json_extract(data_json,'$.outcome') AS outcome,json_extract(data_json,'$.source_id') AS source_id,json_extract(data_json,'$.final_canonical_url') AS url,json_extract(data_json,'$.eligible') AS eligible,json_extract(data_json,'$.legacy_result') AS legacy_result,json_extract(data_json,'$.last_error') AS error FROM article_body_results WHERE first_observed_at>=? LIMIT 10000", (START,))
out['jobs'] = query("SELECT job_id,status,created_at,not_before,json_extract(data_json,'$.source.source_id') AS source_id,json_extract(data_json,'$.binding.ticker') AS ticker,json_extract(data_json,'$.message.url') AS url,json_extract(data_json,'$.attempt_count') AS attempts,json_extract(data_json,'$.deadline_at') AS deadline_at,json_extract(data_json,'$.prior_attempts') AS prior_attempts FROM content_enrichment_jobs WHERE created_at>=? OR status IN ('queued','running','retry_wait') LIMIT 3000", (START,))
out['body_executions'] = query("SELECT created_at,json_extract(data_json,'$.payload') AS payload FROM audit_log WHERE entity_type='body_completion' AND created_at>=? ORDER BY created_at LIMIT 20000", (START,))
keys = [r['fetch_key'] for r in out['new_articles']]
if keys:
    placeholders = ','.join('?' for _ in keys)
    out['cohort_timings'] = query(f"SELECT fetch_key,first_observed_at,status,json_extract(data_json,'$.acquired_at') acquired_at,json_extract(data_json,'$.checked_at') checked_at,json_extract(data_json,'$.source_id') source_id FROM article_body_results WHERE fetch_key IN ({placeholders})", keys)
    out['cohort_observations'] = query(f"SELECT ticker,source_id,canonical_raw_id,first_seen_at,last_seen_at,json_extract(data_json,'$.metadata.article_body_result_key') fetch_key FROM message_observations WHERE json_extract(data_json,'$.metadata.article_body_result_key') IN ({placeholders}) LIMIT 1000", keys)
out['control'] = query("SELECT revision,data_json FROM bus_collection_control ORDER BY revision DESC LIMIT 1")
out['schema'] = {name: query('PRAGMA table_info('+name+')') for name in ('raw_messages','audit_log','article_body_results','stream_items')}
print(json.dumps(out, ensure_ascii=False))
