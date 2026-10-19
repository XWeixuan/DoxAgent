"""Read-only shared run progress and live subscription settings."""
import json, sqlite3, subprocess
from pathlib import Path
c=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-message-bus-1']))[0]
volume=next(m['Source'] for m in c['Mounts'] if m['Destination']=='/data')
d=sqlite3.connect('file:'+volume+'/bus/bus.sqlite3?mode=ro',uri=True,timeout=2)
d.row_factory=sqlite3.Row
out={}
out['terms']=[dict(r) for r in d.execute('SELECT * FROM ticker_monitoring_terms')]
out['recent_runs']=[dict(r) for r in d.execute('SELECT source_id,mode,status,created_at,lease_until,coverage FROM distribution_runs ORDER BY created_at DESC LIMIT 35')]
out['jobs']=[dict(r) for r in d.execute('SELECT status,count(*) n FROM content_enrichment_jobs GROUP BY status')]
out['live_bindings']=[{k:v.get(k) for k in ['ticker','source_id','enabled','active_hours','polling','source_parameters']} for raw, in d.execute('SELECT data_json FROM ticker_source_bindings') for v in [json.loads(raw)] if v['source_id'] in ['trendforce_news','thelec_semiconductors_rss','google_news_search_rss']]
print(json.dumps(out))
