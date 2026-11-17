import json,hashlib,subprocess
from pathlib import Path
commit='6c5cb8b87';repo='/home/ubuntu/doxagent'
paths=['src/doxagent/message_bus_v2/'+p for p in ['publisher_http.py','poll_trace.py','corporate_ir/http.py','defense_sources.py','energy_sources.py','public_listing.py','technology_sources.py','collection_plane/worker.py','collection_plane/store.py','industry_sources.py','adapters.py','newswire_sources.py','scheduler.py','rss_http_race.py','newswire_listing.py','news_adapters.py']]
expected={p:hashlib.sha256(subprocess.check_output(['sudo','-u','ubuntu','git','-C',repo,'show',commit+':'+p])).hexdigest() for p in paths}
code='import json,hashlib;paths='+repr(paths)+';print(json.dumps({p:hashlib.sha256(open("/app/"+p,"rb").read()).hexdigest() for p in paths}))'
roles={}
for role in ['v2-message-collector','v2-site-access','v2-content-enrichment','v2-chrome-supervisor','v2-message-bus']:
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0];env=dict(s.split('=',1) for s in v['Config']['Env'] if '=' in s)
 roles[role]={'commit':env.get('DOXAGENT_BUILD_COMMIT'),'container_id':v['Id'],'image':v['Image'],'started':v['State']['StartedAt']}
 if role in ['v2-message-collector','v2-site-access','v2-content-enrichment']:
  role_commit='af5b47b48' if role=='v2-content-enrichment' else commit
  expected={p:hashlib.sha256(subprocess.check_output(['sudo','-u','ubuntu','git','-C',repo,'show',role_commit+':'+p])).hexdigest() for p in paths}
  actual=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-'+role+'-1','python','-c',code]));assert actual==expected,role+' source drift';roles[role]['verified_files']=len(actual);roles[role]['source_hashes']=actual
out={'roles':roles,'since':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
Path('/tmp/source-stability-repair-r7-release.json').write_text(json.dumps(out),encoding='utf-8');print(json.dumps(out))
