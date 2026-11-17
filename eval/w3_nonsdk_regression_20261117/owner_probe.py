import json,httpx,sqlite3,importlib.util
from pathlib import Path
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings();out={}
r=httpx.get(s.codex_worker_base_url+'/v1/resources',headers={'Authorization':'Bearer '+s.codex_worker_bearer_token},timeout=10,trust_env=False);r.raise_for_status();out['worker_resources']=r.json()
out['source_path']=s.codex_runtime_sqlite_path
p=Path(s.codex_runtime_sqlite_path);d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
out['source_samples']={kind:[dict(raw_bytes=r[0],native_bytes=r[1],has_pointer=r[2]) for r in d.execute("select length(payload_json),json_extract(payload_json,'$.bytes'),json_extract(payload_json,'$.\"$doxagent_content_v1\"') is not null from codex_runtime_records where record_type=? limit 20",(kind,))] for kind in ('sources','documents','bundles')}
print(json.dumps(out))
