"""Host-side exact-image/code verification; never export private release env."""
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

COMMIT='412d5cfad289'
ROOT=Path('/var/lib/doxagent/releases')
REPO='/home/ubuntu/doxagent'
roles=['v2-api','v2-projector','v2-site-access','v2-message-bus','v2-content-enrichment','v2-message-collector','v2-web']
state=json.loads((ROOT/'state.json').read_text())
result={'verified_at':datetime.now(UTC).isoformat(),'roles':{},'code':[]}
for role in roles:
    c=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
    current=state['components'][role]['current']
    result['roles'][role]={'image':c['Image'],'running':c['State']['Running'],
                          'health':c['State'].get('Health',{}).get('Status'),
                          'started_at':c['State']['StartedAt'],'restarts':c['RestartCount'],
                          'release_commit':current.get('commit'),'registered_image_matches':current['image']==c['Image']}
for role,module,path in [
    ('v2-message-collector','doxagent.message_bus_v2.yahoo_graphql','src/doxagent/message_bus_v2/yahoo_graphql.py'),
    ('v2-message-collector','doxagent.message_bus_v2.yahoo_transport','src/doxagent/message_bus_v2/yahoo_transport.py'),
    ('v2-message-bus','doxagent.message_bus_v2.live_repository','src/doxagent/message_bus_v2/live_repository.py'),
    ('v2-content-enrichment','doxagent.content_enrichment.service','src/doxagent/content_enrichment/service.py'),
    ('v2-site-access','doxagent.site_strategy.origin_listing','src/doxagent/site_strategy/origin_listing.py'),
    ('v2-projector','doxagent.v2_read.projectors','src/doxagent/v2_read/projectors.py')]:
    expected=hashlib.sha256(subprocess.check_output(['git','-C',REPO,'show',COMMIT+':'+path])).hexdigest()
    script=f'import importlib,pathlib,hashlib; m=importlib.import_module({module!r}); print(hashlib.sha256(pathlib.Path(m.__file__).read_bytes()).hexdigest())'
    actual=subprocess.check_output(['docker','exec','doxagent-v2-'+role+'-1','python','-c',script],text=True).strip()
    result['code'].append(dict(role=role,path=path,sha256=actual,matches=actual==expected))
assert all(r['running'] and r['registered_image_matches'] and r['restarts']==0 for r in result['roles'].values())
assert all(c['matches'] for c in result['code'])
print(json.dumps(result,indent=2))
