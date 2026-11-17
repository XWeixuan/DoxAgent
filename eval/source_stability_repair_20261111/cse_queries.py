import sqlite3,json
c=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3?mode=ro',uri=True)
queries=set()
for (raw,) in c.execute("select state from units where source_id='calcalist_search' and required>0"):
 s=json.loads(raw);cp=s.get('checkpoint',{})
 if s.get('checkpoint_ref'):
  r=c.execute('select checkpoint from physical_checkpoints where identity=?',(s['checkpoint_ref'],)).fetchone();cp=json.loads(r[0]) if r else cp
 queries.update(cp.get('endpoints',{}))
print(json.dumps(sorted(queries)))
