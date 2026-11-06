import hashlib,json,os,sqlite3
from pathlib import Path
from doxagent.message_bus_v2.rss_http_race import RECIPES
# This script is run only after the new image is present.
root=Path('/data/diagnostics/rss-http-race-20261107');root.mkdir(parents=True,exist_ok=True)
db=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=3)
ids=[*RECIPES,'corporate_ir_press_releases']
out={'sources':{},'bindings':{}}
for sid in ids:
 r=db.execute('select data_json from source_definitions where source_id=?',(sid,)).fetchone()
 if r:out['sources'][sid]=json.loads(r[0])
for bid,data in db.execute('select binding_id,data_json from ticker_source_bindings where source_id in(select value from json_each(?))',(json.dumps(ids),)):out['bindings'][bid]=json.loads(data)
for name,query in [('terms','select ticker,current_revision from ticker_monitoring_terms order by ticker'),('monitors','select ticker,status from ticker_monitoring_states order by ticker')]:
 out[name]=hashlib.sha256(json.dumps(db.execute(query).fetchall()).encode()).hexdigest()
p=root/'configuration-before.private.json'
with p.open('w') as f:os.fchmod(f.fileno(),0o600);json.dump(out,f)
print(json.dumps({'sources':len(out['sources']),'bindings':len(out['bindings']),'snapshot':str(p)}))
