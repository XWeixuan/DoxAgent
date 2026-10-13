import json,hashlib,sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
s=DoxAgentSettings(_env_file=None); r=InitializationRepository(s.ticker_initialization_control_path); a=r.active_revision('RKLB')
root=Path('/data/production-backups/rklb-adoption-20261013');root.mkdir(parents=True,exist_ok=True)
e=Path(a['artifacts']['event_library']['root'])
h={str(p.relative_to(e)):hashlib.sha256(p.read_bytes()).hexdigest() for p in e.rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))}
out={'active':a,'event_files':h}
(root/'before.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False))
