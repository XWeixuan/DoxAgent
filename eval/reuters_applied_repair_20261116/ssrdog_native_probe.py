import sys,subprocess,json,os,yaml,urllib.request,urllib.parse
from pathlib import Path
if '--inside' not in sys.argv:
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0];os.execvp('nsenter',['nsenter','-t',str(v['State']['Pid']),'-n','python3',__file__,'--inside'])
c=yaml.safe_load(Path('/opt/doxagent-egress-clash/config.yaml').read_text());host='http://127.0.0.1:'+str(c.get('external-controller','127.0.0.1:9090')).rsplit(':',1)[1];name='SSRDOG / 🇩🇪 Germany | 01';url=host+'/proxies/'+urllib.parse.quote(name,safe='')+'/delay?'+urllib.parse.urlencode({'url':'https://api.ipify.org','timeout':10000});req=urllib.request.Request(url,headers={'Authorization':'Bearer '+str(c.get('secret',''))})
try:
 with urllib.request.urlopen(req,timeout=13) as r:v=json.load(r);print(json.dumps({'status':r.status,'delay':v.get('delay')}))
except Exception as e:print(json.dumps({'failed':type(e).__name__,'status':getattr(e,'code',None)}))
