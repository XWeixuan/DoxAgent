import asyncio, hashlib, json
from pathlib import Path
from datetime import datetime, UTC
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.workflows.codex_document3.state_v21 import StateV21
async def main():
 s=DoxAgentSettings(); root=Path('/data/ops/be-refresh-20261025'); meta=json.loads((root/'submitted.json').read_text()); rid=meta['run']['initialization_id']; out=root/'export';out.mkdir(exist_ok=True)
 def save(name,obj):
  data=obj if isinstance(obj,str) else json.dumps(obj,ensure_ascii=False,indent=2,default=str)
  (out/name).write_text(data,encoding='utf-8')
 ctrl=InitializationRepository(meta['control_database']); run=ctrl.get(rid); assert run.status=='SUCCEEDED'
 nodes=ctrl.nodes(rid);save('control_run.json',run.model_dump(mode='json'));save('control_nodes.json',[n.model_dump(mode='json') for n in nodes])
 repo=SQLiteCodexRuntimeRepository(s.codex_runtime_sqlite_path)
 bundles={}
 for stage in ['d1','d2','d3']:
  b=repo.get_bundle(rid+'-'+stage); assert b and b.status=='published',(stage,b.status if b else None)
  bundles[stage]=b.model_dump(mode='json');save(stage+'_bundle.json',bundles[stage])
 d2node=next(n for n in nodes if n.key=='d2'); save('document2.json',Path(d2node.result.artifacts['document2_path']).read_text())
 state=StateV21(s.codex_runtime_sqlite_path); st=state.run(rid+'-d3'); save('d3_state.json',st)
 staged=state.get_staged_by_run(rid+'-d3'); assert staged
 payload,receipt=staged;save('d3_staged_receipt.json',receipt)
 w=HttpCodexWorkerClient(s.codex_worker_base_url,s.codex_worker_bearer_token,capability_secret=s.codex_capability_secret)
 try:
  for name,path in receipt['files'].items():
   text=(out/name).read_text() if (out/name).exists() else (await w.read_text(rid+'-d3',path)).content
   assert hashlib.sha256(text.encode()).hexdigest()==receipt['hashes'][name],name
   save(name,text)
  c=bundles['d2']['checkpoint']; seed=(await w.read_text(rid+'-d2',c['final_shell_seed_path'])).content;save('final_shell_seeds.json',seed)
 finally:await w.aclose()
 prod=InitializationRepository(s.ticker_initialization_control_path);active=prod.active_revision('BE');save('active_after.json',active)
 event=PublishedEventLibraryReader(meta['event_root']).reference_view('BE',version=meta['event_version']); assert event.sha256==meta['event_sha256']
 assert active['artifacts']['monitoring_configuration']==meta['active_before']['artifacts']['monitoring_configuration']
 assert active['artifacts']['document1']==meta['active_before']['artifacts']['document1']
 assert active['artifacts']['document2']==meta['active_before']['artifacts']['document2']
 hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()};save('manifest.json',hashes)
 print(json.dumps({'status':run.status,'completed_at':run.updated_at.isoformat(),'staged_phase':st.get('phase'),'state_keys':list(st),'coverage':st.get('coverage'),'policy_count':len(payload.get('policies',[])),'receipt_keys':list(receipt),'export':str(out),'event_sha256':event.sha256,'configuration_reference_unchanged':True,'d1d2_not_activated':True},ensure_ascii=False,default=str))
asyncio.run(main())
