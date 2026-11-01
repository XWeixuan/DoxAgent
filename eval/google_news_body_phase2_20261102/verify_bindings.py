import json,sqlite3
from datetime import datetime,timedelta,timezone
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.collection_plane.store import path_for
s=DoxAgentSettings();repo=MessageBusV2Repository(s.message_bus_v2_sqlite_path)
source=repo.get_source('google_news_search_rss');all_b=repo.list_bindings();google=[b for b in all_b if b.source_id=='google_news_search_rss'];expected={'BE','CEG','COHR','INTC','IONQ','NBIS','ONDS','RKLB','SNOW'}
assert {b.ticker for b in google}==expected
assert len(google)==9 and all(b.enabled and b.polling.enabled for b in google)
with repo._connect() as db:
 versions=[{'binding_id':b.binding_id,'version':b.version} for b in google]
 columns=[r[1] for r in db.execute('PRAGMA table_info(live_articles)')]
 assert 'execution_spent_ms' in columns
with sqlite3.connect(path_for(repo.path).resolve().as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 units=[dict(r) for r in db.execute("SELECT binding_id,unit,ticker FROM units WHERE source_id='google_news_search_rss'")]
 cutoff=(datetime.now(timezone.utc)-timedelta(minutes=8)).isoformat()
 cycles=[dict(r) for r in db.execute("SELECT ticker,status,reason,dispatch_at,finished_at FROM cycles WHERE dispatch_at>=? AND source_id='google_news_search_rss' ORDER BY dispatch_at DESC LIMIT 80",(cutoff,))]
from doxagent.message_bus_v2.collection_plane.config import load
config=load(s)
print(json.dumps({'calendar_realtime':config.realtime,'calendar_day':config.calendar_day,'compiled_google_bindings':sorted(b.ticker for b in config.bindings.values() if b.source_id=='google_news_search_rss'),'source_enabled':source.enabled,'tickers':sorted(expected),'unique_bindings':len(google),'mu_google_binding':repo.get_binding('MU:google_news_search_rss') is not None,'binding_versions':versions,'schema_migrated':True,'collector_units':units,'recent_cycles':cycles}))
