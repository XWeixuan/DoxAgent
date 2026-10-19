"""Read-only deployment, identity continuity and governance acceptance receipt."""
import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

commit='bed59294a24db47231f22deb94b077e42653b45c'
repo=Path('/home/ubuntu/doxagent')
os.environ.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='safe.directory',GIT_CONFIG_VALUE_0=str(repo))
receipt=json.loads(Path('/var/lib/doxagent/releases/barrons-native-'+commit[:12]+'/release.json').read_text())
names=['doxagent-v2-v2-site-access-1','doxagent-v2-v2-message-bus-1','doxagent-v2-v2-chrome-supervisor-1']
containers=json.loads(subprocess.check_output(['docker','inspect',*names],text=True))
out={'at':datetime.now(UTC).isoformat(),'implementation_commit':commit,
     'containers':[{'name':c['Name'],'id':c['Id'],'image':c['Image'],
                    'running':c['State']['Running'],'oom_killed':c['State']['OOMKilled'],
                    'restarts':c['RestartCount']} for c in containers]}
code="import hashlib,json;from pathlib import Path;p=Path('/app/src/doxagent');print(json.dumps({f:hashlib.sha256((p/f).read_bytes()).hexdigest() for f in "+repr(list(receipt['hashes']))+"}))"
actual=json.loads(subprocess.check_output(['docker','exec',names[0],'python','-c',code],text=True))
expected={f:hashlib.sha256(subprocess.check_output(['git','show',commit+':src/doxagent/'+f],cwd=repo)).hexdigest() for f in actual}
out['all_ten_files_match_commit']=actual==expected==receipt['hashes']
code="""import asyncio,json,os
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
async def main():
 client=ChromeSupervisorClient(os.environ['DOXAGENT_SITE_ACCESS_CHROME_SUPERVISOR_SOCKET'],owner_id='readonly-final-acceptance')
 rows=[]
 for identity in ('dowjones-main','dowjones-backup'):
  s=await client.status(identity)
  rows.append({k:s.get(k) for k in ('identity_id','instance_id','running','known_missing','generation')})
 print(json.dumps(rows))
asyncio.run(main())
"""
out['native_status']=json.loads(subprocess.check_output(['docker','exec',names[0],'python','-c',code],text=True))
site_root=next(m['Source'] for m in containers[0]['Mounts'] if m['Destination']=='/site-data')
db=sqlite3.connect('file:'+site_root+'/registry/site_strategy.sqlite3?mode=ro',uri=True,timeout=3)
p=json.loads(db.execute("select data_json from browser_profiles where profile_id='dowjones-main'").fetchone()[0])
out['profile']={k:p.get(k) for k in ('profile_id','operational_state','auth_state','maintenance_session_id','session_revision')}
state=json.loads(db.execute("select data_json from site_runtime where runtime_key='scope:source:barrons_ticker_news'").fetchone()[0])
out['active_identity']=state['active_combination_id']
out['manual_attention']=state['combinations']['dowjones-main']['manual_attention_required']
out['pending_current_challenges']=db.execute("select count(*) from challenge_tasks where identity_id='dowjones-main' and state<>'RESOLVED' and superseded_by is null").fetchone()[0]
out['unfinished_login_sessions']=db.execute("select count(*) from maintenance_sessions where identity_id='dowjones-main' and state in ('OPEN','RELEASED') and json_extract(data_json,'$.kind')='LOGIN'").fetchone()[0]
print(json.dumps(out))
