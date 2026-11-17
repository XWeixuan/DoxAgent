import sqlite3,json,re
c=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=[]
for r in c.execute("select distinct source_id,unit,json_extract(state,'$.last_error_message') message,json_extract(state,'$.http_timing') timing from units where source_id in ('calcalist_search','digitimes_semiconductors') and required>0"):
 v=dict(r);v['message']=re.sub(r'https?://[^\s;]+','<url>',v['message'] or '')[:300];rows.append(v)
print(json.dumps(rows))
