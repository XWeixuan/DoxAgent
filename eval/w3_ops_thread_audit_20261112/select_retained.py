import json,sqlite3,os,signal,datetime,random
from pathlib import Path
os.nice(15);signal.alarm(40);R=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def db(p):return sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
d=db(R/'runtime/runtime.sqlite3');w=db(R/'workspaces/worker-jobs.sqlite3');s=db(R/'codex-home/state_5.sqlite')
def t(x):return datetime.datetime.fromisoformat(x).timestamp()
rows=d.execute("select case_id,thread_id,committed_at,worker_job_id from runtime_v2_w3_stages where phase='post_decision_operations' and status='COMMITTED' order by rowid desc limit 15000").fetchall();eligible=[];missing=0
for cid,tid,comm,jid in rows:
 thread=s.execute('select rollout_path from threads where id=?',(tid,)).fetchone()
 if not thread:missing+=1;continue
 path=R/str(thread[0]).removeprefix('/data/')
 if not path.exists():continue
 jr=w.execute('select payload from jobs where id=?',(jid,)).fetchone()
 if not jr:continue
 j=json.loads(jr[0]);e=j.get('resource_receipt',{}).get('execution_timing',{})
 if not e.get('sdk_completed_at'):continue
 sdk=t(e['sdk_completed_at'])-t(e['sdk_dispatched_at'])
 if sdk<=300:continue
 tr=d.execute("select committed_at from runtime_v2_w3_stages where case_id=? and phase='trade_decision'",(cid,)).fetchone()
 if not tr or not tr[0]:continue
 wall=t(comm)-t(tr[0])
 if wall-sdk>max(10,sdk*.1):continue
 eligible.append(dict(case_id=cid,thread_id=tid,sdk=sdk,wall=wall,job_id=jid,window=j['started_at'][:10]))
eligible.sort(key=lambda c:c['sdk'],reverse=True)
assert len(eligible)>=30,len(eligible)
selected=[dict(c,group='top10') for c in eligible[:10]]+[dict(c,group='random20') for c in random.Random(20261112).sample(eligible[10:],20)]
x={'eligible':len(eligible),'scanned':len(rows),'missing_index':missing,'selection':'retained threads SDK>300s; nonSDK<=max(10s,10%SDK); top10 + disjoint seed20261112 random20','cases':selected};Path('/tmp/w3-ops-retained-20261112.json').write_text(json.dumps(x));print(json.dumps(x))

