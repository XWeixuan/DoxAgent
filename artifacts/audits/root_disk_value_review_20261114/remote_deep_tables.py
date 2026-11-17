import os,json,sqlite3,time,subprocess
from pathlib import Path
os.nice(10)
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');out={'facts':{},'errors':{}}
queries=[('runtime/runtime.sqlite3','namespace_bytes',"SELECT namespace,count(*),sum(octet_length(payload)) FROM runtime_values GROUP BY namespace ORDER BY 3 DESC",35),('bus/acquisition.sqlite3','cycles_status',"SELECT status,count(*),min(dispatch_at),max(dispatch_at),sum(octet_length(data_json)) FROM cycles GROUP BY status",35),('bus/acquisition.sqlite3','physical_bytes',"SELECT count(*),sum(octet_length(requests_json)) FROM physical_cycles",20)]
for rel,key,sql,budget in queries:
 d=sqlite3.connect((b/rel).as_uri()+'?mode=ro',uri=True,timeout=.2);d.execute('PRAGMA query_only=ON');start=time.monotonic();d.set_progress_handler(lambda:time.monotonic()-start>budget,1000)
 try:out['facts'][key]=d.execute(sql).fetchall()
 except sqlite3.Error as e:out['errors'][key]=str(e)
 d.close()
out['df']=subprocess.run(['df','-B1','/'],capture_output=True,text=True).stdout
print(json.dumps(out))
