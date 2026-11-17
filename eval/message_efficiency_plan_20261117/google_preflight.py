import sqlite3,os,json,hashlib,inspect
from datetime import datetime,UTC,timedelta
p=os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH'];c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
s=json.loads(c.execute("select data_json from source_definitions where source_id='google_news_search_rss'").fetchone()[0]);out={'at':datetime.now(UTC).isoformat(),'source':{k:s.get(k) for k in ['source_id','version','acquisition_mode','parameter_schema','distribution_policy','enabled']},'bindings':[],'model_env_enabled':os.environ.get('DOXAGENT_MESSAGE_BUS_JEV_ENABLED'),'model_key_present':bool(os.environ.get('DASHSCOPE_API_KEY'))}
for r in c.execute("select data_json from ticker_source_bindings where source_id='google_news_search_rss' and tombstoned_at is null order by ticker"):
 d=json.loads(r[0]);out['bindings'].append({k:d.get(k) for k in ['binding_id','ticker','version','enabled','source_parameters','polling','streaming','source_version']})
out['tickers']=[r[0] for r in c.execute('select ticker from ticker_monitoring_states')]
out['terms']=[dict(r) for r in c.execute('select ticker,current_revision from ticker_monitoring_terms')]
out['recent_google']=[dict(r) for r in c.execute("select d.ticker,d.state,d.initial_decision,d.regex_result,d.jev_result,d.last_error,count(*) n from live_articles a indexed by live_article_observed join live_deliveries d using(article_key) where a.first_seen_at>=? and d.source_id='google_news_search_rss' group by 1,2,3,4,5,6",((datetime.now(UTC)-timedelta(hours=1)).isoformat(),))]
print(json.dumps(out,ensure_ascii=False))
