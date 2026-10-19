"""Time the production exact-original reader in isolation; no website traffic."""
import subprocess

CODE = r'''
import asyncio,os,json,time,sqlite3
from types import SimpleNamespace
from doxagent.site_strategy.schema import BrowserIdentitySpec
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
from doxagent.site_strategy.maintenance_document import read_original_document
from doxagent.content_enrichment.quality import inspect_html
async def main():
 db=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True)
 session=SimpleNamespace(**json.loads(db.execute("select data_json from maintenance_sessions where session_id='maintenance_eec18ec6dcc8465290f6d1a844691360'").fetchone()[0]))
 identity=BrowserIdentitySpec.model_validate_json(db.execute('select r.spec_json from browser_identity_revisions r join browser_identity_heads h on h.identity_id=r.identity_id and h.active_revision=r.revision where r.identity_id=?',(session.identity_id,)).fetchone()[0])
 target=SimpleNamespace(**json.loads(db.execute('select data_json from owned_browser_targets where allocation_id=?',(session.allocation_id,)).fetchone()[0]))
 t=time.monotonic()
 try:
  c=await read_original_document(ChromeSupervisorClient(os.environ['DOXAGENT_SITE_ACCESS_CHROME_SUPERVISOR_SOCKET'],owner_id='readonly-diagnostic'),identity,session,target)
  read=time.monotonic()-t
  info=inspect_html(c.body,c.final_url,'',generic_extraction=False)
  print(json.dumps({'read_seconds':read,'classification_seconds':time.monotonic()-t-read,'kind':info.page_kind,'reason':info.access_reason,'bytes':len(c.body),'url':c.final_url}))
 except Exception as exc: print(json.dumps({'error':type(exc).__name__,'seconds':time.monotonic()-t}))
 db.close()
asyncio.run(main())
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-'],input=CODE,text=True,capture_output=True,timeout=45)
print(p.stdout)
assert p.returncode==0,p.stderr[-300:]
