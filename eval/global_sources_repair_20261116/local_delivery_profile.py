import sqlite3,json,time,os
p=os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH'];c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=2);c.execute('pragma query_only=on')
queries={
 'alias':("SELECT DISTINCT v.raw_message_id FROM message_identity_aliases a JOIN logical_message_versions v ON v.ticker=a.ticker AND v.logical_message_id=a.logical_message_id JOIN standard_messages s ON s.raw_message_id=v.raw_message_id JOIN stream_members m ON m.standard_message_id=s.standard_message_id WHERE a.ticker=? AND a.alias IN (SELECT value FROM json_each(?)) LIMIT 8",('MU','["url:https://investing.com/audit-no-match"]')),
 'fingerprint':("SELECT v.raw_message_id FROM logical_message_versions v JOIN standard_messages s ON s.raw_message_id=v.raw_message_id JOIN stream_members m ON m.standard_message_id=s.standard_message_id WHERE v.ticker=? AND v.body_fingerprint=? LIMIT 32",('MU','audit-no-match')),
 'url':("SELECT r.raw_message_id FROM raw_messages r JOIN standard_messages s ON s.raw_message_id=r.raw_message_id JOIN stream_members m ON m.standard_message_id=s.standard_message_id WHERE r.ticker=? AND json_extract(r.data_json,'$.url')=? LIMIT 4",('MU','https://investing.com/audit-no-match')),
 'delivery':("SELECT d.article_key,a.first_seen_at FROM live_deliveries d JOIN live_articles a USING(article_key) WHERE d.state='PENDING' AND d.due_at<=? AND a.result_ref IS NOT NULL ORDER BY (a.delivery_mode='test'),(a.first_seen_at>=? OR a.work_kind='content_update') DESC,a.first_seen_at DESC,d.due_at,d.article_key LIMIT 32",('2026-11-16T23:59:00+00:00','2026-11-16T13:00:00+00:00')),
}
out={}
queries['alias_ordered']=(queries['alias'][0].replace(' a JOIN logical_message_versions',' a CROSS JOIN logical_message_versions').replace(' JOIN standard_messages',' CROSS JOIN standard_messages').replace(' JOIN stream_members',' CROSS JOIN stream_members'),queries['alias'][1])
for name,(sql,args) in queries.items():
 count=[0];started=time.monotonic()
 def progress():
  count[0]+=1000
  return time.monotonic()-started>3
 plan=[list(r) for r in c.execute('explain query plan '+sql,args)]
 c.set_progress_handler(progress,1000)
 try:r={'rows':len(c.execute(sql,args).fetchall())}
 except Exception as e:r={'error':type(e).__name__}
 c.set_progress_handler(None,0)
 out[name]={'seconds':round(time.monotonic()-started,4),'instructions':count[0],'plan':plan,**r}
print(json.dumps(out))
