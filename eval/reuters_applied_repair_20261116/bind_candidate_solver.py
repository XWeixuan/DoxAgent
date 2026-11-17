import json,os,subprocess,secrets,sqlite3,hashlib,importlib.util
from pathlib import Path
from urllib.parse import urlsplit
root=Path('/var/lib/doxagent/captcha-proxy');data=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data');p=data/'credentials/datadome-solver.json';config=json.loads(p.read_text());ID='reuters-backup2'
backup=data/'credentials/datadome-solver.pre-backup2-20261116.json'
if not backup.exists():backup.write_bytes(p.read_bytes());os.chown(backup,10001,10001);backup.chmod(0o600)
# Same constrained proxy and exact parent as the identity. Existing bindings unchanged.
bindings=config['bindings'];prior=json.dumps(bindings,sort_keys=True)
if ID not in bindings:
 b=dict(bindings['digitimes-de-1']);b.update(proxyLogin='dd-'+secrets.token_hex(8),proxyPassword=secrets.token_urlsafe(32));bindings[ID]=b
spec=importlib.util.spec_from_file_location('bootstrap','/home/ubuntu/doxagent/deploy/configure-datadome.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
db=sqlite3.connect((data/'registry/site_strategy.sqlite3').as_uri()+'?mode=ro',uri=True);listeners={}
for b in bindings.values():
 e=json.loads(db.execute('select data_json from proxy_egresses where egress_id=?',(b['egress_id'],)).fetchone()[0]);u=urlsplit(e['endpoint']);listeners[b['egress_id']]=(u.hostname,u.port)
net=json.loads(subprocess.check_output(['docker','network','inspect','doxagent-v2_default']))[0];subnet=net['IPAM']['Config'][0]['Subnet']
passwords=[]
for b in bindings.values():
 hashed=subprocess.check_output(['openssl','passwd','-6','-stdin'],input=(b['proxyPassword']+'\n').encode()).decode().strip();passwords.append(b['proxyLogin']+':'+hashed)
old={n:(root/n).read_bytes() for n in ['squid.conf','passwords']}
try:
 for n,c in [('squid.conf',m.squid_config(bindings,listeners,subnet)),('passwords','\n'.join(passwords)+'\n')]:
  file=root/n;file.write_text(c);os.chown(file,0,13);file.chmod(0o640)
 r=subprocess.run(['docker','exec','doxagent-captcha-proxy','squid','-k','parse'],capture_output=True)
 if r.returncode:raise RuntimeError('squid_parse_failed')
 subprocess.run(['docker','kill','--signal=HUP','doxagent-captcha-proxy'],check=True,capture_output=True)
 p.write_text(json.dumps(config));os.chown(p,10001,10001);p.chmod(0o600)
 print(json.dumps({'added_binding':ID,'egress_id':bindings[ID]['egress_id'],'old_bindings_unchanged':all(v==json.loads(backup.read_text())['bindings'][k] for k,v in bindings.items() if k!=ID),'allowlist_unchanged':True,'proxy_restarted':False}))
except Exception:
 for n,c in old.items():(root/n).write_bytes(c)
 raise
