import sqlite3,json
p='/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3'
c=sqlite3.connect('file:'+p+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
rows=[dict(r) for r in c.execute("SELECT source_id,ticker,unit,json_extract(state,'$.status') status,json_extract(state,'$.last_success_at') success,json_extract(state,'$.last_attempt_at') attempt,json_extract(state,'$.last_error_code') error FROM units WHERE source_id='spacenews_rss'")]
print(json.dumps(rows));c.close()
