import json, sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.collection_plane.observability import store_from_environment
s=DoxAgentSettings();r=MessageBusV2Repository(s.message_bus_v2_sqlite_path,initialize=False)
source=r.get_source('bloomberg_massive');terms=MonitoringTermsService(r)
print(json.dumps({'source_version':source.version,'mode':source.delivery_mode,'enabled':source.enabled,
 'bindings':[{'ticker':b.ticker,'enabled':b.enabled,'source_version':b.source_version} for b in r.list_bindings(source_id=source.source_id)]}))
before=json.loads(Path('/data/bus/ops/bloomberg-production-20261113/before.private.json').read_text())
for ticker, old in before['terms'].items():
 current=terms.get(ticker)[1].model_dump(mode='json')
 ignored={'expected_revision','massive_l2'} if ticker=='IONQ' else {'expected_revision'}
 assert {k:v for k,v in current.items() if k not in ignored}=={k:v for k,v in old.items() if k not in ignored}
print(json.dumps({'ordinary_rules_unchanged':True,'other_nine_massive_rules_unchanged':True,'ionq_massive_groups':len(terms.get('IONQ')[1].massive_l2['en'].groups)}))
store=store_from_environment()
if store:
 with store.connect() as db:
  rows=db.execute('select unit,head_json,count(*) n from units where source_id=? and required=1 group by unit,head_json',('bloomberg_massive',)).fetchall()
  for row in rows:print(json.dumps({'unit':row['unit'],'bindings':row['n'],'head':json.loads(row['head_json'])}))
with sqlite3.connect('file:'+str(r.path)+'?mode=ro',uri=True,timeout=3) as db:
 db.row_factory=sqlite3.Row
 for row in db.execute('select title,state,outcome,last_error,checked_at,delivery_mode from live_articles where source_id=? and delivery_mode=? order by first_seen_at desc limit 8',('bloomberg_massive','production')):
  print(json.dumps({'body':dict(row)}))
 print(json.dumps({'test_promoted_to_production':db.execute("select count(*) from live_deliveries d join live_articles a using(article_key) where d.source_id=? and d.state='PUBLISHED' and a.delivery_mode='test'",('bloomberg_massive',)).fetchone()[0]}))
