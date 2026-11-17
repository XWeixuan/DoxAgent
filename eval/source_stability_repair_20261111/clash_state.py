import json,subprocess,urllib.request
from pathlib import Path
import yaml
config=yaml.safe_load(Path('/opt/doxagent-egress-clash/config.yaml').read_text())
v=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0]
ctl=config.get('external-controller','127.0.0.1:9090')
ip=next(x['IPAddress'] for x in v['NetworkSettings']['Networks'].values() if x.get('IPAddress'))
ctl=ip+':'+ctl.rsplit(':',1)[-1]
headers={'Authorization':'Bearer '+str(config.get('secret',''))}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
 with opener.open(urllib.request.Request('http://'+ctl+path,headers=headers),timeout=5) as r:return json.load(r)
try:
 ps=get('/proxies')['proxies'];out={'controller_reachable':True,'selectors':{k:{'selected':p.get('now'),'alive':p.get('alive'),'history':p.get('history',[])[-2:]} for k,p in ps.items() if p.get('type') in ['Selector','Fallback','URLTest']},'listener_types':[(x.get('port'),x.get('type'),x.get('proxy')) for x in config.get('listeners',[])]}
 cs=get('/connections').get('connections',[])
 out['connections']=[{'host':c.get('metadata',{}).get('host'),'chains':c.get('chains'),'start':c.get('start'),'download':c.get('download'),'upload':c.get('upload')} for c in cs if c.get('metadata',{}).get('host') in ['www.army.mil','technews.tw','www.tomshardware.com','nebius.com','canarymedia.com','www.canarymedia.com','www.utilitydive.com','cse.google.com']]
 print(json.dumps(out,ensure_ascii=False))
except Exception as e:print(json.dumps({'controller_reachable':False,'error_type':type(e).__name__,'network_mode':v['HostConfig']['NetworkMode']}))
