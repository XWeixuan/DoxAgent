"""Release finalizer limited to this repair's superseded image tags, without force."""
import json
import re
import shutil
import subprocess
from pathlib import Path

def output(args):
    return subprocess.check_output(args,text=True)

ids=output(['docker','ps','-aq']).split()
containers=json.loads(output(['docker','inspect',*ids]))
protected={c['Image'] for c in containers}
for container in containers:
    for item in container['Config'].get('Env',[]):
        key,_,value=item.partition('=')
        if key.endswith('_IMAGE') and value:
            result=subprocess.run(['docker','image','inspect',value],capture_output=True,text=True)
            if result.returncode==0:
                protected.add(json.loads(result.stdout)[0]['Id'])
for tag in ['doxagent-v2:disk-repair-read-20261013-r7','doxagent-v2:disk-repair-read-20261013-r8']:
    protected.add(json.loads(output(['docker','image','inspect',tag]))[0]['Id'])
images=json.loads(output(['docker','image','inspect',*set(output(['docker','image','ls','-q','--no-trunc']).split())]))
pattern=re.compile(r'doxagent-v2:disk-repair-(?:read-20261013-r[1-6]|bus-20261013-r1)$')
report=dict(before_free=shutil.disk_usage('/').free,retired=[],retained=[])
for image in images:
    tags=image.get('RepoTags') or []
    if not tags or not any(pattern.fullmatch(tag) for tag in tags):
        continue
    if image['Id'] in protected or not all(pattern.fullmatch(tag) for tag in tags):
        report['retained'].append(dict(id=image['Id'],tags=tags,reason='container/dynamic/multi-tag dependency'))
        continue
    result=subprocess.run(['docker','image','rm',*tags],capture_output=True,text=True)
    entry=dict(id=image['Id'],tags=tags,result=result.stdout[-1500:],error=result.stderr[-1500:])
    report['retired' if result.returncode==0 else 'retained'].append(entry)
report['after_free']=shutil.disk_usage('/').free
Path('/home/ubuntu/disk-repair-20261013/owned-image-finalization.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
