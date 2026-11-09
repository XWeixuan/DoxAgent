import collections, datetime, json, pathlib, sqlite3, subprocess, sys

root = pathlib.Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
cutoff = sys.argv[1] if len(sys.argv) > 1 else '2026-11-09T19:24:04+00:00'
def connect(path):
    db = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db
db = connect(root/'runtime/runtime.sqlite3')
def rows(sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]
out = {'at':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'cutoff':cutoff}
out['cases'] = rows('SELECT status,count(*) n FROM runtime_v2_cases WHERE trading_date=? GROUP BY status', ('2026-11-09',))
out['new_failed'] = rows("SELECT case_id,ticker,updated_at,json_extract(payload_json,'$.error_code') code,json_extract(payload_json,'$.error_message') message FROM runtime_v2_cases WHERE status='FAILED' AND updated_at>=? ORDER BY updated_at", (cutoff,))
out['effects'] = rows("SELECT effect_type,status,count(*) n FROM runtime_v2_effects WHERE updated_at>=? GROUP BY effect_type,status", (cutoff,))
out['stages'] = rows("SELECT phase,status,count(*) n FROM runtime_v2_w3_stages WHERE accepted_at>=? OR committed_at>=? GROUP BY phase,status", (cutoff,cutoff))
stages = rows("SELECT case_id,phase,worker_job_id,committed_at FROM runtime_v2_w3_stages WHERE status='COMMITTED' AND committed_at>=? ORDER BY committed_at LIMIT 200", (cutoff,))
worker = connect(root/'workspaces/worker-jobs.sqlite3')
timings=[]
for stage in stages:
    row = worker.execute('SELECT payload FROM jobs WHERE id=?', (stage['worker_job_id'],)).fetchone()
    if not row: continue
    job=json.loads(row[0]); timing=job.get('resource_receipt',{}).get('execution_timing',{})
    sdk=timing.get('sdk_completed_at')
    if sdk:
        delay=(datetime.datetime.fromisoformat(stage['committed_at'])-datetime.datetime.fromisoformat(sdk)).total_seconds()
        timings.append(dict(stage, sdk_completed_at=sdk, sdk_to_commit_s=round(delay,3)))
out['sdk_to_commit']=timings
out['turns']=rows("SELECT lane,round_name,status,json_extract(payload_json,'$.error_code') code,count(*) n FROM runtime_v2_turns WHERE created_at>=? GROUP BY lane,round_name,status,code",(cutoff,))
for role in ['v2-scheduler','v2-codex-worker']:
    name=('doxagent-v2-codex-worker-1' if role == 'v2-codex-worker' else 'doxagent-v2-'+role+'-1')
    info=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    cmd=['docker','exec',name,'sh','-c','cat /sys/fs/cgroup/pids.current /sys/fs/cgroup/pids.max /sys/fs/cgroup/pids.events']
    out[role]={'image':info['Image'],'started':info['State']['StartedAt'],'restarts':info['RestartCount'], 'pids':subprocess.check_output(cmd,text=True).strip()}
print(json.dumps(out,ensure_ascii=False))
