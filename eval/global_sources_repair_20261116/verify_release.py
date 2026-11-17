import subprocess,json,hashlib,shutil
from datetime import datetime,UTC
sha='cd57c3cf3a826e8238604363be5b0ed754782b07';repo='/home/ubuntu/doxagent';out={}
files=['src/doxagent/message_bus_v2/yahoo_transport.py','src/doxagent/message_bus_v2/sec_edgar/http.py','src/doxagent/message_bus_v2/repository.py']
for role in ['v2-message-bus','v2-message-collector','v2-content-enrichment','v2-chrome-supervisor']:
 name='doxagent-v2-'+role+'-1';info=json.loads(subprocess.check_output(['docker','inspect',name]))[0];env=dict(x.split('=',1) for x in info['Config']['Env'])
 r={'id':info['Id'],'started':info['State']['StartedAt'],'running':info['State']['Running'],'health':info['State'].get('Health',{}).get('Status'),'commit':env.get('DOXAGENT_BUILD_COMMIT')}
 if role!='v2-chrome-supervisor':
  r.update(yahoo_routes=env.get('DOXAGENT_MESSAGE_BUS_V2_YAHOO_HTTP_ROUTES'),recheck=env.get('DOXAGENT_CONTENT_ENRICHMENT_RECHECK_ENABLED'))
  r['hash_matches']={}
  for path in files:
   actual=subprocess.check_output(['docker','exec',name,'sha256sum','/app/'+path],text=True).split()[0]
   expected=hashlib.sha256(subprocess.check_output(['git','-C',repo,'show',sha+':'+path])).hexdigest()
   r['hash_matches'][path]=actual==expected
 out[role]=r
print(json.dumps({'at':datetime.now(UTC).isoformat(),'root_free_gib':round(shutil.disk_usage('/').free/1024**3,2),'roles':out}))
