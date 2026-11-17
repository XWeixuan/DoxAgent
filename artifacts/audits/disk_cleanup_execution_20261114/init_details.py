import json,sqlite3,subprocess
from pathlib import Path
from collections import Counter
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.initialization_owners import control_paths
s=DoxAgentSettings();out={}
for p in control_paths(s):
 db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
 runs=db.execute("SELECT id,ticker,payload FROM initialization_runs WHERE json_extract(payload,'$.status')='SUCCEEDED'").fetchall();rows=[]
 for run,ticker,raw in runs:
  v=json.loads(raw);rows.append({'run':run,'ticker':ticker,'keys':list(v),'small_fields':{k:v[k] for k in ['status','created_at','updated_at','completed_at','manual_resume_required','workspace_run_id','activation_revision','activation_revision_id','production_revision','candidate_revision_id'] if k in v},'nodes':db.execute("SELECT node_key,json_extract(payload,'$.status'),json_extract(payload,'$.run_id'),json_extract(payload,'$.workspace_run_id') FROM initialization_nodes WHERE run_id=?",(run,)).fetchall()})
 out[str(p)]=rows;db.close()
print(json.dumps(out))
