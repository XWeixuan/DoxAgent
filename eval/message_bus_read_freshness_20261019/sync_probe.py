"""Inspect the business settlement sync boundary without writing it."""
import subprocess
code='''
import sqlite3,json,time
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.source_lifecycle import BUSINESS_FACT_KINDS
s=ReadStore('/data/read/active.json');out={}
with s.connect() as db:
 out['checkpoints']=[dict(r) for r in db.execute('select * from source_health')]
 out['facts']=[list(r) for r in db.execute("select kind,count(*),min(valid_from),max(valid_from),sum(length(payload)) from object_current where kind in (select value from json_each(?)) group by kind",(json.dumps(BUSINESS_FACT_KINDS),))]
 out['seq']=s.highwater(db)
d=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True,timeout=1)
out['settlements']=[list(r) for r in d.execute('select kind,count(*),sum(length(payload)) from v2_serving_business_facts group by kind')]
out['triggers']=[list(r) for r in d.execute("select name,sql from sqlite_master where type='trigger' and tbl_name='v2_serving_business_facts'")]
print(json.dumps(out))
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-api-1','python','-'],input=code,text=True,capture_output=True,timeout=80)
print(p.stdout);print(p.stderr[-1200:]);assert p.returncode==0
