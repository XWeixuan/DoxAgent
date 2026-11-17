import asyncio,json,sys
from pathlib import Path
from datetime import datetime,timezone
import httpx
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings()
async def main():
 async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',headers={'Authorization':'Bearer '+Path(s.site_access_admin_token_file).read_text().strip()},timeout=30,trust_env=False) as h:
  r=await h.get('/v1/maintenance/operations/'+(sys.argv[1] if len(sys.argv)>1 else 'maintenance_op_398c57ab49f04df2bcab29a4a689b23e'));v=r.json();print(json.dumps({k:v.get(k) for k in ['operation_id','state','stage','error','result','updated_at','stage_started_at']},indent=2))
asyncio.run(main())


