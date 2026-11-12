import json, sqlite3
from datetime import datetime, UTC
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.live_repository import LiveRepository
s=DoxAgentSettings();repo=MessageBusV2Repository(s.message_bus_v2_sqlite_path,initialize=False);live=LiveRepository(repo)
with sqlite3.connect('file:'+str(repo.path)+'?mode=ro',uri=True,timeout=3) as db:
 db.row_factory=sqlite3.Row
 source=repo.get_source('bloomberg_massive')
 print(json.dumps({'kind':'source','at':datetime.now(UTC).isoformat(),'version':source.version,'enabled':source.enabled,'delivery_mode':source.delivery_mode,'endpoints':source.default_parameters['endpoints']}))
 for table in ['raw_messages','standard_messages','ticker_source_bindings','test_publications']:
  cols=[r[1] for r in db.execute('pragma table_info('+table+')')]
  if 'source_id' in cols:
   print(json.dumps({'kind':'count','table':table,'count':db.execute('select count(*) from '+table+' where source_id=?',('bloomberg_massive',)).fetchone()[0]}))
 rows=db.execute("select article_key,title,url,state,outcome,checked_at,last_error,result_ref,fetch_total,cache_total,delivery_mode from live_articles where source_id=? and checked_at>? order by checked_at desc limit 12",('bloomberg_massive','2026-11-12T20:52:00+00:00')).fetchall()
 for row in rows:
  data=dict(row);ref=data.pop('result_ref');payload=live.load(ref) if ref else {};meta=payload.get('metadata',{}).get('media_enrichment',{})
  data.update(kind='natural_body',chars=len(payload.get('body') or ''),method=meta.get('extraction_method'),candidates=meta.get('candidate_summary'),access_path=meta.get('access_path'),identity=meta.get('identity_id'),publisher_published_at=meta.get('publisher_published_at'))
  print(json.dumps(data),flush=True)
 for row in db.execute('select ticker,body_outcome,published_at,source_version from test_publications where source_id=? and published_at>? order by published_at desc limit 12',('bloomberg_massive','2026-11-12T20:52:00+00:00')):
  print(json.dumps({'kind':'test_publication',**dict(row)}),flush=True)
