import sqlite3,json,re
c=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
q="""select dispatch_at,reason,json_extract(data_json,'$.terminal_error_message') message,json_extract(data_json,'$.http_timing') timing from cycles indexed by cycles_time where dispatch_at>=? and source_id='digitimes_semiconductors' and status='FAILED' group by json_extract(data_json,'$.physical_run_id')"""
rows=[]
for r in c.execute(q,('2026-11-11T17:49:24+00:00',)):
 v=dict(r);v['message']=re.sub(r'https?://[^\s;]+','<url>',v['message'] or '')[:300];rows.append(v)
print(json.dumps(rows))
