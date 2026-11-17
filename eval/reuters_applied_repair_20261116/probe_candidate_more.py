import subprocess,json,yaml
from pathlib import Path
cfg=yaml.safe_load(Path('/opt/doxagent-egress-clash/config.yaml').read_text());node=next(p for p in cfg['proxies'] if p['name']=='SSRDOG / 🇩🇪 Germany | 01')
print(json.dumps({'keys':list(node),'type':node['type'],'port':node.get('port'),'udp':node.get('udp'),'skip-cert-verify':node.get('skip-cert-verify'),'dialer-proxy':node.get('dialer-proxy')}))
v=json.loads(subprocess.check_output(['docker','inspect','doxagent-reuters-backup2-egress']))[0];ip=next(iter(v['NetworkSettings']['Networks'].values()))['IPAddress']
for url in ['http://ip-api.com/json','https://ifconfig.co/json']:
 r=subprocess.run(['curl','--silent','--show-error','--max-time','15','--proxy',f'http://{ip}:18086',url],capture_output=True,text=True)
 try:p=json.loads(r.stdout);p={k:p.get(k) for k in ['ip','query','country','countryCode','country_iso','status']}
 except:p={'bytes':len(r.stdout)}
 print(json.dumps({'url':url,'code':r.returncode,'result':p,'error':r.stderr[:120]}))
