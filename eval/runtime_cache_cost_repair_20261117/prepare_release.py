"""Inventory development files without staging bulk operator captures."""
import subprocess,json,re,collections
from pathlib import Path
root=Path.cwd()
tracked=subprocess.check_output(['git','diff','--name-only'],encoding='utf8').splitlines()
new=subprocess.check_output(['git','ls-files','--others','--exclude-standard'],encoding='utf8').splitlines()
selected=list(tracked)
for p in new:
 path=Path(p)
 if path.suffix in {'.py','.md','.ps1','.sh','.yaml','.yml','.toml'} or p.startswith('prompts/'):
  selected.append(p)
 elif p.startswith('eval/runtime_cache_cost_repair_20261117/') and path.suffix in {'.json','.xml','.txt'}:
  selected.append(p)
patterns=[re.compile(rb'sk-(?:or-v1-)?[A-Za-z0-9_-]{24,}'),re.compile(rb'AIza[A-Za-z0-9_-]{30,}'),
 re.compile(rb'AQ\.[A-Za-z0-9_-]{25,}'),re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
blocked=[];safe=[]
for p in sorted(set(selected)):
 path=root/p
 if not path.is_file():safe.append(p);continue
 raw=path.read_bytes()
 if any(x.search(raw) for x in patterns):blocked.append(p)
 else:safe.append(p)
out={'head':subprocess.check_output(['git','rev-parse','HEAD'],encoding='ascii').strip(),'files':safe,
 'credential_pattern_files_excluded':blocked,'untracked_capture_files_remaining':len(set(new)-set(safe))}
(root/'eval/runtime_cache_cost_repair_20261117/commit-inventory.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'tracked':len(tracked),'selected':len(safe),'credential_pattern_files_excluded':blocked,
 'untracked_capture_files_remaining':out['untracked_capture_files_remaining']},ensure_ascii=False))
