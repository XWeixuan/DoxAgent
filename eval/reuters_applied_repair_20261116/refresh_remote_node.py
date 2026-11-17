from pathlib import Path
import yaml,json,subprocess,shlex
root=Path.home()/'AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev';meta=yaml.safe_load((root/'profiles.yaml').read_text(encoding='utf-8'));url=next(i for i in meta['items'] if i.get('name')=='SSRDOG')['url']
code="""import sys,urllib.request,json,yaml,subprocess
from pathlib import Path
url=sys.stdin.read().strip();v=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0];ip=next(iter(v['NetworkSettings']['Networks'].values()))['IPAddress'];op=urllib.request.build_opener(urllib.request.ProxyHandler({'https':f'http://{ip}:18080'}))
try:
 r=op.open(urllib.request.Request(url,headers={'User-Agent':'ClashMeta/1.19.31'}),timeout=25);data=yaml.safe_load(r.read());nodes=[n for n in data.get('proxies',[]) if 'Germany' in n.get('name','') or '德国' in n.get('name','')];print(json.dumps({'status':r.status,'german_nodes':[n['name'] for n in nodes]}))
 if nodes:
  p=Path('/opt/doxagent-egress-clash/reuters-backup2/fresh-node.yaml');p.write_text(yaml.safe_dump(nodes[0],allow_unicode=True));p.chmod(0o600)
except Exception as e:print(json.dumps({'failed':type(e).__name__,'status':getattr(e,'code',None)}))
"""
r=subprocess.run(['ssh','-o','ProxyCommand=none','doxagent-sg','sudo python3 -c '+shlex.quote(code)],input=url,text=True,capture_output=True);print(r.stdout)
