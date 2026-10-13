import json,hashlib
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
s=DoxAgentSettings(_env_file=None)
r=InitializationRepository(s.ticker_initialization_control_path)
a=r.active_revision('RKLB')
print(json.dumps({'active':a,'database':str(s.codex_runtime_sqlite_path),'workspace':str(s.codex_workspace_root),'control':str(s.ticker_initialization_control_path)},ensure_ascii=False,default=str))
