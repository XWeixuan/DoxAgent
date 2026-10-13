"""Exact-baseline three-service overlay; preserve supervisor and Chrome processes."""
import json,subprocess,time
from pathlib import Path
root=Path('/home/ubuntu/source-repair-20261012')
def run(args,timeout=120):
 p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(p.stderr[-1600:] or p.stdout[-1600:])
 return p.stdout
def inspect(name):return json.loads(run(['docker','inspect',name]))[0]
names=['doxagent-v2-v2-'+s+'-1' for s in ('site-access','message-bus','content-enrichment','chrome-supervisor')]
before={n:inspect(n) for n in names}
for n in names[:3]:
 expected={'site-access':'doxagent-site-access:source-storage-20261009-r1','message-bus':'doxagent-v2:source-storage-bus-20261009-r1','content-enrichment':'doxagent-v2:source-storage-enrich-20261009-r1'}
 key=n.removeprefix('doxagent-v2-v2-').removesuffix('-1')
 assert before[n]['Config']['Image']==expected[key], 'production_baseline_changed'
labels=before[names[0]]['Config']['Labels']
files=labels['com.docker.compose.project.config_files'].split(',')
assert all(Path(f).is_file() for f in files)
compose=['docker','compose','-p','doxagent-v2','--env-file','/home/ubuntu/doxagent/.env.v2']
for f in files+[str(root/'compose.override.yml')]:compose+=['-f',f]
# Root-only receipt contains no container environment or credentials.
receipt={'before':{n:{'id':v['Id'],'image':v['Image'],'tag':v['Config']['Image'],'started':v['State']['StartedAt']} for n,v in before.items()},'compose_files':files,'overlay':str(root/'compose.override.yml')}
(root/'release.json').write_text(json.dumps(receipt,indent=2));(root/'release.json').chmod(0o600)
run(compose+['config','--quiet'])
print('draining_bus_and_enrichment',flush=True)
run(['docker','stop','-t','100',names[1],names[2]],timeout=210)
print('draining_site_access',flush=True)
run(['docker','stop','-t','100',names[0]],timeout=110)
try:
 print(run(compose+['up','-d','--no-deps','--no-build','v2-site-access','v2-message-bus','v2-content-enrichment'],timeout=180),flush=True)
except Exception:
 # Retain exact rollback metadata and old images. Start old containers only
 # when still present; never restart supervisor to mask a deployment error.
 for n in names[:3]:
  try:run(['docker','start',n])
  except Exception:pass
 raise
after={n:inspect(n) for n in names}
assert before[names[3]]['Id']==after[names[3]]['Id'] and before[names[3]]['State']['StartedAt']==after[names[3]]['State']['StartedAt']
receipt['after']={n:{'id':v['Id'],'image':v['Image'],'tag':v['Config']['Image'],'started':v['State']['StartedAt']} for n,v in after.items()}
(root/'release.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
