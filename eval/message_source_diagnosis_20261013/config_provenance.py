"""Compare mounted and on-disk proxy configs without exposing credentials."""
import hashlib
import json
import subprocess
from pathlib import Path

import yaml

disk = Path('/opt/doxagent-egress-clash/config.yaml').read_bytes()
mounted = subprocess.check_output(['docker','exec','doxagent-egress-clash','cat','/root/.config/mihomo/config.yaml'])
out = {'same_bytes':disk==mounted,'disk_sha256':hashlib.sha256(disk).hexdigest(),'mounted_sha256':hashlib.sha256(mounted).hexdigest(),'configs':{}}
for name, data in [('disk',disk),('mounted',mounted)]:
    c = yaml.safe_load(data)
    proxies = c.get('proxies',[])
    out['configs'][name] = {'nodes':len(proxies),'controller':c.get('external-controller'),'provider_names':list(c.get('proxy-providers',{})),'labels':[p['name'] for p in proxies if any(s in p['name'] for s in ('到期','剩余','重置'))]}
    out['configs'][name]['fixed'] = {k:v[-1] for k,v in c.get('sub-rules',{}).items() if k.startswith('doxagent-egress-')}
    out['configs'][name]['endpoint_hashes'] = {p['name']:hashlib.sha256(str((p.get('server'),p.get('port'),p.get('type'))).encode()).hexdigest()[:12] for p in proxies if '德国标准' in p['name'] or '土耳其标准' in p['name']}
print(json.dumps(out,ensure_ascii=False))
