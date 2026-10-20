import json,subprocess
logs=subprocess.run(['docker','logs','--since','12m','doxagent-v2-codex-worker-1'],capture_output=True,text=True)
lines=(logs.stdout+logs.stderr).splitlines()
selected=[l[-700:] for l in lines if any(t in l.lower() for t in ['invalid value','unknown variant','invalid configuration','startup timeout','start_thread','exceeded','error:', 'exception:'])]
code="import json,sqlite3;d=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True);print(json.dumps([{'id':r[0],'state':r[1],'started':json.loads(r[2]).get('started_at'),'node':json.loads(r[3] or '{}').get('node'),'phase':json.loads(r[3] or '{}').get('task_phase')} for r in d.execute(\"SELECT id,status,payload,request FROM jobs WHERE status IN ('queued','running')\")]))"
active=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-codex-worker-1','python','-c',code],text=True))
print(json.dumps({'errors':selected[-15:],'active':active}))
