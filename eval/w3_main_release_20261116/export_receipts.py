"""Export bounded release metadata; never export Compose/env/owner state."""
import json
from pathlib import Path

root = Path('/var/lib/doxagent/releases/w3-main-20261116')
out = Path('/home/ubuntu')
for filename in ('verification.json', 'observation.jsonl', 'source-audit.json'):
    target = out / ('w3-main-final-' + filename)
    target.write_bytes((root / filename).read_bytes())
    target.chmod(0o644)
data = json.loads((root / 'deploy.json').read_text())
target = out / 'w3-main-final-deploy-summary.json'
target.write_text(json.dumps({k: data.get(k) for k in ('errors', 'pressure', 'available_after')}, indent=2))
target.chmod(0o644)
