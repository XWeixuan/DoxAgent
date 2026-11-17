import json,subprocess,yaml,os,urllib.request,time
from pathlib import Path
cfg=yaml.safe_load(Path('/opt/doxagent-egress-clash/config.yaml').read_text())
name='SSRDOG / 🇩🇪 Germany | 01'
node=next(p for p in cfg['proxies'] if p['name']==name)
i=json.loads(subprocess.check_output(['docker','inspect','doxagent-egress-clash']))[0]
network=next(iter(i['NetworkSettings']['Networks']))
p=Path('/opt/doxagent-egress-clash/reuters-backup2');p.mkdir(mode=0o700,exist_ok=True)
private={'mode':'rule','log-level':'warning','ipv6':False,'allow-lan':True,'bind-address':'*','proxies':[node], 'listeners':[{'name':'reuters-backup2','type':'http','listen':'0.0.0.0','port':18086,'proxy':name}],'rules':['MATCH,'+name]}
file=p/'config.yaml';file.write_text(yaml.safe_dump(private,allow_unicode=True));file.chmod(0o600)
container='doxagent-reuters-backup2-egress'
existing=subprocess.run(['docker','inspect',container],capture_output=True)
if existing.returncode:subprocess.run(['docker','run','-d','--name',container,'--restart','unless-stopped','--network',network,'--memory','128m','--cpus','0.25','--log-driver','json-file','--log-opt','max-size=2m','--log-opt','max-file=2','-v',str(file)+':/root/.config/mihomo/config.yaml:ro',i['Config']['Image']],check=True,capture_output=True)
v=json.loads(subprocess.check_output(['docker','inspect',container]))[0];ip=next(iter(v['NetworkSettings']['Networks'].values()))['IPAddress']
time.sleep(2)
# curl's explicit --proxy avoids ambient host proxy and prints only the selected exit.
r=subprocess.run(['curl','--fail','--silent','--show-error','--max-time','20','--proxy',f'http://{ip}:18086','https://api.ipify.org?format=json'],capture_output=True,text=True)
if r.returncode:raise RuntimeError('candidate_exit_probe_failed')
exit=json.loads(r.stdout)['ip'];print(json.dumps({'container':container,'network':network,'node':name,'exit_ip':exit,'endpoint':f'http://{container}:18086','public_ports':v['HostConfig']['PortBindings'],'memory_limit':v['HostConfig']['Memory']}))
