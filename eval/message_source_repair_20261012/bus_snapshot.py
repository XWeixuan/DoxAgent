"""Compact read-only natural polling and durable sweep snapshot."""
import json,sqlite3
from datetime import UTC,datetime
d=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=2)
states=[json.loads(r[0]) for r in d.execute('select data_json from poll_states')]
wanted=('barrons_ticker_news','reuters_site_search','investorshub_ticker_news','investing_ticker_news','digitimes_semiconductors','digitimes_tw_rss','huggingnews_rss','trendforce_news','trendforce_press','yahoo_finance_news','globenewswire_semiconductors_rss')
print(json.dumps({'at':datetime.now(UTC).isoformat(),'states':[{k:v.get(k) for k in ('binding_id','source_id','status','last_attempt_at','last_success_at','last_error_code','last_error_message','consecutive_failures','collected_count','filtered_count')} for v in states if any(w in v['source_id'] for w in wanted)],'runs':[list(r) for r in d.execute("select status,count(*) from distribution_runs group by status")]}))
