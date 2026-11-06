"""Read only ownership facts for an existing target; no browser commands."""
import json
import sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings

db = sqlite3.connect(Path(DoxAgentSettings().site_access_sqlite_path).as_uri() + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row
out = []
for row in db.execute("select * from owned_browser_targets where identity_id='reuters-backup' "
                      "and target_id in ('6AA6C851194AAEFC28E498AEA893FC72',"
                      "'8552A991AC8C5CFD7E330A08E06D85C5','FCC0BC51693FF0D7517F8EEEA41347EB')"):
    v = json.loads(row['data_json'])
    item = {k: v.get(k) for k in ('allocation_id','target_id','owner_kind','state','created_at','updated_at')}
    item['sessions'] = []
    for session_row in db.execute("select data_json from maintenance_sessions where identity_id='reuters-backup'"):
        session = json.loads(session_row[0])
        if session.get('allocation_id') == v['allocation_id']:
            item['sessions'].append({k: session.get(k) for k in ('session_id','kind','state','created_at','updated_at')})
    item['challenge_tasks'] = []
    for task_row in db.execute("select data_json from challenge_tasks where identity_id='reuters-backup'"):
        task = json.loads(task_row[0])
        if task.get('allocation_id') == v['allocation_id']:
            item['challenge_tasks'].append({k: task.get(k) for k in ('task_id','state','opened_at','resolved_at','superseded_by')})
    out.append(item)
print(json.dumps(out, indent=2))
db.close()
