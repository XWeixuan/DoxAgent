import json,sqlite3
R='/var/lib/docker/volumes/doxagent-v2_v2-data/_data'
eid='trade:case_7b76ce24051a44c990d602d5db7b8027'
d=sqlite3.connect('file:'+R+'/runtime/runtime.sqlite3?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('PRAGMA query_only=ON');d.execute('BEGIN')
for table in ('te_executions','te_jobs','te_attempts','te_lots','te_allocations'):
 q={'te_executions':('id=?',(eid,)),'te_jobs':('id LIKE ?',(eid+'%',)),'te_attempts':('id LIKE ?',(eid+'%',)),'te_lots':('id=?',(eid,)),'te_allocations':('lot_id=?',(eid,))}[table]
 for r in d.execute('SELECT * FROM '+table+' WHERE '+q[0],q[1]):
  p=dict(r)
  for k in list(p):
   if k in ('account','expected_account_id'): p[k]='[REDACTED_ACCOUNT]'
  if 'payload' in p:
   p['payload']=json.loads(p['payload'])
   for k in ('account','expected_account_id'):
    if k in p['payload']:p['payload'][k]='[REDACTED_ACCOUNT]'
  print(json.dumps({'table':table,'row':p},ensure_ascii=False))
  if table=='te_allocations':
   f=d.execute('SELECT payload FROM te_fills WHERE exec_id=?',(r['fill_id'],)).fetchone()
   print(json.dumps({'table':'te_fills','row':json.loads(f['payload']) if f else None},ensure_ascii=False))
d.rollback()
