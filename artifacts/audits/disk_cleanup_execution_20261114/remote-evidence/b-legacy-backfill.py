import json,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from doxagent.trade_execution.worker import WriterLock
from doxagent.v2_read.maintenance import atomic_json
w=Path('/data/workspaces');d=json.loads(Path('/tmp/doxagent-legacy-map.json').read_text());out={'backfilled':[],'skipped':[]}
with WriterLock(w/'initialization-snapshot-retirement'):
 for identity,matches in d['matches'].items():
  if len(matches)!=1:out['skipped'].append(identity);continue
  v=dict(matches[0]);parent=v.pop('parent');marker=w/'.init-owners'/(identity+'.json')
  if marker.exists() or parent['status']!='SUCCEEDED' or parent['manual']:out['skipped'].append(identity);continue
  with sqlite3.connect((Path('/data')/v['initialization_control_path']).as_uri()+'?mode=ro',uri=True) as db:
   row=db.execute("SELECT json_extract(payload,'$.status'),json_extract(payload,'$.manual_resume_required') FROM initialization_runs WHERE id=?",(v['initialization_id'],)).fetchone()
   if not row or row[0]!='SUCCEEDED' or row[1]:continue
  v.update(state='LIVE',backfilled_at=datetime.now(timezone.utc).isoformat(),basis='sha256-workspace-and-exact-historical-execution-id');atomic_json(marker,v);out['backfilled'].append(identity)
print(json.dumps(out))
