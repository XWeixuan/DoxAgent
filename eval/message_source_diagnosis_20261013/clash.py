"""Read-only fixed-route provenance and bounded node delay probes. No selector writes."""
import concurrent.futures
import json
import socket
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import yaml

config = yaml.safe_load(open('/opt/doxagent-egress-clash/config.yaml'))
controller = config.get('external-controller') or '127.0.0.1:9090'
if controller.startswith('0.0.0.0:'): controller = '127.0.0.1:' + controller.split(':')[1]
headers = {'Authorization':'Bearer '+str(config.get('secret',''))}

def get(path):
    try:
        request = urllib.request.Request('http://'+controller+path, headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except Exception as exc: return {'error': type(exc).__name__, 'message': str(exc)[:120]}

all_nodes = {p['name']:p for p in config.get('proxies',[])}
fixed = {k: v[-1].split(',',1)[-1] for k,v in config.get('sub-rules',{}).items() if k.startswith('doxagent-egress-')}
nodes = list(dict.fromkeys(n for n in fixed.values() if n in all_nodes))
for n in all_nodes:
    if len(nodes) >= 7: break
    if n not in nodes and any(s in n.lower() for s in ('美国','日本','香港','singapore','ssrdog')): nodes.append(n)

def probe(name):
    p = all_nodes[name]
    row = {'name':name,'type':p.get('type'),'port':p.get('port')}
    try:
        with socket.create_connection((p['server'],int(p['port'])),timeout=4): row['upstream_tcp']=True
    except Exception as exc: row['upstream_tcp']=False;row['tcp_error']=type(exc).__name__
    row['delay'] = get('/proxies/'+urllib.parse.quote(name,safe='')+'/delay?timeout=5000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204')
    return row

out = {'at':datetime.now(UTC).isoformat(),'fixed':fixed,'inline_nodes':len(all_nodes),'providers':get('/providers/proxies'),'subscription_labels':[n for n in all_nodes if any(k in n for k in ('到期','剩余','重置','更新'))]}
# Provider API can include subscription source URL; retain only health/expiry/counters.
providers = out['providers'].get('providers',{})
out['providers'] = {k:{f:v.get(f) for f in ('vehicleType','updatedAt','subscriptionInfo')} for k,v in providers.items()}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool: out['probes'] = list(pool.map(probe,nodes))
print(json.dumps(out,ensure_ascii=False))
