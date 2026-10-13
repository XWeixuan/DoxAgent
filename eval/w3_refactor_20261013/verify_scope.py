import hashlib
import json
from pathlib import Path

baseline = json.loads(Path('eval/w3_refactor_20261013/baseline.json').read_text(encoding='utf-8'))
protected = {k: v for k, v in baseline.items() if k.startswith(('prompts/', '.agents/skills/', 'skills/'))}
changed = [name for name, sha in protected.items()
           if not Path(name).is_file() or hashlib.sha256(Path(name).read_bytes()).hexdigest() != sha]
result = {'protected_assets_checked': len(protected), 'changed_since_start': changed,
          'prompt_skill_scope_unchanged': not changed}
Path('eval/w3_refactor_20261013/scope_receipt.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
