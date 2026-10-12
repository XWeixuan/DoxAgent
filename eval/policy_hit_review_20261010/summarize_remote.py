import json, sqlite3
from datetime import datetime, timedelta, timezone
R='/var/lib/docker/volumes/doxagent-v2_v2-data/_data'
d=sqlite3.connect('file:'+R+'/read/v2.sqlite3?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('PRAGMA query_only=ON');d.execute('BEGIN')
since=(datetime.now(timezone.utc)-timedelta(days=21)).isoformat()
hit_rows=[]
for table in ('object_current','objects'):
 for row in d.execute("SELECT ticker,id,parent,payload,valid_from,valid_to FROM "+table+" WHERE kind='policy_hit_observation' AND json_extract(payload,'$.hit')=1"):
  p=json.loads(row['payload']); at=p['occurred_at']
  if at and datetime.fromisoformat(at.replace('Z','+00:00'))>=datetime.fromisoformat(since):
   hit_rows.append({'ticker':row['ticker'],'id':row['id'],'case_id':row['parent'],'at':at,'policy_id':p['policy_id'],'table':table,'from':row['valid_from'],'to':row['valid_to']})
found={}
for h in hit_rows: found.setdefault((h['ticker'],h['id']),[]).append(h)
print('WINDOW',since,'RAW_ROWS',len(hit_rows),'DISTINCT_HITS',len(found))
for (ticker,hid),rows in sorted(found.items()):
 case_id=rows[0]['case_id']
 c=d.execute("SELECT payload FROM object_current WHERE kind='case' AND ticker=? AND id=?",(ticker,case_id)).fetchone()
 case=json.loads(c['payload']) if c else None
 e=[json.loads(r['payload']) for r in d.execute("SELECT payload FROM object_current WHERE kind='execution' AND ticker=? AND parent=?",(ticker,case_id))]
 p=[]
 for ex in e:
  p.extend(json.loads(r['payload']) for r in d.execute("SELECT payload FROM object_current WHERE kind='realized_pnl' AND ticker=? AND parent=?",(ticker,ex['execution_id'])))
 current=d.execute("SELECT payload FROM object_current WHERE kind='policy_hit_observation' AND ticker=? AND id=?",(ticker,hid)).fetchone()
 current=json.loads(current['payload']) if current else None
 print(json.dumps({'ticker':ticker,'hit_id':hid,'at':rows[0]['at'],'revisions':len(rows),'current_hit':current['hit'] if current else None,'case_id':case_id,'case_present':bool(case),'case_hit':case['final_policy_hit']['value'] if case else None,'trade':case['trade'] if case else None,'trade_disposition':case['trade_disposition'] if case else None,'execution':e,'pnl':p},ensure_ascii=False))
d.rollback()
