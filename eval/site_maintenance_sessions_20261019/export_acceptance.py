"""Export non-secret release/canary receipts, not the registry/profile/private backups."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT=Path('/var/lib/doxagent/site-maintenance-sessions-20261019')
REPO=Path('/home/ubuntu/doxagent')
SITE='doxagent-v2-v2-site-access-1'
SUP='doxagent-v2-v2-chrome-supervisor-1'


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=90, **kwargs).strip()


items=json.loads(run(['docker','inspect',SITE,SUP]))
receipt={'containers':[{'name':c['Name'],'id':c['Id'],'image':c['Image'],
          'tag':c['Config']['Image'],'health':c['State'].get('Health',{}).get('Status'),
          'namespace':os.readlink(f"/proc/{c['State']['Pid']}/ns/net")} for c in items],
         'root_available_bytes':shutil.disk_usage('/').free}
commit=json.loads((ROOT/'build-site-refinement.json').read_text())['repair_commit']
paths=run(['git','ls-tree','-r','--name-only',commit,'--','src/doxagent/site_strategy',
           'src/doxagent/content_enrichment/quality.py','src/doxagent/message_bus_v2/reuters_sources.py',
           'src/doxagent/message_bus_v2/yahoo_sources.py'],cwd=REPO).splitlines()
probe="import hashlib,json; from pathlib import Path; paths="+repr(paths)+"; print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() for p in paths}))"
actual=json.loads(run(['docker','exec',SITE,'python','-c',probe]))
mismatches=[]
for path in paths:
    expected=subprocess.check_output(['git','show',commit+':'+path],cwd=REPO)
    if hashlib.sha256(expected).hexdigest()!=actual[path]:mismatches.append(path)
receipt['provenance']={'repair_commit':commit,'checked_files':len(paths),'mismatches':mismatches}
desktop=json.loads(run(['sudo','-u','doxagent-desktop','sudo','-n',
                       '/usr/local/sbin/doxagent-site-login-admin','list']))
receipt['desktop_bridge']={'ok':desktop.get('ok'),'profiles':len(desktop.get('profiles',[])),
                          'sessions':len(desktop.get('sessions',[]))}
receipt['desktop_groups']=run(['id','-nG','doxagent-desktop'])
receipt['vnc_listeners']=[line for line in run(['ss','-lnt']).splitlines() if ':5900 ' in line]
receipt['network_guard']=json.loads(run(['python3','/usr/local/sbin/doxagent-site-access-network-guard']))
(ROOT/'final.json').write_text(json.dumps(receipt,indent=2))
exports=ROOT/'public-receipts'
exports.mkdir(exist_ok=True)
for name in ('final.json','desktop-ui.json','build-site-refinement.json','after-site-refinement.json'):
    shutil.copyfile(ROOT/name,exports/name)
for phase in ('login','verify','business'):
    subprocess.run(['docker','cp',SITE+':/site-data/registry/site-maintenance-sessions-20261019/acceptance.'+phase+'.json',
                    str(exports/('acceptance.'+phase+'.json'))],check=True)
print(json.dumps(receipt,indent=2))
