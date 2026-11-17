import json,sqlite3,statistics
from datetime import datetime
c=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
out={}
for sid in ['access_newswire_massive','businesswire_massive','prnewswire_massive','globenewswire_massive','tmx_newsfile_massive']:
 rows=[dict(r) for a in c.execute('select article_key,first_seen_at,initial_result_at from live_articles indexed by live_article_observed where first_seen_at>=? and source_id=?',('2026-11-12T20:00:46',sid)).fetchall() for r in c.execute('select d.state,d.initial_decision_at,d.created_at,? initial_result_at from live_deliveries d where article_key=?',(a['initial_result_at'],a['article_key']))]
 vals=sorted((datetime.fromisoformat(r['initial_decision_at'])-datetime.fromisoformat(r['initial_result_at'])).total_seconds() for r in rows if r['initial_decision_at'] and r['initial_result_at'])
 out[sid]={'decisions':len(vals),'mean_wait':round(statistics.mean(vals),3) if vals else None,'p95_wait':vals[int(len(vals)*.95)] if vals else None,'max_wait':max(vals) if vals else None,'slow_examples':sorted([r for r in rows if r['initial_decision_at']],key=lambda r:(datetime.fromisoformat(r['initial_decision_at'])-datetime.fromisoformat(r['initial_result_at'])).total_seconds(),reverse=True)[:2]}
print(json.dumps(out))
