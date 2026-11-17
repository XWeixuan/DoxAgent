import json,hashlib,subprocess
from pathlib import Path
commit='07a64d526';repo='/home/ubuntu/doxagent'
paths=['src/doxagent/message_bus_v2/'+p for p in ['publisher_http.py','poll_trace.py','corporate_ir/http.py','energy_sources.py','public_listing.py','technology_sources.py','collection_plane/worker.py','collection_plane/store.py']]
expected={p:hashlib.sha256(subprocess.check_output(['sudo','-u','ubuntu','git','-C',repo,'show',commit+':'+p])).hexdigest() for p in paths}
code='import json,hashlib;paths='+repr(paths)+';print(json.dumps({p:hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest() for p in paths}))'
actual=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-v2-message-collector-1','python','-c',code]))
assert expected==actual,'source hash drift'
v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-message-collector-1']))[0]
env=dict(s.split('=',1) for s in v['Config']['Env'] if '=' in s)
out={'commit':env.get('DOXAGENT_BUILD_COMMIT'),'source_sha':env.get('DOXAGENT_SOURCE_SHA256'),'container_id':v['Id'],'image':v['Image'],'started':v['State']['StartedAt'],'source_hashes':actual,'since':'2026-11-11T15:38:21+00:00','observe_until':'2026-11-11T16:08:21+00:00'}
Path('/tmp/source-stability-repair-release.json').write_text(json.dumps(out),encoding='utf-8');print(json.dumps(out))
