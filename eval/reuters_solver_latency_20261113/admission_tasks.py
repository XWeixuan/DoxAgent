import sqlite3,json
from pathlib import Path
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings();d=sqlite3.connect(Path(s.site_access_sqlite_path).as_uri()+'?mode=ro',uri=True)
for id in ('digitimes-de-1','reuters-backup'):
 i=json.loads(d.execute('select spec_json from browser_identity_revisions where identity_id=? order by revision desc limit 1',(id,)).fetchone()[0]);p=json.loads(d.execute('select data_json from browser_profiles where profile_id=?',(i['profile_id'],)).fetchone()[0]);e=json.loads(d.execute('select data_json from proxy_egresses where egress_id=?',(i['egress_id'],)).fetchone()[0]);a=d.execute("select data_json from site_identity_auth where site_id='reuters' and identity_id=?",(id,)).fetchone()
 print(json.dumps({'identity':id,'enabled':i.get('enabled'),'access':i['access'],'profile':{k:p.get(k) for k in ('operational_state','maintenance_session_id','auth_state')},'egress':{k:e.get(k) for k in ('enabled','status')},'auth':json.loads(a[0]) if a else None}))
print('challenge_rows',d.execute("select task_id,state,superseded_by from challenge_tasks where identity_id='digitimes-de-1' order by updated_at desc limit 5").fetchall())
print('sessions',d.execute("select session_id,state,json_extract(data_json,'$.kind') from maintenance_sessions where identity_id='digitimes-de-1' order by updated_at desc limit 5").fetchall())
