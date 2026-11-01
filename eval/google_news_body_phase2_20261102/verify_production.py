import hashlib,json,subprocess
from pathlib import Path
root=Path('/var/lib/doxagent/releases');repo=Path('/home/ubuntu/doxagent')
commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','ae5f3f8a'],text=True).strip()
built=json.loads((root/(commit+'.build.json')).read_text());state=json.loads((root/'state.json').read_text())
changed=subprocess.check_output(['git','-C',str(repo),'diff-tree','--no-commit-id','--name-only','-r',commit],text=True).splitlines()
paths=[p for p in changed if p.startswith('src/') and p.endswith('.py')]
expected={p:hashlib.sha256(subprocess.check_output(['git','-C',str(repo),'show',commit+':'+p])).hexdigest() for p in paths}
containers=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q'],text=True).split()]))
secret=Path('/var/lib/doxagent/secrets/google-news-body.env').read_text().splitlines()[0].split('=',1)[1]
out=[]
for info in containers:
 labels=info['Config'].get('Labels') or {};role=labels.get('com.docker.compose.service')
 if role not in state['components']:continue
 env=dict(e.split('=',1) for e in info['Config'].get('Env',[]) if '=' in e)
 assert info['State']['Running'],role
 assert env.get('DOXAGENT_BUILD_COMMIT')==commit,(role,'wrong commit')
 assert env.get('DOXAGENT_SOURCE_SHA256')==built['source_archive_sha256'],(role,'wrong source archive')
 assert info['Image']==state['components'][role]['current']['image'],(role,'wrong image')
 if role=='v2-content-enrichment':assert env.get('ZYTE_API_KEY')==secret
 else:assert secret not in env.values(),(role,'provider credential outside BODY')
 if role!='v2-web':
  code='import hashlib,json;from pathlib import Path; p='+repr(paths)+';print(json.dumps({n:hashlib.sha256((Path("/app")/n).read_bytes()).hexdigest() for n in p}))'
  actual=json.loads(subprocess.check_output(['docker','exec',info['Id'],'python','-c',code]))
  assert actual==expected,(role,'code hash mismatch')
 out.append(dict(role=role,image=info['Image'],commit=commit,running=True,health=info['State'].get('Health',{}).get('Status'),restart_count=info['RestartCount'],files_verified=len(paths) if role!='v2-web' else 0))
assert len(out)==19,len(out)
receipt={'commit':commit,'source_archive_sha256':built['source_archive_sha256'],'credential_in_body_only':True,'provider_env_file_mode':oct(Path('/var/lib/doxagent/secrets/google-news-body.env').stat().st_mode&0o777),'roles':out}
print(json.dumps(receipt))
