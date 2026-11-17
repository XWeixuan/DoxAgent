import json,hashlib,yaml,httpx,subprocess
from pathlib import Path
root=Path.home()/'AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev';meta=yaml.safe_load((root/'profiles.yaml').read_text(encoding='utf-8'));item=next(i for i in meta['items'] if i.get('name')=='SSRDOG');url=item.get('url')
try:
 r=httpx.get(url,trust_env=False,timeout=25,follow_redirects=True);r.raise_for_status();data=yaml.safe_load(r.text)
 nodes=[n for n in data.get('proxies',[]) if 'Germany' in n.get('name','') or '德国' in n.get('name','')]
 print(json.dumps({'http_status':r.status_code,'german_nodes':[n['name'] for n in nodes]}))
 if nodes:
  # Root-only transfer of selected node; never persist subscription or node secret in repo.
  n=nodes[0];payload=json.dumps(n);code="import sys,json,yaml;from pathlib import Path;p=Path('/opt/doxagent-egress-clash/reuters-backup2');p.mkdir(exist_ok=True,mode=0o700);f=p/'fresh-node.yaml';f.write_text(yaml.safe_dump(json.loads(sys.stdin.read()),allow_unicode=True));f.chmod(0o600)"
  q=subprocess.run(['ssh','-o','ProxyCommand=none','doxagent-sg',"sudo python3 -c "+__import__('shlex').quote(code)],input=payload,text=True,capture_output=True);print(json.dumps({'private_node_transfer':q.returncode==0}))
except Exception as e: print(json.dumps({'refresh_failed':type(e).__name__}))

