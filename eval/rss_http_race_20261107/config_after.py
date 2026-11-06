import json,sqlite3,hashlib
from doxagent.message_bus_v2.schema import SourceDefinition
from pathlib import Path
root=Path('/data/diagnostics/rss-http-race-20261107');before=json.loads((root/'configuration-before.private.json').read_text())
db=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=3);bad=[]
for sid,old in before['sources'].items():
 old=SourceDefinition.model_validate(old).model_dump(mode='json')
 new=SourceDefinition.model_validate_json(db.execute('select data_json from source_definitions where source_id=?',(sid,)).fetchone()[0]).model_dump(mode='json')
 for k in ['adapter_ref','content_enrichment_mode','content_language','acquisition_mode','default_polling_config','distribution_policy','enabled','site_id']:
  if new.get(k)!=old.get(k):bad.append(sid+':'+k)
for bid,old in before['bindings'].items():
 new=json.loads(db.execute('select data_json from ticker_source_bindings where binding_id=?',(bid,)).fetchone()[0])
 for k in ['source_parameters','polling','enabled','ticker']:
  if new.get(k)!=old.get(k):bad.append(bid+':'+k)
for name,query in [('terms','select ticker,current_revision from ticker_monitoring_terms order by ticker'),('monitors','select ticker,status from ticker_monitoring_states order by ticker')]:
 if before[name]!=hashlib.sha256(json.dumps(db.execute(query).fetchall()).encode()).hexdigest():bad.append(name)
print(json.dumps({'checked_sources':len(before['sources']),'checked_bindings':len(before['bindings']),'unexpected_changes':bad}))
assert not bad,bad
