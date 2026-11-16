import subprocess,json,hashlib,sqlite3
from pathlib import Path
from datetime import datetime,timezone
repo=Path('/home/ubuntu/doxagent')
root=Path('/var/lib/doxagent/releases/w3-main-20261116')
commit='27d8c0ab7e60949d73073374faa9081167ffdbd8'
paths=['src/doxagent/persistent_runtime_v2/'+p+'.py' for p in ('w3','w3_execution','w3_consensus','w3_threads','w3_models','w3_assets','w3_contracts','w3_v6','execution_bundle','service','schema')]
paths += ['src/doxagent/codex_worker/'+p+'.py' for p in ('sdk_runtime','w3_ready')]
paths += ['prompts/persistent_runtime_v2/w3/'+p for p in ('AGENTS.md','skills/skill.md','skills/operations.md','contract.json')]
expected={p:hashlib.sha256(subprocess.check_output(['git','-C',str(repo),'show',commit+':'+p])).hexdigest() for p in paths}
code='import json,hashlib;from pathlib import Path;p='+repr(paths)+";print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in p}))"
receipt=dict(at=datetime.now(timezone.utc).isoformat(),commit=commit,components={},source_hashes=expected)
state=json.loads(Path('/var/lib/doxagent/releases/state.json').read_text())
roles=['codex-worker','v2-api','v2-projector','v2-scheduler','v2-maintenance','v2-web',
       'v2-initialization','v2-cdecr-executor','v2-o4','v2-executor','v2-delivery',
       'v2-transport-maintenance','v2-control','v2-content-enrichment','v2-message-bus','v2-message-collector']
for role in roles:
    name='doxagent-v2-'+role+'-1'
    info=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    assert info['State']['Running'],role
    assert info['State'].get('Health',{}).get('Status','healthy')=='healthy',role
    assert info['Image']==state['components'][role]['current']['image'],role
    revision=(info['Config'].get('Labels') or {}).get('org.opencontainers.image.revision')
    assert revision==commit,(role,revision)
    receipt['components'][role]=dict(image=info['Image'],revision=revision,health=info['State'].get('Health',{}).get('Status','running'))
for role in ('codex-worker','v2-scheduler'):
    observed=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-'+role+'-1','python','-c',code]))
    assert observed==expected,role
receipt['verified_source_roles']=['codex-worker','v2-scheduler']
data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
with sqlite3.connect((data/'workspaces/worker-jobs.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as db:
    receipt['ready_profiles']=db.execute("select state,json_extract(config,'$.profile.model'),json_extract(config,'$.profile.effort'),json_extract(config,'$.w3_entry_version'),count(*) from w3_ready_slots where json_extract(config,'$.w3_entry_version')='w3-entry-v6' group by 1,2,3,4").fetchall()
with sqlite3.connect((data/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1) as db:
    receipt['historical_cleanup_errors']=db.execute("select kind,status,substr(error,1,200),count(*) from runtime_v2_w3_threads where error is not null group by 1,2,3 limit 6").fetchall()
(root/'verification.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
