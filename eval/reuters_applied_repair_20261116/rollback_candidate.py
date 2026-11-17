import json,asyncio
from pathlib import Path
from datetime import datetime,timezone
import importlib.util
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
s=DoxAgentSettings();m=importlib.util.spec_from_file_location('promotion','/tmp/promote_reuters_identity.py');ops=importlib.util.module_from_spec(m);m.loader.exec_module(ops)
async def main():
 repo=SiteStrategyRepository(s.site_access_sqlite_path);store=GovernanceRepository(repo);snapshot=json.loads((Path(s.site_access_sqlite_path).parent/'reuters-promotion-config-20261116.json').read_text());revision=store.get_scope(ops.SOURCE).revision
 result=ops.restore(repo,snapshot,source_revision=revision,apply=True)
 sup=ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='rollback-observer');processes={}
 for id in ['digitimes-de-1','reuters-backup','reuters-backup2']:
  v=await sup.status(id);processes[id]={k:v.get(k) for k in ['instance_id','pid','running']}
 print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'result':result,'processes':processes,'revision':store.get_scope(ops.SOURCE).revision},indent=2));repo.close()
asyncio.run(main())
