import json,subprocess,yaml,urllib.request,sys,os
if '--inside' not in sys.argv:
 v=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0]
 os.execvp('nsenter',['nsenter','-t',str(v['State']['Pid']),'-n','python3',__file__,'--inside'])
from pathlib import Path
cfg=yaml.safe_load(Path('/opt/doxagent-egress-clash/config.yaml').read_text());v=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0];ip='127.0.0.1';headers={'Authorization':'Bearer '+str(cfg.get('secret',''))};port=int(str(cfg.get('external-controller','0.0.0.0:9090')).rsplit(':',1)[1]);request=urllib.request.Request(f'http://{ip}:{port}/proxies',headers=headers)
with urllib.request.urlopen(request,timeout=5) as r:proxies=json.load(r)['proxies']
names=[{'name':k,'type':p.get('type'),'alive':p.get('alive')} for k,p in proxies.items() if any(t in k.lower() for t in ('德国','germany','frankfurt','🇩🇪'))]
print(json.dumps({'providers':list((cfg.get('proxy-providers') or {}).keys()),'german_proxies':names,'listeners':[{k:p.get(k) for k in ('name','type','port','proxy')} for p in cfg.get('listeners',[])],'groups':[{'name':p['name'],'type':p['type']} for p in cfg.get('proxy-groups',[])],'image':v['Config']['Image']},ensure_ascii=False,indent=2))
