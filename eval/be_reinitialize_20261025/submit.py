import json, os, sqlite3, hashlib
from pathlib import Path
from datetime import datetime, UTC
from doxagent.settings import DoxAgentSettings
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.schema import NodeSpec
s=DoxAgentSettings(); prod=InitializationRepository(s.ticker_initialization_control_path)
a=prod.active_revision('BE'); assert a
root=a['artifacts']['event_library'].get('root') or s.event_library_root
v=a['artifacts']['event_library']['version']
e=PublishedEventLibraryReader(root,market='US').reference_view('BE',version=v); assert e and e.published_at
base=Path('/data/ops/be-refresh-20261025'); base.mkdir(parents=True,exist_ok=True)
repo=InitializationRepository(base/'control.sqlite3')
prior=base/'submitted.json'
if prior.exists():
    print(prior.read_text()); raise SystemExit(0)
with prod._connection() as db:
    assert not db.execute("select 1 from ticker_operations where ticker='BE'").fetchone(), 'BE production initialization active'
cutoff=datetime.now(UTC)
assert e.published_at<=cutoff
replay={'o2':{'artifacts':{'event_library':{'version':v}}}}
plan=[NodeSpec(key='d1',block='D1',inputs={'research_brief':'Refresh Bloom Energy Global Research using the latest officially released earnings, financial statements and guidance available by the research cutoff. Verify actual release dates; do not treat scheduled or future earnings as released.', '_operator_reason':'User authorized BE earnings refresh: D1/D2/D3 only; preserve Event Library and monitoring configuration.'}),NodeSpec(key='d2',block='D2',dependencies=['d1'],inputs={'_replay_dependencies':replay}),NodeSpec(key='d3',block='D3',dependencies=['d2'],inputs={'_replay_dependencies':replay})]
run=repo.submit('BE',cutoff,plan,reinitialize=True,control_operation_id='be-earnings-refresh-20261025')
record={'run':run.model_dump(mode='json'),'event_root':root,'event_version':v,'event_sha256':e.sha256,'active_before':a,'control_database':str(repo.path),'research_database':s.codex_runtime_sqlite_path,'container':'doxagent-be-refresh-20261025','settings':{k:getattr(s,k) for k in ['codex_d2_max_concurrency','codex_d3_max_concurrency','codex_startup_concurrency']},'plan':[n.model_dump(mode='json') for n in plan]}
prior.write_text(json.dumps(record,ensure_ascii=False,indent=2))
print(json.dumps(record,ensure_ascii=False))
