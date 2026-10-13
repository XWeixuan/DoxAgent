"""Read Mihomo live API in its network namespace; only GET diagnostics, no selection."""
import json
import subprocess

import yaml

config = yaml.safe_load(subprocess.check_output(['docker','exec','doxagent-egress-clash','cat','/root/.config/mihomo/config.yaml']))
pid = subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}','doxagent-egress-clash'],text=True).strip()
payload = {'controller':config.get('external-controller'),'secret':config.get('secret','')}
code = '''
import concurrent.futures,json,urllib.request,urllib.parse
config=CONFIG
def get(path):
 try:
  req=urllib.request.Request('http://'+config['controller']+path,headers={'Authorization':'Bearer '+config['secret']})
  with urllib.request.urlopen(req,timeout=9) as r:return json.load(r)
 except Exception as exc:return {'error':type(exc).__name__,'message':str(exc)[:100]}
data=get('/proxies').get('proxies',{})
names=[n for n in data if '德国标准' in n or '土耳其标准' in n]
names += [n for n in data if ('香港高级' in n or '日本高级' in n)][:3]
def probe(name):
 value=data[name]
 return {'name':name,'alive':value.get('alive'),'type':value.get('type'),'now':value.get('now'),'history':value.get('history',[])[-3:],'test':get('/proxies/'+urllib.parse.quote(name,safe='')+'/delay?timeout=5000&url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204')}
providers=get('/providers/proxies').get('providers',{})
out={'live_node_count':len(data),'subscription_labels':[n for n in data if any(s in n for s in ('到期','剩余','重置'))],'providers':{k:{f:v.get(f) for f in ('vehicleType','updatedAt','subscriptionInfo')} for k,v in providers.items()}}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:out['probes']=list(pool.map(probe,names))
print(json.dumps(out,ensure_ascii=False))
'''.replace('CONFIG',repr(payload))
result = subprocess.run(['nsenter','-t',pid,'-n','python3','-'],input=code,capture_output=True,text=True,timeout=50)
print(result.stdout or json.dumps({'error':result.stderr[-200:]}))
