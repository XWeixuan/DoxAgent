import json, sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.event_library.provider import PublishedEventLibraryReader
s=DoxAgentSettings(); r=InitializationRepository(s.ticker_initialization_control_path)
e=PublishedEventLibraryReader(s.event_library_root,market='US').reference_view('BE')
with r._connection() as db:
    ops=[dict(x) for x in db.execute("select ticker,run_id from ticker_operations where ticker='BE'")]
print(json.dumps({'settings':{k:getattr(s,k) for k in ['ticker_initialization_control_path','codex_runtime_sqlite_path','event_library_root','codex_d2_max_concurrency','codex_model','codex_reasoning_effort']},'active':r.active_revision('BE'),'operations':ops,'event':e.model_dump(mode='json') if e else None},ensure_ascii=False,default=str))
