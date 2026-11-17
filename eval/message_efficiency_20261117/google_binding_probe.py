import json,sqlite3,os,inspect,hashlib
from doxagent.message_bus_v2 import live,relevance
c=sqlite3.connect('file:'+os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');out={'bindings':[]}
for r in c.execute("select data_json from ticker_source_bindings where tombstoned_at is null and source_id='google_news_search_rss'"):
 d=json.loads(r[0]);out['bindings'].append({k:d.get(k) for k in ['ticker','enabled','version','created_at','updated_at']}|{'require_relevance':d.get('source_parameters',{}).get('require_relevance'),'parameter_names':list(d.get('source_parameters',{}))})
out['term_l1']=[{'ticker':r['ticker'],'l1':json.loads(r['config_json']).get('l1'),'definition':json.loads(r['config_json']).get('definition')} for r in c.execute('select r.* from ticker_monitoring_term_revisions r join ticker_monitoring_terms t on r.ticker=t.ticker and r.revision=t.current_revision')]
print(json.dumps(out,ensure_ascii=False))
