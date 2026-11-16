import json,subprocess,hashlib
from pathlib import Path
root=Path('/var/lib/doxagent/releases/w3-emergency-20261116');build=json.loads((root/'build.json').read_text());image=build['images']['backend']['image']
paths=['src/doxagent/codex_worker/w3_ready.py','src/doxagent/persistent_runtime_v2/w3.py']
code='import json,hashlib;from pathlib import Path;p='+repr(paths)+";print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in p}))"
actual=json.loads(subprocess.check_output(['docker','run','--rm','-i','--entrypoint','python',image,'-c',code]))
expected={p:hashlib.sha256(subprocess.check_output(['git','-C','/home/ubuntu/doxagent','show',build['commit']+':'+p])).hexdigest() for p in paths}
assert actual==expected
out=dict(commit=build['commit'],image=image,hashes=actual);(root/'candidate-verification.json').write_text(json.dumps(out));print(json.dumps(out))
