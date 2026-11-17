import subprocess,json,sqlite3
from pathlib import Path
out={}
for role in ['v2-site-access','v2-content-enrichment']:
 code='import ast,json;from pathlib import Path;p=Path("/app/src/doxagent/message_bus_v2/publisher_http.py");s=p.read_text();m=ast.parse(s);print(json.dumps({"lines":len(s.splitlines()),"joint_permits":any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=="joint_permits" for n in m.body),"old_held_reservation":"held = blocked" in s}))'
 out[role]=json.loads(subprocess.check_output(['docker','exec','doxagent-v2-'+role+'-1','python','-c',code]))
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3');c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.execute('pragma query_only=on')
for name,sql in c.execute("select name,sql from sqlite_master where type='table' and (name like '%session%' or name like '%login%')"):
 out[name]={'columns':[r[1] for r in c.execute('pragma table_info('+name+')')]}
print(json.dumps(out));c.close()
