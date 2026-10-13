import json,subprocess
from pathlib import Path
root=Path('/home/ubuntu/v21-production-20261012')
receipt=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/production-backups/v21-20261012/upgrade-v21-receipt.json')
assert receipt.is_file(),'upgrade must finish first'
for ticker in ('MU','BE','INTC'):
 cmd=['docker','run','--rm','--network','none','-v','doxagent-v2_v2-data:/data','-v',str(root)+':/ops:ro','-e','DOXAGENT_CODEX_RUNTIME_SQLITE_PATH=/data/research/research.sqlite3','-e','DOXAGENT_CODEX_WORKSPACE_ROOT=/data/workspaces','doxagent-v2:v21-production-20261012-r1','python','-m','doxagent.research_adoption','import','--manifest','/ops/ops/adoption-manifest.json','--ticker',ticker]
 with (root/('production-import-'+ticker+'.json')).open('w') as out:
  result=subprocess.run(cmd,stdout=out,text=True,check=True)
 d=json.loads((root/('production-import-'+ticker+'.json')).read_text())
 print(json.dumps({k:d[k] for k in ('ticker','run_id','version','policy_count','evidence_files','upstream_workspace_files')}),flush=True)
