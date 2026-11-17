import json,sqlite3,urllib.request
from pathlib import Path
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings();d=sqlite3.connect(Path(s.site_access_sqlite_path).as_uri()+'?mode=ro',uri=True)
sc=json.loads(d.execute("select data_json from source_access_scopes where scope_id='source:reuters_site_search'").fetchone()[0]);out={'scope':sc}
for table,col in [('site_runtime','runtime_key'),('identity_runtime','identity_id')]:
 rows=d.execute('select data_json from '+table+' where '+col+' in (?,?,?)',('scope:source:reuters_site_search','digitimes-de-1','reuters-backup')).fetchall()
 out[table]=[json.loads(r[0]) for r in rows]
token=Path(s.site_access_admin_token_file).read_text().strip()
req=urllib.request.Request('http://127.0.0.1:8011/v1/diagnostics/access',headers={'Authorization':'Bearer '+token})
with urllib.request.urlopen(req,timeout=15) as r:out['diag']=json.load(r)
# Runtime records and diagnostics contain no cookie/token values.
print(json.dumps(out,indent=2))
