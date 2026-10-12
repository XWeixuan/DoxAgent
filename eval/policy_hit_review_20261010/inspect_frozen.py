import gzip,hashlib,json,sqlite3
from pathlib import Path
R=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/read')
d=sqlite3.connect('file:'+str(R/'v2.sqlite3')+'?mode=ro',uri=True);d.execute('PRAGMA query_only=ON');d.execute('BEGIN')
p=json.loads(d.execute("SELECT payload FROM object_current WHERE kind='native:runtime_v2_cases' AND id='case_c35b297c6dec48f88f44c00f96128821'").fetchone()[0])
def dec(v):
 if isinstance(v,dict) and '$doxagent_content_v1' in v:
  h=v['$doxagent_content_v1']; root=R/'native-files'/h[:2]/h
  m=json.loads((root/'manifest.json').read_text());raw=b''.join(gzip.decompress((root/str(i)).read_bytes()) for i in range(len(m['chunks'])))
  assert hashlib.sha256(raw).hexdigest()==h
  return dec(json.loads(raw))
 if isinstance(v,dict):return {k:dec(x) for k,x in v.items()}
 if isinstance(v,list):return [dec(x) for x in v]
 return v
f=dec(p['frozen_inputs']);print(json.dumps({'keys':list(f),'projection_keys':list((f.get('projection') or {})),'policies_count':len((f.get('projection') or {}).get('policies',[])),'matching_policy':next((x for x in (f.get('projection') or {}).get('policies',[]) if x.get('policy_id')=='pol_f7cde392e3eb2e1ef3d3'),None)},ensure_ascii=False))
d.rollback()
