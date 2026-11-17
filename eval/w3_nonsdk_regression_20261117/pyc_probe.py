import json,subprocess
code="""import json,importlib.util;from pathlib import Path
paths=['/app/src/doxagent/codex_worker/schema.py','/app/src/doxagent/codex_worker/sdk_runtime.py','/app/src/doxagent/persistent_runtime_v2/schema.py','/app/.venv/lib/python3.11/site-packages/openai_codex/async_client.py']
out={}
for p in paths:
 f=Path(p);b=Path(importlib.util.cache_from_source(p));h=b.read_bytes()[:16] if b.exists() else b'';out[p]={'cache_exists':b.exists(),'checked_hash_matches':bool(h and h[8:16]==importlib.util.source_hash(f.read_bytes()))}
print(json.dumps(out))"""
print(subprocess.check_output(['docker','exec','doxagent-v2-codex-worker-1','python','-c',code]).decode())
