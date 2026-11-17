import subprocess,json,hashlib,shutil
from datetime import datetime,UTC
out={'at':datetime.now(UTC).isoformat(),'roles':{}}
paths=['factory.py','live.py','live_repository.py','schema.py','storage_lane.py','polling.py','scheduler.py','test_delivery.py','collection_plane/store.py','collection_plane/bridge.py','collection_plane/worker.py']
for role in ['message-bus','message-collector','content-enrichment','chrome-supervisor','site-access']:
 name='doxagent-v2-v2-'+role+'-1';r=json.loads(subprocess.check_output(['docker','inspect',name]))[0];env=dict(v.split('=',1) for v in r['Config']['Env']);sha=env.get('DOXAGENT_BUILD_COMMIT');v={'id':r['Id'][:12],'commit':sha,'started':r['State']['StartedAt'],'running':r['State']['Running'],'restarts':r['RestartCount'],'health':r['State'].get('Health',{}).get('Status')}
 if role in ['message-bus','message-collector','content-enrichment']:
  v['files']={}
  for p in paths:
   rel='src/doxagent/message_bus_v2/'+p;expected=hashlib.sha256(subprocess.check_output(['git','-C','/home/ubuntu/doxagent','show',sha+':'+rel])).hexdigest();actual=subprocess.check_output(['docker','exec',name,'sha256sum','/app/'+rel],text=True).split()[0];v['files'][p]=actual==expected
 out['roles'][role]=v
out['disk_free']=shutil.disk_usage('/').free
out['pressure']={k:open('/proc/pressure/'+k).read() for k in ['memory','io','cpu']}
print(json.dumps(out))
