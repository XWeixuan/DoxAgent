import asyncio,json,hashlib
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.codex_runtime.client import HttpCodexWorkerClient
async def main():
 s=DoxAgentSettings();p=Path('/data/ops/rklb-refresh-20261111/export');b=json.loads((p/'d1_bundle.json').read_text());w=HttpCodexWorkerClient(s.codex_worker_base_url,s.codex_worker_bearer_token,capability_secret=s.codex_capability_secret)
 try:
  for k,v in b['reports'].items():
   t=(await w.read_text(b['run_id'],v['relative_path'])).content;assert hashlib.sha256(t.encode()).hexdigest()==v['sha256'];(p/('d1_'+k+'.md')).write_text(t,encoding='utf-8')
 finally:await w.aclose()
 m={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in p.iterdir() if x.is_file() and x.name!='manifest.json'};(p/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps({'reports':list(b['reports']),'files':len(m)}))
asyncio.run(main())

