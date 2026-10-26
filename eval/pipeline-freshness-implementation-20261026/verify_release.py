"""Host-side component, source hash and protected-target verification, without env dumps."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

commits = {'v2-site-access':'c9dd47628236f83fe6525b6643fd994d2295422a',
           'v2-message-bus':'dfdd0510be20c8853220d4270991e4b8fb74eda8',
           'v2-content-enrichment':'ffbe2efb565135f9883ab2adaa9dd5c0b762fd8a',
           'v2-api':'ffbe2efb565135f9883ab2adaa9dd5c0b762fd8a'}
root = Path('/var/lib/doxagent/bus-collection-20261026')
before = json.loads((root/'pipeline-freshness-release-after.json').read_text())
after = json.loads((root/'pipeline-freshness-final.json').read_text())
out = {'at':after['at'],'cpus':after['cpus'],'memory':after['memory'],
       'supervisor_unchanged':before['roles']['v2-chrome-supervisor']['id']==after['roles']['v2-chrome-supervisor']['id'],
       'protected_targets_unchanged':before['protected_targets']==after['protected_targets'] if 'protected_targets' in before else None,
       'roles':{},'source_hashes':{}}
for role, commit in commits.items():
    info = after['roles'][role]
    out['roles'][role] = info
    out['roles'][role]['expected_commit'] = commit
    out['roles'][role]['commit_matches'] = info['commit']==commit
    files = ['src/doxagent/message_bus_v2/polling.py','src/doxagent/content_enrichment/service.py'] if role!='v2-site-access' else ['src/doxagent/site_strategy/runtime.py','src/doxagent/site_strategy/service.py']
    for file in files:
        expected = subprocess.check_output(['git','-C','/home/ubuntu/doxagent','show',commit+':'+file])
        actual = subprocess.check_output(['docker','exec',info['id'],'cat','/app/'+file])
        out['source_hashes'][role+':'+file] = hashlib.sha256(expected).hexdigest()==hashlib.sha256(actual).hexdigest()
    logs = subprocess.run(['docker','logs','--since','5m','--tail','80',info['id']],capture_output=True,text=True)
    lines = (logs.stdout+logs.stderr).splitlines()
    selected = [line for line in lines if any(w in line for w in ('Traceback','ERROR','Exception','Error','failed'))][-12:]
    out['roles'][role]['recent_errors'] = [re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?<redacted>',line)[:500] for line in selected]
out['protected_before_count'] = len(before.get('protected',before.get('protected_targets',[])))
out['protected_after_count'] = len(after.get('protected',after.get('protected_targets',[])))
if 'protected' in before:
    out['protected_targets_unchanged'] = before['protected']==after['protected']
out['identities_unchanged'] = before.get('identity_revisions')==after.get('identity_revisions')
print(json.dumps(out))
