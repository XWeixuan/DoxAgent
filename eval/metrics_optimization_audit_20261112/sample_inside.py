"""Bounded metadata reads in the scheduler container; never initialize a store."""
import os,json,sqlite3,time
from pathlib import Path
from datetime import datetime,UTC,timedelta
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.query_budget import deadline

started=time.monotonic();out={'at':datetime.now(UTC).isoformat(),'cases':[]}
def connect(path):
 db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
 db.row_factory=sqlite3.Row;db.set_progress_handler(lambda:time.monotonic()-started>12,1000);return db
rt=connect(os.environ['DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH'])
jobs_path=Path(os.environ['DOXAGENT_CODEX_WORKSPACE_ROOT'])/'worker-jobs.sqlite3'
worker=connect(jobs_path) if jobs_path.is_file() else None
out['worker_visible']=worker is not None
rows=rt.execute("SELECT case_id,ticker,created_at,status,json_extract(payload_json,'$.runtime_mode') mode,json_extract(payload_json,'$.decision_completed_at') decision_at FROM runtime_v2_cases ORDER BY rowid DESC LIMIT 100").fetchall()
for row in rows:
 if len(out['cases'])>=6:break
 if row['mode']!='REALTIME' or not row['decision_at']:continue
 cid=row['case_id']
 st=rt.execute("SELECT phase,status,worker_job_id,accepted_at,committed_at,disposition FROM runtime_v2_w3_stages WHERE case_id=? AND phase='trade_decision'",(cid,)).fetchone()
 if not st or st['status']!='COMMITTED':continue
 item={'case':dict(row),'stage':dict(st)}
 turns=rt.execute("SELECT turn_id,lane,round_name,attempt_number,status,json_extract(payload_json,'$.started_at') started_at,json_extract(payload_json,'$.finished_at') finished_at FROM runtime_v2_turns WHERE case_id=? ORDER BY lane,round_name,attempt_number",(cid,)).fetchall()
 item['turns']=[dict(t) for t in turns]
 receipt=[]
 from doxagent.persistent_runtime_v2.journal import digest
 for t in turns:
  identity=f"{cid}:{t['lane']}:{t['round_name']}:{t['attempt_number']}"
  p=rt_path=Path(os.environ['DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH']).parent/'runtime-round-receipts'/(digest(identity)+'.json')
  if p.is_file():
   v=json.loads(p.read_text());receipt.append({k:v.get(k) for k in ['identity','status','provider_started_at','provider_completed_at']})
 item['round_receipts']=receipt
 inv=rt.execute("SELECT json_extract(payload,'$.job.resource_receipt.execution_timing'),json_extract(payload,'$.job.telemetry.sdk_duration_ms'),json_extract(payload,'$.job.started_at'),json_extract(payload,'$.job.finished_at'),json_extract(payload,'$.task_phase') FROM runtime_values WHERE namespace='worker_invocations' AND json_extract(payload,'$.job.job_id')=? LIMIT 1",(st['worker_job_id'],)).fetchone() if False else None
 if worker and st['worker_job_id']:
  j=worker.execute("SELECT id,status,json_extract(payload,'$.run_id') run_id,json_extract(payload,'$.attempt_id') attempt_id,json_extract(payload,'$.started_at') started_at,json_extract(payload,'$.finished_at') finished_at,json_extract(payload,'$.resource_receipt.execution_timing') execution_timing,json_extract(payload,'$.telemetry.sdk_duration_ms') sdk_duration_ms,json_extract(payload,'$.thread_id') thread_id FROM jobs WHERE id=?",(st['worker_job_id'],)).fetchone()
  item['job']=dict(j) if j else None
  if j:
   item['attempt_jobs']=[dict(x) for x in worker.execute("SELECT id,status,json_extract(payload,'$.attempt_id') attempt_id,json_extract(payload,'$.resource_receipt.execution_timing') execution_timing FROM jobs INDEXED BY jobs_run_owner WHERE json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.attempt_id') LIKE ? LIMIT 24",(j['run_id'],cid[-24:]+'-%'))]
 intent=rt.execute("SELECT json_extract(payload,'$.status') status,json_extract(payload,'$.released_at') released_at,json_extract(payload,'$.ready_accepted_at') ready_accepted_at,json_extract(payload,'$.decision_created_at') decision_created_at FROM runtime_values WHERE namespace='trade_intents' AND key=?",('trade:'+cid,)).fetchone()
 item['intent']=dict(intent) if intent else None
 out['cases'].append(item)

store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']);token=deadline.set(time.monotonic()+4)
with store.connect() as db:
 out['article_plan']=[tuple(x) for x in db.execute("EXPLAIN QUERY PLAN SELECT id FROM object_current WHERE kind='native:live_deliveries' AND ticker=? AND source_id=? ORDER BY sort_key DESC,id DESC LIMIT 20",('INTC','yahoo_finance_news'))]
 out['article_sample']=[tuple(r) for r in db.execute("SELECT id,sort_key,json_extract(payload,'$.first_discovered_at'),json_extract(payload,'$.standard_id') FROM object_current INDEXED BY current_source WHERE kind='native:live_deliveries' AND ticker='INTC' AND source_id='yahoo_finance_news' AND day=? ORDER BY sort_key DESC LIMIT 5",(datetime.now(UTC).date().isoformat(),))]
 out['message_sample']=[tuple(r) for r in db.execute("SELECT id,json_extract(payload,'$.source_published_at'),json_extract(payload,'$.publication_time_basis'),json_extract(payload,'$.stream_published_at'),json_extract(payload,'$.pipeline.discovered_at') FROM object_current INDEXED BY current_source WHERE kind='message' AND ticker='INTC' AND source_id='yahoo_finance_news' AND day=? ORDER BY sort_key DESC LIMIT 5",(datetime.now(UTC).date().isoformat(),))]
deadline.reset(token);rt.close()
if worker:worker.close()
out['seconds']=time.monotonic()-started
print(json.dumps(out))
