import hashlib,json,sqlite3,subprocess,sys
from datetime import datetime,UTC
from pathlib import Path
expected=json.loads(Path(sys.argv[1]).read_text())
out=dict(at=datetime.now(UTC).isoformat(),roles={})
for role,proof in expected.items():
    name='doxagent-v2-'+role+'-1'
    info=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    env=dict(s.split('=',1) for s in info['Config']['Env'] if '=' in s)
    code='import hashlib,json\npaths='+repr(list(proof['files']))+'\nprint(json.dumps({p:hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest() for p in paths}))\n'
    hashes=json.loads(subprocess.check_output(['docker','exec','-i',name,'python','-'],input=code.encode()))
    out['roles'][role]=dict(image=info['Image'],running=info['State']['Running'],health=info['State'].get('Health',{}).get('Status'),oom=info['State']['OOMKilled'],restarts=info['RestartCount'],started=info['State']['StartedAt'],revision=env.get('DOXAGENT_BUILD_COMMIT'),revision_ok=env.get('DOXAGENT_BUILD_COMMIT')==proof['commit'],files={p:hashes[p]==h for p,h in proof['files'].items()},ready_target=env.get('DOXAGENT_W3_READY_LUNA'))
data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out['wal']={}
for name in ('runtime/runtime.sqlite3','research/research.sqlite3','workspaces/worker-jobs.sqlite3','read/serving-v4-20261018/v2.sqlite3'):
    with sqlite3.connect((data/name).as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        out['wal'][name]=db.execute('PRAGMA journal_mode').fetchone()[0]
out['all_pass']=all(r['revision_ok'] and r['running'] and all(r['files'].values()) and not r['oom'] for r in out['roles'].values()) and all(v=='wal' for v in out['wal'].values())
print(json.dumps(out))
