import json,sqlite3
from pathlib import Path
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/v21-production-validation')
d=sqlite3.connect(root/'research.sqlite3');x=json.loads(d.execute("select payload from codex_document3_v21_runs where run_id='v21-deployment-maintain-smoke-3'").fetchone()[0])
items=[a for a in x['prepared']['manifest'] if '/d1/' in a['path'] or '/shared/document2' in a['path']]
result=dict(status=x['status'],upstream_manifest=items,warnings=x['prepared'].get('warnings'),document2_ref=x['prepared']['document2_ref'])
assert x['status']=='PARTIAL'
assert any('/shared/document2.json' in a['path'] and a.get('availability')=='available' for a in items)
assert any('/d1/' in a['path'] and a.get('availability')=='available' for a in items)
Path('/home/ubuntu/v21-production-20261012/maintain-input-proof.json').write_text(json.dumps(result,indent=2))
print(json.dumps(dict(status=x['status'],paths=[a['path'] for a in items],warnings=x['prepared'].get('warnings'))))
