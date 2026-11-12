import json,subprocess,shutil,hashlib
from datetime import datetime,UTC
roles={
 'v2-site-access':['site_strategy/runtime.py','site_strategy/seeds.py','content_enrichment/strategies/catalog.py'],
 'v2-content-enrichment':['content_enrichment/extractor.py','content_enrichment/managed.py','content_enrichment/pipeline.py','content_enrichment/publisher_documents.py','content_enrichment/publishers.py','content_enrichment/transport.py'],
 'v2-message-collector':['message_bus_v2/newswire_listing.py'],
 'v2-message-bus':['message_bus_v2/live.py'],
 'v2-chrome-supervisor':[],
}
def run(args):return subprocess.check_output(args,text=True).strip()
out={'at':datetime.now(UTC).isoformat(),'containers':{}}
for role,files in roles.items():
 name='doxagent-v2-'+role+'-1';raw=json.loads(run(['docker','inspect',name]))[0]
 out['containers'][role]={'id':raw['Id'],'started_at':raw['State']['StartedAt'],'state':raw['State']['Status'],'health':raw['State'].get('Health',{}).get('Status'),
    'recheck_env':[x for x in raw['Config'].get('Env',[]) if 'RECHECK' in x],
    'hashes':{f:run(['docker','exec',name,'python','-c','import hashlib,doxagent,pathlib;print(hashlib.sha256((pathlib.Path(doxagent.__file__).parent/'+repr(f)+').read_bytes()).hexdigest())']) for f in files}}
out['root_free_gib']=round(shutil.disk_usage('/').free/2**30,2)
out['mem_available_kib']=next(int(l.split()[1]) for l in open('/proc/meminfo') if l.startswith('MemAvailable:'))
print(json.dumps(out))
