import json
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.event_library.provider import PublishedEventLibraryReader
s=DoxAgentSettings(); r=InitializationRepository(s.ticker_initialization_control_path)
x=json.loads(Path('/data/ops/be-refresh-20261025/submitted.json').read_text()); run=r.get(x['run']['initialization_id'])
e=PublishedEventLibraryReader(s.event_library_root).reference_view('BE',version=37)
assert e.sha256==x['event_sha256']
print(json.dumps({'run':run.model_dump(mode='json'),'nodes':[{'key':n.key,'status':n.status,'error':n.error,'child_run_id':n.receipt.get('child_run_id')} for n in r.nodes(run.initialization_id)],'event_root':s.event_library_root,'event_sha256':e.sha256},ensure_ascii=False))
