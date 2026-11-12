import subprocess,json,hashlib,tarfile,io,os
from datetime import datetime,timezone
from pathlib import Path
expected={'v2-projector': {'src/doxagent/v2_read/projector.py': 'f0b7c4c74f941a4f6231ecbd4163b182b800c37ac5d382f2a31b0d90ba85b7ab', 'src/doxagent/v2_read/repository.py': 'ed7fc351e3ccba286659fdbbd2514fb6736bd81e73c3c7a80968c0b644c1f5ed', 'src/doxagent/v2_read/libraries.py': 'b2a199dfa807005d25d80a5c5e23c0ec13696860438560187f93e7a09999b9a7', 'src/doxagent/v2_read/reference.py': 'b014ef3b9e3d214e317bc260c0bd32066f5b162ea50aee25aa4bef5e60776fbd', 'src/doxagent/v2_read/formal.py': '2e75e7450899ebbe6baf03ecea36705eb985b538068602765a190c4dba761d3d', 'src/doxagent/v2_read/artifacts.py': '1e02660711a25b7cce7a964527e52fa715fb030669f37975809f1c3602e7c60c', 'src/doxagent/v2_read/lifecycle.py': 'fc0ffafcdcec22d4eb218b9929a2a2696402f94b0f6929af9102bf82903fc45e', 'src/doxagent/v2_read/projectors.py': '7bce1a157a91d7ee388b039c55ca40ce9f875958528fba170d89d59bbfa5e5d7', 'src/doxagent/v2_read/bus_aggregates.py': '5f7aec0eadc6d4f6d0fb172305cd7db33cd930bd68d11a1c9f7541e647d8d37b', 'src/doxagent/v2_read/outbox.py': '1e62206681960a36e064207fa8a9c47046f8dece7837e7915587de815e0addad'}, 'v2-api': {'src/doxagent/api_v2/app.py': '10af8bc0f67c83999cf2c82b4c71d6e1ce58034ba9a44e33c40f2c5eab45dd55', 'src/doxagent/api_v2/views.py': '64d78c796f4f0d8dab334bb9d786ed73e06f414dd28a4868cd53f957e57b908d', 'src/doxagent/api_v2/projection_health.py': 'e4d5c37dc0ebd6ea2a6355e6415be1a38a3f7124489ad309f0492a672b0f9f08', 'src/doxagent/api_v2/wire_schema.json': 'b9034a965669d596b1e4e3ba0fcff6d41120685a521108e976f96c99f7526671'}}
out={'at':datetime.now(timezone.utc).isoformat(),'files':{},'containers':[]}
for role,paths in expected.items():
 values={}
 for path,h in paths.items():
  raw=subprocess.check_output(['docker','cp','doxagent-v2-'+role+'-1:/app/'+path,'-'])
  with tarfile.open(fileobj=io.BytesIO(raw)) as archive:raw=archive.extractfile(archive.getmembers()[0]).read()
  values[path]={'sha256':hashlib.sha256(raw).hexdigest(),'matches_committed_source':hashlib.sha256(raw).hexdigest()==h}
 out['files'][role]=values
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
for info in json.loads(subprocess.check_output(['docker','inspect',*names])):
 out['containers'].append({'name':info['Name'].lstrip('/'),'image':info['Image'],'started':info['State']['StartedAt'],'restarts':info['RestartCount']})
r=subprocess.run(['docker','exec','doxagent-v2-v2-web-1','sh','-c','cd /usr/share/nginx/html && sha256sum -c /tmp/projection-web.sha256'],capture_output=True,text=True)
out['web_assets']={'verified':r.returncode==0,'files':len(r.stdout.splitlines())}
s=json.loads(Path('/var/lib/doxagent/releases/state.json').read_text())
out['release_state']={role:{'image':s['components'][role]['current']['image'],'commit':s['components'][role]['current'].get('commit')} for role in ['v2-projector','v2-api','v2-web']}
out['root_free_bytes']=__import__('shutil').disk_usage('/').free
out['temp_probe_directories']=[str(p) for prefix in ['dox-projection-cache-probe-','dox-projection-profile-d33-'] for p in Path('/tmp').glob(prefix+'*')]
print(json.dumps(out))
