import json
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings(); r=SQLiteCodexRuntimeRepository(s.codex_runtime_sqlite_path)
p='init-be-a79840f3bcbf44218b49a1aceba5e6a3'
for suffix in ['d1','d2']:
 b=r.get_bundle(p+'-'+suffix); d=b.model_dump(mode='json'); c=d.get('checkpoint',{}) or {}
 print(json.dumps({'stage':suffix,'status':d.get('status'),'publication_state':d.get('publication_state'),'checkpoint_keys':list(c),'warnings':c.get('warnings',[]),'shells':c.get('shell_states',c.get('shells',[]))},ensure_ascii=False))
