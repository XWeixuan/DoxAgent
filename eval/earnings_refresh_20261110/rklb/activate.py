import json
from pathlib import Path
from datetime import datetime
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.operations import submit_activation
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
s=DoxAgentSettings();repo=InitializationRepository(s.ticker_initialization_control_path);root=Path('/data/ops/rklb-refresh-20261111');record=root/'activation.json'
if record.exists():print(record.read_text());raise SystemExit(0)
meta=json.loads((root/'submitted.json').read_text());rid=meta['run']['initialization_id'];assert InitializationRepository(meta['control_database']).get(rid).status=='SUCCEEDED'
a=repo.active_revision('RKLB');assert a
runtime=SQLiteCodexRuntimeRepository(s.codex_runtime_sqlite_path)
for suffix in ['d1','d2','d3']:assert runtime.get_bundle(rid+'-'+suffix).status=='published'
from doxagent.workflows.codex_document3.state_v21 import StateV21
staged=StateV21(s.codex_runtime_sqlite_path).get_staged_by_run(rid+'-d3');assert staged
version=staged[1]['version']
p=SQLiteDocument3PolicyRepository(s.codex_runtime_sqlite_path).get_version('RKLB',version);assert p and len(p.policies)>0 and p.document2_ref.run_id==rid+'-d2'
refs={**a['artifacts'],'document1':{'run_id':rid+'-d1'},'document2':{'run_id':rid+'-d2'},'document3':{'run_id':rid+'-d3','version':version}}
reason='User authorized '+'RKLB'+' earnings refresh and production application on 2026-11-10 after full D1/D2/D3 verification; preserve current Event Library and effective monitoring configuration; retain coverage limitations.'
run=submit_activation(repo,'RKLB',artifacts=refs,reason=reason,cutoff=datetime.fromisoformat(meta['run']['research_cutoff_at'].replace('Z','+00:00')),expected_base_revision=a['revision_id'],preserve_monitoring_configuration=True)
result={'run':run.model_dump(mode='json'),'before':a,'target_artifacts':refs};record.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))

