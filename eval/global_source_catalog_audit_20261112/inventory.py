import subprocess

CODE = r'''import os,json,sqlite3,time
from pathlib import Path
from datetime import datetime,UTC
start=time.monotonic()
def ro(path):
 db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.3);db.row_factory=sqlite3.Row
 db.set_progress_handler(lambda: int(time.monotonic()-start>9),10000)
 return db
from doxagent.v2_read.repository import ReadStore
read=ro(ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']).path)
fields=['display_name','kind','acquisition_mode','delivery_mode','entry_url','content_enrichment_mode','enabled','adapter_ref']
q="SELECT id,"+','.join("json_extract(payload,'$."+f+"') AS "+f for f in fields)+" FROM object_current INDEXED BY current_identity WHERE kind='native:source_definitions' AND ticker=''"
sources=[dict(r) for r in read.execute(q)]
bindings=[dict(r) for r in read.execute("SELECT id,ticker,source_id,json_extract(payload,'$.enabled') enabled,json_extract(payload,'$.polling.enabled') poll_enabled FROM object_current INDEXED BY current_identity WHERE kind='native:ticker_source_bindings' AND json_extract(payload,'$.tombstoned_at') IS NULL")]
acq=ro(Path(os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']).parent/'acquisition.sqlite3')
units=[dict(r) for r in acq.execute("SELECT source_id,ticker,binding_id,unit,required,json_extract(head_json,'$.last_success_at') last_success,json_extract(head_json,'$.coverage_plan.group') logical_group,json_extract(head_json,'$.status') status FROM units WHERE required!=0")]
indexes=[dict(r) for r in read.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND (name LIKE 'current_%' OR name LIKE '%article%' OR name LIKE '%bus%')")]
examples=[]
for sid in ['defensenews_rss','quantum_zeitgeist','register_software_cloud_security','yahoo_finance_news','barrons_ticker_news']:
 rows=[]
 for b in (b for b in bindings if b['source_id']==sid):
  rows.extend(dict(r) for r in read.execute("SELECT k.ticker,d.parent article_key,json_extract(d.payload,'$.standard_id') standard_id,k.discovered_at FROM article_sample_keys k INDEXED BY article_sample_source_time JOIN object_current d INDEXED BY current_identity ON d.kind='native:live_deliveries' AND d.ticker=k.ticker AND d.id=k.id WHERE k.ticker=? AND k.source_id=? ORDER BY k.discovered_at DESC,k.id DESC LIMIT 5",(b['ticker'],sid)))
 examples.append({'source_id':sid,'rows':rows})
print(json.dumps({'at':datetime.now(UTC).isoformat(),'seconds':time.monotonic()-start,'sources':sources,'bindings':bindings,'unit_heads':units,'indexes':indexes,'duplicate_examples':examples}))
'''
cid = subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.service=v2-api'],text=True).strip()
p = subprocess.run(['docker','exec','-i',cid,'python','-'],input=CODE,text=True,capture_output=True,timeout=18)
if p.returncode:
    raise RuntimeError(p.stderr[-1800:])
print(p.stdout)
