import json,subprocess,shutil
from pathlib import Path
root=Path('/var/lib/doxagent/releases/runtime-cache-cost-20261117')
before=json.loads((root/'cutover-before.json').read_text())
ids=subprocess.check_output(['docker','ps','-aq'],text=True).splitlines()
cs=json.loads(subprocess.check_output(['docker','inspect',*ids]));current={c['Name'].lstrip('/'):c for c in cs}
roles=['doxagent-v2-v2-api-1','doxagent-v2-v2-scheduler-1'];unaffected=[];changed=[]
for name,old in before.items():
 if name in roles:continue
 c=current.get(name)
 if c and c['Image']==old['image'] and c['State']['StartedAt']==old['started']:unaffected.append(name)
 else:changed.append(name)
built=json.loads((root/'build.json').read_text())
targets={n:{'image':current[n]['Image'],'started':current[n]['State']['StartedAt'],'running':current[n]['State']['Running'],'health':current[n]['State'].get('Health',{}).get('Status'),'restart_count':current[n]['RestartCount']} for n in roles}
assert all(t['image']==built['images']['backend']['image'] and t['running'] for t in targets.values())
print(json.dumps({'commit':built['commit'],'roles':targets,'unaffected_containers':unaffected,'other_changed_containers':changed,'disk_free_bytes':shutil.disk_usage('/').free},indent=2))
