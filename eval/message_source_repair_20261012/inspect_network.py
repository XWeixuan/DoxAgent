"""Read-only controller inventory; no credentials or subscription URLs in output."""
import json, subprocess, urllib.request
from urllib.parse import quote, urlencode
parser = "import sys,json,yaml; c=yaml.safe_load(sys.stdin.read()); print(json.dumps({'controller':c.get('external-controller'),'secret':c.get('secret'), 'providers':list((c.get('proxy-providers') or {}).keys()),'names':[p['name'] for p in c.get('proxies',[])], 'listeners':c.get('listeners'), 'rules':c.get('sub-rules')}))"
with open('/opt/doxagent-egress-clash/config.yaml') as f:
    raw = f.read()
p = subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-c',parser],input=raw,text=True,capture_output=True,check=True)
c=json.loads(p.stdout)
address=c['controller'].replace('0.0.0.0','127.0.0.1')
def get(path):
    code="import sys,json,urllib.request; c=json.load(sys.stdin); req=urllib.request.Request('http://127.0.0.1:'+c['controller'].rsplit(':',1)[1]+c['path'],headers={'Authorization':'Bearer '+(c['secret'] or '')}); print(urllib.request.urlopen(req,timeout=12).read().decode())"
    data={**c,'path':path}
    pid=subprocess.check_output(['docker','inspect','-f','{{.State.Pid}}','doxagent-egress-clash'],text=True).strip()
    p=subprocess.run(['nsenter','-t',pid,'-n','python3','-c',code],input=json.dumps(data),capture_output=True,text=True,timeout=20,check=True)
    return json.loads(p.stdout)
out={'providers':c['providers'],'names':[n for n in c['names'] if any(s in n for s in ('荷兰','Netherlands','Expire','Traffic:'))],'listeners':c['listeners']}
try:
    data=get('/providers/proxies')
    out['provider_inventory']={k: {'vehicleType':v.get('vehicleType'),'count':len(v.get('proxies',[]))} for k,v in data.get('providers',{}).items()}
    proxies=get('/proxies')['proxies']
    candidates=[k for k in proxies if '荷兰' in k or 'Netherlands' in k or k == '🇩🇪 德国标准 IEPL 专线 1']
    out['candidate_checks']=[]
    for name in candidates:
        try:
            result=get('/proxies/'+quote(name,safe='')+'/delay?'+urlencode({'url':'https://api.ipify.org?format=json','timeout':6000}))
            out['candidate_checks'].append({'name':name,'result':result})
        except Exception as exc: out['candidate_checks'].append({'name':name,'error':type(exc).__name__})
except Exception as e: out['controller_error']=type(e).__name__
print(json.dumps(out,ensure_ascii=False))
