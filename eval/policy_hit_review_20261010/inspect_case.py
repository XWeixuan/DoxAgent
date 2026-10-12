import json, sqlite3
R='/var/lib/docker/volumes/doxagent-v2_v2-data/_data'
c='case_c35b297c6dec48f88f44c00f96128821'
d=sqlite3.connect('file:'+R+'/read/v2.sqlite3?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('PRAGMA query_only=ON');d.execute('BEGIN')
for kind in ('native:runtime_v2_cases','message','case_reasoning','execution','realized_pnl','native:runtime_values'):
 rows=d.execute('SELECT id,payload FROM object_current WHERE kind=? AND ticker=? AND (id=? OR parent=?) LIMIT 3',(kind,'BE',c,c)).fetchall()
 for row in rows:
  p=json.loads(row['payload'])
  if kind=='native:runtime_v2_cases':
   p={'frozen_inputs_keys':list(p['frozen_inputs']), 'projection_keys':list((p['frozen_inputs'].get('projection') or {})), 'source':p['source'], 'w1_final':p['w1_final'], 'w2_final':p['w2_final'], 'w3_result':p['w3_result'], 'frozen_policy_first':(p['frozen_inputs'].get('projection') or {}).get('policies',[])[:1]}
  if kind=='message':
   p={'body':p['body'],'source_published_at':p['source_published_at'],'title':p['title'],'url':p['url']}
  if kind=='case_reasoning':
   p={k:str(v)[:800] for k,v in p.items()}
  print(json.dumps({'kind':kind,'id':row['id'],'payload':p},ensure_ascii=False))
d.rollback()
