import sqlite3,json,hashlib,subprocess
from pathlib import Path
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');r=sqlite3.connect((b/'read/serving-v4-20261018/v2.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.3);s=sqlite3.connect((b/'runtime/runtime.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.3)
cp=r.execute("SELECT position FROM checkpoints WHERE source='runtime'").fetchone()[0];out={'events':[],'case_receipts':[]}
rows=s.execute('SELECT o.seq,o.table_name,o.operation,r.payload FROM v2_source_outbox o JOIN v2_source_receipts r ON r.id=o.receipt_id WHERE o.seq BETWEEN ? AND ? ORDER BY o.seq',(cp-1000,cp+500)).fetchall();wanted=set()
def value(raw):
 row=json.loads(raw);v=row.get('payload_json') or row.get('payload') or row.get('data_json') or row
 return json.loads(v) if isinstance(v,str) else v
for seq,table,op,raw in rows:
 if seq in [4624585,4624587,4624591,4624593,4624595]:
  v=value(raw);cid=v.get('case_id');wanted.add(cid)
  out['events'].append({'seq':seq,'table':table,'operation':op,'case_hash':hashlib.sha256(cid.encode()).hexdigest()[:12] if cid else None})
for seq,table,op,raw in rows:
 if table=='runtime_v2_cases':
  cid=value(raw).get('case_id')
  if cid in wanted:
   out['case_receipts'].append({'seq':seq,'operation':op,'case_hash':hashlib.sha256(cid.encode()).hexdigest()[:12],'gap':r.execute("SELECT reason FROM gaps WHERE source='runtime' AND event=?",(str(seq),)).fetchall()})
for unit in ['doxagent-release-resources.service','doxagent-resource-safety.service']:
 p=subprocess.run(['systemctl','cat',unit],capture_output=True,text=True,timeout=10);out[unit]=p.stdout
print(json.dumps(out))
