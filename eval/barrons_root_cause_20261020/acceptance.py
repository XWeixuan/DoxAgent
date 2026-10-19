"""Read-only time-window acceptance. Does not generate publisher traffic."""
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime

since = sys.argv[1]
names = ['doxagent-v2-v2-site-access-1','doxagent-v2-v2-message-bus-1']
containers=json.loads(subprocess.check_output(['docker','inspect',*names],text=True))
paths=[next(m['Source'] for m in c['Mounts'] if m['Destination']==dest)
       for c,dest in zip(containers,['/site-data','/data'])]
site=sqlite3.connect('file:'+paths[0]+'/registry/site_strategy.sqlite3?mode=ro',uri=True,timeout=3)
site.row_factory=sqlite3.Row
bus=sqlite3.connect('file:'+paths[1]+'/bus/bus.sqlite3?mode=ro',uri=True,timeout=3)
events=[{**dict(r),'payload':json.loads(r['payload_json'])} for r in site.execute(
    "select operation_id,occurred_at,category,payload_json from access_events where site_id='barrons' and occurred_at>=? order by occurred_at",(since,))]
success=[r for r in events if r['category']=='ACCESS_RESULT' and r['payload'].get('disposition')=='SUCCESS']
results=[r for r in events if r['category']=='ACCESS_RESULT']
out={'at':datetime.now(UTC).isoformat(),'since':since,
     'successes':dict(Counter(r['payload'].get('purpose') for r in success)),
     'result_reasons':dict(Counter(r['payload'].get('reason') for r in results)),
     'first_success':success[0] if success else None,'last_success':success[-1] if success else None,
     'risk_events':[{k:v for k,v in r.items() if k!='payload_json'} for r in events if r['category']=='COMBINATION_RISK_FAILURE']}
out['polls']=[{k:v.get(k) for k in ('ticker','status','last_success_at','last_attempt_at','last_error_code','last_error_message','consecutive_failures','last_latency_ms','collected_count','filtered_count')} for v in
              (json.loads(r[0]) for r in bus.execute("select data_json from poll_states where source_id='barrons_ticker_news'"))]
out['body_outcomes']=[dict(r) for r in site.execute("select job_id,outcome,reason,completed_at from body_outcomes where final_site_id='barrons' and completed_at>=? order by completed_at desc limit 15",(since,))]
out['tasks']=[{k:v.get(k) for k in ('task_id','state','reason','opened_at','resolved_at','original_target_id')} for v in (json.loads(r[0]) for r in site.execute("select data_json from challenge_tasks where identity_id='dowjones-main' order by updated_at desc limit 5"))]
out['scope']=json.loads(site.execute("select data_json from site_runtime where runtime_key='scope:source:barrons_ticker_news'").fetchone()[0])
print(json.dumps(out))
