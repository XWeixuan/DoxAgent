"""Read the scheduler's published immutable prompt assets without dispatching a Case."""
import hashlib
import json
from pathlib import Path

from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings

s = DoxAgentSettings()
j = RuntimeJournal(s.persistent_runtime_v2_sqlite_path)
identity = j.get('execution', 'active')
bundle = ExecutionBundles(j).get(identity)
checks = {}
for name in ('AGENTS.md', 'skills/skill.md'):
    content = bundle['assets']['w3/'+name]
    checks[name] = {
        'matches_image': content == (Path(s.persistent_runtime_v2_w3_prompt_root)/name).read_text(encoding='utf-8'),
        'sha256': hashlib.sha256(content.encode()).hexdigest(),
    }
assert all(v['matches_image'] for v in checks.values())
print(json.dumps({'execution': identity, 'assets': checks, 'w3_entry_pending': j.get('w3_entry', 'pending')}))
