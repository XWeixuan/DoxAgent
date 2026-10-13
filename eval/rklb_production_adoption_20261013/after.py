import json,hashlib
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
s=DoxAgentSettings(_env_file=None);r=InitializationRepository(s.ticker_initialization_control_path)
a=r.active_revision('RKLB');root=Path('/data/production-backups/rklb-adoption-20261013');b=json.loads((root/'before.json').read_text())
e=Path(b['active']['artifacts']['event_library']['root']);h={str(p.relative_to(e)):hashlib.sha256(p.read_bytes()).hexdigest() for p in e.rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))}
out={'active':a,'event_ref_preserved':a['artifacts']['event_library']==b['active']['artifacts']['event_library'],'event_files_unchanged':h==b['event_files'],'monitoring_preserved':a['artifacts']['monitoring_configuration']==b['active']['artifacts']['monitoring_configuration'],'acks':{w:r.revision_acknowledged('RKLB',a['revision_id'],w) for w in ['bus','runtime']}}
(root/'after.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
